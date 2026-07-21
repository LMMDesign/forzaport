"""Live DXIL binding extraction for material import (fail closed).

Disassembles per-family ``{shader}CarLightScenario.pcdxil.pso`` (one raster
pass — not a complete material schema) plus any SHA-keyed additional passes
from ``pass_contracts``. Recovers per-pass sample sites: UV, sampler,
components, opacity/gate evidence, tiling CB hashes.

Static pass analysis is cached by exact shaderbin SHA + PSO SHA + pass.
Instance evaluation (UVChoice, MatI bools) is never cached by shader name alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field

from ..parsing.disk_cache import zipfs_cache_dir
from .pass_contracts import (
    PRIMARY_RASTER_PASS,
    PassMergeSpec,
    additional_passes_for_sha,
)
from .pass_identity import parse_pass_identity, variant_from_member, stage_from_member

GENERATOR_VERSION = 8

_SIG_ROW = re.compile(r";\s+(\S+)\s+(\d+)\s+\S.*?\s+(\d+)\s+\S+\s+\S+")
_LOADIN = re.compile(
    r"@dx\.op\.loadInput\.\w+\(i32 \d+, i32 (\d+), i32 \d+, i8 (\d+)"
)
_SRV_LEGACY = re.compile(
    r"%(\d+) = call %dx\.types\.Handle @dx\.op\.createHandle\(i32 57, i8 (\d+), "
    r"i32 (\d+), i32 (\d+),"
)
_SRV_BIND = re.compile(
    r"%(\d+) = call %dx\.types\.Handle @dx\.op\.createHandleFromBinding\("
    r"i32 \d+, %dx\.types\.ResBind \{ i32 (\d+), i32 (\d+), i32 (\d+), i8 (\d+) \}, "
    r"i32 (\d+),"
)
_ANNOTATE = re.compile(
    r"%(\d+) = call %dx\.types\.Handle @dx\.op\.annotateHandle\("
    r"i32 \d+, %dx\.types\.Handle %(\d+),"
)
_SAMPLE = re.compile(
    r"%(\d+) = call %dx\.types\.ResRet\.\w+ @dx\.op\.(?:sample\w*)\.\w+\("
    r"i32 \d+, %dx\.types\.Handle %(\d+), %dx\.types\.Handle %(\d+), "
    r"float ([^,]+), float ([^,]+)"
)
_EXTRACT_COMP = re.compile(
    r"%\d+ = extractvalue %dx\.types\.ResRet\.\w+ %(\d+), (\d+)"
)
_CBUF = re.compile(
    r"@dx\.op\.cbufferLoadLegacy\.\w+\(i32 \d+, %dx\.types\.Handle %\d+, i32 (\d+)\)"
)
_EXTRACT_CB = re.compile(r"extractvalue %dx\.types\.CBufRet\.\w+ %(\d+), (\d+)")
_STORE_ALPHA = re.compile(
    r"storeOutput\.\w+\(i32 \d+, i32 \d+, i32 \d+, i8 3, float %(\d+)"
)
_DISCARD = re.compile(r"@dx\.op\.discard")
_FADD = re.compile(r"fadd(?:\s+fast)?\s+float")
_FMUL = re.compile(r"fmul(?:\s+fast)?\s+float")
_NORMAL_UNPACK = re.compile(
    r"fmul(?:\s+fast)?\s+float %(\d+), (?:float )?2(?:\.0+)?|"
    r"fadd(?:\s+fast)?\s+float %(\d+), (?:float )?-1(?:\.0+)?"
)


class ShaderBindingError(RuntimeError):
    """DXIL / PSO / dxc failure — material import must not soft-fallback."""


@dataclass
class TextureBinding:
    treg: int
    uv_semantic: int | None = None
    uv_semantics_all: list[int] = field(default_factory=list)
    comps: list[int] = field(default_factory=list)
    sampler_reg: int | None = None
    role: str | None = None
    channel_roles: dict[str, str] = field(default_factory=dict)
    opacity_from_w: bool = False
    opacity_gate_hash: int | None = None
    gate_bool_hashes: list[int] = field(default_factory=list)
    tiling_cb_hashes: list[int] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass
class ShaderBindings:
    shader_name: str
    textures: dict[int, TextureBinding] = field(default_factory=dict)
    source_hashes: dict[str, str] = field(default_factory=dict)
    pso_member: str = ""
    evidence: list[str] = field(default_factory=list)
    pass_name: str = PRIMARY_RASTER_PASS
    passes_analyzed: list[str] = field(default_factory=list)
    # Authoritative per-site records (not keyed by register alone).
    sample_sites: list[dict] = field(default_factory=list)
    compatibility_bridge_used: bool = False
    rejection_reasons: list[str] = field(default_factory=list)


@dataclass
class StaticShaderPassAnalysis:
    """Instance-independent DXIL facts for one PSO pass."""

    shader_name: str
    pass_name: str
    pso_member: str
    shaderbin_sha256: str
    pso_sha256: str
    textures: dict[int, TextureBinding] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    generator_version: int = GENERATOR_VERSION


@dataclass
class EvaluatedMaterialInstanceBindings:
    """Instance-evaluated bindings (UVChoice / MatI) over static pass analysis."""

    static: StaticShaderPassAnalysis
    bindings: ShaderBindings
    shaderbin_sha256: str


def _addon_dxc() -> str:
    env = os.environ.get("FORZA_DXC")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.realpath(__file__))
    candidates = [
        os.path.join(here, "..", "tools", "dxc", "bin", "x64", "dxc.exe"),
        os.path.join(here, "..", "..", "..", "_tools", "dxc", "bin", "x64", "dxc.exe"),
    ]
    cur = here
    for _ in range(8):
        cur = os.path.dirname(cur)
        if not cur or cur == os.path.dirname(cur):
            break
        candidates.append(os.path.join(cur, "_tools", "dxc", "bin", "x64", "dxc.exe"))
        candidates.append(os.path.join(cur, "tools", "dxc", "bin", "x64", "dxc.exe"))
    for cand in candidates:
        path = os.path.normpath(cand)
        if os.path.isfile(path):
            return path
    raise ShaderBindingError(
        "dxc.exe not found (expected addon tools/dxc, repo _tools/dxc, or FORZA_DXC). "
        "DXIL binding extraction is required for materials."
    )


def _decode_disasm(raw: bytes) -> str:
    txt = raw.decode("utf-8", "replace")
    if "Input signature" not in txt:
        txt = raw.decode("utf-16", "replace")
    if "Input signature" not in txt and "@dx.op" not in txt:
        raise ShaderBindingError("dxc produced no usable DXIL disassembly")
    return txt


def _disasm(dxc: str, data: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="forza_dxc_") as tmp:
        path = os.path.join(tmp, "s.bin")
        with open(path, "wb") as f:
            f.write(data)
        try:
            r = subprocess.run(
                [dxc, "-dumpbin", path],
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ShaderBindingError(f"dxc failed: {e}") from e
        if r.returncode != 0 and not r.stdout:
            err = (r.stderr or b"").decode("utf-8", "replace")[:500]
            raise ShaderBindingError(f"dxc -dumpbin failed (code {r.returncode}): {err}")
        return _decode_disasm(r.stdout)


def _parse_signature(lines, header: str) -> dict[int, tuple[str, int]]:
    sig: dict[int, tuple[str, int]] = {}
    state = "search"
    row = 0
    for ln in lines:
        if state == "done":
            break
        if state == "search":
            if ln.startswith(header):
                state = "wait"
            continue
        if state == "wait":
            if ln.startswith("; ---"):
                state = "rows"
            continue
        if state == "rows":
            m = _SIG_ROW.match(ln)
            if m:
                sig[row] = (m.group(1), int(m.group(2)))
                row += 1
            elif ln.startswith("; shader") or (
                header.endswith("Output signature:") and ln.strip() == "" and row
            ):
                state = "done"
            elif not ln.startswith(";"):
                state = "done"
    return sig


def _build_ssa(lines):
    defs = {}
    loadin = {}
    for ln in lines:
        m = re.match(r"\s*%(\d+) = (.*)", ln)
        if m:
            defs[m.group(1)] = m.group(2)
            lm = _LOADIN.search(m.group(2))
            if lm:
                loadin[m.group(1)] = (int(lm.group(1)), int(lm.group(2)))
    return defs, loadin


def _texcoord_semantic(sig, row):
    e = sig.get(row)
    return e[1] if e and e[0] == "TEXCOORD" else None


def _trace_ssa_to_loadin(rid, defs, loadin, seen=None, depth=0):
    if seen is None:
        seen = set()
    if rid in seen or depth > 96:
        return []
    seen.add(rid)
    if rid in loadin:
        return [loadin[rid]]
    rhs = defs.get(rid, "")
    out = []
    for op in re.findall(r"%(\d+)", rhs):
        out.extend(_trace_ssa_to_loadin(op, defs, loadin, seen, depth + 1))
    return out


def _ancestors(ssa_id, defs, max_depth=80):
    seen = set()
    stack = [ssa_id]
    while stack and len(seen) < max_depth:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        rhs = defs.get(cur, "")
        stack.extend(re.findall(r"%(\d+)", rhs))
    return seen


def _descendants(start_ids, uses, max_depth=96):
    seen = set(start_ids)
    stack = list(start_ids)
    while stack and len(seen) < max_depth:
        cur = stack.pop()
        for child in uses.get(cur, ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _ssa_uses(defs):
    uses = defaultdict(set)
    for rid, rhs in defs.items():
        for op in re.findall(r"%(\d+)", rhs):
            uses[op].add(rid)
    return uses


def _resolve_handles(ps_txt: str) -> tuple[dict[str, int], dict[str, int]]:
    """Return (srv_handle->treg, sampler_handle->sreg)."""
    srv: dict[str, int] = {}
    smp: dict[str, int] = {}
    for m in _SRV_LEGACY.finditer(ps_txt):
        cls, idx = int(m.group(2)), int(m.group(4))
        if cls == 0:
            srv[m.group(1)] = idx
        elif cls == 3:
            smp[m.group(1)] = idx
    for m in _SRV_BIND.finditer(ps_txt):
        cls = int(m.group(5))
        idx = int(m.group(6))
        if cls == 0:
            srv[m.group(1)] = idx
        elif cls == 3:
            smp[m.group(1)] = idx
    changed = True
    while changed:
        changed = False
        for m in _ANNOTATE.finditer(ps_txt):
            dst, src = m.group(1), m.group(2)
            if dst not in srv and src in srv:
                srv[dst] = srv[src]
                changed = True
            if dst not in smp and src in smp:
                smp[dst] = smp[src]
                changed = True
    return srv, smp


def _cb_bool_loads(ps_txt: str, cbmp: dict[int, int]) -> dict[int, set[str]]:
    """CBMP hash -> SSA ids loaded from that cbuffer slot (static; no MatI types)."""
    defs, _ = _build_ssa(ps_txt.splitlines())
    slot_to_hashes: dict[tuple[int, int], list[int]] = defaultdict(list)
    for h, off in cbmp.items():
        slot_to_hashes[(off // 16, (off % 16) // 4)].append(int(h))
    loads: dict[int, set[str]] = defaultdict(set)
    for rid, rhs in defs.items():
        em = re.search(r"extractvalue %dx\.types\.CBufRet\.\w+ %(\d+), (\d+)", rhs)
        if not em:
            continue
        src_rhs = defs.get(em.group(1), "")
        cm = _CBUF.search(src_rhs)
        if not cm:
            continue
        slot = (int(cm.group(1)), int(em.group(2)))
        for h in slot_to_hashes.get(slot, ()):
            loads[h].add(rid)
    return loads


def _alpha_output_ssa(ps_txt: str) -> set[str]:
    return {m.group(1) for m in _STORE_ALPHA.finditer(ps_txt)}


def _feeds_alpha_or_discard(ps_txt: str, ssa_ids: set[str], defs) -> bool:
    if not ssa_ids:
        return False
    alpha = _alpha_output_ssa(ps_txt)
    for aid in alpha:
        if ssa_ids & _ancestors(aid, defs) or aid in ssa_ids:
            return True
    if _DISCARD.search(ps_txt):
        for ln in ps_txt.splitlines():
            if "@dx.op.discard" not in ln:
                continue
            for rid in re.findall(r"%(\d+)", ln):
                if ssa_ids & _ancestors(rid, defs) or rid in ssa_ids:
                    return True
    return False


def _bool_gates_target(bool_ssa: set[str], target_ssa: set[str], defs) -> bool:
    if not bool_ssa or not target_ssa:
        return False
    for tid in target_ssa:
        if bool_ssa & _ancestors(tid, defs):
            return True
    return False


def _extract_id_for_comp(ps_txt: str, res_id: str, comp: int) -> set[str]:
    ids = set()
    for ln in ps_txt.splitlines():
        m = re.match(
            rf"\s*%(\d+) = extractvalue %dx\.types\.ResRet\.\w+ %{res_id}, {comp}\b",
            ln,
        )
        if m:
            ids.add(m.group(1))
    return ids


def _comp_used_as_rgb_vector(defs, uses, res_id: str, extract_map: dict[int, set[str]]) -> bool:
    """True when x/y/z extracts (or the ResRet) feed a vector-style path together."""
    xyz = set()
    for c in (0, 1, 2):
        xyz |= extract_map.get(c, set())
    if len(xyz) < 2:
        return False
    # Shared consumer of multiple comps → color/normal vector use.
    consumers = None
    for eid in xyz:
        desc = _descendants({eid}, uses, max_depth=24)
        consumers = desc if consumers is None else (consumers & desc)
    return bool(consumers)


def _comp_multiplies_color(defs, uses, extract_ids: set[str]) -> bool:
    for eid in extract_ids:
        for uid in uses.get(eid, ()):
            rhs = defs.get(uid, "")
            if _FMUL.search(rhs):
                return True
    return False


def _looks_like_normal_unpack(defs, uses, extract_ids: set[str]) -> bool:
    for eid in extract_ids:
        for uid in _descendants({eid}, uses, max_depth=12):
            rhs = defs.get(uid, "")
            if _NORMAL_UNPACK.search(rhs) or (
                _FMUL.search(rhs) and "2" in rhs and _FADD.search(defs.get(uid, ""))
            ):
                return True
            if "2.000000e+00" in rhs or " -1.000000e+00" in rhs:
                return True
    return False


def _classify_binding(
    *,
    ps_txt: str,
    defs,
    uses,
    bind: TextureBinding,
    res_ids: set[str],
    extract_map: dict[int, set[str]],
    bool_loads: dict[int, set[str]],
) -> None:
    comps = set(bind.comps)
    w_ids: set[str] = set()
    for res in res_ids:
        w_ids |= _extract_id_for_comp(ps_txt, res, 3)
    opacity = _feeds_alpha_or_discard(ps_txt, w_ids, defs)
    bind.opacity_from_w = bool(opacity and 3 in comps)
    if bind.opacity_from_w:
        bind.evidence.append("w_feeds_sv_target_alpha_or_discard")
        for h, ids in bool_loads.items():
            if _bool_gates_target(ids, w_ids, defs):
                bind.opacity_gate_hash = h
                bind.gate_bool_hashes.append(h)
                bind.evidence.append(f"opacity_gate_0x{h:08X}")
                break

    # Non-W opacity (e.g. Alpha t16 R → SV_Target alpha on car_livery SimpleCarLight).
    for comp in sorted(comps):
        if comp == 3 and bind.opacity_from_w:
            continue
        ids: set[str] = set()
        for res in res_ids:
            ids |= _extract_id_for_comp(ps_txt, res, comp)
        if not _feeds_alpha_or_discard(ps_txt, ids, defs):
            continue
        ch = "xyzw"[comp] if 0 <= comp <= 3 else f"c{comp}"
        bind.evidence.append(f"{ch}_feeds_sv_target_alpha_or_discard")
        bind.channel_roles["opacity"] = ch
        if comps == {comp}:
            bind.role = "alpha"
        for h, bids in bool_loads.items():
            if _bool_gates_target(bids, ids, defs):
                bind.opacity_gate_hash = h
                if h not in bind.gate_bool_hashes:
                    bind.gate_bool_hashes.append(h)
                bind.evidence.append(f"opacity_gate_0x{h:08X}")
                break
        if bind.role == "alpha":
            bind.evidence.append("alpha_mask_sample")
            return
        break

    xy_ids = set()
    for c in (0, 1):
        xy_ids |= extract_map.get(c, set())
    zw_ids = set()
    for c in (2, 3):
        zw_ids |= extract_map.get(c, set())

    if comps == {3} or (
        comps <= {3}
        and bind.opacity_from_w
        and not _comp_used_as_rgb_vector(defs, uses, "", extract_map)
    ):
        bind.role = "alpha"
        bind.channel_roles["opacity"] = (
            "w" if 3 in comps else comps_str(sorted(comps))[:1] or "x"
        )
        bind.evidence.append("alpha_mask_sample")
        return

    # Role/channel packing is assigned from TXMP NameHash in txmp_semantics.
    # DXIL only records sample shape evidence for diagnostics — never invents
    # RoughMetalAO swizzles (ao_default_comp2 / mul-trace AO reordering).
    scalar_comps = [c for c in (0, 1, 2) if c in comps and extract_map.get(c)]
    rgb_vector = _comp_used_as_rgb_vector(defs, uses, "", extract_map)
    if (3 in comps and len(comps) >= 3) and (
        rgb_vector or len(scalar_comps) >= 3 or comps >= {0, 1, 2}
    ):
        bind.evidence.append("rgba_sample_shape")
        if bind.opacity_from_w:
            bind.channel_roles["opacity"] = "w"
        bind.role = None
        return
    if len(scalar_comps) >= 3 and not rgb_vector:
        bind.evidence.append("packed_scalar_rgb_sample_shape")
        bind.role = None
        return

    if _looks_like_normal_unpack(defs, uses, xy_ids) or (
        comps <= {0, 1, 3}
        and len(comps & {0, 1}) == 2
        and _looks_like_normal_unpack(defs, uses, xy_ids | zw_ids)
    ):
        bind.evidence.append("normal_unpack_mad2_minus1")
        if bind.opacity_from_w:
            bind.channel_roles["opacity"] = "w"
        bind.role = None
        return

    if len(comps) == 1 or (len(comps) <= 2 and not rgb_vector):
        only = sorted(comps)[0] if comps else 0
        if _comp_multiplies_color(defs, uses, extract_map.get(only, set())):
            bind.evidence.append("scalar_multiplies_color")
        else:
            invert = False
            for eid in extract_map.get(only, set()):
                for uid in uses.get(eid, ()):
                    rhs = defs.get(uid, "")
                    if "fsub" in rhs and ("1.000000e+00" in rhs or "float 1" in rhs):
                        invert = True
                        break
            if invert:
                bind.evidence.append("scalar_one_minus_sample")
            else:
                bind.evidence.append("scalar_sample")
        bind.role = None
        return

    if rgb_vector or comps >= {0, 1, 2}:
        bind.evidence.append("rgb_vector_sample_shape")
        if bind.opacity_from_w:
            bind.channel_roles["opacity"] = "w"
        bind.role = None
        return

    bind.role = None
    bind.evidence.append("unclassified_sample")


def _resolve_uv_semantic(
    uvs: set[int],
    *,
    gate_bool_hashes: list[int],
    params: dict,
    txmp_name: str | None = None,
    shaderbin_sha256: str | None = None,
) -> tuple[int | None, list[str]]:
    """Pick one mesh TEXCOORD from DXIL candidates + SHA-keyed UVChoice.

    Multi-UV without a proven SHA UVChoice contract stays unresolved —
    never ``min(uvs)`` (MAT006).
    """
    del gate_bool_hashes  # sample-gate lists do not carry UVChoice
    evidence: list[str] = []
    if not uvs:
        evidence.append("no_mesh_texcoord")
        return None, evidence
    if len(uvs) == 1:
        return next(iter(uvs)), evidence

    from .uv.uv_choice_contracts import UV_CHOICE_BY_SHA, resolve_uv_choice_texcoord

    choice = resolve_uv_choice_texcoord(params, shaderbin_sha256=shaderbin_sha256)
    if choice is not None and shaderbin_sha256:
        texcoord, prov = choice
        policy = UV_CHOICE_BY_SHA.get(shaderbin_sha256)
        if policy is not None and (
            txmp_name is None or txmp_name in policy.applies_to_txmp
        ):
            if texcoord in uvs:
                evidence.append(prov.detail)
                return texcoord, evidence
            evidence.append(
                f"UVChoice wants TEXCOORD{texcoord} but DXIL candidates are {sorted(uvs)}"
            )
            return None, evidence

    evidence.append(f"multiple_texcoord_semantics:{sorted(uvs)}")
    return None, evidence


def _analyze_ps(ps_txt: str, cbmp: dict[int, int]) -> dict[int, TextureBinding]:
    """Static DXIL analysis — CBMP layout only; no MatI instance values."""
    lines = ps_txt.splitlines()
    sig = _parse_signature(lines, "; Input signature:")
    defs, loadin = _build_ssa(lines)
    uses = _ssa_uses(defs)
    srv, smp = _resolve_handles(ps_txt)
    bool_loads = _cb_bool_loads(ps_txt, cbmp)

    extracted: dict[str, set[int]] = defaultdict(set)
    for m in _EXTRACT_COMP.finditer(ps_txt):
        extracted[m.group(1)].add(int(m.group(2)))

    res_by_treg: dict[int, set[str]] = defaultdict(set)
    info: dict[int, TextureBinding] = {}
    uvs_by_treg: dict[int, set[int]] = defaultdict(set)

    for m in _SAMPLE.finditer(ps_txt):
        res, hnd, smp_hnd, c0, c1 = m.groups()
        if hnd not in srv:
            continue
        treg = srv[hnd]
        bind = info.setdefault(treg, TextureBinding(treg=treg))
        res_by_treg[treg].add(res)
        if smp_hnd in smp:
            bind.sampler_reg = smp[smp_hnd]
        for c in (c0, c1):
            cm = re.match(r"%(\d+)", c.strip())
            if not cm:
                continue
            for leaf_row, _ in _trace_ssa_to_loadin(cm.group(1), defs, loadin):
                t = _texcoord_semantic(sig, leaf_row)
                if t is not None:
                    uvs_by_treg[treg].add(t)
        comps = set(bind.comps) | extracted.get(res, set())
        bind.comps = sorted(comps)

        sample_targets = {res}
        for c in range(4):
            sample_targets |= _extract_id_for_comp(ps_txt, res, c)
        for h, ids in bool_loads.items():
            if _bool_gates_target(ids, sample_targets, defs):
                if h not in bind.gate_bool_hashes:
                    bind.gate_bool_hashes.append(h)

        tiling_hashes = set(bind.tiling_cb_hashes)
        for c in (c0, c1):
            cm = re.match(r"%(\d+)", c.strip())
            if not cm:
                continue
            anc = _ancestors(cm.group(1), defs)
            for rid in anc:
                rhs = defs.get(rid, "")
                em = re.search(
                    r"extractvalue %dx\.types\.CBufRet\.\w+ %(\d+), (\d+)", rhs
                )
                if not em:
                    continue
                src_rhs = defs.get(em.group(1), "")
                cbm = _CBUF.search(src_rhs)
                if not cbm:
                    continue
                row, comp = int(cbm.group(1)), int(em.group(2))
                off = row * 16 + comp * 4
                for h, byte_off in cbmp.items():
                    if byte_off == off or (
                        byte_off // 16 == row and (byte_off % 16) // 4 == comp
                    ):
                        tiling_hashes.add(h)
        bind.tiling_cb_hashes = sorted(tiling_hashes)

    if not info:
        raise ShaderBindingError("PSO has no texture sample sites in DXIL disassembly")

    for treg, bind in info.items():
        uvs = uvs_by_treg.get(treg, set())
        bind.uv_semantics_all = sorted(uvs)
        # Tentative UV from structure alone; builder re-resolves with SHA UVChoice.
        if len(uvs) == 1:
            bind.uv_semantic = next(iter(uvs))
        elif not uvs:
            bind.uv_semantic = None
            bind.evidence.append("no_mesh_texcoord")
        else:
            bind.uv_semantic = None
            bind.evidence.append(f"multiple_texcoord_semantics:{sorted(uvs)}")
        extract_map: dict[int, set[str]] = defaultdict(set)
        for res in res_by_treg[treg]:
            for comp in bind.comps:
                extract_map[comp] |= _extract_id_for_comp(ps_txt, res, comp)
        _classify_binding(
            ps_txt=ps_txt,
            defs=defs,
            uses=uses,
            bind=bind,
            res_ids=res_by_treg[treg],
            extract_map=extract_map,
            bool_loads=bool_loads,
        )
    return info


def resolve_binding_uv(
    bind: TextureBinding,
    params: dict,
    *,
    txmp_name: str | None = None,
    shaderbin_sha256: str | None = None,
) -> int | None:
    """Per-material UV pick: unique DXIL semantic, else SHA-keyed UVChoice."""
    uvs = set(bind.uv_semantics_all or [])
    if bind.uv_semantic is not None:
        uvs.add(int(bind.uv_semantic))
    if len(uvs) == 1:
        return next(iter(uvs))
    if not uvs:
        return None
    uv, _ev = _resolve_uv_semantic(
        uvs,
        gate_bool_hashes=list(bind.gate_bool_hashes or []),
        params=params,
        txmp_name=txmp_name,
        shaderbin_sha256=shaderbin_sha256,
    )
    return uv


def _cache_dir() -> str:
    d = os.path.join(zipfs_cache_dir(), "shader_descriptors")
    os.makedirs(d, exist_ok=True)
    return d


def _content_key(shaderbin: bytes, pso: bytes) -> str:
    h = hashlib.sha256()
    h.update(shaderbin)
    h.update(pso)
    return h.hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_cache(key: str) -> StaticShaderPassAnalysis | None:
    path = os.path.join(_cache_dir(), f"{key}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if raw.get("generator_version") != GENERATOR_VERSION:
        return None
    if raw.get("kind") not in (None, "static_pass"):
        # Reject legacy instance-blended caches (generator v5 and earlier).
        if raw.get("kind") != "static_pass" and "pass_name" not in raw:
            return None
    textures: dict[int, TextureBinding] = {}
    for treg_s, row in (raw.get("textures") or {}).items():
        textures[int(treg_s)] = TextureBinding(
            treg=int(treg_s),
            uv_semantic=row.get("uv_semantic"),
            uv_semantics_all=list(row.get("uv_semantics_all") or []),
            comps=list(row.get("comps") or []),
            sampler_reg=row.get("sampler_reg"),
            role=row.get("role"),
            channel_roles=dict(row.get("channel_roles") or {}),
            opacity_from_w=bool(row.get("opacity_from_w")),
            opacity_gate_hash=row.get("opacity_gate_hash"),
            gate_bool_hashes=list(row.get("gate_bool_hashes") or []),
            tiling_cb_hashes=list(row.get("tiling_cb_hashes") or []),
            evidence=list(row.get("evidence") or []),
        )
    return StaticShaderPassAnalysis(
        shader_name=raw["shader_name"],
        pass_name=raw.get("pass_name") or PRIMARY_RASTER_PASS,
        pso_member=raw.get("pso_member") or "",
        shaderbin_sha256=str((raw.get("source_hashes") or {}).get("shaderbin_sha256") or ""),
        pso_sha256=str((raw.get("source_hashes") or {}).get("pso_sha256") or ""),
        textures=textures,
        evidence=list(raw.get("evidence") or []),
        generator_version=int(raw.get("generator_version") or 0),
    )


def _save_cache(key: str, analysis: StaticShaderPassAnalysis) -> None:
    path = os.path.join(_cache_dir(), f"{key}.json")
    raw = {
        "kind": "static_pass",
        "generator_version": GENERATOR_VERSION,
        "generated_from": "dxil_pass_pso",
        "pass_name": analysis.pass_name,
        "source_hashes": {
            "shaderbin_sha256": analysis.shaderbin_sha256,
            "pso_sha256": analysis.pso_sha256,
            "descriptor_key": key,
        },
        "shader_name": analysis.shader_name,
        "pso_member": analysis.pso_member,
        "evidence": analysis.evidence,
        "textures": {
            str(t): {
                "uv_semantic": b.uv_semantic,
                "uv_semantics_all": b.uv_semantics_all,
                "comps": b.comps,
                "sampler_reg": b.sampler_reg,
                "role": b.role,
                "channel_roles": b.channel_roles,
                "opacity_from_w": b.opacity_from_w,
                "opacity_gate_hash": b.opacity_gate_hash,
                "gate_bool_hashes": b.gate_bool_hashes,
                "tiling_cb_hashes": b.tiling_cb_hashes,
                "evidence": b.evidence,
            }
            for t, b in analysis.textures.items()
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)


def _shader_search_roots(media_root: str) -> list[str]:
    """Directories that contain per-shader zips and/or monolithic Shaders.zip."""
    roots: list[str] = []
    for cars in ("cars", "Cars"):
        for rel in (
            (cars, "_library", "shaders"),
            (cars, "_library", "Shaders"),
            ("_library", "shaders"),
            ("_library", "Shaders"),
        ):
            d = os.path.join(media_root, *rel)
            if os.path.isdir(d) and d not in roots:
                roots.append(d)
    return roots


def _mono_shader_zips(media_root: str) -> list[str]:
    """Locate monolithic Shaders.zip only — never scan every zip under _library."""
    mono: list[str] = []
    for cars in ("cars", "Cars"):
        for rel in (
            (cars, "_library", "shaders", "Shaders.zip"),
            (cars, "_library", "Shaders", "Shaders.zip"),
            (cars, "_library", "Shaders.zip"),
            ("_library", "shaders", "Shaders.zip"),
            ("_library", "Shaders.zip"),
        ):
            path = os.path.join(media_root, *rel)
            if os.path.isfile(path) and path not in mono:
                mono.append(path)
    return mono


def _archive_candidates(media_root: str, shader_name: str, game_key: str | None) -> list[str]:
    """Prefer the direct ``{shader}.zip``; only fall back to Shaders.zip / scan when needed."""
    key = (game_key or "").lower()
    prefer_mono_first = key in ("fh5", "fm")
    direct_hits: list[str] = []
    for root in _shader_search_roots(media_root):
        direct = os.path.join(root, f"{shader_name}.zip")
        if os.path.isfile(direct):
            direct_hits.append(direct)
    # FH6 (and any title with per-shader archives): never open sibling zips.
    if direct_hits and not prefer_mono_first:
        return direct_hits

    mono = _mono_shader_zips(media_root)
    if prefer_mono_first:
        return mono + [p for p in direct_hits if p not in mono]

    # No direct zip — last resort: other per-shader archives that may embed the member.
    scanned: list[str] = []
    for root in _shader_search_roots(media_root):
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            if not entry.lower().endswith(".zip"):
                continue
            path = os.path.join(root, entry)
            if path not in direct_hits and path not in mono and path not in scanned:
                scanned.append(path)
    return direct_hits + mono + scanned


_ZIP_MEMBER_CACHE: dict[tuple[str, str, str], tuple[str, list[str]]] = {}
# Static pass analysis only — never cache instance-evaluated bindings by shader name.
_STATIC_PASS_MEM: dict[
    tuple[str, str, str, str, int, str, str, str, str], StaticShaderPassAnalysis
] = {}


def _find_zip_and_members(
    media_root: str, shader_name: str, game_key: str | None = None
) -> tuple[str, list[str]]:
    cache_key = (os.path.normcase(media_root), shader_name.lower(), (game_key or "").lower())
    hit = _ZIP_MEMBER_CACHE.get(cache_key)
    if hit is not None:
        return hit

    want = f"{shader_name}.shaderbin".lower()
    candidates = _archive_candidates(media_root, shader_name, game_key)
    if not candidates:
        raise ShaderBindingError(
            f"no shader archives under media root {media_root!r} "
            f"(expected cars/_library/shaders/*.zip or Shaders.zip)"
        )
    for path in candidates:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                for n in names:
                    base = os.path.basename(n.replace("\\", "/")).lower()
                    if base == want:
                        _ZIP_MEMBER_CACHE[cache_key] = (path, names)
                        return path, names
        except zipfile.BadZipFile as e:
            raise ShaderBindingError(f"bad shader archive {path}: {e}") from e
    raise ShaderBindingError(
        f"shaderbin {shader_name}.shaderbin not found in {len(candidates)} archive(s) "
        f"under {media_root!r}"
    )


def _prefer_standard_pso(exact: list[str], *, label: str) -> str:
    """Pick unique path; prefer ``_Standard`` technique over DXR duplicates."""
    if len(exact) == 1:
        return exact[0]
    standard = [
        n
        for n in exact
        if "/_standard/" in n.lower() or n.lower().startswith("_standard/")
    ]
    if len(standard) == 1:
        return standard[0]
    if len(standard) > 1:
        raise ShaderBindingError(f"ambiguous _Standard PSO for {label}: {standard}")
    raise ShaderBindingError(
        f"ambiguous PSO for {label} (no unique _Standard technique): {exact}"
    )


def _exact_named_pso(names: list[str], basename: str) -> str:
    want = basename.lower()
    exact = [
        n.replace("\\", "/")
        for n in names
        if os.path.basename(n.replace("\\", "/")).lower() == want
    ]
    if not exact:
        raise ShaderBindingError(f"missing PSO {basename}")
    return _prefer_standard_pso(exact, label=basename)


def _exact_carlight_pso(names: list[str], shader_name: str) -> str:
    """Require the exact raster CarLightScenario PSO (``_Standard`` technique).

    Archives may also ship DXR hit-group copies with the same basename; those are
    not the mesh/raster technique used for car import. CarLightScenario is one
    pass — additional passes come from ``pass_contracts`` by exact SHA.
    """
    return _exact_named_pso(names, f"{shader_name}CarLightScenario.pcdxil.pso")


def _static_mem_key(
    *,
    game_key: str,
    media_root: str,
    shaderbin_sha256: str,
    pso_sha256: str,
    pass_name: str,
    archive_member: str,
    variant: str,
    stage: str,
) -> tuple[str, str, str, str, int, str, str, str, str]:
    return (
        (game_key or "").lower(),
        os.path.normcase(media_root),
        shaderbin_sha256.lower(),
        pso_sha256.lower(),
        GENERATOR_VERSION,
        pass_name,
        archive_member.replace("\\", "/").lower(),
        (variant or "").lower(),
        (stage or "ps").lower(),
    )


def _clone_textures(src: dict[int, TextureBinding]) -> dict[int, TextureBinding]:
    out: dict[int, TextureBinding] = {}
    for treg, b in src.items():
        out[treg] = TextureBinding(
            treg=b.treg,
            uv_semantic=b.uv_semantic,
            uv_semantics_all=list(b.uv_semantics_all or []),
            comps=list(b.comps or []),
            sampler_reg=b.sampler_reg,
            role=b.role,
            channel_roles=dict(b.channel_roles or {}),
            opacity_from_w=bool(b.opacity_from_w),
            opacity_gate_hash=b.opacity_gate_hash,
            gate_bool_hashes=list(b.gate_bool_hashes or []),
            tiling_cb_hashes=list(b.tiling_cb_hashes or []),
            evidence=list(b.evidence or []),
        )
    return out


def _analyze_named_pass(
    *,
    media_root: str,
    shader_name: str,
    pass_name: str,
    pso_member: str,
    sb_bytes: bytes,
    pso_bytes: bytes,
    cbmp: dict[int, int],
    dxc: str,
    game_key: str,
    zip_basename: str,
) -> StaticShaderPassAnalysis:
    shaderbin_sha = _sha256(sb_bytes)
    pso_sha = _sha256(pso_bytes)
    variant = variant_from_member(pso_member)
    stage = stage_from_member(pso_member)
    mem_key = _static_mem_key(
        game_key=game_key or "",
        media_root=media_root,
        shaderbin_sha256=shaderbin_sha,
        pso_sha256=pso_sha,
        pass_name=pass_name,
        archive_member=pso_member,
        variant=variant,
        stage=stage,
    )
    hit = _STATIC_PASS_MEM.get(mem_key)
    if hit is not None:
        return hit

    disk_key = _content_key(
        sb_bytes,
        pso_bytes
        + f"\0PASS:{pass_name}\0MEMBER:{pso_member}\0VAR:{variant}\0".encode(),
    )
    cached = _load_cache(disk_key)
    if cached is not None and cached.pass_name == pass_name:
        _STATIC_PASS_MEM[mem_key] = cached
        return cached

    print(
        f"Forza: DXIL analyze {shader_name}/{variant or 'root'}/{pass_name} "
        f"(static; cached after this)",
        flush=True,
    )
    textures = _analyze_ps(_disasm(dxc, pso_bytes), cbmp)
    identity = parse_pass_identity(
        member=pso_member,
        shader_name=shader_name,
        shaderbin_sha256=shaderbin_sha,
        pso_sha256=pso_sha,
    )
    analysis = StaticShaderPassAnalysis(
        shader_name=shader_name,
        pass_name=pass_name,
        pso_member=pso_member,
        shaderbin_sha256=shaderbin_sha,
        pso_sha256=pso_sha,
        textures=textures,
        evidence=[
            f"pso={pso_member}",
            f"pass={pass_name}",
            f"variant={variant or 'root'}",
            f"stage={stage}",
            f"identity={identity.as_key()}",
            f"archive={zip_basename}",
        ],
    )
    _save_cache(disk_key, analysis)
    _STATIC_PASS_MEM[mem_key] = analysis
    return analysis


def _merge_pass_sites(
    *,
    textures: dict[int, TextureBinding],
    sample_sites: list[dict],
    primary: StaticShaderPassAnalysis,
    secondary: StaticShaderPassAnalysis,
    spec: PassMergeSpec,
    site_identity_key: str = "",
    resolved_texcoord: int | None = None,
    allow_same_register: bool = True,
) -> None:
    """Import one contracted sample site from a secondary pass.

    Never discards a secondary site solely because ``treg`` already exists in
    the primary register map — same-register multi-site cases are recorded in
    ``sample_sites``. The ``textures`` dict remains a compatibility bridge only.
    """
    for treg in spec.merge_texture_registers:
        src = secondary.textures.get(treg)
        if src is None:
            raise ShaderBindingError(
                f"pass contract {spec.pass_name} has no t{treg} sample "
                f"(shader={primary.shader_name!r} sha={primary.shaderbin_sha256[:12]}…)"
            )
        if spec.expected_uv_semantics is not None:
            if tuple(src.uv_semantics_all or []) != tuple(spec.expected_uv_semantics):
                # Multi-UV Select sites: expected_uv_semantics is None; unique only.
                raise ShaderBindingError(
                    f"pass contract {spec.pass_name} t{treg} UV drift: "
                    f"expected {list(spec.expected_uv_semantics)}, "
                    f"got {src.uv_semantics_all}"
                )
        if spec.expected_comps is not None:
            if tuple(src.comps or []) != tuple(spec.expected_comps):
                raise ShaderBindingError(
                    f"pass contract {spec.pass_name} t{treg} comps drift: "
                    f"expected {list(spec.expected_comps)}, got {src.comps}"
                )
        bind = _clone_textures({treg: src})[treg]
        if resolved_texcoord is not None:
            bind.uv_semantic = int(resolved_texcoord)
        elif len(bind.uv_semantics_all or []) == 1:
            bind.uv_semantic = int(bind.uv_semantics_all[0])
        if spec.require_sv_target_alpha:
            bind.role = "alpha"
            bind.channel_roles["opacity"] = "x"
            if not any("feeds_sv_target_alpha" in e for e in bind.evidence):
                raise ShaderBindingError(
                    f"pass contract {spec.pass_name} t{treg} lacks SV_Target alpha "
                    f"evidence: {bind.evidence}"
                )
        bind.evidence.append(
            f"sample_site_contract={spec.pass_name};{spec.evidence}"
        )
        site_rec = {
            "identity_key": site_identity_key
            or f"{secondary.pass_name}|t{treg}|{secondary.pso_member}",
            "pass_name": spec.pass_name,
            "archive_member": secondary.pso_member,
            "pso_sha256": secondary.pso_sha256,
            "texture_register": treg,
            "sampler_register": bind.sampler_reg,
            "comps": list(bind.comps or []),
            "uv_semantic": bind.uv_semantic,
            "uv_semantics_all": list(bind.uv_semantics_all or []),
            "role": bind.role,
            "same_register_as_primary": treg in textures,
            "evidence": list(bind.evidence or []),
        }
        sample_sites.append(site_rec)
        if treg in textures:
            if not allow_same_register:
                raise ShaderBindingError(
                    f"same-register site t{treg} from {spec.pass_name} conflicts "
                    f"with primary without multi-site support"
                )
            # Keep primary TextureBinding; secondary lives in sample_sites only.
            continue
        textures[treg] = bind


def extract_bindings(
    *,
    media_root: str,
    shader_name: str,
    params: dict,
    cbmp: dict[int, int],
    game_key: str | None = None,
) -> ShaderBindings:
    """Static CarLight analysis + exact-SHA sample-site contract evaluation.

    Additional passes contribute contracted sample sites (not register unions).
    Instance MatI values select UVChoice / variant; static PSO analysis stays
    cached without MatI contamination.

    ``textures`` remains a compatibility TextureBinding bridge keyed by register;
    authoritative per-site records live in ``sample_sites``.
    """
    from .sample_site_eval import evaluate_material_sample_sites

    if not shader_name:
        raise ShaderBindingError("shader_name is empty")
    if not media_root or not os.path.isdir(media_root):
        raise ShaderBindingError(f"media_root missing: {media_root!r}")

    zip_path, names = _find_zip_and_members(media_root, shader_name, game_key=game_key)
    pso_member = _exact_carlight_pso(names, shader_name)
    sb_member = None
    want = f"{shader_name}.shaderbin".lower()
    for n in names:
        if os.path.basename(n.replace("\\", "/")).lower() == want:
            sb_member = n
            break
    if sb_member is None:
        raise ShaderBindingError(f"shaderbin member missing in {zip_path}")

    dxc = _addon_dxc()
    with zipfile.ZipFile(zip_path, "r") as zf:
        sb_bytes = zf.read(sb_member)
        pso_bytes = zf.read(pso_member)
        primary = _analyze_named_pass(
            media_root=media_root,
            shader_name=shader_name,
            pass_name=PRIMARY_RASTER_PASS,
            pso_member=pso_member,
            sb_bytes=sb_bytes,
            pso_bytes=pso_bytes,
            cbmp=cbmp,
            dxc=dxc,
            game_key=game_key or "",
            zip_basename=os.path.basename(zip_path),
        )
        textures = _clone_textures(primary.textures)
        evidence = list(primary.evidence)
        passes_analyzed = [primary.pass_name]
        extra_pso_hashes: dict[str, str] = {}
        sample_sites: list[dict] = []
        compatibility_bridge_used = False
        rejection_reasons: list[str] = []

        # Seed primary-pass sites (one row per register present in analysis).
        for treg, bind in sorted(textures.items()):
            sample_sites.append(
                {
                    "identity_key": (
                        f"{primary.shaderbin_sha256[:16]}|{primary.pso_member}|"
                        f"{primary.pass_name}|t{treg}|primary"
                    ),
                    "pass_name": primary.pass_name,
                    "archive_member": primary.pso_member,
                    "pso_sha256": primary.pso_sha256,
                    "texture_register": treg,
                    "sampler_register": bind.sampler_reg,
                    "comps": list(bind.comps or []),
                    "uv_semantic": bind.uv_semantic,
                    "uv_semantics_all": list(bind.uv_semantics_all or []),
                    "role": bind.role,
                    "same_register_as_primary": False,
                    "origin": "primary_pass_bridge",
                    "evidence": list(bind.evidence or []),
                }
            )
            compatibility_bridge_used = True

        evaluated = evaluate_material_sample_sites(
            shaderbin_sha256=primary.shaderbin_sha256,
            params=params,
        )
        if evaluated.variant.status == "REJECTED":
            msg = f"variant_rejected:{evaluated.variant.provenance}"
            evidence.append(msg)
            rejection_reasons.append(msg)
            raise ShaderBindingError(
                f"variant selection rejected for {shader_name}: "
                f"{evaluated.variant.provenance}"
            )
        for reason in evaluated.rejection_reasons:
            evidence.append(f"sample_site:{reason}")
            rejection_reasons.append(reason)

        # Fail closed: unresolved/rejected *active* blender_import sites must not
        # silently fall back to CarLight-only register bindings.
        bad_active = [
            s
            for s in evaluated.sites
            if s.blender_import and s.status in ("REJECTED", "UNRESOLVED")
        ]
        if bad_active:
            detail = "; ".join(
                f"{s.sample_site_id}:{s.status}" for s in bad_active[:8]
            )
            raise ShaderBindingError(
                f"unresolved active sample site(s) for {shader_name}: {detail}"
            )

        active_sites = [
            s
            for s in evaluated.sites
            if s.blender_import and s.status == "ACTIVE"
        ]

        # One PassMergeSpec per site (compatibility adapter) — never collapse a
        # pass to first-site UV/comps for all registers.
        for spec in additional_passes_for_sha(primary.shaderbin_sha256):
            matching = [
                s
                for s in active_sites
                if s.scenario == spec.pass_name
                and s.texture_register in spec.merge_texture_registers
            ]
            if not matching and (
                spec.expected_uv_semantics is not None
                and len(spec.expected_uv_semantics) == 1
            ):
                # Unique-UV JSON contracts without MatI Select (livery/reflector).
                blocked = any(
                    s.scenario == spec.pass_name
                    and s.blender_import
                    and s.status == "UNRESOLVED"
                    for s in evaluated.sites
                )
                if blocked:
                    continue
                matching = []
                # Synthetic one-site merge using the spec register.
                matching_regs = list(spec.merge_texture_registers)
            else:
                matching_regs = [s.texture_register for s in matching]
            if not matching and not matching_regs:
                continue
            if not matching_regs:
                continue

            member = _exact_named_pso(names, spec.pso_basename)
            raw = zf.read(member)
            secondary = _analyze_named_pass(
                media_root=media_root,
                shader_name=shader_name,
                pass_name=spec.pass_name,
                pso_member=member,
                sb_bytes=sb_bytes,
                pso_bytes=raw,
                cbmp=cbmp,
                dxc=dxc,
                game_key=game_key or "",
                zip_basename=os.path.basename(zip_path),
            )
            # Merge each register as its own site (already one per spec typically).
            for treg in matching_regs:
                site = next(
                    (s for s in matching if s.texture_register == treg),
                    None,
                )
                narrowed = PassMergeSpec(
                    pass_name=spec.pass_name,
                    pso_basename=spec.pso_basename,
                    merge_texture_registers=(treg,),
                    expected_uv_semantics=spec.expected_uv_semantics,
                    expected_comps=(
                        site.sampled_components
                        if site and site.sampled_components
                        else spec.expected_comps
                    ),
                    require_sv_target_alpha=spec.require_sv_target_alpha,
                    evidence=spec.evidence,
                    blender_relevance=spec.blender_relevance,
                    expected_uv_expression=spec.expected_uv_expression,
                )
                _merge_pass_sites(
                    textures=textures,
                    sample_sites=sample_sites,
                    primary=primary,
                    secondary=secondary,
                    spec=narrowed,
                    site_identity_key=(
                        site.identity.as_key() if site else f"{spec.pass_name}|t{treg}"
                    ),
                    resolved_texcoord=(
                        site.resolved_texcoord if site else None
                    ),
                )
                compatibility_bridge_used = True
                if site is not None and site.resolved_texcoord is not None:
                    if treg in textures:
                        bind = textures[treg]
                        bind.uv_semantic = int(site.resolved_texcoord)
                        bind.evidence.append(
                            f"evaluated_sample_site={site.sample_site_id};"
                            f"TEXCOORD{site.resolved_texcoord}"
                        )
            if spec.pass_name not in passes_analyzed:
                passes_analyzed.append(spec.pass_name)
            evidence.append(f"sample_site_contract_pass={member}")
            extra_pso_hashes[f"pso_sha256:{spec.pass_name}"] = secondary.pso_sha256

    source_hashes = {
        "shaderbin_sha256": primary.shaderbin_sha256,
        "pso_sha256": primary.pso_sha256,
        "primary_pass": primary.pass_name,
        **extra_pso_hashes,
    }
    return ShaderBindings(
        shader_name=shader_name,
        textures=textures,
        source_hashes=source_hashes,
        pso_member=pso_member,
        evidence=evidence,
        pass_name=primary.pass_name,
        passes_analyzed=passes_analyzed,
        sample_sites=sample_sites,
        compatibility_bridge_used=compatibility_bridge_used,
        rejection_reasons=rejection_reasons,
    )


def comps_str(comps: list[int]) -> str:
    return "".join("xyzw"[i] for i in comps if 0 <= i <= 3)

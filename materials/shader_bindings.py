"""Live DXIL binding extraction for material import (fail closed).

Disassembles the exact ``{shader}CarLightScenario.pcdxil.pso`` with dxc and
recovers per-texture UV, sampler register, component liveness, opacity/gate
evidence, and tiling CB hashes. Principled role and channel packing are
assigned from TXMP NameHash strings in ``txmp_semantics`` — DXIL must not
invent RoughMetalAO swizzles. Results are cached under zipfs
``shader_descriptors/`` with content hashes and instruction provenance.
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

GENERATOR_VERSION = 5

# When CarLightScenario does not sample Alpha, merge that treg from a DXIL-proven
# secondary PSO only. Proven for car_livery: SimpleCarLightScenario samples Alpha
# t16 at TEXCOORD0 / R and feeds SV_Target alpha (see local DXIL / PSO scans).
PROVEN_ALPHA_SUPPLEMENT_PSO: dict[str, tuple[str, int]] = {
    "car_livery": ("car_liverySimpleCarLightScenario.pcdxil.pso", 16),
}

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


def _cb_bool_loads(ps_txt: str, cbmp: dict[int, int], params: dict) -> dict[int, set[str]]:
    """bool param hash -> SSA ids loaded from that cbuffer bool."""
    defs, _ = _build_ssa(ps_txt.splitlines())
    slot_to_hash = {}
    for h, off in cbmp.items():
        p = params.get(h)
        if p is None or getattr(p, "type", None) != 3:
            continue
        slot_to_hash[(off // 16, (off % 16) // 4)] = h
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
        if slot in slot_to_hash:
            loads[slot_to_hash[slot]].add(rid)
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
) -> tuple[int | None, list[str]]:
    """Pick one mesh TEXCOORD from DXIL candidates + proven MatI UV policy.

    Multi-UV without UVChoice stays unresolved — never ``min(uvs)`` (MAT006).
    """
    del gate_bool_hashes  # sample-gate lists do not carry UVChoice
    evidence: list[str] = []
    if not uvs:
        evidence.append("no_mesh_texcoord")
        return None, evidence
    if len(uvs) == 1:
        return next(iter(uvs)), evidence

    from .capabilities import PROVEN_UV_POLICIES, resolve_uv_choice_texcoord

    choice = resolve_uv_choice_texcoord(params)
    if choice is not None:
        texcoord, prov = choice
        policy = PROVEN_UV_POLICIES[0]
        if txmp_name is None or txmp_name in policy.applies_to_txmp:
            if texcoord in uvs:
                evidence.append(prov.detail)
                return texcoord, evidence
            evidence.append(
                f"UVChoice wants TEXCOORD{texcoord} but DXIL candidates are {sorted(uvs)}"
            )
            return None, evidence

    evidence.append(f"multiple_texcoord_semantics:{sorted(uvs)}")
    return None, evidence


def _analyze_ps(
    ps_txt: str, cbmp: dict[int, int], params: dict
) -> dict[int, TextureBinding]:
    lines = ps_txt.splitlines()
    sig = _parse_signature(lines, "; Input signature:")
    defs, loadin = _build_ssa(lines)
    uses = _ssa_uses(defs)
    srv, smp = _resolve_handles(ps_txt)
    bool_loads = _cb_bool_loads(ps_txt, cbmp, params)

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
                        p = params.get(h)
                        if p is not None and getattr(p, "type", None) in (2, 11):
                            tiling_hashes.add(h)
        bind.tiling_cb_hashes = sorted(tiling_hashes)

    if not info:
        raise ShaderBindingError("PSO has no texture sample sites in DXIL disassembly")

    for treg, bind in info.items():
        uvs = uvs_by_treg.get(treg, set())
        bind.uv_semantics_all = sorted(uvs)
        # Tentative UV from structure alone; builder re-resolves with this material's bools.
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
) -> int | None:
    """Per-material UV pick: unique DXIL semantic, else proven UVChoice."""
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


def _load_cache(key: str) -> ShaderBindings | None:
    path = os.path.join(_cache_dir(), f"{key}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not raw.get("source_hashes") or raw.get("generator_version") != GENERATOR_VERSION:
        return None
    b = ShaderBindings(
        shader_name=raw["shader_name"],
        source_hashes=dict(raw.get("source_hashes") or {}),
        pso_member=raw.get("pso_member") or "",
        evidence=list(raw.get("evidence") or []),
    )
    for treg_s, row in (raw.get("textures") or {}).items():
        b.textures[int(treg_s)] = TextureBinding(
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
    return b


def _save_cache(key: str, bindings: ShaderBindings) -> None:
    path = os.path.join(_cache_dir(), f"{key}.json")
    raw = {
        "generator_version": GENERATOR_VERSION,
        "generated_from": "dxil_carlightscenario_pso",
        "source_hashes": bindings.source_hashes,
        "shader_name": bindings.shader_name,
        "pso_member": bindings.pso_member,
        "evidence": bindings.evidence,
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
            for t, b in bindings.textures.items()
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
_BINDINGS_MEM: dict[tuple[str, str, str], ShaderBindings] = {}


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
    not the mesh/raster technique used for car import.
    """
    return _exact_named_pso(names, f"{shader_name}CarLightScenario.pcdxil.pso")


def _supplement_alpha_binding(
    *,
    textures: dict[int, TextureBinding],
    zip_path: str,
    names: list[str],
    zf: zipfile.ZipFile,
    shader_name: str,
    cbmp: dict[int, int],
    params: dict,
    dxc: str,
) -> tuple[bytes, str | None]:
    """Merge Alpha treg from a proven secondary PSO when CarLightScenario omits it.

    Returns (supplement_pso_bytes, member_path_or_None).
    """
    spec = PROVEN_ALPHA_SUPPLEMENT_PSO.get((shader_name or "").lower())
    if spec is None:
        return b"", None
    basename, treg = spec
    if treg in textures:
        return b"", None
    member = _exact_named_pso(names, basename)
    raw = zf.read(member)
    print(
        f"Forza: DXIL Alpha supplement {shader_name} t{treg} via {basename}",
        flush=True,
    )
    supp = _analyze_ps(_disasm(dxc, raw), cbmp, params)
    bind = supp.get(treg)
    if bind is None:
        raise ShaderBindingError(
            f"proven Alpha supplement PSO {basename} has no t{treg} sample "
            f"(shader={shader_name!r})"
        )
    # Locked evidence from car_livery SimpleCarLightScenario probe — refuse drift.
    if sorted(bind.uv_semantics_all or []) != [0]:
        raise ShaderBindingError(
            f"Alpha supplement t{treg} UV drift in {basename}: "
            f"expected [0], got {bind.uv_semantics_all}"
        )
    if list(bind.comps or []) != [0]:
        raise ShaderBindingError(
            f"Alpha supplement t{treg} comps drift in {basename}: "
            f"expected [0], got {bind.comps}"
        )
    bind.uv_semantic = 0
    bind.role = "alpha"
    bind.channel_roles["opacity"] = "x"
    if "x_feeds_sv_target_alpha_or_discard" not in bind.evidence:
        # Classifier should have set this; keep fail-visible if missing.
        if not any("feeds_sv_target_alpha" in e for e in bind.evidence):
            raise ShaderBindingError(
                f"Alpha supplement t{treg} in {basename} lacks SV_Target alpha evidence: "
                f"{bind.evidence}"
            )
    bind.evidence.append(f"alpha_supplement_pso={member}")
    textures[treg] = bind
    return raw, member


def extract_bindings(
    *,
    media_root: str,
    shader_name: str,
    params: dict,
    cbmp: dict[int, int],
    game_key: str | None = None,
) -> ShaderBindings:
    """Extract UV/comps/role bindings for shader_name. Raises ShaderBindingError on failure."""
    if not shader_name:
        raise ShaderBindingError("shader_name is empty")
    if not media_root or not os.path.isdir(media_root):
        raise ShaderBindingError(f"media_root missing: {media_root!r}")

    mem_key = (os.path.normcase(media_root), shader_name.lower(), (game_key or "").lower())
    mem = _BINDINGS_MEM.get(mem_key)
    if mem is not None:
        return mem

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

    with zipfile.ZipFile(zip_path, "r") as zf:
        sb_bytes = zf.read(sb_member)
        pso_bytes = zf.read(pso_member)
        # Hash includes proven Alpha supplement PSO when applicable (cache bump).
        supp_spec = PROVEN_ALPHA_SUPPLEMENT_PSO.get(shader_name.lower())
        supp_preface = b""
        if supp_spec is not None:
            try:
                supp_member_peek = _exact_named_pso(names, supp_spec[0])
                supp_preface = zf.read(supp_member_peek)
            except ShaderBindingError:
                supp_preface = b""

    key = _content_key(sb_bytes, pso_bytes + b"\0ALPHASUPP\0" + supp_preface)
    cached = _load_cache(key)
    if cached is not None:
        _BINDINGS_MEM[mem_key] = cached
        return cached

    print(f"Forza: DXIL analyze {shader_name} (first use; cached after this)", flush=True)
    dxc = _addon_dxc()
    ps_txt = _disasm(dxc, pso_bytes)
    textures = _analyze_ps(ps_txt, cbmp, params)
    evidence = [f"pso={pso_member}", f"archive={os.path.basename(zip_path)}"]
    with zipfile.ZipFile(zip_path, "r") as zf:
        _supp_bytes, supp_member = _supplement_alpha_binding(
            textures=textures,
            zip_path=zip_path,
            names=names,
            zf=zf,
            shader_name=shader_name,
            cbmp=cbmp,
            params=params,
            dxc=dxc,
        )
    if supp_member:
        evidence.append(f"alpha_supplement_pso={supp_member}")
    bindings = ShaderBindings(
        shader_name=shader_name,
        textures=textures,
        source_hashes={
            "shaderbin_sha256": _sha256(sb_bytes),
            "pso_sha256": _sha256(pso_bytes),
            "descriptor_key": key,
        },
        pso_member=pso_member,
        evidence=evidence,
    )
    _save_cache(key, bindings)
    _BINDINGS_MEM[mem_key] = bindings
    return bindings


def comps_str(comps: list[int]) -> str:
    return "".join("xyzw"[i] for i in comps if 0 <= i <= 3)

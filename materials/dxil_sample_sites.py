"""Per-instruction DXIL sample-site extraction (not register aggregates)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from . import shader_bindings as sb


@dataclass
class DxilSampleSite:
    """One ``@dx.op.sample*`` instruction site."""

    instruction_index: int
    instruction_id: str  # SSA result id of sample
    operation: str
    texture_register: int
    sampler_register: int | None
    sampled_components: list[int]
    coord_ssa: tuple[str, str]  # (u, v) operands as written
    texcoord_sources: list[int]  # mesh TEXCOORD indices reachable
    uv_expression: str
    branch_predicates: list[str]
    feeds_sv_target_alpha: bool
    feeds_discard: bool
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_OP_NAME = re.compile(
    r"@dx\.op\.(sample\w*)\.\w+\("
)


def _uv_expression(texcoord_sources: list[int], coord_ssa: tuple[str, str]) -> str:
    unique = sorted(set(texcoord_sources))
    if len(unique) == 1:
        return f"TEXCOORD{unique[0]}"
    if len(unique) > 1:
        return (
            "SELECT_AMONG["
            + ",".join(f"TEXCOORD{t}" for t in unique)
            + f"]; coords=({coord_ssa[0]},{coord_ssa[1]})"
        )
    if coord_ssa[0].startswith("%") or coord_ssa[1].startswith("%"):
        return f"NON_MESH_OR_UNRESOLVED; coords=({coord_ssa[0]},{coord_ssa[1]})"
    return f"IMMEDIATE; coords=({coord_ssa[0]},{coord_ssa[1]})"


def extract_sample_sites(
    ps_txt: str,
    *,
    cbmp: dict[int, int] | None = None,
    recover_control_deps: bool = True,
) -> list[DxilSampleSite]:
    """Return one row per DXIL texture sample instruction.

    When ``recover_control_deps`` is true and ``cbmp`` is provided, CFG
    control-dependence analysis attributes branch predicates. Empty predicate
    lists remain ``NO_PREDICATE_RECOVERED`` — never renamed unconditional.
    """
    cbmp = cbmp or {}
    lines = ps_txt.splitlines()
    sig = sb._parse_signature(lines, "; Input signature:")
    defs, loadin = sb._build_ssa(lines)
    srv, smp = sb._resolve_handles(ps_txt)
    bool_loads = sb._cb_bool_loads(ps_txt, cbmp)
    extracted: dict[str, set[int]] = {}
    for m in sb._EXTRACT_COMP.finditer(ps_txt):
        extracted.setdefault(m.group(1), set()).add(int(m.group(2)))

    sites: list[DxilSampleSite] = []
    for idx, m in enumerate(sb._SAMPLE.finditer(ps_txt)):
        res, hnd, smp_hnd, c0, c1 = m.groups()
        if hnd not in srv:
            continue
        treg = srv[hnd]
        op_m = _OP_NAME.search(m.group(0))
        op = op_m.group(1) if op_m else "sample"
        texcoords: list[int] = []
        for c in (c0, c1):
            cm = re.match(r"%(\d+)", c.strip())
            if not cm:
                continue
            for leaf_row, _ in sb._trace_ssa_to_loadin(cm.group(1), defs, loadin):
                t = sb._texcoord_semantic(sig, leaf_row)
                if t is not None and t not in texcoords:
                    texcoords.append(t)

        comps = sorted(extracted.get(res, set()))
        sample_targets = {res}
        for c in range(4):
            sample_targets |= sb._extract_id_for_comp(ps_txt, res, c)
        predicates: list[str] = []
        data_hashes: list[int] = []
        for h, ids in bool_loads.items():
            if sb._bool_gates_target(ids, sample_targets, defs):
                predicates.append(f"cb_bool:0x{h & 0xFFFFFFFF:08X}")
                data_hashes.append(int(h))

        branch_status = "NO_PREDICATE_RECOVERED"
        if recover_control_deps and cbmp:
            from .dxil_control_deps import analyze_sample_control

            analysis = analyze_sample_control(
                ps_txt,
                instruction_id=f"%{res}",
                cbmp=cbmp,
                data_dep_hashes=data_hashes,
            )
            branch_status = analysis.status
            for row in analysis.control_predicates:
                if row.param_hash is not None:
                    tag = f"ctrl:0x{row.param_hash & 0xFFFFFFFF:08X}:{row.polarity}"
                    if tag not in predicates:
                        predicates.append(tag)
        elif predicates:
            branch_status = "EXECUTABLE_PREDICATE"
        elif not cbmp:
            branch_status = "NO_PREDICATE_RECOVERED"

        feeds_alpha = sb._feeds_alpha_or_discard(ps_txt, sample_targets, defs)
        feeds_discard = bool(sb._DISCARD.search(ps_txt)) and feeds_alpha
        # Narrow: discard only if discard ancestors intersect sample
        if sb._DISCARD.search(ps_txt):
            feeds_discard = False
            for ln in ps_txt.splitlines():
                if "@dx.op.discard" not in ln:
                    continue
                for rid in re.findall(r"%(\d+)", ln):
                    if sample_targets & sb._ancestors(rid, defs) or rid in sample_targets:
                        feeds_discard = True
                        break

        evidence = [
            f"instruction=%{res}",
            f"t{treg}",
            f"comps={comps}",
            f"branch_status={branch_status}",
        ]
        if feeds_alpha:
            evidence.append("feeds_sv_target_alpha_or_discard_chain")

        site = DxilSampleSite(
            instruction_index=idx,
            instruction_id=f"%{res}",
            operation=op,
            texture_register=treg,
            sampler_register=smp.get(smp_hnd),
            sampled_components=comps,
            coord_ssa=(c0.strip(), c1.strip()),
            texcoord_sources=texcoords,
            uv_expression=_uv_expression(texcoords, (c0.strip(), c1.strip())),
            branch_predicates=predicates,
            feeds_sv_target_alpha=feeds_alpha and not feeds_discard,
            feeds_discard=feeds_discard,
            evidence=evidence,
        )
        # Attach recovered status for inventory consumers (non-schema field via evidence).
        site.evidence.append(f"control_analysis_status={branch_status}")
        sites.append(site)
    return sites


def register_summary(sites: list[DxilSampleSite]) -> dict[str, dict]:
    """Optional aggregate view — must not replace per-instruction rows."""
    out: dict[str, dict] = {}
    for s in sites:
        key = f"t{s.texture_register}"
        row = out.setdefault(
            key,
            {
                "texture_register": s.texture_register,
                "sample_count": 0,
                "instruction_ids": [],
                "comps_union": set(),
                "texcoord_union": set(),
                "samplers": set(),
                "uv_expressions": set(),
            },
        )
        row["sample_count"] += 1
        row["instruction_ids"].append(s.instruction_id)
        row["comps_union"].update(s.sampled_components)
        row["texcoord_union"].update(s.texcoord_sources)
        if s.sampler_register is not None:
            row["samplers"].add(s.sampler_register)
        row["uv_expressions"].add(s.uv_expression)
    for row in out.values():
        row["comps_union"] = sorted(row["comps_union"])
        row["texcoord_union"] = sorted(row["texcoord_union"])
        row["samplers"] = sorted(row["samplers"])
        row["uv_expressions"] = sorted(row["uv_expressions"])
    return out

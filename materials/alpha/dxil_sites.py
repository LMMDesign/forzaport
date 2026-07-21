"""Parse car_standard DXIL .ll dumps into real alpha sample-site rows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import (
    ALPHA_TXMP_NAMEHASH,
    BASECOLORALPHA_TXMP_NAMEHASH,
    CAR_STANDARD_SHADERBIN_SHA256,
    AUTHORED_MASK_EQUATION,
)

_CREATE = re.compile(
    r"%(?P<dest>\d+)\s*=\s*call\s+%dx\.types\.Handle\s+"
    r"@dx\.op\.createHandleFromBinding\(i32\s+\d+,\s+"
    r"%dx\.types\.ResBind\s*\{\s*i32\s+(?P<bind>\d+),"
)
_ANNOTATE = re.compile(
    r"%(?P<dest>\d+)\s*=\s*call\s+%dx\.types\.Handle\s+"
    r"@dx\.op\.annotateHandle\(i32\s+\d+,\s+%dx\.types\.Handle\s+%"
    r"(?P<src>\d+),"
)
_SAMPLE = re.compile(
    r"%(?P<dest>\d+)\s*=\s*call\s+%dx\.types\.ResRet\.f32\s+"
    r"@dx\.op\.(?P<op>sample(?:Bias|Level|Grad|Cmp)?)\.f32\(i32\s+\d+,\s+"
    r"%dx\.types\.Handle\s+%(?P<tex>\d+),\s+%dx\.types\.Handle\s+%(?P<samp>\d+),\s+"
    r"float\s+(?P<u>[^,]+),\s+float\s+(?P<v>[^,]+)"
)
_EXTRACT = re.compile(
    r"%(?P<dest>\d+)\s*=\s*extractvalue\s+%dx\.types\.ResRet\.f32\s+%"
    r"(?P<src>\d+),\s*(?P<comp>\d+)"
)
_FMUL = re.compile(
    r"%(?P<dest>\d+)\s*=\s*fmul(?:\s+fast)?\s+float\s+%"
    r"(?P<a>\d+),\s*%(?P<b>\d+)"
)
_SAT = re.compile(
    r"%(?P<dest>\d+)\s*=\s*call\s+float\s+@dx\.op\.unary\.f32\(i32\s+7,\s+float\s+%"
    r"(?P<src>\d+)\)"  # Saturate opcode 7
)
_DISCARD = re.compile(r"call\s+void\s+@dx\.op\.discard\(i32\s+\d+,\s+i1\s+%(?:(?P<cond>\d+))\)")
_CB32 = re.compile(
    r"%(?P<dest>\d+)\s*=\s*call\s+%dx\.types\.CBufRet\.f32\s+"
    r"@dx\.op\.cbufferLoadLegacy\.f32\(i32\s+\d+,\s+%dx\.types\.Handle\s+%"
    r"(?P<h>\d+),\s+i32\s+32\)"
)
_CB_EXT = re.compile(
    r"%(?P<dest>\d+)\s*=\s*extractvalue\s+%dx\.types\.CBufRet\.f32\s+%"
    r"(?P<src>\d+),\s*(?P<comp>\d+)"
)

_COMP = {0: "r", 1: "g", 2: "b", 3: "a"}


def _ssa(tok: str) -> str | None:
    tok = tok.strip()
    if tok.startswith("%") and tok[1:].isdigit():
        return tok
    if tok.isdigit():
        return f"%{tok}"
    return None


def parse_pass_ll(
    path: Path,
    *,
    pass_name: str,
    shader_kind: str = "PS",
) -> dict[str, Any]:
    """Extract t16.r / t17.a sample sites and authored-mask expression from one .ll."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # handle_id -> (kind, register) kind: texture|sampler
    binding: dict[str, tuple[str, int]] = {}
    annotate_src: dict[str, str] = {}
    line_of: dict[str, int] = {}

    for i, line in enumerate(lines, 1):
        m = _CREATE.search(line)
        if m:
            dest = m.group("dest")
            bind = int(m.group("bind"))
            # ResBind last i8: 0=SRV texture, 3=sampler (from dumps)
            kind = "sampler" if "i8 3" in line else "texture"
            if "i8 2" in line:
                kind = "cbuffer"
            binding[dest] = (kind, bind)
            line_of[f"%{dest}"] = i
            continue
        m = _ANNOTATE.search(line)
        if m:
            annotate_src[m.group("dest")] = m.group("src")
            line_of[f"%{m.group('dest')}"] = i

    def resolve_reg(hid: str) -> tuple[str, int] | None:
        seen: set[str] = set()
        cur = hid
        while cur not in binding:
            if cur in seen:
                return None
            seen.add(cur)
            if cur not in annotate_src:
                return None
            cur = annotate_src[cur]
        return binding[cur]

    samples: list[dict[str, Any]] = []
    extracts: dict[str, dict[str, Any]] = {}  # extract_ssa -> info

    for i, line in enumerate(lines, 1):
        m = _SAMPLE.search(line)
        if not m:
            continue
        tex_h = m.group("tex")
        samp_h = m.group("samp")
        tex = resolve_reg(tex_h)
        samp = resolve_reg(samp_h)
        if not tex or tex[0] != "texture":
            continue
        treg = tex[1]
        if treg not in (16, 17):
            continue
        sreg = samp[1] if samp and samp[0] == "sampler" else None
        u = _ssa(m.group("u"))
        v = _ssa(m.group("v"))
        sample_ssa = f"%{m.group('dest')}"
        samples.append(
            {
                "sample_ssa": sample_ssa,
                "sample_op": m.group("op"),
                "texture_register": treg,
                "sampler_register": sreg,
                "texture_handle_ssa": f"%{tex_h}",
                "sampler_handle_ssa": f"%{samp_h}",
                "uv_u_ssa": u,
                "uv_v_ssa": v,
                "line": i,
            }
        )
        line_of[sample_ssa] = i

    for i, line in enumerate(lines, 1):
        m = _EXTRACT.search(line)
        if not m:
            continue
        src = f"%{m.group('src')}"
        dest = f"%{m.group('dest')}"
        comp_i = int(m.group("comp"))
        parent = next((s for s in samples if s["sample_ssa"] == src), None)
        if not parent:
            continue
        info = {
            **parent,
            "extract_ssa": dest,
            "sampled_component": _COMP.get(comp_i, str(comp_i)),
            "extract_line": i,
        }
        extracts[dest] = info
        line_of[dest] = i

    # Prefer Alpha.r (t16,r) and BaseColorAlpha.a (t17,a) extracts.
    t16_r = next(
        (
            e
            for e in extracts.values()
            if e["texture_register"] == 16 and e["sampled_component"] == "r"
        ),
        None,
    )
    t17_a = next(
        (
            e
            for e in extracts.values()
            if e["texture_register"] == 17 and e["sampled_component"] == "a"
        ),
        None,
    )

    mul_ssa = None
    mul_line = None
    if t16_r and t17_a:
        a_id = t16_r["extract_ssa"].lstrip("%")
        b_id = t17_a["extract_ssa"].lstrip("%")
        for i, line in enumerate(lines, 1):
            m = _FMUL.search(line)
            if not m:
                continue
            ops = {m.group("a"), m.group("b")}
            if a_id in ops and b_id in ops:
                mul_ssa = f"%{m.group('dest')}"
                mul_line = i
                line_of[mul_ssa] = i
                break

    sat_ssa = None
    sat_line = None
    if mul_ssa:
        mid = mul_ssa.lstrip("%")
        for i, line in enumerate(lines, 1):
            m = _SAT.search(line)
            if m and m.group("src") == mid:
                sat_ssa = f"%{m.group('dest')}"
                sat_line = i
                break

    # AlphaTransparency CB reg32.y loads
    cb_loads: list[dict[str, Any]] = []
    cb32_dests: set[str] = set()
    for i, line in enumerate(lines, 1):
        m = _CB32.search(line)
        if m:
            cb32_dests.add(m.group("dest"))
            line_of[f"%{m.group('dest')}"] = i
    for i, line in enumerate(lines, 1):
        m = _CB_EXT.search(line)
        if not m:
            continue
        if m.group("src") in cb32_dests and int(m.group("comp")) == 1:
            cb_loads.append(
                {
                    "cb_load_ssa": f"%{m.group('src')}",
                    "extract_ssa": f"%{m.group('dest')}",
                    "cb_register": 32,
                    "cb_component": "y",
                    "line": i,
                    "meaning": "AlphaTransparencyBool",
                }
            )

    discard_ids: list[str] = []
    for i, line in enumerate(lines, 1):
        m = _DISCARD.search(line)
        if m:
            discard_ids.append(f"%{m.group('cond')}")

    def site_row(info: dict[str, Any] | None, *, label: str) -> dict[str, Any] | None:
        if not info:
            return None
        treg = info["texture_register"]
        name_hash = ALPHA_TXMP_NAMEHASH if treg == 16 else BASECOLORALPHA_TXMP_NAMEHASH
        row = {
            "shader_sha256": CAR_STANDARD_SHADERBIN_SHA256,
            "pass": pass_name,
            "shader_kind": shader_kind,
            "sample_site_id": f"car_standard|{pass_name}|t{treg}|{label}",
            "dxil_texture_sample_instruction_id": info["sample_ssa"],
            "texture_register": treg,
            "sampler_register": info["sampler_register"],
            "sampled_component": info["sampled_component"],
            "binding_name_hash": name_hash,
            "binding_name_hash_hex": hex(name_hash),
            "uv_operand_ssa_ids": [
                x for x in (info.get("uv_u_ssa"), info.get("uv_v_ssa")) if x
            ],
            "extracted_channel_ssa_id": info["extract_ssa"],
            "multiplication_ssa_id": mul_ssa,
            "saturate_ssa_id": sat_ssa,
            "branch_select_ssa_id": (
                cb_loads[0]["extract_ssa"] if cb_loads else "UNRESOLVED"
            ),
            "discard_consumer_ids": discard_ids or None,
            "constant_buffer_loads": cb_loads or None,
            "dxil_instruction_ids": [
                x
                for x in (
                    info["sample_ssa"],
                    info["extract_ssa"],
                    mul_ssa,
                    sat_ssa,
                )
                if x
            ],
            "evidence_file": str(path).replace("\\", "/"),
            "evidence_line_sample": info["line"],
            "evidence_line_extract": info["extract_line"],
            "evidence_line_multiply": mul_line,
            "evidence_line_saturate": sat_line,
        }
        # Drop explicit nulls except unresolved markers
        return {k: v for k, v in row.items() if v is not None and v != []}

    sites = []
    r16 = site_row(t16_r, label="Alpha.r")
    r17 = site_row(t17_a, label="BaseColorAlpha.a")
    if r16:
        sites.append(r16)
    if r17:
        sites.append(r17)

    expr = None
    if t16_r and t17_a and mul_ssa:
        expr = {
            "expression_id": f"car_standard|{pass_name}|authored_mask",
            "equation": AUTHORED_MASK_EQUATION,
            "references_sample_site_ids": [
                s["sample_site_id"] for s in sites
            ],
            "alpha_r_extract_ssa": t16_r["extract_ssa"],
            "bc_a_extract_ssa": t17_a["extract_ssa"],
            "multiplication_ssa_id": mul_ssa,
            "saturate_ssa_id": sat_ssa or "UNRESOLVED_IN_PASS",
            "shader_sha256": CAR_STANDARD_SHADERBIN_SHA256,
            "pass": pass_name,
            "evidence_file": str(path).replace("\\", "/"),
            "evidence_line_multiply": mul_line,
            "evidence_line_saturate": sat_line,
        }

    return {
        "pass": pass_name,
        "shader_kind": shader_kind,
        "sample_sites": sites,
        "authored_mask_expression": expr,
        "alpha_transparency_cb_loads": cb_loads,
        "discard_condition_ssas": discard_ids,
        "source_ll": str(path).replace("\\", "/"),
    }


def parse_all_dxil_dumps(dxil_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    exprs: list[dict[str, Any]] = []
    passes: list[dict[str, Any]] = []
    for path in sorted(dxil_dir.glob("car_standard_*.ll")):
        name = path.stem.replace("car_standard_", "", 1)
        kind = "PS"
        if "RayTracing" in name or "Hit" in name:
            kind = "RT"
        if "ShadowDepthNoPS" in name:
            kind = "PS_or_null"
        parsed = parse_pass_ll(path, pass_name=name, shader_kind=kind)
        passes.append(
            {
                "pass": name,
                "sample_site_count": len(parsed["sample_sites"]),
                "has_authored_mask_expr": parsed["authored_mask_expression"] is not None,
                "cb_alpha_transparency_loads": len(
                    parsed["alpha_transparency_cb_loads"]
                ),
            }
        )
        rows.extend(parsed["sample_sites"])
        if parsed["authored_mask_expression"]:
            exprs.append(parsed["authored_mask_expression"])
    return {
        "schema_version": 2,
        "policy": "Real DXIL sample instruction IDs from .ll dumps; not product-SSA-only",
        "shader_sha256": CAR_STANDARD_SHADERBIN_SHA256,
        "dxil_dir": str(dxil_dir).replace("\\", "/"),
        "pass_summary": passes,
        "sample_site_rows": rows,
        "authored_mask_expression_rows": exprs,
        "row_count": len(rows),
        "expression_row_count": len(exprs),
    }

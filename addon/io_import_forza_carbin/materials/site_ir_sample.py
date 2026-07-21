"""Sample Forza IR textures from evaluated sample sites (not capability slots).

Carries typed sample-site identity, registers, channels, UV AST, and evidence
into ``TextureSampleExpression``. Does not parse diagnostic strings for IDs.
"""

from __future__ import annotations

from .forza_ir import (
    MeshUV,
    RotateUV,
    SamplerState,
    ScaleUV,
    TextureSample,
    TextureSampleExpression,
)
from .model import ProvenanceDiagnostic as PD
from .resolved_texture_resource import site_txmp_name
from .sample_site_eval import EvaluatedSampleSite
from .texture_source import resolve_texture_source
from .uv_ir_bridge import uv_expr_to_forza_ir


def _pd(*details: str, source: str = "materials.site_ir_sample") -> tuple[PD, ...]:
    return tuple(PD(kind="sample", detail=d, source=source) for d in details if d)


def find_site_for_txmp(
    evaluated,
    *,
    txmp_name: str,
    texture_register: int | None = None,
) -> EvaluatedSampleSite | None:
    if evaluated is None:
        return None
    sites = list(evaluated.active_import_sites())
    if texture_register is not None:
        for site in sites:
            if int(site.texture_register) != int(texture_register):
                continue
            name = site_txmp_name(site)
            if name and name != txmp_name:
                continue
            return site
    for site in sites:
        if site_txmp_name(site) == txmp_name:
            return site
    return None


def find_site_for_slot(evaluated, slot) -> EvaluatedSampleSite | None:
    """Match a derived capability slot back to its evaluated sample site by ID."""
    if evaluated is None or slot is None:
        return None
    # Prefer exact sample_site_id carried on slot evidence as structured tag —
    # but never *parse* free-form diagnostics to invent IDs. Evidence rows that
    # start with ``sample_site:`` were written by slots_from_evaluated_sites
    # from ``site.sample_site_id``; we only accept an exact match against a live
    # site object.
    tagged = None
    for e in getattr(slot, "evidence", None) or ():
        detail = getattr(e, "detail", "") or ""
        if detail.startswith("sample_site:"):
            tagged = detail.split(":", 1)[1]
            break
    if tagged:
        for site in evaluated.active_import_sites():
            if site.sample_site_id == tagged:
                return site
    # Fallback: TXMP name + path register via resources is not available here;
    # match by param name against declared_txmp / site_txmp_name.
    name = getattr(slot, "param_name", None) or ""
    if name:
        return find_site_for_txmp(evaluated, txmp_name=name)
    return None


def sample_from_evaluated_site(
    site: EvaluatedSampleSite,
    *,
    path: str,
    binding_name_hash: int,
    channels: tuple[str, ...],
    color_space: str,
    resolver,
    params: dict,
    address: dict | None = None,
    uv_overlay=None,
    extra_evidence: tuple[PD, ...] = (),
) -> tuple[TextureSample | None, str | None]:
    """Build a TextureSample from one evaluated site + MatI path.

    ``uv_overlay``: optional callable ``(base_uv_expr) -> (expr, reject)`` for
    family-specific MatI transforms (weave rotate/scale, car_standard tiling)
    that wrap the site UV AST without reconstructing from ResolvedTextureSlot.
    """
    src = resolve_texture_source(path, resolver) if path else None
    if src is None or not getattr(src, "exists", False):
        return None, f"texture source missing for IR site {site.sample_site_id}"

    uv_ir, uv_err = uv_expr_to_forza_ir(site.uv_node, params=params)
    if uv_err:
        # Fall back to proven mesh TEXCOORD when contract UV is a bare index
        # string already evaluated into uv_eval.
        if site.resolved_texcoord is not None:
            uv_ir = MeshUV(
                index=int(site.resolved_texcoord),
                evidence=_pd(
                    f"site:{site.sample_site_id}:TEXCOORD{site.resolved_texcoord}",
                    f"uv_bridge_fallback:{uv_err}",
                ),
            )
        else:
            return None, f"{site.sample_site_id}: {uv_err}"

    if uv_overlay is not None:
        uv_ir, overlay_err = uv_overlay(uv_ir)
        if overlay_err:
            return None, overlay_err

    addr = address or {}
    samp_reg = site.identity.sampler_register
    expr = TextureSampleExpression(
        binding_name_hash=int(binding_name_hash) & 0xFFFFFFFF,
        source=src,
        uv=uv_ir,
        channels=tuple(channels),
        color_space=color_space,
        sampler=SamplerState(
            address_u=str(addr.get("U", "REPEAT")),
            address_v=str(addr.get("V", "REPEAT")),
        ),
        evidence=tuple(site.evidence or ())
        + _pd(
            f"sample_site_id={site.sample_site_id}",
            f"texture_register=t{int(site.texture_register)}",
            f"sampler_register=s{samp_reg}" if samp_reg is not None else "",
            f"branch_status={site.branch_status}",
        )
        + tuple(extra_evidence),
        sample_site_id=site.sample_site_id,
        sample_site_key=site.identity.as_key() if hasattr(site.identity, "as_key") else None,
        texture_register=int(site.texture_register),
        sampler_register=int(samp_reg) if samp_reg is not None else None,
        pass_name=getattr(site.identity, "scenario", None),
    )
    return TextureSample(sample=expr, sample_site_id=site.sample_site_id), None


def wrap_mesh_uv_tiling_rotate(
    base_uv,
    *,
    params: dict,
    orient_hash: int,
    u_tiling_hash: int,
    v_tiling_hash: int,
    require_orient: bool = True,
    label: str = "",
):
    """MatI overlay: RotateUV(orient) → ScaleUV(u,v) around a site UV expression."""

    def _f(h: int):
        p = params.get(h)
        if p is None or getattr(p, "type", None) != 2:
            return None
        return float(p.value)

    orient = _f(orient_hash)
    u_tile = _f(u_tiling_hash)
    v_tile = _f(v_tiling_hash)
    if require_orient and orient is None:
        return None, f"{label}UV_Orientation missing in MatI"
    if u_tile is None or v_tile is None:
        return None, f"{label}U_Tiling/V_Tiling missing in MatI"
    expr = base_uv
    ang = float(orient) if orient is not None else 0.0
    if ang != 0.0:
        expr = RotateUV(
            source=expr,
            degrees=ang,
            evidence=_pd(f"rotate {ang}deg"),
        )
    expr = ScaleUV(
        source=expr,
        scale=(float(u_tile), float(v_tile)),
        evidence=_pd(f"scale=({u_tile},{v_tile})"),
    )
    return expr, None

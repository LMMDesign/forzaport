"""Build capability texture slots from evaluated sample sites.

Production path for FULL_SAMPLE_SITE_IR — does not call register-keyed
texture maps or TextureBinding UV helpers for semantic decisions.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .model import ProvenanceDiagnostic
from .name_hashes import MaterialNameError, require_name
from .resolved_texture_resource import (
    resolve_texture_resources,
    resource_for_site,
    site_txmp_name,
)
from .txmp_semantics import semantics_for_txmp_hash


def _ev(*details: str, kind: str = "contract", source: str = "materials.sample_site_slots"):
    return tuple(
        ProvenanceDiagnostic(kind=kind, detail=d, source=source) for d in details if d
    )


def slots_from_evaluated_sites(
    *,
    bindings: Any,
    params: dict,
    txmp: dict[int, int],
    spmp: dict,
    overrides: set,
    shader_name: str,
    resolver,
    slot_builder,
    prefer_fn,
    path_exists,
    capability_kind,
) -> tuple[dict[str, Any], list[str], list[ProvenanceDiagnostic]]:
    """Return role→slot maps built from ACTIVE blender_import sample sites.

    ``slot_builder`` is resolver._slot; ``prefer_fn`` is resolver._prefer.
    """
    errors: list[str] = []
    evidence: list[ProvenanceDiagnostic] = []
    base_map = None
    weave_mask = None
    normal_map = None
    rmao_map = None
    alpha_map = None

    evaluated = getattr(bindings, "evaluated_sites", None)
    if evaluated is None:
        return (
            {
                "base_map": None,
                "weave_mask": None,
                "normal_map": None,
                "rmao_map": None,
                "alpha_map": None,
            },
            ["missing evaluated_sites — refuse TextureBinding semantic fallback"],
            evidence,
        )

    def _name_lookup(h: int) -> str:
        return require_name(h, context=f"{shader_name} TXMP")

    resources = resolve_texture_resources(
        params=params,
        txmp=txmp,
        name_lookup=_name_lookup,
        source_mati=getattr(bindings, "source_mati_path", None),
    )

    # Prefer one ACTIVE import site per TXMP role (first wins; override hash later).
    sites = list(evaluated.active_import_sites())
    if not sites:
        return (
            {
                "base_map": None,
                "weave_mask": None,
                "normal_map": None,
                "rmao_map": None,
                "alpha_map": None,
            },
            [f"{shader_name}: no ACTIVE blender_import sample sites"],
            evidence,
        )

    evidence.append(
        ProvenanceDiagnostic(
            kind="route",
            detail=f"slots_from_evaluated_sites n={len(sites)}",
            source="materials.sample_site_slots",
        )
    )

    for site in sites:
        treg = int(site.texture_register)
        txmp_name = site_txmp_name(site)
        res = resource_for_site(
            resources, texture_register=treg, declared_txmp=txmp_name
        )
        if res is None or not res.texture_path:
            errors.append(
                f"sample_site {site.sample_site_id}: no MatI TXMP path for t{treg}"
            )
            continue
        if not path_exists(res.texture_path, resolver):
            errors.append(
                f"sample_site {site.sample_site_id}: texture missing: {res.texture_path}"
            )
            continue

        h = res.name_hash
        if h is None:
            errors.append(f"sample_site {site.sample_site_id}: missing TXMP name hash")
            continue
        try:
            param_name = txmp_name or require_name(
                h, context=f"{shader_name} sample site t{treg}"
            )
        except MaterialNameError as exc:
            errors.append(str(exc))
            continue

        uv = site.resolved_texcoord
        if uv is None:
            errors.append(
                f"sample_site {site.sample_site_id}: no evaluated mesh TEXCOORD"
            )
            continue

        # Neutral bind stand-in for address/tiling helpers only — not semantics.
        bind = SimpleNamespace(
            sampler_reg=site.identity.sampler_register
            if site.identity.sampler_register is not None
            else getattr(site, "sampler_register", None),
            tiling_cb_hashes=(),
            uv_semantic=int(uv),
            uv_semantics_all=(int(uv),),
            role=param_name,
            comps=list(site.sampled_components or ()),
            evidence=list(site.evidence or ()),
        )

        site_ev = _ev(
            f"sample_site:{site.sample_site_id}",
            f"TXMP:0x{h & 0xFFFFFFFF:08X}:{param_name}",
            f"DXIL:t{treg}:TEXCOORD{uv}",
            f"instruction={site.identity.instruction_id}",
            f"branch_status={site.branch_status}",
        )

        if param_name == "WeaveMask":
            weave_mask = slot_builder(
                role="weave_mask",
                h=h,
                name=param_name,
                path=res.texture_path,
                bind=bind,
                params=params,
                spmp=spmp,
                uv=int(uv),
                channel="r",
                evidence=site_ev + _ev("activation:weave_mask"),
                resolver=resolver,
            )
            continue

        try:
            sem = semantics_for_txmp_hash(h, context=f"{shader_name} site t{treg}")
        except MaterialNameError as exc:
            errors.append(str(exc))
            continue
        if not sem.supports(capability_kind):
            continue

        if param_name == "Alpha":
            alpha_map = slot_builder(
                role="alpha",
                h=h,
                name=param_name,
                path=res.texture_path,
                bind=bind,
                params=params,
                spmp=spmp,
                uv=int(uv),
                channel="x",
                evidence=site_ev,
                resolver=resolver,
            )
            continue

        if param_name in ("BaseColorAlpha", "BaseColorAlpha_1"):
            candidate = slot_builder(
                role="base_color",
                h=h,
                name=param_name,
                path=res.texture_path,
                bind=bind,
                params=params,
                spmp=spmp,
                uv=int(uv),
                evidence=site_ev,
                resolver=resolver,
            )
            base_map = prefer_fn(base_map, candidate, is_override=h in overrides)
        elif param_name in ("Normal", "WeaveNormal"):
            candidate = slot_builder(
                role="normal",
                h=h,
                name=param_name,
                path=res.texture_path,
                bind=bind,
                params=params,
                spmp=spmp,
                uv=int(uv),
                evidence=site_ev,
                resolver=resolver,
            )
            normal_map = prefer_fn(normal_map, candidate, is_override=h in overrides)
        elif param_name == "RoughMetalAO":
            candidate = slot_builder(
                role="rmao",
                h=h,
                name=param_name,
                path=res.texture_path,
                bind=bind,
                params=params,
                spmp=spmp,
                uv=int(uv),
                evidence=site_ev
                + _ev("packing:R=roughness,G=metallic,B=AO"),
                resolver=resolver,
            )
            rmao_map = prefer_fn(rmao_map, candidate, is_override=h in overrides)

    return (
        {
            "base_map": base_map,
            "weave_mask": weave_mask,
            "normal_map": normal_map,
            "rmao_map": rmao_map,
            "alpha_map": alpha_map,
        },
        errors,
        evidence,
    )

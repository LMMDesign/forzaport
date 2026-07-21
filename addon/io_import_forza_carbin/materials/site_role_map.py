"""Map evaluated sample sites → role payloads without slots_from_evaluated_sites.

Authoritative IR path for FULL_SAMPLE_SITE_IR: sites + MatI TXMP resources.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import ProvenanceDiagnostic, ResolvedTextureSlot
from .resolved_texture_resource import resource_for_site, site_txmp_name


@dataclass(frozen=True)
class SiteRoleBinding:
    role: str
    site: Any
    path: str
    name_hash: int
    param_name: str


def _pd(*details: str) -> tuple[ProvenanceDiagnostic, ...]:
    return tuple(
        ProvenanceDiagnostic(kind="site_role", detail=d, source="materials.site_role_map")
        for d in details
        if d
    )


def collect_site_role_bindings(evaluation_context) -> dict[str, SiteRoleBinding]:
    """Return role → site+path for ACTIVE blender_import sites."""
    evaluated = evaluation_context.evaluated_sites
    resources = evaluation_context.texture_resources or {}
    out: dict[str, SiteRoleBinding] = {}
    if evaluated is None:
        return out
    for site in evaluated.active_import_sites():
        treg = int(site.texture_register)
        txmp_name = site_txmp_name(site)
        res = resource_for_site(
            dict(resources), texture_register=treg, declared_txmp=txmp_name
        )
        if res is None or not res.texture_path:
            continue
        pname = txmp_name or res.declared_txmp or ""
        role = None
        if pname in ("BaseColorAlpha", "BaseColorAlpha_1"):
            role = "base"
        elif pname == "Alpha":
            role = "alpha"
        elif pname in ("Normal", "WeaveNormal"):
            role = "normal" if pname == "Normal" else "weave_normal"
        elif pname == "RoughMetalAO":
            role = "rmao"
        elif pname == "WeaveMask":
            role = "weave_mask"
        if role is None:
            continue
        # Prefer first BaseColorAlpha; WeaveNormal over Normal when both seen.
        if role in out and role != "weave_normal":
            continue
        if role == "normal" and "weave_normal" in out:
            continue
        out[role] = SiteRoleBinding(
            role=role,
            site=site,
            path=res.texture_path,
            name_hash=int(res.name_hash or 0) & 0xFFFFFFFF,
            param_name=pname,
        )
    # Collapse weave_normal → normal for capability-shaped consumers.
    if "weave_normal" in out and "normal" not in out:
        wn = out.pop("weave_normal")
        out["normal"] = SiteRoleBinding(
            role="normal",
            site=wn.site,
            path=wn.path,
            name_hash=wn.name_hash,
            param_name=wn.param_name,
        )
    return out


def slot_view_from_binding(binding: SiteRoleBinding) -> ResolvedTextureSlot:
    """Lightweight ResolvedTextureSlot view — no resolve_texture_source."""
    uv = binding.site.resolved_texcoord
    texcoord = f"TEXCOORD{int(uv) if uv is not None else 0}"
    return ResolvedTextureSlot(
        role=binding.role,
        path=binding.path,
        texcoord=texcoord,
        param_hash=binding.name_hash,
        param_name=binding.param_name,
        evidence=_pd(
            f"sample_site:{binding.site.sample_site_id}",
            f"TXMP:0x{binding.name_hash:08X}:{binding.param_name}",
            f"DXIL:t{int(binding.site.texture_register)}:{texcoord}",
        ),
    )

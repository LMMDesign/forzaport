"""Evaluate exact-SHA sample-site contracts (not register merges).

Pipeline stage:
  SerializedShaderSchema
  → StaticPassAnalysis
  → ShaderSampleSiteContract (JSON)
  → MatI branch/variant evaluation
  → EvaluatedMaterialSampleSites
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .pass_contracts import load_shader_pass_contract
from .uv.uv_choice_contracts import resolve_uv_choice_texcoord
from .variant_selection import VariantResolution, resolve_shader_variant


@dataclass
class EvaluatedSampleSite:
    sample_site_id: str
    shaderbin_sha256: str
    scenario: str
    variant: str
    texture_register: int
    sampler_register: int | None
    sampled_components: tuple[int, ...]
    uv_expression: Any
    resolved_texcoord: int | None
    semantic_role: str | None
    blender_relevance: str
    blender_import: bool
    status: str  # ACTIVE | INACTIVE | REJECTED | UNRESOLVED
    evidence: tuple[str, ...] = ()


@dataclass
class EvaluatedMaterialSampleSites:
    shaderbin_sha256: str
    variant: VariantResolution
    sites: list[EvaluatedSampleSite] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)


def _resolve_uv_expr(
    expr: Any,
    *,
    params: dict,
    shaderbin_sha256: str,
) -> tuple[int | None, str, tuple[str, ...]]:
    """Return (texcoord|None, status, evidence)."""
    if expr is None:
        return None, "UNRESOLVED", ("missing uv_expression",)
    if isinstance(expr, str):
        if expr.startswith("TEXCOORD") and expr[8:].isdigit():
            return int(expr[8:]), "PROVEN_DIRECT", (f"direct {expr}",)
        if expr.startswith("SELECT_AMONG") or expr == "UNRESOLVED_SAMPLE_SITE_CONTRACT":
            return None, "UNRESOLVED", (expr,)
        return None, "UNRESOLVED", (f"unparsed uv string: {expr}",)
    if isinstance(expr, dict):
        kind = expr.get("kind")
        if kind == "Select":
            pred = expr.get("predicate") or {}
            choice = resolve_uv_choice_texcoord(
                params, shaderbin_sha256=shaderbin_sha256
            )
            if choice is None:
                return (
                    None,
                    "UNRESOLVED",
                    (
                        "Select predicate requires UVChoice contract for this SHA",
                        str(pred),
                    ),
                )
            texcoord, prov = choice
            # Contract true/false arms
            true_uv = expr.get("true", "TEXCOORD0")
            false_uv = expr.get("false", "TEXCOORD1")
            want = true_uv if texcoord == 0 else false_uv
            # UVChoice returns the texcoord index directly matching true/false arms
            if want.startswith("TEXCOORD") and want[8:].isdigit():
                return (
                    int(want[8:]),
                    "PROVEN_SWITCH",
                    (prov.detail, f"Select → {want}"),
                )
            return texcoord, "PROVEN_SWITCH", (prov.detail,)
        if kind == "Compose":
            return (
                None,
                "PROVEN_COMPOSITION_PENDING_IR",
                (expr.get("summary") or "Compose", "IR lowering pending"),
            )
        if kind == "UNRESOLVED_SAMPLE_SITE_CONTRACT":
            return None, "UNRESOLVED", (expr.get("note") or kind,)
    return None, "UNRESOLVED", (f"unknown uv_expression: {expr!r}",)


def evaluate_material_sample_sites(
    *,
    shaderbin_sha256: str | None,
    params: dict,
) -> EvaluatedMaterialSampleSites:
    """Evaluate JSON contracts for one MatI instance."""
    if not shaderbin_sha256:
        return EvaluatedMaterialSampleSites(
            shaderbin_sha256="",
            variant=resolve_shader_variant(shaderbin_sha256=None, params=params),
            rejection_reasons=["missing shaderbin_sha256 — fail closed"],
        )

    variant = resolve_shader_variant(
        shaderbin_sha256=shaderbin_sha256, params=params
    )
    out = EvaluatedMaterialSampleSites(
        shaderbin_sha256=shaderbin_sha256,
        variant=variant,
    )
    if variant.status == "REJECTED":
        out.rejection_reasons.append(
            f"variant rejected: {variant.provenance}"
        )

    data = load_shader_pass_contract(shaderbin_sha256)
    if data is None:
        # No pass contract — caller may still use CarLight static analysis only.
        return out

    for pass_row in data.get("relevant_passes") or []:
        req = pass_row.get("requires_variant")
        if req and variant.decoded_variant and req != variant.decoded_variant:
            continue
        if req and variant.status == "REJECTED":
            continue
        for s in pass_row.get("import_sample_sites") or []:
            regs: list[int] = []
            if "texture_register" in s:
                regs.append(int(s["texture_register"]))
            if "texture_register_range" in s:
                lo, hi = s["texture_register_range"]
                regs.extend(range(int(lo), int(hi) + 1))
            uv_expr = s.get("uv_expression")
            if uv_expr is None:
                uv_expr = s.get("expected_uv_expression")

            texcoord, uv_status, uv_ev = _resolve_uv_expr(
                uv_expr,
                params=params,
                shaderbin_sha256=shaderbin_sha256,
            )
            blender_import = bool(s.get("blender_import"))
            status = "ACTIVE"
            if not blender_import:
                status = "INACTIVE"
            elif uv_status.startswith("UNRESOLVED"):
                status = "UNRESOLVED"
                out.rejection_reasons.append(
                    f"sample site {s.get('sample_site_id')}: UV unresolved"
                )
            elif uv_status == "PROVEN_COMPOSITION_PENDING_IR":
                status = "UNRESOLVED"
                out.rejection_reasons.append(
                    f"sample site {s.get('sample_site_id')}: Compose UV pending IR"
                )

            for treg in regs or [int(s.get("texture_register", -1))]:
                if treg < 0:
                    continue
                out.sites.append(
                    EvaluatedSampleSite(
                        sample_site_id=str(
                            s.get("sample_site_id")
                            or f"{pass_row.get('scenario')}|t{treg}"
                        ),
                        shaderbin_sha256=shaderbin_sha256,
                        scenario=str(pass_row.get("scenario") or ""),
                        variant=str(
                            pass_row.get("variant")
                            or variant.decoded_variant
                            or ""
                        ),
                        texture_register=treg,
                        sampler_register=s.get("sampler_register"),
                        sampled_components=tuple(s.get("expected_comps") or ()),
                        uv_expression=uv_expr,
                        resolved_texcoord=texcoord,
                        semantic_role=s.get("semantic_role"),
                        blender_relevance=str(
                            s.get("blender_relevance")
                            or pass_row.get("blender_relevance")
                            or "UNRESOLVED"
                        ),
                        blender_import=blender_import,
                        status=status,
                        evidence=tuple(s.get("evidence") or ()) + uv_ev,
                    )
                )
    return out

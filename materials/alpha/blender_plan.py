"""Translate AlphaSemanticsIR → BlenderAlphaPlan (explicit policy, not expr sniffing)."""

from __future__ import annotations

from .ir import AlphaSemanticsIR, BlenderAlphaPlan
from .types import (
    BlenderRenderMode,
    SourceVisibilitySemantic,
    ThresholdStatus,
)


def blender_plan_from_alpha_ir(alpha: AlphaSemanticsIR) -> BlenderAlphaPlan:
    """Map proven source visibility to a Blender backend plan.

    Thresholds and modes come from contract evidence recorded on AlphaSemanticsIR,
    never from scanning Clamp/Multiply tree shapes.
    """
    sem = alpha.source_visibility
    mv = alpha.main_visibility
    atten = alpha.shading_attenuation

    if sem in (
        SourceVisibilitySemantic.PROVEN_OPAQUE,
        SourceVisibilitySemantic.PROVEN_NO_ALPHA_SEMANTICS,
        SourceVisibilitySemantic.PROVEN_SHADING_ONLY_MASK,
        SourceVisibilitySemantic.PROVEN_OUTPUT_ALPHA_ONLY,
    ):
        return BlenderAlphaPlan(
            render_mode=BlenderRenderMode.OPAQUE.value,
            alpha_expression=None,
            alpha_threshold=None,
            threshold_status=ThresholdStatus.UNUSED,
            shading_attenuation_expression=(
                atten.expression if atten else None
            ),
            apply_shading_attenuation_to_base_color=bool(
                atten and atten.expression is not None
            ),
            approximation_reason=(
                "BaseColor×attenuation is Blender backend approx — not exact BRDF"
                if atten and atten.expression is not None
                else None
            ),
            evidence=("source_visibility→OPAQUE Blender plan",),
            unresolved=tuple(alpha.unresolved),
        )

    if sem is SourceVisibilitySemantic.PROVEN_MASKED_VISIBILITY:
        thr = mv.source_threshold if mv else None
        thr_status = ThresholdStatus.BACKEND_APPROXIMATION
        if mv and mv.source_threshold_provenance:
            if "exact" in mv.source_threshold_provenance.lower():
                thr_status = ThresholdStatus.EXACT
            elif "shadow" in mv.source_threshold_provenance.lower():
                thr_status = ThresholdStatus.SOURCE_PASS_DERIVED
        return BlenderAlphaPlan(
            render_mode=BlenderRenderMode.CLIP.value,
            alpha_expression=mv.expression if mv else None,
            alpha_threshold=thr if thr is not None else 0.5,
            threshold_status=thr_status,
            approximation_reason=(
                mv.source_threshold_provenance
                if mv
                else "CLIP threshold backend approximation"
            ),
            evidence=(
                "PROVEN_MASKED_VISIBILITY→CLIP Blender plan",
                "threshold not inferred from Clamp IR nodes",
            ),
            unresolved=tuple(alpha.unresolved)
            + ("fixed_function_blend_unresolved",),
        )

    if sem is SourceVisibilitySemantic.PROVEN_BLENDED_VISIBILITY:
        return BlenderAlphaPlan(
            render_mode=BlenderRenderMode.BLEND.value,
            alpha_expression=mv.expression if mv else None,
            alpha_threshold=None,
            threshold_status=ThresholdStatus.UNUSED,
            evidence=("PROVEN_BLENDED_VISIBILITY→BLEND Blender plan",),
            unresolved=tuple(alpha.unresolved),
        )

    if sem is SourceVisibilitySemantic.PROVEN_TRANSMISSIVE_VISIBILITY:
        return BlenderAlphaPlan(
            render_mode=BlenderRenderMode.TRANSMISSION.value,
            alpha_expression=mv.expression if mv else None,
            alpha_threshold=None,
            threshold_status=ThresholdStatus.UNUSED,
            evidence=("PROVEN_TRANSMISSIVE_VISIBILITY→TRANSMISSION plan",),
            unresolved=tuple(alpha.unresolved),
        )

    # Rejected / unresolved → opaque fail-closed for Blender (IR should reject).
    return BlenderAlphaPlan(
        render_mode=BlenderRenderMode.OPAQUE.value,
        alpha_expression=None,
        alpha_threshold=None,
        threshold_status=ThresholdStatus.UNUSED,
        evidence=("fail_closed_unresolved_or_rejected",),
        unresolved=tuple(alpha.unresolved) + ("alpha_semantics_unresolved",),
    )

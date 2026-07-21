"""Explicit source-level alpha IR (separate from Blender translation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..forza_ir import MaterialExpression
from ..model import ProvenanceDiagnostic
from .types import SourceVisibilitySemantic, ThresholdStatus


@dataclass(frozen=True)
class AuthoredMaskIR:
    mask_id: str
    equation: str
    expression: MaterialExpression | None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class SurfaceVisibilityIR:
    semantic: SourceVisibilitySemantic
    expression: MaterialExpression | None = None
    source_threshold: float | None = None
    source_threshold_provenance: str | None = None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class PassVisibilityIR:
    pass_name: str
    semantic: SourceVisibilitySemantic
    expression: MaterialExpression | None = None
    discard_threshold: float | None = None
    feeds_discard: bool = False
    feeds_ignore_hit: bool = False
    feeds_sv_target0_a: bool = False
    evidence: tuple[ProvenanceDiagnostic, ...] = ()
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputAlphaIR:
    expression: MaterialExpression | None
    equation: str | None = None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ShadingAttenuationIR:
    expression: MaterialExpression | None
    equation: str | None = None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class TintAlphaIR:
    expression: MaterialExpression | None = None
    equation: str | None = None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class AlphaSemanticsIR:
    """Game-file-derived alpha semantics for one material instance branch."""

    source_visibility: SourceVisibilitySemantic
    authored_masks: tuple[AuthoredMaskIR, ...] = ()
    main_visibility: SurfaceVisibilityIR | None = None
    shadow_visibility: PassVisibilityIR | None = None
    ray_visibility: PassVisibilityIR | None = None
    depth_visibility: PassVisibilityIR | None = None
    output_alpha: OutputAlphaIR | None = None
    shading_attenuation: ShadingAttenuationIR | None = None
    tint_alpha: TintAlphaIR | None = None
    channel_classifications: tuple[tuple[str, str], ...] = ()
    contract_id: str | None = None
    shader_sha256: str | None = None
    branch_key: str | None = None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()
    unresolved: tuple[str, ...] = ()

    def to_fingerprint_dict(self) -> dict[str, Any]:
        """Deterministic fragment for effective-material fingerprinting."""
        mv = self.main_visibility
        return {
            "source_visibility": self.source_visibility.value,
            "contract_id": self.contract_id,
            "shader_sha256": self.shader_sha256,
            "branch_key": self.branch_key,
            "main_semantic": mv.semantic.value if mv else None,
            "main_threshold": mv.source_threshold if mv else None,
            "authored_mask_equations": [m.equation for m in self.authored_masks],
            "shading_attenuation_equation": (
                self.shading_attenuation.equation if self.shading_attenuation else None
            ),
            "unresolved": list(self.unresolved),
        }


@dataclass(frozen=True)
class BlenderAlphaPlan:
    """Backend translation of AlphaSemanticsIR — never inferred from expr shape."""

    render_mode: str  # BlenderRenderMode value
    alpha_expression: MaterialExpression | None
    alpha_threshold: float | None
    threshold_status: ThresholdStatus
    shadow_mode: str | None = None
    backface_policy: str | None = None
    show_transparent_back: bool | None = None
    depth_write_policy: str | None = None
    blend_policy: str | None = None
    approximation_reason: str | None = None
    shading_attenuation_expression: MaterialExpression | None = None
    apply_shading_attenuation_to_base_color: bool = False
    evidence: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def to_fingerprint_dict(self) -> dict[str, Any]:
        return {
            "render_mode": self.render_mode,
            "alpha_threshold": self.alpha_threshold,
            "threshold_status": self.threshold_status.value,
            "apply_shading_attenuation_to_base_color": (
                self.apply_shading_attenuation_to_base_color
            ),
            "approximation_reason": self.approximation_reason,
        }


@dataclass(frozen=True)
class EvaluatedAlphaSemantics:
    """Result of contract evaluation + Blender plan (production entry)."""

    source_visibility: SourceVisibilitySemantic
    alpha_ir: AlphaSemanticsIR
    blender_plan: BlenderAlphaPlan
    branch_key: str
    contract_id: str
    evidence: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    # --- Compatibility accessors (pre-closure tests / call sites) -------------
    @property
    def classification(self) -> SourceVisibilitySemantic:
        return self.source_visibility

    @property
    def source_visibility_semantic(self) -> str:
        return self.source_visibility.value

    @property
    def surface_visibility(self) -> str:
        mode = self.blender_plan.render_mode
        if mode == "CLIP":
            return "CLIP"
        if mode == "BLEND":
            return "BLEND"
        if mode == "TRANSMISSION":
            return "BLEND"
        if self.source_visibility in (
            SourceVisibilitySemantic.REJECTED_UNSUPPORTED_BRANCH,
            SourceVisibilitySemantic.UNRESOLVED,
        ):
            return "UNRESOLVED"
        return "OPAQUE"

    @property
    def blender_translation(self) -> str:
        return self.blender_plan.render_mode + (
            "_APPROXIMATION"
            if self.blender_plan.threshold_status.value == "BACKEND_APPROXIMATION"
            and self.blender_plan.render_mode == "CLIP"
            else ""
        )

    @property
    def blender_threshold(self) -> float | None:
        return self.blender_plan.alpha_threshold

    @property
    def clip_threshold(self) -> float | None:
        return self.blender_plan.alpha_threshold

    @property
    def threshold_provenance(self) -> str | None:
        return self.blender_plan.approximation_reason

    @property
    def opacity_expression(self) -> str | None:
        if self.blender_plan.render_mode not in ("CLIP", "HASHED", "BLEND", "TRANSMISSION"):
            return None
        if self.alpha_ir.authored_masks:
            return self.alpha_ir.authored_masks[0].equation
        if self.blender_plan.alpha_expression is not None:
            return "expression"
        return None

    @property
    def shading_attenuation_expression(self) -> str | None:
        if self.alpha_ir.shading_attenuation:
            return self.alpha_ir.shading_attenuation.equation
        return None

    @property
    def principled_alpha(self) -> str:
        if self.blender_plan.render_mode in ("CLIP", "HASHED", "BLEND", "TRANSMISSION"):
            return "expression"
        return "unused"

    @property
    def secondary_classification(self) -> str | None:
        if self.source_visibility.value == "PROVEN_OPAQUE" and (
            self.shading_attenuation_expression
        ):
            return "PROVEN_SHADING_ONLY_MASK"
        return None

"""Game-file-derived alpha contracts — no texture/material-name heuristics.

Stages (must stay separate):

A. Binding resolution (MatI/TXMP → texture at t-reg)
B. Shader semantic recovery (DXIL → channel use / expression)
C. Branch evaluation (MatI switches + defaults → active expression)
D. IR construction (SurfaceVisibility / ShadingAttenuation / …)
E. Blender compilation (evaluated IR only)

Texture payload statistics are validation-only and never select a branch.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AlphaClassification(str, Enum):
    """Source game-file classifications (not Blender backend mode names)."""

    GAME_FILES_PROVEN_OPAQUE = "GAME_FILES_PROVEN_OPAQUE"
    GAME_FILES_PROVEN_TEXTURE_VISIBILITY_MASK = (
        "GAME_FILES_PROVEN_TEXTURE_VISIBILITY_MASK"
    )
    GAME_FILES_PROVEN_SHADING_MASK = "GAME_FILES_PROVEN_SHADING_MASK"
    GAME_FILES_PROVEN_UNUSED_ALPHA = "GAME_FILES_PROVEN_UNUSED_ALPHA"
    UNRESOLVED_GAME_FILE_ALPHA = "UNRESOLVED_GAME_FILE_ALPHA"
    REJECTED_ALPHA_BRANCH = "REJECTED_ALPHA_BRANCH"
    # Deprecated alias kept for one release of report readers.
    GAME_FILES_PROVEN_CUTOUT = "GAME_FILES_PROVEN_TEXTURE_VISIBILITY_MASK"
    GAME_FILES_PROVEN_BLEND = "GAME_FILES_PROVEN_BLEND"
    PARTIAL_ALPHA = "PARTIAL_ALPHA"


class AlphaEvidenceStatus(str, Enum):
    PROVEN = "PROVEN"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"


class AlphaTransparencyProvenance(str, Enum):
    MATI_EXPLICIT = "MATI_EXPLICIT"
    MATERIAL_TEMPLATE_INHERITED = "MATERIAL_TEMPLATE_INHERITED"
    SHADER_DEFAULT = "SHADER_DEFAULT"
    UNRESOLVED = "UNRESOLVED"


class BlenderAlphaTranslation(str, Enum):
    """Backend approximation — not claimed exact Forza FF behaviour."""

    UNUSED = "UNUSED"
    CLIP_APPROXIMATION = "CLIP_APPROXIMATION"
    HASHED_APPROXIMATION = "HASHED_APPROXIMATION"
    BLEND_APPROXIMATION = "BLEND_APPROXIMATION"
    UNRESOLVED = "UNRESOLVED"


# car_standard exact production SHA (Milestone B1 / B1.75).
CAR_STANDARD_SHADERBIN_SHA256 = (
    "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16"
)
ALPHA_TRANSPARENCY_NAMEHASH = 0x5D3E6F2D
ALPHA_TXMP_NAMEHASH = 0x698CA64F
BASECOLORALPHA_TXMP_NAMEHASH = 0x85E937A9
AUTHORED_MASK_EQUATION = "saturate(Alpha.r * BaseColorAlpha.a)"


@dataclass(frozen=True)
class SampledChannel:
    """One DXIL-proven texture component contributing to an alpha-related expr."""

    texture_register: int
    sampler_register: int | None
    component: str  # r|g|b|a|x|y|z|w
    binding_name_hash: int | None = None
    sample_site_id: str = ""
    dxil_instruction_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaskExpression:
    """Authored mask equation recovered from DXIL (not from texture images)."""

    equation: str
    ssa_ids: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class PassAlphaBehaviour:
    """Per-pass visibility / output behaviour from DXIL (+ FF when known)."""

    pass_name: str
    samples_t16_alpha_r: bool
    samples_t17_bc_a: bool
    authored_mask_ssa: str | None
    feeds_discard: bool
    feeds_ignore_hit: bool
    feeds_sv_target0_a: bool
    feeds_sv_target0_rgb: bool
    discard_threshold: float | None
    fixed_function: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlphaContract:
    """Typed alpha contract keyed by exact shader identity + branch."""

    contract_id: str
    shader_sha256: str
    shader_family: str
    permutation: str
    branch_conditions: tuple[str, ...]
    sampled_channels: tuple[SampledChannel, ...]
    authored_mask: MaskExpression
    visibility_equation: str
    output_alpha_equation: str
    shading_attenuation_equation: str | None
    tint_alpha_equation: str | None
    classification: AlphaClassification
    passes: tuple[PassAlphaBehaviour, ...]
    alpha_transparency: dict[str, Any]
    fixed_function_status: str
    evidence_status: AlphaEvidenceStatus
    evidence: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["classification"] = self.classification.value
        d["evidence_status"] = self.evidence_status.value
        return d


@dataclass(frozen=True)
class EvaluatedAlphaSemantics:
    """Result of applying an AlphaContract to one MatI branch (IR-facing)."""

    classification: AlphaClassification
    source_visibility_semantic: str
    surface_visibility: str  # OPAQUE | CLIP | BLEND | UNRESOLVED (IR/backend)
    blender_translation: str
    blender_threshold: float | None
    threshold_provenance: str | None
    opacity_expression: str | None
    shading_attenuation_expression: str | None
    principled_alpha: str  # unused|expression
    secondary_classification: str | None = None
    evidence: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()


def _car_standard_pass_table() -> tuple[PassAlphaBehaviour, ...]:
    """From DXIL audit run 2026-07-21_*_grille-coverage_alpha-pso (exact SHA)."""
    ff_unresolved = {
        "blend_enable": "UNRESOLVED_NOT_IN_PSO_BLOB",
        "depth_write": "UNRESOLVED_NOT_IN_PSO_BLOB",
        "alpha_to_coverage": "UNRESOLVED_NOT_IN_PSO_BLOB",
    }
    rows = [
        ("CarLightScenario", False, False, True, False, None, "%2020"),
        ("CarLOD15LightScenario", False, False, True, False, None, "%2014"),
        ("CarFPlusPlusLightScenario", False, True, True, False, None, "%2056"),
        ("CarFPlusPlusDebugLightScenario", False, True, True, False, None, "%2305"),
        ("CarLOD15FPlusPlusLightScenario", False, True, False, False, None, "%2048"),
        ("SimpleCarLightScenario", True, True, True, True, 1e-7, "%892"),
        ("CarShadowDepthLightScenario", True, True, False, True, 0.5, "%120"),
        ("CarShadowDepthNoPSLightScenario", True, True, False, True, 0.5, "%60"),
        ("WheelBlurScenario", True, True, False, True, None, "%220"),
        ("CarRTBufferLightScenario", True, False, False, True, None, "%1381"),
        ("ProxyLODLightScenario", True, True, False, True, None, "%281"),
        ("CarRayTracing_T10LightScenario", False, False, False, False, None, "%267"),
    ]
    out: list[PassAlphaBehaviour] = []
    for name, disc, a, rgb, _clipish, thr, ssa in rows:
        ignore = name.startswith("CarRayTracing")
        out.append(
            PassAlphaBehaviour(
                pass_name=name,
                samples_t16_alpha_r=True,
                samples_t17_bc_a=True,
                authored_mask_ssa=ssa,
                feeds_discard=disc and not ignore,
                feeds_ignore_hit=ignore,
                feeds_sv_target0_a=a,
                feeds_sv_target0_rgb=rgb,
                discard_threshold=thr,
                fixed_function=dict(ff_unresolved),
                evidence=(
                    "dxil_audit:2026-07-21_grille-coverage_alpha-pso",
                    f"product=Alpha.r*BaseColorAlpha.a ({ssa})",
                ),
                unresolved=("fixed_function_blend_depth_a2c",),
            )
        )
    return tuple(out)


def car_standard_alpha_contract() -> AlphaContract:
    """Exact SHA contract: authored_mask = saturate(Alpha.r × BaseColorAlpha.a).

    Scoped to this SHA only. Blender CLIP 0.5 is a documented approximation,
    not exact Forza fixed-function state.
    """
    channels = (
        SampledChannel(
            texture_register=16,
            sampler_register=1,
            component="r",
            binding_name_hash=ALPHA_TXMP_NAMEHASH,
            sample_site_id="car_standard|t16|Alpha.r",
            dxil_instruction_ids=("%2018", "%118", "%890"),
            evidence=("DXIL sample Alpha_Texture → extract .r / .x",),
        ),
        SampledChannel(
            texture_register=17,
            sampler_register=1,
            component="a",
            binding_name_hash=BASECOLORALPHA_TXMP_NAMEHASH,
            sample_site_id="car_standard|t17|BaseColorAlpha.a",
            dxil_instruction_ids=("%263", "%98", "%202"),
            evidence=("DXIL sample BaseColorAlpha_Texture → extract .a / .w",),
        ),
    )
    mask = MaskExpression(
        equation=AUTHORED_MASK_EQUATION,
        ssa_ids=("%2020", "%2467", "%120", "%892"),
        operations=("multiply", "saturate"),
        evidence=(
            "CarLightScenario SSA %2020 = Alpha.r * BC.a; saturate %2467",
            "same product form in all audited raster/RT passes",
        ),
    )
    return AlphaContract(
        contract_id="car_standard.alpha.v2",
        shader_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        shader_family="car_standard",
        permutation="CarLightScenario",
        branch_conditions=(
            "AlphaTransparencyBool MatI → CB reg32.y (hash 0x5D3E6F2D)",
            "VariantOptions empty — shared PS set, not compile-time perm",
        ),
        sampled_channels=channels,
        authored_mask=mask,
        visibility_equation=(
            "Select(AlphaTransparency, authored_mask, Constant(1.0)) "
            "in cutout/shadow/simple/RT/F++ alpha paths"
        ),
        output_alpha_equation=(
            "CarLightScenario: SV_Target0.a from CB (typically 1), not product; "
            "F++/shadow/simple: product may write SV_Target0.a when transparency on"
        ),
        shading_attenuation_equation=(
            "CarLightScenario: authored_mask modulates selected lighting RGB "
            "(%2656=max(CB,sat(product)); not full final lit RGB)"
        ),
        tint_alpha_equation="tint.a * BaseColorAlpha.a on tint path (separate)",
        classification=AlphaClassification.GAME_FILES_PROVEN_SHADING_MASK,
        passes=_car_standard_pass_table(),
        alpha_transparency={
            "name": "AlphaTransparencyBool",
            "name_hash": hex(ALPHA_TRANSPARENCY_NAMEHASH),
            "name_hash_int": ALPHA_TRANSPARENCY_NAMEHASH,
            "cb_register": 32,
            "cb_component": "y",
            "type": "bool",
            "false_branch": (
                "cutout-related effective coverage forced to 1.0; "
                "CarLightScenario still applies ungated product to lighting"
            ),
            "true_branch": (
                "authored_mask participates in discard / IgnoreHit / "
                "SV_Target0.a on gated passes"
            ),
            "absent": "UNRESOLVED — do not invent CLIP from Alpha TXMP presence alone",
            "evidence": (
                "shaderbin.xml parameter AlphaTransparencyBool referenced=True",
                "DXIL cbufferLoadLegacy(..., i32 32) extract .y",
                "PSO blobs lack BLEND/RAST chunks",
            ),
        },
        fixed_function_status="UNRESOLVED_NOT_IN_PSO_BLOB",
        evidence_status=AlphaEvidenceStatus.PROVEN,
        evidence=(
            "reports/.../2026-07-21_grille-coverage_alpha-pso/",
            "CAR_STANDARD_ALPHA_COVERAGE_AUDIT.md",
        ),
        unresolved=(
            "fixed_function_blend_depth_stencil_a2c",
            "deferred consumer of SV_Target0.rgb",
            "exact Blender cutout mode/threshold (CLIP 0.5 is approximation)",
        ),
    )


def evaluate_car_standard_alpha(
    *,
    alpha_transparency: bool | None,
    shaderbin_sha256: str | None,
) -> EvaluatedAlphaSemantics:
    """Evaluate MatI AlphaTransparency against the exact SHA contract."""
    if (shaderbin_sha256 or "") != CAR_STANDARD_SHADERBIN_SHA256:
        return EvaluatedAlphaSemantics(
            classification=AlphaClassification.REJECTED_ALPHA_BRANCH
            if shaderbin_sha256
            else AlphaClassification.UNRESOLVED_GAME_FILE_ALPHA,
            source_visibility_semantic="UNRESOLVED",
            surface_visibility="UNRESOLVED",
            blender_translation=BlenderAlphaTranslation.UNRESOLVED.value,
            blender_threshold=None,
            threshold_provenance=None,
            opacity_expression=None,
            shading_attenuation_expression=None,
            principled_alpha="unused",
            unresolved=("shader_sha_not_car_standard_contract",),
        )
    mask = AUTHORED_MASK_EQUATION
    if alpha_transparency is False:
        return EvaluatedAlphaSemantics(
            classification=AlphaClassification.GAME_FILES_PROVEN_OPAQUE,
            source_visibility_semantic=(
                AlphaClassification.GAME_FILES_PROVEN_OPAQUE.value
            ),
            surface_visibility="OPAQUE",
            blender_translation=BlenderAlphaTranslation.UNUSED.value,
            blender_threshold=None,
            threshold_provenance=None,
            opacity_expression=None,
            shading_attenuation_expression=mask,
            principled_alpha="unused",
            secondary_classification=(
                AlphaClassification.GAME_FILES_PROVEN_SHADING_MASK.value
            ),
            evidence=(
                "AlphaTransparency=false → visibility Constant(1.0) on gated passes",
                "shading_attenuation remains authored_mask on CarLightScenario",
            ),
        )
    if alpha_transparency is True:
        return EvaluatedAlphaSemantics(
            classification=(
                AlphaClassification.GAME_FILES_PROVEN_TEXTURE_VISIBILITY_MASK
            ),
            source_visibility_semantic=(
                AlphaClassification.GAME_FILES_PROVEN_TEXTURE_VISIBILITY_MASK.value
            ),
            # IR surface mode used by Blender backend (approximation).
            surface_visibility="CLIP",
            blender_translation=BlenderAlphaTranslation.CLIP_APPROXIMATION.value,
            blender_threshold=0.5,
            threshold_provenance=(
                "ShadowDepth discard 0.5 (approximation); "
                "SimpleCar ~1e-7; F++ may write authored_mask to SV_Target0.a; "
                "fixed-function blend unresolved"
            ),
            opacity_expression=mask,
            shading_attenuation_expression=None,
            principled_alpha="expression",
            evidence=(
                "AlphaTransparency=true → authored_mask drives discard/IgnoreHit/"
                "output-alpha paths (source fact)",
                "Blender CLIP 0.5 is backend approximation — not exact Forza FF",
            ),
        )
    return EvaluatedAlphaSemantics(
        classification=AlphaClassification.UNRESOLVED_GAME_FILE_ALPHA,
        source_visibility_semantic="UNRESOLVED",
        surface_visibility="UNRESOLVED",
        blender_translation=BlenderAlphaTranslation.UNRESOLVED.value,
        blender_threshold=None,
        threshold_provenance=None,
        opacity_expression=None,
        shading_attenuation_expression=None,
        principled_alpha="unused",
        unresolved=(
            "AlphaTransparency absent — refuse has_alpha CLIP heuristic; "
            "do not treat missing as false without proven default",
        ),
    )


def contract_registry() -> dict[str, AlphaContract]:
    c = car_standard_alpha_contract()
    return {c.shader_sha256: c}


def load_or_build_registry() -> dict[str, AlphaContract]:
    return contract_registry()


def write_contract_json(path: Path) -> None:
    reg = load_or_build_registry()
    payload = {
        "schema_version": 2,
        "policy": {
            "no_filename_heuristics": True,
            "no_material_name_heuristics": True,
            "no_texture_payload_branch_selection": True,
            "texture_stats_validation_only": True,
            "blender_compiles_evaluated_ir_only": True,
            "blender_clip_is_approximation": True,
            "scoped_to_exact_sha_only": True,
        },
        "contracts": {sha: c.to_json() for sha, c in reg.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

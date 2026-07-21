"""Game-file-derived alpha contracts — generic registry architecture.

Production entry point:

    evaluate_alpha_semantics(shader_identity, permutation, mati_parameters, bindings)

Family-specific facts live in ``materials/alpha/contracts/<sha>.json``.
Texture payload statistics never select a production branch.
"""

from __future__ import annotations

from .blender_plan import blender_plan_from_alpha_ir
from .ir import (
    AlphaSemanticsIR,
    AuthoredMaskIR,
    BlenderAlphaPlan,
    EvaluatedAlphaSemantics,
    OutputAlphaIR,
    PassVisibilityIR,
    ShadingAttenuationIR,
    SurfaceVisibilityIR,
    TintAlphaIR,
)
from .registry import (
    AlphaContractError,
    AlphaContractRegistry,
    ShaderIdentityView,
    car_standard_alpha_contract,
    evaluate_alpha_semantics,
    evaluate_car_standard_alpha,
    load_contract,
    load_index,
)
from .types import (
    ALPHA_TRANSPARENCY_NAMEHASH,
    ALPHA_TXMP_NAMEHASH,
    AUTHORED_MASK_EQUATION,
    BASECOLORALPHA_TXMP_NAMEHASH,
    CAR_STANDARD_SHADERBIN_SHA256,
    SCHEMA_VERSION,
    AlphaTransparencyProvenance,
    BlenderRenderMode,
    ChannelClassification,
    ContractShaStatus,
    SourceVisibilitySemantic,
    ThresholdStatus,
)

# Back-compat aliases used by earlier foundation tests / reports.
AlphaClassification = SourceVisibilitySemantic
BlenderAlphaTranslation = BlenderRenderMode

__all__ = [
    "ALPHA_TRANSPARENCY_NAMEHASH",
    "ALPHA_TXMP_NAMEHASH",
    "AUTHORED_MASK_EQUATION",
    "BASECOLORALPHA_TXMP_NAMEHASH",
    "CAR_STANDARD_SHADERBIN_SHA256",
    "SCHEMA_VERSION",
    "AlphaClassification",
    "AlphaContractError",
    "AlphaContractRegistry",
    "AlphaSemanticsIR",
    "AlphaTransparencyProvenance",
    "AuthoredMaskIR",
    "BlenderAlphaPlan",
    "BlenderAlphaTranslation",
    "BlenderRenderMode",
    "ChannelClassification",
    "ContractShaStatus",
    "EvaluatedAlphaSemantics",
    "OutputAlphaIR",
    "PassVisibilityIR",
    "SCHEMA_VERSION",
    "ShaderIdentityView",
    "ShadingAttenuationIR",
    "SourceVisibilitySemantic",
    "SurfaceVisibilityIR",
    "ThresholdStatus",
    "TintAlphaIR",
    "blender_plan_from_alpha_ir",
    "car_standard_alpha_contract",
    "evaluate_alpha_semantics",
    "evaluate_car_standard_alpha",
    "load_contract",
    "load_index",
]

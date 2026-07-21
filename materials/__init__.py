"""Clean FH6 material pipeline (fail closed).

Only the v3 Base/Alpha/Normal/RMAO contract is production-active.
Capability selection is owned by materials.resolver; diagnostics observe it.
"""

from .pipeline_v3 import (
    CleanMaterialBuilder,
    MaterialSpec,
    MaterialTranslateError,
)
from .diagnostics import ImportMaterialReport, MaterialStatus
from .model import (
    CleanSurfaceCapability,
    MaterialCapabilityKind,
    ResolvedMaterial,
)
from .resolver import MaterialCapabilityResolver
from .translate import translator_for

MaterialBuilder = CleanMaterialBuilder

__all__ = (
    "CleanMaterialBuilder",
    "MaterialBuilder",
    "MaterialCapabilityKind",
    "MaterialCapabilityResolver",
    "MaterialSpec",
    "MaterialTranslateError",
    "MaterialStatus",
    "ImportMaterialReport",
    "CleanSurfaceCapability",
    "ResolvedMaterial",
    "translator_for",
)

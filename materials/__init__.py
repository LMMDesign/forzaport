"""Clean FH6 material pipeline (fail closed).

Only the v3 Base/Alpha/Normal/RMAO contract is production-active.
Diagnostics / capability probes are rewrite infrastructure.
"""

from .pipeline_v3 import (
    CleanMaterialBuilder,
    MaterialSpec,
    MaterialTranslateError,
)
from .diagnostics import ImportMaterialReport, MaterialStatus
from .translate import translator_for

MaterialBuilder = CleanMaterialBuilder

__all__ = (
    "CleanMaterialBuilder",
    "MaterialBuilder",
    "MaterialSpec",
    "MaterialTranslateError",
    "MaterialStatus",
    "ImportMaterialReport",
    "translator_for",
)

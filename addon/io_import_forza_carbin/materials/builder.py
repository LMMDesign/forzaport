"""Compatibility import for the clean material pipeline.

Production implementation lives in pipeline_v3.py. No legacy builder remains.
"""

from .pipeline_v3 import (
    CleanMaterialBuilder,
    MaterialSpec,
    MaterialTranslateError,
    TextureSlot,
)

MaterialBuilder = CleanMaterialBuilder

__all__ = (
    "CleanMaterialBuilder",
    "MaterialBuilder",
    "MaterialSpec",
    "MaterialTranslateError",
    "TextureSlot",
)

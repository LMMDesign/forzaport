"""ShaderParameterName hash helpers for paint/glass observations.

Paint/glass hashes are diagnostic/observation signals for MatI families.
They do not register production capabilities — only CLEAN_SURFACE is selected
by the authoritative resolver. Prefer Instance / override maps when present so
DFPR layout pollution (shared CB slots) does not flag glass on paint shaders.
"""

from __future__ import annotations

from ..parsing.material import ShaderParameterName as SPN

# Game-authored parameter hashes (ShaderParameterName). Presence proves
# paint/glass scalar capability — not the shader filename.
_PAINT_PARAM_HASHES = frozenset(
    {
        SPN.PaintColorColorParam,
        SPN.PaintColorGroupColorParam,
        SPN.UniqueBaseColorColorParam,
        SPN.UseUniqueBaseColorSwitchBool,
        SPN.UniqueBaseColorSwitchBool,
    }
)
_GLASS_PARAM_HASHES = frozenset(
    {
        SPN.GlassSurfaceColorParam,
        SPN.GlassTintColorParam,
        SPN.GlassInteriorTintColorParam,
        SPN.GlassRoughnessFloat,
        SPN.GlassSwitchBool,
        SPN.GlassOpacityFloat,
        SPN.GlassOpacityAltFloat,
        SPN.GlassSmoothnessFloat,
        SPN.GlassIORFloat,
    }
)


def params_have_paint_scalars(params: dict) -> bool:
    return any((h & 0xFFFFFFFF) in _PAINT_PARAM_HASHES for h in params)


def params_have_glass_scalars(params: dict) -> bool:
    return any((h & 0xFFFFFFFF) in _GLASS_PARAM_HASHES for h in params)


def capability_params(material) -> dict:
    """Params used for paint/glass capability: Instance when non-empty, else merged.

    MatI often has empty Instance (stock library colors live in Local DFPR).
    Callers that apply glass colors must still prefer paint when paint hashes
    are present and respect GlassSwitchBool.
    """
    instance = getattr(material, "parameters_instance", None) or {}
    if instance:
        return instance
    return getattr(material, "parameters", None) or {}

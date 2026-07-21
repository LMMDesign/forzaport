"""Shared Blender diagnostic materials for unresolved import slots.

Distinct colours / names so unsupported capability is never confused with
genuine missing-texture / decode failures.
"""

from __future__ import annotations

from .diagnostics import MaterialStatus, is_missing_texture_family

DIAGNOSTIC_MATERIAL_NAME = "FORZAPORT_UNRESOLVED_MATERIAL"
UNSUPPORTED_MATERIAL_NAME = "FORZAPORT_UNSUPPORTED_CAPABILITY"
MISSING_TEXTURE_MATERIAL_NAME = "FORZAPORT_MISSING_TEXTURE"
TEXTURE_DECODE_MATERIAL_NAME = "FORZAPORT_TEXTURE_DECODE_ERROR"
BUILDER_ERROR_MATERIAL_NAME = "FORZAPORT_BUILDER_ERROR"

# Emission RGB (linear). Magenta reserved for unsupported capability.
_COLORS = {
    UNSUPPORTED_MATERIAL_NAME: (1.0, 0.0, 1.0),
    MISSING_TEXTURE_MATERIAL_NAME: (1.0, 0.35, 0.0),
    TEXTURE_DECODE_MATERIAL_NAME: (1.0, 0.85, 0.0),
    BUILDER_ERROR_MATERIAL_NAME: (0.0, 0.85, 1.0),
    DIAGNOSTIC_MATERIAL_NAME: (1.0, 0.0, 1.0),
}


def _ensure_emission_material(name: str, rgb: tuple[float, float, float]):
    import bpy

    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat["forza_diagnostic"] = True
    mat["forza_pipeline"] = "diagnostic"
    mat["forza_diagnostic_kind"] = name
    nt = mat.node_tree
    nt.nodes.clear()
    output = nt.nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.name = f"ForzaPort {name}"
    emission.inputs[0].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    emission.inputs[1].default_value = 1.0
    emission.location = (80, 0)
    nt.links.new(emission.outputs[0], output.inputs["Surface"])
    try:
        mat.surface_render_method = "DITHERED"
    except (AttributeError, TypeError):
        pass
    return mat


def diagnostic_material_name_for_status(status: MaterialStatus | str | None) -> str:
    """Map material status to a shared diagnostic datablock name."""
    if status is None:
        return UNSUPPORTED_MATERIAL_NAME
    if not isinstance(status, MaterialStatus):
        try:
            status = MaterialStatus(str(status))
        except ValueError:
            return DIAGNOSTIC_MATERIAL_NAME
    if status is MaterialStatus.BUILDER_ERROR:
        return BUILDER_ERROR_MATERIAL_NAME
    if status in (
        MaterialStatus.TEXTURE_DECODE_FAILED,
        MaterialStatus.BLENDER_IMAGE_CREATION_FAILED,
    ):
        return TEXTURE_DECODE_MATERIAL_NAME
    if is_missing_texture_family(status):
        return MISSING_TEXTURE_MATERIAL_NAME
    if status in (
        MaterialStatus.UNRESOLVED_CAPABILITY,
        MaterialStatus.MISSING_PROVENANCE,
        MaterialStatus.INVALID_BINDING,
    ):
        return UNSUPPORTED_MATERIAL_NAME
    return DIAGNOSTIC_MATERIAL_NAME


def get_diagnostic_material(status: MaterialStatus | str | None = None):
    """Return the shared diagnostic material for ``status``."""
    name = diagnostic_material_name_for_status(status)
    rgb = _COLORS.get(name, _COLORS[DIAGNOSTIC_MATERIAL_NAME])
    return _ensure_emission_material(name, rgb)


def get_unresolved_material():
    """Backward-compatible alias: unsupported-capability magenta."""
    return get_diagnostic_material(MaterialStatus.UNRESOLVED_CAPABILITY)

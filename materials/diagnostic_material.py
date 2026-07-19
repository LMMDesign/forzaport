"""Shared Blender diagnostic material for unresolved import slots."""

from __future__ import annotations

DIAGNOSTIC_MATERIAL_NAME = "FORZAPORT_UNRESOLVED_MATERIAL"


def get_unresolved_material():
    """Return the shared diagnostic material, creating it once per blend file."""
    import bpy

    existing = bpy.data.materials.get(DIAGNOSTIC_MATERIAL_NAME)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(DIAGNOSTIC_MATERIAL_NAME)
    mat.use_nodes = True
    mat["forza_diagnostic"] = True
    mat["forza_pipeline"] = "diagnostic"
    nt = mat.node_tree
    nt.nodes.clear()
    output = nt.nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.name = "ForzaPort Unresolved"
    emission.inputs[0].default_value = (1.0, 0.0, 1.0, 1.0)
    emission.inputs[1].default_value = 1.0
    emission.location = (80, 0)
    nt.links.new(emission.outputs[0], output.inputs["Surface"])
    try:
        mat.surface_render_method = "DITHERED"
    except (AttributeError, TypeError):
        pass
    return mat

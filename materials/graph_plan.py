"""Bpy-free graph build plan + MaterialSpec → ResolvedMaterial boundary."""

from __future__ import annotations

from typing import Any

from .model import (
    BaseColorSourceKind,
    CleanSurfaceCapability,
    MaterialCapabilityKind,
    ResolvedMaterial,
    ResolvedTextureSlot,
)
from .pipeline_v3 import MaterialSpec, resolved_material_from_spec

MATERIAL_GRAPH_VERSION = 401


def ensure_resolved_material(material: ResolvedMaterial | MaterialSpec) -> ResolvedMaterial:
    """Boundary: adapt MaterialSpec once; construction uses typed models only."""
    if isinstance(material, ResolvedMaterial):
        return material
    if isinstance(material, MaterialSpec):
        return resolved_material_from_spec(material)
    raise TypeError(
        f"build_material expects ResolvedMaterial or MaterialSpec, got {type(material)!r}"
    )


def _slot_plan(slot: ResolvedTextureSlot | None) -> dict[str, Any] | None:
    if slot is None:
        return None
    address = dict(slot.address) if slot.address else None
    return {
        "role": slot.role,
        "path": slot.path,
        "texcoord": slot.texcoord,
        "channel": slot.channel,
        "tiling": list(slot.tiling),
        "address": address,
        "param_hash": int(slot.param_hash) & 0xFFFFFFFF,
        "param_name": slot.param_name,
    }


def graph_build_plan(resolved: ResolvedMaterial) -> tuple[dict[str, Any], ...]:
    """Deterministic node-build instructions (no bpy). Used for equivalence tests."""
    if resolved.capability_kind is not MaterialCapabilityKind.CLEAN_SURFACE:
        raise RuntimeError(
            f"unsupported capability for node graph: {resolved.capability_kind}"
        )
    cap = resolved.capability
    source = cap.base_color_source
    steps: list[dict[str, Any]] = [
        {
            "op": "material_meta",
            "name": resolved.name,
            "shader_name": resolved.shader_name,
            "graph_version": MATERIAL_GRAPH_VERSION,
            "pipeline": "clean-v3",
        },
        {"op": "new_principled_graph"},
    ]
    if source.kind is BaseColorSourceKind.TEXTURE:
        steps.append(
            {
                "op": "texture",
                "slot": _slot_plan(source.texture),
                "location": [-520, 300],
                "binds": "base_color",
            }
        )
    elif source.kind is BaseColorSourceKind.WEAVE_COMPOSITE:
        assert source.weave is not None
        steps.append(
            {
                "op": "weave_composite_base_color",
                "tint_a": list(source.weave.tint_a),
                "tint_b": list(source.weave.tint_b),
                "mask": _slot_plan(source.weave.mask),
                "blend": source.weave.blend,
            }
        )
    elif source.kind in (
        BaseColorSourceKind.MATERIAL_CONSTANT,
        BaseColorSourceKind.INSTANCE_PAINT,
    ):
        steps.append(
            {
                "op": "constant_base_color",
                "rgba": list(source.color),
            }
        )
    else:
        raise RuntimeError(
            f"node graph cannot build BaseColorSourceKind.{source.kind.name}"
        )
    if cap.rmao_map is not None:
        steps.append(
            {
                "op": "texture",
                "slot": _slot_plan(cap.rmao_map),
                "location": [-520, -260],
                "binds": "rmao",
            }
        )
        steps.append({"op": "rmao_separate_and_ao_multiply"})
        steps.append({"op": "link_rmao_rough_metal"})
    steps.append({"op": "link_base_color"})
    if cap.normal_map is not None:
        steps.append(
            {
                "op": "texture",
                "slot": _slot_plan(cap.normal_map),
                "location": [-520, -620],
                "binds": "normal",
            }
        )
        steps.append(
            {
                "op": "normal_map",
                "uv_map": cap.normal_map.texcoord,
            }
        )
    if cap.alpha_map is not None:
        steps.append(
            {
                "op": "texture",
                "slot": _slot_plan(cap.alpha_map),
                "location": [-520, -920],
                "binds": "alpha",
            }
        )
        steps.append({"op": "alpha_separate_red"})
        if cap.alpha_mode == "CLIP":
            steps.append(
                {
                    "op": "alpha_clip",
                    "threshold": float(cap.alpha_threshold),
                }
            )
        steps.append({"op": "link_alpha_and_transparent_mix"})
        steps.append(
            {
                "op": "configure_transparency",
                "mode": cap.alpha_mode,
                "threshold": float(cap.alpha_threshold),
            }
        )
    steps.append({"op": "link_surface_output"})
    return tuple(steps)

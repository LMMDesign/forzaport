"""Compile ForzaMaterialIR → Blender nodes / graph plans (no MatI inspection)."""

from __future__ import annotations

from typing import Any

from .forza_ir import (
    Channel,
    Clamp,
    ConstantColor,
    ConstantScalar,
    ForzaMaterialIR,
    MeshUV,
    Mix,
    Multiply,
    NormalDecode,
    OffsetUV,
    RotateUV,
    ScaleUV,
    SelectUV,
    ShadingAttenuation,
    TextureSample,
    TextureSampleExpression,
)
from .graph_plan import MATERIAL_GRAPH_VERSION
from .model import ResolvedTextureSlot


def _uv_index(expr) -> int:
    if isinstance(expr, MeshUV):
        return int(expr.index)
    if isinstance(expr, (ScaleUV, OffsetUV, RotateUV)):
        return _uv_index(expr.source)
    if isinstance(expr, SelectUV):
        # Production compiler receives already-selected MeshUV via ScaleUV parent;
        # if SelectUV appears raw, prefer `a` when condition ConstantScalar==1.
        if isinstance(expr.condition, ConstantScalar) and expr.condition.value >= 0.5:
            return _uv_index(expr.a)
        return _uv_index(expr.b)
    raise RuntimeError(f"unsupported UV expression for compiler: {type(expr)!r}")


def _tiling_of(expr) -> tuple[float, float]:
    if isinstance(expr, ScaleUV):
        return (float(expr.scale[0]), float(expr.scale[1]))
    if isinstance(expr, (OffsetUV, RotateUV)):
        return _tiling_of(expr.source)
    return (1.0, 1.0)


def _rotation_of(expr) -> float:
    """Degrees of ``RotateUV`` anywhere in the UV chain (0.0 when absent).

    car_carbonfiber weave UV only; every other family's UV chain has no
    ``RotateUV`` node, so this is always ``0.0`` for them (no behaviour change).
    """
    if isinstance(expr, RotateUV):
        return float(expr.degrees)
    if isinstance(expr, (ScaleUV, OffsetUV)):
        return _rotation_of(expr.source)
    return 0.0


def _pan_of(expr) -> tuple[float, float]:
    """Offset anywhere in the UV chain (0,0 when absent)."""
    if isinstance(expr, OffsetUV):
        return (float(expr.offset[0]), float(expr.offset[1]))
    if isinstance(expr, (ScaleUV, RotateUV)):
        return _pan_of(expr.source)
    return (0.0, 0.0)


def _slot_from_sample(sample: TextureSampleExpression, *, role: str) -> ResolvedTextureSlot:
    src = sample.source
    path = src.original_path or ""
    if not path and src.canonical_game_path:
        path = "GAME:\\" + src.canonical_game_path.replace("/", "\\")
    address = {"U": sample.sampler.address_u, "V": sample.sampler.address_v}
    if address == {"U": "REPEAT", "V": "REPEAT"}:
        address = None
    return ResolvedTextureSlot(
        role=role,
        path=path,
        texcoord=f"TEXCOORD{_uv_index(sample.uv)}",
        channel=sample.channels[0] if len(sample.channels) == 1 else None,
        tiling=_tiling_of(sample.uv),
        address=address,
        param_hash=int(sample.binding_name_hash) & 0xFFFFFFFF,
        param_name=role,
        evidence=(),
        source_kind=src.kind.value if src.kind else None,
        canonical_path=src.canonical_game_path,
        filesystem_path=src.filesystem_path,
        archive_path=src.archive_path,
        archive_member=src.archive_member,
        rotation_degrees=_rotation_of(sample.uv),
        pan=_pan_of(sample.uv),
    )


def _find_texture_sample(expr) -> TextureSample | None:
    if expr is None:
        return None
    if isinstance(expr, TextureSample):
        return expr
    if isinstance(expr, (Channel, NormalDecode, Clamp)):
        return _find_texture_sample(expr.source)
    if isinstance(expr, Multiply):
        return _find_texture_sample(expr.a) or _find_texture_sample(expr.b)
    if isinstance(expr, Mix):
        return (
            _find_texture_sample(expr.a)
            or _find_texture_sample(expr.b)
            or _find_texture_sample(expr.factor)
        )
    return None


def _find_mix(expr) -> Mix | None:
    """Locate a ``Mix`` in the base-color chain (weave or TintMode)."""
    if isinstance(expr, Mix):
        return expr
    if isinstance(expr, Multiply):
        return _find_mix(expr.a) or _find_mix(expr.b)
    return None


def _constant_rgba(expr) -> tuple[float, float, float, float] | None:
    if isinstance(expr, ConstantColor):
        return expr.rgba
    if isinstance(expr, Multiply):
        return _constant_rgba(expr.a) or _constant_rgba(expr.b)
    return None


def _peel_ao_multiply(expr):
    """If expr is Multiply(base, AO Channel), return (base, True); else (expr, False)."""
    if not isinstance(expr, Multiply):
        return expr, False
    if isinstance(expr.b, Channel) and expr.b.channel in ("b", "z"):
        return expr.a, True
    if isinstance(expr.a, Channel) and expr.a.channel in ("b", "z"):
        return expr.b, True
    return expr, False


def _peel_coverage_multiply(expr):
    """Peel outer Multiply(base, Alpha.r×BC.a) coverage product.

    Returns ``(base, coverage_multiply_or_None)``.
    """
    if not isinstance(expr, Multiply):
        return expr, None
    for cov, base in ((expr.b, expr.a), (expr.a, expr.b)):
        if not isinstance(cov, Multiply):
            continue
        if isinstance(cov.a, Channel) and isinstance(cov.b, Channel):
            return base, cov
    return expr, None


def _coverage_alpha_sample(cov: Multiply):
    """Return the Alpha TXMP TextureSample from a coverage product Multiply."""
    for side in (cov.a, cov.b):
        if isinstance(side, Channel) and side.channel in ("x", "r"):
            return _find_texture_sample(side)
    return None


def _attenuation_expr(att: ShadingAttenuation | Multiply | None):
    """Unwrap Clamp/Saturate wrapper around the Alpha.r×BC.a product."""
    if att is None:
        return None
    expr = att.expression if isinstance(att, ShadingAttenuation) else att
    if isinstance(expr, Clamp):
        return expr.source
    return expr


def _coverage_alpha_sample_from_attenuation(att: ShadingAttenuation | None):
    if att is None:
        return None
    prod = _attenuation_expr(att)
    if isinstance(prod, Multiply):
        return _coverage_alpha_sample(prod)
    # Single-channel Alpha.r only
    return _find_texture_sample(prod)


def _unwrap_opacity_core(opacity):
    """Strip CLIP threshold Clamp then saturate Clamp → product or channel."""
    expr = opacity
    # Outer CLIP remapper: Clamp(sat, threshold, 1)
    if isinstance(expr, Clamp) and float(expr.lo) > 0.0:
        expr = expr.source
    if isinstance(expr, Clamp):
        expr = expr.source
    return expr


def _opacity_is_alpha_times_bc_a(opacity) -> bool:
    """True when IR opacity is DXIL saturate(Alpha.r × BaseColorAlpha.a)."""
    prod = _unwrap_opacity_core(opacity)
    if not isinstance(prod, Multiply):
        return False
    chs: set[str] = set()
    for side in (prod.a, prod.b):
        if isinstance(side, Channel):
            chs.add(side.channel.lower())
    return ({"x", "a"} <= chs) or ({"r", "a"} <= chs)


def _find_tint_multiply_color(expr) -> tuple[float, float, float, float] | None:
    """Find ConstantColor partner of tex×tint Multiply (not AO Channel)."""
    if isinstance(expr, Multiply):
        if isinstance(expr.b, ConstantColor):
            return expr.b.rgba
        if isinstance(expr.a, ConstantColor):
            return expr.a.rgba
        return _find_tint_multiply_color(expr.a) or _find_tint_multiply_color(expr.b)
    if isinstance(expr, Mix):
        return _find_tint_multiply_color(expr.a) or _find_tint_multiply_color(expr.b)
    return None


def _tint_metal_blend_of(expr) -> str | None:
    """Classify TintMode metal Mix; None when weave Mix or absent."""
    if isinstance(expr, Mix):
        if isinstance(expr.factor, Channel) and expr.factor.channel in ("g", "y"):
            a_has_tint = _find_tint_multiply_color(expr.a) is not None
            b_has_tint = _find_tint_multiply_color(expr.b) is not None
            if a_has_tint and not b_has_tint:
                return "lerp_tinted_tex_metal"
            if b_has_tint and not a_has_tint:
                return "lerp_tex_tinted_metal"
        return None
    if isinstance(expr, Multiply):
        return _tint_metal_blend_of(expr.a) or _tint_metal_blend_of(expr.b)
    return None


def _is_weave_mix(expr) -> Mix | None:
    """car_carbonfiber: Mix(ConstantA, ConstantB, WeaveMask.R Channel)."""
    mix = _find_mix(expr)
    if mix is None:
        return None
    if not isinstance(mix.factor, Channel) or mix.factor.channel not in ("r", "x"):
        return None
    if _constant_rgba(mix.a) is None or _constant_rgba(mix.b) is None:
        return None
    # TintMode mixes carry TextureSample on a/b; weave does not.
    if _find_texture_sample(mix.a) is not None or _find_texture_sample(mix.b) is not None:
        return None
    if _find_texture_sample(mix.factor) is None:
        return None
    return mix


def _slot_dict(slot: ResolvedTextureSlot, *, param_name: str | None = None) -> dict[str, Any]:
    """Slot → plan dict. Rotation/pan keys are omitted at their (0.0) defaults
    so every non-carbon family's plan is byte-identical to before this change.
    """
    d: dict[str, Any] = {
        "role": slot.role,
        "path": slot.path,
        "texcoord": slot.texcoord,
        "channel": slot.channel,
        "tiling": list(slot.tiling),
        "address": dict(slot.address) if slot.address else None,
        "param_hash": slot.param_hash,
        "param_name": param_name if param_name is not None else slot.param_name,
    }
    if slot.rotation_degrees:
        d["rotation_degrees"] = float(slot.rotation_degrees)
    if tuple(slot.pan or (0.0, 0.0)) != (0.0, 0.0):
        d["pan"] = list(slot.pan)
    return d


def graph_build_plan_from_ir(ir: ForzaMaterialIR) -> tuple[dict[str, Any], ...]:
    """Deterministic graph plan from IR only (no MatI)."""
    if ir.rejection_reasons:
        raise RuntimeError("; ".join(ir.rejection_reasons))
    steps: list[dict[str, Any]] = [
        {
            "op": "material_meta",
            "name": None,  # filled by caller if needed
            "shader_name": ir.shader.shader_name,
            "graph_version": MATERIAL_GRAPH_VERSION,
            "pipeline": "forza-ir-v1",
            "shaderbin_sha256": ir.shader.shaderbin_sha256,
            "permutation": ir.shader.permutation,
        },
        {"op": "new_principled_graph"},
    ]

    base_core, coverage_baked = _peel_coverage_multiply(ir.base_color)
    # Prefer explicit ShadingAttenuation field (not baked into Base Color).
    attenuation = ir.shading_attenuation
    if attenuation is None and coverage_baked is not None:
        attenuation = ShadingAttenuation(
            expression=coverage_baked,
            evidence=(),
        )
    base_core, ao_from_multiply = _peel_ao_multiply(base_core)
    base_tex = _find_texture_sample(base_core)
    base_const = _constant_rgba(base_core)
    weave_mix = _is_weave_mix(base_core)
    tint_rgba = _find_tint_multiply_color(base_core)
    tint_blend = _tint_metal_blend_of(base_core)

    if weave_mix is not None:
        mask_tex = _find_texture_sample(weave_mix.factor)
        tint_a = _constant_rgba(weave_mix.a)
        tint_b = _constant_rgba(weave_mix.b)
        if mask_tex is None or tint_a is None or tint_b is None:
            raise RuntimeError("IR Mix base color missing mask TextureSample or tint constants")
        mask_slot = _slot_from_sample(mask_tex.sample, role="weave_mask")
        steps.append(
            {
                "op": "weave_composite_base_color",
                "tint_a": list(tint_a),
                "tint_b": list(tint_b),
                "mask": _slot_dict(mask_slot, param_name="WeaveMask"),
                "blend": "lerp_a_b_mask_r",
            }
        )
    elif base_tex is not None:
        slot = _slot_from_sample(base_tex.sample, role="base_color")
        steps.append(
            {
                "op": "texture",
                "slot": _slot_dict(slot),
                "location": [-520, 300],
                "binds": "base_color",
            }
        )
        if tint_rgba is not None:
            steps.append(
                {
                    "op": "multiply_base_color_tint",
                    "rgba": list(tint_rgba),
                }
            )
            if tint_blend is not None:
                steps.append(
                    {
                        "op": "tint_mode_metal_lerp",
                        "variant": tint_blend,
                    }
                )
        if attenuation is not None and ir.opacity is None:
            steps.append(
                {
                    "op": "multiply_base_color_shading_attenuation",
                    "label": (
                        "Blender backend approximation — not exact Forza BRDF "
                        "(BaseColor × saturate(Alpha.r×BC.a))"
                    ),
                }
            )
    elif base_const is not None:
        steps.append({"op": "constant_base_color", "rgba": list(base_const)})
    else:
        raise RuntimeError("IR missing Base Color expression")

    rmao_tex = None
    if ir.roughness is not None:
        rmao_tex = _find_texture_sample(ir.roughness)
    if rmao_tex is not None:
        slot = _slot_from_sample(rmao_tex.sample, role="rmao")
        steps.append(
            {
                "op": "texture",
                "slot": _slot_dict(slot, param_name="RoughMetalAO"),
                "location": [-520, -260],
                "binds": "rmao",
            }
        )
        steps.append({"op": "rmao_separate_and_ao_multiply"})
        steps.append({"op": "link_rmao_rough_metal"})
    elif ao_from_multiply:
        # Multiply without separable rmao channels — refuse (incomplete IR).
        raise RuntimeError("IR Multiply base×AO without RMAO sample")

    steps.append({"op": "link_base_color"})

    if ir.normal is not None:
        ntex = _find_texture_sample(ir.normal)
        if ntex is None:
            raise RuntimeError("IR normal missing TextureSample")
        slot = _slot_from_sample(ntex.sample, role="normal")
        steps.append(
            {
                "op": "texture",
                "slot": _slot_dict(slot, param_name="Normal"),
                "location": [-520, -620],
                "binds": "normal",
            }
        )
        normal_step: dict[str, Any] = {"op": "normal_map", "uv_map": slot.texcoord}
        if isinstance(ir.normal, NormalDecode) and float(ir.normal.strength) != 1.0:
            normal_step["strength"] = float(ir.normal.strength)
        steps.append(normal_step)

    if ir.opacity is not None:
        atex = _find_texture_sample(ir.opacity)
        if atex is None:
            raise RuntimeError("IR opacity missing TextureSample")
        slot = _slot_from_sample(atex.sample, role="alpha")
        alpha_slot_dict = _slot_dict(slot, param_name="Alpha")
        alpha_slot_dict["channel"] = slot.channel or "x"
        steps.append(
            {
                "op": "texture",
                "slot": alpha_slot_dict,
                "location": [-520, -920],
                "binds": "alpha",
            }
        )
        steps.append({"op": "alpha_separate_red"})
        if _opacity_is_alpha_times_bc_a(ir.opacity):
            steps.append(
                {
                    "op": "multiply_alpha_by_basecolor_a",
                    "label": (
                        "cutout = saturate(Alpha.r × BaseColorAlpha.a) "
                        "(AlphaTransparency=true)"
                    ),
                }
            )
        mode = "CLIP" if isinstance(ir.opacity, Clamp) else "BLEND"
        threshold = float(ir.opacity.lo) if isinstance(ir.opacity, Clamp) else 0.5
        if mode == "CLIP":
            steps.append({"op": "alpha_clip", "threshold": threshold})
        steps.append({"op": "link_alpha_and_transparent_mix"})
        steps.append(
            {
                "op": "configure_transparency",
                "mode": mode,
                "threshold": threshold,
            }
        )
    elif attenuation is not None and ir.opacity is None:
        atex = _coverage_alpha_sample_from_attenuation(attenuation)
        if atex is None:
            raise RuntimeError(
                "IR shading_attenuation missing Alpha TextureSample"
            )
        slot = _slot_from_sample(atex.sample, role="alpha")
        alpha_slot_dict = _slot_dict(slot, param_name="Alpha")
        alpha_slot_dict["channel"] = slot.channel or "x"
        steps.append(
            {
                "op": "texture",
                "slot": alpha_slot_dict,
                "location": [-520, -920],
                "binds": "alpha",
            }
        )
        steps.append(
            {
                "op": "configure_transparency",
                "mode": "OPAQUE",
                "threshold": 0.5,
            }
        )

    steps.append({"op": "link_surface_output"})
    return tuple(steps)


def compile_forza_material_ir(ir: ForzaMaterialIR, image_service, *, material_name: str):
    """Build a Blender material from IR via existing typed slot graph builder.

    ``image_service`` is ``(resolver, image_cache)`` — no MatI access.
    """
    import bpy

    from .nodes_v3 import _build_resolved_material
    from .model import (
        BaseColorSourceKind,
        MaterialCapabilityKind,
        ResolvedBaseColorSource,
        ResolvedMaterial,
        ResolvedWeaveComposite,
        make_clean_surface_capability,
    )

    if ir.rejection_reasons:
        raise RuntimeError("; ".join(ir.rejection_reasons))

    resolver, image_cache = image_service

    # Reconstruct a CleanSurfaceCapability mirror for the existing node path.
    # This is an adapter inside the IR compiler only — decisions already made.
    base_core, coverage_baked = _peel_coverage_multiply(ir.base_color)
    attenuation = ir.shading_attenuation
    if attenuation is None and coverage_baked is not None:
        attenuation = ShadingAttenuation(
            expression=coverage_baked,
            evidence=(),
        )
    base_core, _ao = _peel_ao_multiply(base_core)
    base_tex = _find_texture_sample(base_core)
    base_const = _constant_rgba(base_core)
    weave_mix = _is_weave_mix(base_core)
    tint_rgba = _find_tint_multiply_color(base_core)
    tint_blend = _tint_metal_blend_of(base_core)

    if weave_mix is not None:
        mask_tex = _find_texture_sample(weave_mix.factor)
        tint_a = _constant_rgba(weave_mix.a)
        tint_b = _constant_rgba(weave_mix.b)
        if mask_tex is None or tint_a is None or tint_b is None:
            raise RuntimeError(
                "IR Mix base color missing mask TextureSample or tint constants"
            )
        mask_slot = _slot_from_sample(mask_tex.sample, role="weave_mask")
        weave = ResolvedWeaveComposite(
            tint_a=tint_a,
            tint_b=tint_b,
            mask=mask_slot,
            blend="lerp_a_b_mask_r",
        )
        source = ResolvedBaseColorSource(
            kind=BaseColorSourceKind.WEAVE_COMPOSITE,
            weave=weave,
        )
    elif base_tex is not None:
        base_slot = _slot_from_sample(base_tex.sample, role="base_color")
        source = ResolvedBaseColorSource(
            kind=BaseColorSourceKind.TEXTURE,
            texture=base_slot,
            multiply_tint=tint_rgba,
            tint_metal_blend=tint_blend,
            multiply_coverage=(
                attenuation is not None and ir.opacity is None
            ),
        )
    elif base_const is not None:
        source = ResolvedBaseColorSource(
            kind=BaseColorSourceKind.MATERIAL_CONSTANT,
            color=base_const,
        )
    else:
        raise RuntimeError("IR has no Base Color")

    normal_slot = None
    normal_strength = 1.0
    if ir.normal is not None:
        n = _find_texture_sample(ir.normal)
        if n is not None:
            normal_slot = _slot_from_sample(n.sample, role="normal")
        if isinstance(ir.normal, NormalDecode):
            normal_strength = float(ir.normal.strength)

    rmao_slot = None
    if ir.roughness is not None:
        r = _find_texture_sample(ir.roughness)
        if r is not None:
            rmao_slot = _slot_from_sample(r.sample, role="rmao")

    alpha_slot = None
    alpha_mode = "OPAQUE"
    alpha_threshold = 0.5
    alpha_cutout_uses_bc_a_product = False
    if ir.opacity is not None:
        a = _find_texture_sample(ir.opacity)
        if a is not None:
            alpha_slot = _slot_from_sample(a.sample, role="alpha")
            if isinstance(ir.opacity, Clamp):
                alpha_mode = "CLIP"
                alpha_threshold = float(ir.opacity.lo)
            else:
                alpha_mode = "BLEND"
        alpha_cutout_uses_bc_a_product = _opacity_is_alpha_times_bc_a(ir.opacity)
    elif attenuation is not None:
        a = _coverage_alpha_sample_from_attenuation(attenuation)
        if a is not None:
            alpha_slot = _slot_from_sample(a.sample, role="alpha")
        alpha_mode = "OPAQUE"

    cap = make_clean_surface_capability(
        base_color_source=source,
        alpha_map=alpha_slot,
        normal_map=normal_slot,
        rmao_map=rmao_slot,
        alpha_mode=alpha_mode,
        alpha_threshold=alpha_threshold,
        evidence=(),
        normal_strength=normal_strength,
        alpha_cutout_uses_bc_a_product=alpha_cutout_uses_bc_a_product,
    )
    resolved = ResolvedMaterial(
        name=material_name,
        game_key="fh6",
        shader_name=ir.shader.shader_name,
        capability_kind=MaterialCapabilityKind.CLEAN_SURFACE,
        capability=cap,
    )
    return _build_resolved_material(resolved, resolver, image_cache)

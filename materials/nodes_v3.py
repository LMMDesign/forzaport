"""Minimal CleanSurfaceCapability -> Blender graph.

Only Base Color, external Alpha, Normal and RoughMetalAO are implemented.
Production construction accepts ``ResolvedMaterial`` only after the boundary;
``MaterialSpec`` is converted once via ``ensure_resolved_material``.
Graph version 401: UV transform uses ShaderNodeMapping (visible scale).
"""

from __future__ import annotations

import hashlib
import math
import os

import bpy

from ..parsing.disk_cache import dds_cache_dir
from ..parsing.material import image_name
from ..parsing.texture import Texture
from .graph_plan import (
    MATERIAL_GRAPH_VERSION,
    ensure_resolved_material,
    graph_build_plan,
)
from .model import CleanSurfaceCapability, MaterialCapabilityKind, ResolvedMaterial, ResolvedTextureSlot, BaseColorSourceKind
from .pipeline_v3 import MaterialSpec

# Re-export boundary helpers for callers / tests.
__all__ = (
    "MATERIAL_GRAPH_VERSION",
    "build_material",
    "ensure_resolved_material",
    "graph_build_plan",
)
_DDS_CACHE = dds_cache_dir()


def _bsdf_input(bsdf, name: str, index: int):
    return bsdf.inputs.get(name) or bsdf.inputs[index]


def _new_graph(material):
    material.use_nodes = True
    nt = material.node_tree
    nt.links.clear()
    nt.nodes.clear()
    output = nt.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Forza Output v3"
    output.location = (900, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "Forza Principled v3"
    bsdf.location = (520, 80)
    return nt, bsdf, output


def _material(resolved: ResolvedMaterial):
    material = bpy.data.materials.get(resolved.name)
    if material is None:
        material = bpy.data.materials.new(resolved.name)
    material["forza_graph_v"] = MATERIAL_GRAPH_VERSION
    material["forza_pipeline"] = "clean-v3"
    material["forza_shader"] = resolved.shader_name
    return material


def _dds_path(texture: Texture) -> str:
    guid = texture.guid or hashlib.sha256(texture.buffer).hexdigest()[:24]
    safe = "".join(
        c if c.isalnum() or c in "._-" else "_" for c in image_name(texture.path)
    )
    return os.path.join(_DDS_CACHE, f"{guid}_{safe}.dds")


def _load_image(slot: ResolvedTextureSlot, resolver, image_cache):
    """Load Blender image for a typed slot via ``Texture.from_path`` (resolver).

    Does not search directories or invent archive membership -- ``resolver`` must
    already map the GAME path. Cache key includes path identity + colour intent.
    """
    from .texture_source import resolve_texture_source

    src = resolve_texture_source(slot.path, resolver)
    if not src.exists:
        fail = src.failure.value if src.failure else "SOURCE_TEXTURE_NOT_FOUND"
        raise RuntimeError(f"{slot.param_name}: {fail}: {slot.path}")

    texture = Texture.from_path(slot.path, resolver)
    if texture is None:
        raise RuntimeError(
            f"{slot.param_name}: TEXTURE_READ_FAILED: {slot.path}"
        )
    non_color = slot.role != "base_color"
    # Identity includes canonical path / archive member so same basename cannot collide.
    key = (
        f"v3:{src.cache_identity(non_color=non_color)}:"
        f"{texture.guid or ''}"
    )
    cached = image_cache.get(key)
    if cached is not None:
        return cached

    os.makedirs(_DDS_CACHE, exist_ok=True)
    path = _dds_path(texture)
    if not os.path.isfile(path):
        with open(path, "wb") as f:
            f.write(texture.buffer)
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError as exc:
        if not texture.ensure_rgba_pixels():
            raise RuntimeError(
                f"BLENDER_IMAGE_CREATION_FAILED: {path}"
            ) from exc
        image = bpy.data.images.new(
            f"v3_{image_name(texture.path)}",
            texture.width,
            texture.height,
            alpha=True,
        )
        image.pixels.foreach_set(texture.rgba_pixels)
        image.source = "GENERATED"
    image.name = f"v3_{image_name(texture.path)}"
    image.alpha_mode = "CHANNEL_PACKED"
    if non_color:
        try:
            image.colorspace_settings.name = "Non-Color"
        except TypeError:
            image.colorspace_settings.is_data = True
    image_cache[key] = image
    return image


def _texture_node(nt, slot: ResolvedTextureSlot, resolver, image_cache, x: int, y: int):
    image = _load_image(slot, resolver, image_cache)
    tex = nt.nodes.new("ShaderNodeTexImage")
    proof = "PROVEN"
    if any(getattr(e, "kind", "") == "uv_unresolved" for e in (slot.evidence or ())):
        proof = "UNRESOLVED"
    tex.name = f"Forza {slot.role}: {slot.param_name}"
    tex.label = (
        f"{slot.param_name} [{slot.texcoord}] "
        f"scale=({slot.tiling[0]:g},{slot.tiling[1]:g})"
        + (
            f" rot={float(getattr(slot, 'rotation_degrees', 0.0) or 0.0):g}°"
            if float(getattr(slot, "rotation_degrees", 0.0) or 0.0)
            else ""
        )
        + f" [{proof}]"
    )
    tex.image = image
    tex.location = (x, y)

    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.name = f"Forza UV: {slot.texcoord} ({slot.role})"
    uv.uv_map = slot.texcoord
    uv.location = (x - 520, y)
    vector = uv.outputs[0]

    # UV Conformance Foundation: unambiguous Vector Math chain.
    # Order: Rotate → Scale (UV × (U,V)) → Offset. Do not use Mapping scale
    # (inverse-scale ambiguity). Invariant: UV×30 ⇒ 30 repetitions.
    tiling = tuple(float(c) for c in (slot.tiling or (1.0, 1.0))[:2])
    if len(tiling) < 2:
        tiling = (1.0, 1.0)
    rotation_degrees = float(getattr(slot, "rotation_degrees", 0.0) or 0.0)
    pan = tuple(float(c) for c in (getattr(slot, "pan", (0.0, 0.0)) or (0.0, 0.0))[:2])
    if len(pan) < 2:
        pan = (0.0, 0.0)
    needs_xform = (
        tiling != (1.0, 1.0)
        or rotation_degrees != 0.0
        or pan != (0.0, 0.0)
    )
    if needs_xform:
        if rotation_degrees != 0.0:
            rot = nt.nodes.new("ShaderNodeVectorRotate")
            rot.name = f"Forza UV Rotate: {slot.param_name}"
            rot.label = f"RotateUV {rotation_degrees:g}°"
            rot.rotation_type = "Z_AXIS"
            rot.inputs["Angle"].default_value = math.radians(rotation_degrees)
            rot.location = (x - 400, y)
            nt.links.new(vector, rot.inputs["Vector"])
            vector = rot.outputs["Vector"]
        if tiling != (1.0, 1.0):
            scale = nt.nodes.new("ShaderNodeVectorMath")
            scale.name = f"Forza UV Scale: {slot.param_name}"
            scale.label = f"ScaleUV ×({tiling[0]:g},{tiling[1]:g})"
            scale.operation = "MULTIPLY"
            scale.inputs[1].default_value = (tiling[0], tiling[1], 1.0)
            scale.location = (x - 280, y)
            nt.links.new(vector, scale.inputs[0])
            vector = scale.outputs["Vector"]
        if pan != (0.0, 0.0):
            offset = nt.nodes.new("ShaderNodeVectorMath")
            offset.name = f"Forza UV Pan: {slot.param_name}"
            offset.label = f"OffsetUV ({pan[0]:g},{pan[1]:g})"
            offset.operation = "ADD"
            offset.inputs[1].default_value = (pan[0], pan[1], 0.0)
            offset.location = (x - 160, y)
            nt.links.new(vector, offset.inputs[0])
            vector = offset.outputs["Vector"]
    nt.links.new(vector, tex.inputs["Vector"])

    modes = set((slot.address or {}).values())
    if "MIRROR" in modes:
        tex.extension = "MIRROR"
    elif "EXTEND" in modes or "CLIP" in modes:
        tex.extension = "EXTEND"
    else:
        # Sampler evidence: default REPEAT only when address absent or REPEAT.
        tex.extension = "REPEAT"

    # Dev-only: replace active Base Color sample with a UV checker on the same
    # UV expression (FORZA_DEV_UV_CHECKER=1). Does not alter IR evaluation.
    if (
        slot.role == "base_color"
        and os.environ.get("FORZA_DEV_UV_CHECKER", "").strip().lower()
        in ("1", "true", "yes")
    ):
        checker = nt.nodes.new("ShaderNodeTexChecker")
        checker.name = f"Forza DEV UV Checker: {slot.param_name}"
        checker.label = f"DEV UV Checker [{slot.texcoord}]"
        checker.inputs["Scale"].default_value = 8.0
        checker.location = (x, y + 160)
        nt.links.new(vector, checker.inputs["Vector"])
        tex["forza_dev_uv_checker"] = checker.name
        return checker

    return tex


def _separate(nt, tex, label: str, x: int, y: int):
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.name = label
    sep.label = label
    sep.location = (x, y)
    nt.links.new(tex.outputs["Color"], sep.inputs["Color"])
    return sep


def _constant_color(nt, rgba):
    rgb = nt.nodes.new("ShaderNodeRGB")
    rgb.name = "Forza Base Color Constant"
    rgb.outputs[0].default_value = rgba
    rgb.location = (-180, 260)
    return rgb.outputs[0]


def _configure_transparency(material, mode: str, threshold: float):
    """Blender 5.1 EEVEE Next: Transparent MixShader needs BLENDED.

    CLIP enum does not stick (becomes HASHED). Hard cutout is done in the
    graph with GREATER_THAN; the material must still render as BLENDED so
    discarded texels are actually transparent in the viewport.
    """
    material.use_backface_culling = False
    try:
        material.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        material.blend_method = "BLEND" if mode == "BLEND" else "CLIP"
    material.alpha_threshold = 0.0 if mode == "BLEND" else threshold


def _configure_opaque(material):
    """Force opaque EEVEE surface — no clip / blend / hashed transparency."""
    material.use_backface_culling = False
    try:
        material.surface_render_method = "DITHERED"
    except (AttributeError, TypeError):
        pass
    try:
        material.blend_method = "OPAQUE"
    except (AttributeError, TypeError):
        pass
    try:
        material.alpha_threshold = 0.0
    except (AttributeError, TypeError):
        pass


def _weave_composite_color(nt, weave, resolver, image_cache):
    """rgb = A + mask.R * (B - A) — proven car_carbonfiber blend."""
    mask_tex = _texture_node(nt, weave.mask, resolver, image_cache, -520, 300)
    sep = _separate(nt, mask_tex, "WeaveMask: R", -240, 300)
    mix = nt.nodes.new("ShaderNodeMixRGB")
    mix.name = "Forza WeaveColorTint lerp"
    mix.blend_type = "MIX"
    mix.location = (-40, 280)
    mix.inputs[1].default_value = weave.tint_a
    mix.inputs[2].default_value = weave.tint_b
    nt.links.new(sep.outputs["Red"], mix.inputs[0])
    return mix.outputs[0]


def _build_resolved_material(resolved: ResolvedMaterial, resolver, image_cache):
    """Single production graph path — typed CleanSurfaceCapability only."""
    if resolved.capability_kind is not MaterialCapabilityKind.CLEAN_SURFACE:
        raise RuntimeError(
            f"unsupported capability for node graph: {resolved.capability_kind}"
        )
    cap: CleanSurfaceCapability = resolved.capability
    material = _material(resolved)
    nt, bsdf, output = _new_graph(material)

    source = cap.base_color_source
    tex_color = None
    if source.kind is BaseColorSourceKind.TEXTURE:
        assert source.texture is not None
        base_tex = _texture_node(
            nt, source.texture, resolver, image_cache, -520, 300
        )
        tex_color = base_tex.outputs["Color"]
        base = tex_color
        if source.multiply_tint is not None:
            tint_rgb = nt.nodes.new("ShaderNodeRGB")
            tint_rgb.name = "Forza BaseColor_Tint"
            tint_rgb.label = "BaseColor_Tint×Multiplier"
            tint_rgb.outputs[0].default_value = source.multiply_tint
            tint_rgb.location = (-180, 420)
            tint_mul = nt.nodes.new("ShaderNodeMixRGB")
            tint_mul.name = "Forza Base Color × Tint"
            tint_mul.label = "tex × BaseColor_Tint"
            tint_mul.blend_type = "MULTIPLY"
            tint_mul.inputs[0].default_value = 1.0
            tint_mul.location = (-40, 340)
            nt.links.new(tex_color, tint_mul.inputs[1])
            nt.links.new(tint_rgb.outputs[0], tint_mul.inputs[2])
            base = tint_mul.outputs[0]
    elif source.kind is BaseColorSourceKind.WEAVE_COMPOSITE:
        assert source.weave is not None
        base = _weave_composite_color(nt, source.weave, resolver, image_cache)
    elif source.kind in (
        BaseColorSourceKind.MATERIAL_CONSTANT,
        BaseColorSourceKind.INSTANCE_PAINT,
    ):
        assert source.color is not None
        base = _constant_color(nt, source.color)
    else:
        raise RuntimeError(
            f"node builder refused BaseColorSourceKind.{source.kind.name}"
        )

    if cap.rmao_map is not None:
        rmao = _texture_node(nt, cap.rmao_map, resolver, image_cache, -520, -260)
        channels = _separate(nt, rmao, "RoughMetalAO: R/G/B", -240, -260)
        # TintMode metal lerp (car_standard) — after tint multiply, before AO.
        blend = getattr(source, "tint_metal_blend", None)
        if (
            source.kind is BaseColorSourceKind.TEXTURE
            and blend is not None
            and tex_color is not None
            and source.multiply_tint is not None
        ):
            tint_lerp = nt.nodes.new("ShaderNodeMixRGB")
            tint_lerp.name = "Forza TintMode metal lerp"
            tint_lerp.label = blend
            tint_lerp.blend_type = "MIX"
            tint_lerp.location = (40, 280)
            nt.links.new(channels.outputs["Green"], tint_lerp.inputs[0])
            if blend == "lerp_tinted_tex_metal":
                nt.links.new(base, tint_lerp.inputs[1])
                nt.links.new(tex_color, tint_lerp.inputs[2])
            else:
                nt.links.new(tex_color, tint_lerp.inputs[1])
                nt.links.new(base, tint_lerp.inputs[2])
            base = tint_lerp.outputs[0]
        multiply = nt.nodes.new("ShaderNodeMixRGB")
        multiply.name = "Forza Base Color × AO"
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        multiply.location = (60, 220)
        nt.links.new(base, multiply.inputs[1])
        nt.links.new(channels.outputs["Blue"], multiply.inputs[2])
        base = multiply.outputs[0]
        nt.links.new(channels.outputs["Red"], _bsdf_input(bsdf, "Roughness", 2))
        nt.links.new(channels.outputs["Green"], _bsdf_input(bsdf, "Metallic", 1))

    # Opaque coverage: Alpha.r * BaseColorAlpha.a → Base Color (not Principled Alpha).
    # Prefer Vector Math MULTIPLY for UV scale semantics (UV × N = N repetitions).
    if (
        cap.alpha_map is not None
        and cap.alpha_mode == "OPAQUE"
        and getattr(source, "multiply_coverage", False)
        and source.kind is BaseColorSourceKind.TEXTURE
        and tex_color is not None
    ):
        assert source.texture is not None
        alpha_tex = _texture_node(
            nt, cap.alpha_map, resolver, image_cache, -520, -920
        )
        alpha_channels = _separate(
            nt, alpha_tex, "ShadingAttenuation Alpha: R", -240, -900
        )
        base_img_nodes = [
            n
            for n in nt.nodes
            if n.bl_idname == "ShaderNodeTexImage"
            and abs(n.location.x + 520) < 1
            and abs(n.location.y - 300) < 1
        ]
        if base_img_nodes:
            bc_alpha = base_img_nodes[0].outputs.get("Alpha")
        else:
            bc_node = _texture_node(
                nt, source.texture, resolver, image_cache, -700, 120
            )
            bc_alpha = bc_node.outputs["Alpha"]
        math_mul = nt.nodes.new("ShaderNodeMath")
        math_mul.name = "Forza ShadingAttenuation Product"
        math_mul.label = "saturate(Alpha.r × BC.a)"
        math_mul.operation = "MULTIPLY"
        math_mul.use_clamp = True  # DXIL saturate(%2020)
        math_mul.location = (-200, -40)
        nt.links.new(alpha_channels.outputs["Red"], math_mul.inputs[0])
        nt.links.new(bc_alpha, math_mul.inputs[1])
        apply = nt.nodes.new("ShaderNodeMixRGB")
        apply.name = "Forza Base Color × ShadingAttenuation"
        apply.label = (
            "Blender backend approximation — not exact Forza BRDF "
            "equivalence (BaseColor × attenuation)"
        )
        apply.blend_type = "MULTIPLY"
        apply.inputs[0].default_value = 1.0
        apply.location = (200, 120)
        # MixRGB Fac=1, Color1=base, Color2=coverage as grey via RGB from scalar:
        # use MixRGB multiply with Color2 all channels = product via a second Math→
        # feed product into Color2 by combining — Blender MixRGB Color2 is color.
        # Fac multiply: Color1 * Color2, so set Color2 = (c,c,c).
        rgb = nt.nodes.new("ShaderNodeCombineColor")
        rgb.name = "Forza ShadingAttenuation as RGB"
        rgb.location = (-40, -120)
        nt.links.new(math_mul.outputs[0], rgb.inputs[0])
        nt.links.new(math_mul.outputs[0], rgb.inputs[1])
        nt.links.new(math_mul.outputs[0], rgb.inputs[2])
        nt.links.new(base, apply.inputs[1])
        nt.links.new(rgb.outputs[0], apply.inputs[2])
        base = apply.outputs[0]

    nt.links.new(base, _bsdf_input(bsdf, "Base Color", 0))

    if cap.normal_map is not None:
        normal_tex = _texture_node(
            nt, cap.normal_map, resolver, image_cache, -520, -620
        )
        normal = nt.nodes.new("ShaderNodeNormalMap")
        normal.name = "Forza Normal"
        normal.uv_map = cap.normal_map.texcoord
        normal.location = (120, -520)
        strength = float(getattr(cap, "normal_strength", 1.0) or 1.0)
        if strength != 1.0:
            normal.inputs["Strength"].default_value = strength
        nt.links.new(normal_tex.outputs["Color"], normal.inputs["Color"])
        nt.links.new(normal.outputs["Normal"], _bsdf_input(bsdf, "Normal", 5))

    surface = bsdf.outputs[0]
    if cap.alpha_map is not None and cap.alpha_mode != "OPAQUE":
        alpha_tex = _texture_node(
            nt, cap.alpha_map, resolver, image_cache, -520, -920
        )
        alpha_channels = _separate(
            nt, alpha_tex, "External Alpha: R", -240, -900
        )
        opacity = alpha_channels.outputs["Red"]
        # DXIL (exact SHA): effective = Alpha.r * BaseColorAlpha.a when
        # AlphaTransparency=true (game-file alpha contract). Not inferred from
        # texture payload or binding display names.
        if (
            getattr(cap, "alpha_cutout_uses_bc_a_product", False)
            and source.kind is BaseColorSourceKind.TEXTURE
            and source.texture is not None
        ):
            base_img_nodes = [
                n
                for n in nt.nodes
                if n.bl_idname == "ShaderNodeTexImage"
                and abs(n.location.x + 520) < 1
                and abs(n.location.y - 300) < 1
            ]
            if base_img_nodes:
                bc_alpha = base_img_nodes[0].outputs.get("Alpha")
            else:
                bc_node = _texture_node(
                    nt, source.texture, resolver, image_cache, -700, 120
                )
                bc_alpha = bc_node.outputs["Alpha"]
            prod = nt.nodes.new("ShaderNodeMath")
            prod.name = "Forza Cutout Alpha.r × BC.a"
            prod.label = "saturate(Alpha.r × BC.a) cutout"
            prod.operation = "MULTIPLY"
            prod.use_clamp = True
            prod.location = (-40, -760)
            nt.links.new(opacity, prod.inputs[0])
            nt.links.new(bc_alpha, prod.inputs[1])
            opacity = prod.outputs[0]
        if cap.alpha_mode == "CLIP":
            clip = nt.nodes.new("ShaderNodeMath")
            clip.name = "Forza Alpha Test"
            clip.operation = "GREATER_THAN"
            clip.inputs[1].default_value = cap.alpha_threshold
            clip.location = (40, -760)
            nt.links.new(opacity, clip.inputs[0])
            opacity = clip.outputs[0]
        nt.links.new(opacity, _bsdf_input(bsdf, "Alpha", 4))

        transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
        transparent.name = "Forza Transparent"
        transparent.location = (500, -180)
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.name = "Forza Surface Alpha"
        mix.location = (700, 0)
        nt.links.new(opacity, mix.inputs[0])
        nt.links.new(transparent.outputs[0], mix.inputs[1])
        nt.links.new(bsdf.outputs[0], mix.inputs[2])
        surface = mix.outputs[0]
        _configure_transparency(material, cap.alpha_mode, cap.alpha_threshold)
    else:
        # AlphaTransparency=false shading attenuation (and plain opaque) must
        # not inherit Blender's default HASHED/DITHERED transparency mode.
        _configure_opaque(material)

    nt.links.new(surface, output.inputs["Surface"])
    return material


_IR_CONTRACT_SHADERS: dict[str, str] = {
    "car_standard": "eval_car_standard",
    "car_carbonfiber": "eval_car_carbonfiber",
}


def _build_via_ir_contract(
    resolved: ResolvedMaterial,
    resolver,
    image_cache,
    *,
    source_material,
    media_root: str,
    module_name: str,
):
    import importlib

    from .ir_compiler import compile_forza_material_ir
    from .shader_bindings import extract_bindings

    mod = importlib.import_module(f".{module_name}", __package__)
    shaderbin_sha256 = getattr(mod, f"{resolved.shader_name.upper()}_SHADERBIN_SHA256")
    is_contract_identity = getattr(
        mod, f"is_{resolved.shader_name}_contract_identity"
    )
    evaluate = getattr(mod, f"evaluate_{resolved.shader_name}")

    if source_material is None or not media_root:
        raise RuntimeError(
            f"{resolved.shader_name} contract selected: require source_material + "
            f"media_root (compatibility fallback must not compile {resolved.shader_name})"
        )
    params = getattr(source_material, "parameters", None) or {}
    cbmp = getattr(source_material, "cbmp", None) or {}
    try:
        bindings = extract_bindings(
            media_root=media_root,
            shader_name=resolved.shader_name,
            params=params,
            cbmp=cbmp,
            game_key="fh6",
        )
        sha = (bindings.source_hashes or {}).get("shaderbin_sha256")
    except Exception as exc:
        raise RuntimeError(
            f"{resolved.shader_name} contract: failed to resolve shaderbin identity: {exc}"
        ) from exc
    if not is_contract_identity(resolved.shader_name, sha):
        raise RuntimeError(
            f"{resolved.shader_name} shaderbin sha mismatch: got {sha!r}, "
            f"want {shaderbin_sha256}"
        )
    eval_kwargs = {
        "name": resolved.name,
        "material": source_material,
        "resolver": resolver,
        "media_root": media_root,
        "production_mode": True,
    }
    if resolved.shader_name == "car_standard":
        eval_kwargs["revision"] = os.environ.get(
            "FORZA_CAR_STANDARD_REVISION", "b1.75"
        )
    ir = evaluate(**eval_kwargs)
    if ir.rejection_reasons:
        raise RuntimeError("; ".join(ir.rejection_reasons))
    mat = compile_forza_material_ir(
        ir,
        (resolver, image_cache),
        material_name=resolved.name,
    )
    mat["forza_pipeline"] = "forza-ir-v1"
    mat["forza_shaderbin_sha256"] = shaderbin_sha256
    return mat


def build_material(
    material: ResolvedMaterial | MaterialSpec,
    resolver,
    image_cache,
    *,
    source_material=None,
    media_root: str | None = None,
):
    """Build Blender material.

    ``car_standard`` and ``car_carbonfiber`` (exact contract identity) compile
    from ForzaMaterialIR only. Other families keep the capability → nodes path
    (compatibility fallback); those two exact SHAs must never fall through it.
    """
    resolved = ensure_resolved_material(material)
    module_name = _IR_CONTRACT_SHADERS.get(resolved.shader_name)
    if module_name is not None:
        return _build_via_ir_contract(
            resolved,
            resolver,
            image_cache,
            source_material=source_material,
            media_root=media_root,
            module_name=module_name,
        )
    return _build_resolved_material(resolved, resolver, image_cache)

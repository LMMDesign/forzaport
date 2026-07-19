"""Minimal MaterialSpec v3 -> Blender graph.

Only Base Color, external Alpha, Normal and RoughMetalAO are implemented.
Each connection is direct and labelled; there are no legacy graph branches.
"""

from __future__ import annotations

import hashlib
import os

import bpy

from ..parsing.disk_cache import dds_cache_dir
from ..parsing.material import image_name
from ..parsing.texture import Texture
from .pipeline_v3 import MaterialSpec, TextureSlot

MATERIAL_GRAPH_VERSION = 400
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


def _material(spec: MaterialSpec):
    material = bpy.data.materials.get(spec.name)
    if material is None:
        material = bpy.data.materials.new(spec.name)
    material["forza_graph_v"] = MATERIAL_GRAPH_VERSION
    material["forza_pipeline"] = "clean-v3"
    material["forza_shader"] = spec.shader_name
    return material


def _dds_path(texture: Texture) -> str:
    guid = texture.guid or hashlib.sha256(texture.buffer).hexdigest()[:24]
    safe = "".join(
        c if c.isalnum() or c in "._-" else "_" for c in image_name(texture.path)
    )
    return os.path.join(_DDS_CACHE, f"{guid}_{safe}.dds")


def _load_image(slot: TextureSlot, resolver, image_cache):
    texture = Texture.from_path(slot.path, resolver)
    if texture is None:
        raise RuntimeError(f"{slot.param_name}: texture not resolved: {slot.path}")
    non_color = slot.role != "base_color"
    key = f"v3:{texture.guid}:{non_color}"
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
            raise RuntimeError(f"Blender could not load DDS {path}") from exc
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


def _texture_node(nt, slot: TextureSlot, resolver, image_cache, x: int, y: int):
    image = _load_image(slot, resolver, image_cache)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.name = f"Forza {slot.role}: {slot.param_name}"
    tex.label = f"{slot.param_name} [{slot.texcoord}]"
    tex.image = image
    tex.location = (x, y)

    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.name = f"Forza UV: {slot.texcoord} ({slot.role})"
    uv.uv_map = slot.texcoord
    uv.location = (x - 420, y)
    vector = uv.outputs[0]

    if slot.tiling != (1.0, 1.0):
        scale = nt.nodes.new("ShaderNodeVectorMath")
        scale.name = f"Forza Tiling: {slot.param_name}"
        scale.operation = "MULTIPLY"
        scale.inputs[1].default_value = (slot.tiling[0], slot.tiling[1], 1.0)
        scale.location = (x - 210, y)
        nt.links.new(vector, scale.inputs[0])
        vector = scale.outputs[0]
    nt.links.new(vector, tex.inputs["Vector"])

    modes = set((slot.address or {}).values())
    if "MIRROR" in modes:
        tex.extension = "MIRROR"
    elif "EXTEND" in modes or "CLIP" in modes:
        tex.extension = "EXTEND"
    else:
        tex.extension = "REPEAT"
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


def build_material(spec: MaterialSpec, resolver, image_cache):
    if not spec.valid:
        raise RuntimeError(spec.error or "invalid clean material spec")
    material = _material(spec)
    nt, bsdf, output = _new_graph(material)

    if spec.base_color_map is not None:
        base_tex = _texture_node(
            nt, spec.base_color_map, resolver, image_cache, -520, 300
        )
        base = base_tex.outputs["Color"]
    else:
        base = _constant_color(nt, spec.base_color)

    if spec.rmao_map is not None:
        rmao = _texture_node(nt, spec.rmao_map, resolver, image_cache, -520, -260)
        channels = _separate(nt, rmao, "RoughMetalAO: R/G/B", -240, -260)
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
    nt.links.new(base, _bsdf_input(bsdf, "Base Color", 0))

    if spec.normal_map is not None:
        normal_tex = _texture_node(
            nt, spec.normal_map, resolver, image_cache, -520, -620
        )
        normal = nt.nodes.new("ShaderNodeNormalMap")
        normal.name = "Forza Normal"
        normal.uv_map = spec.normal_map.texcoord
        normal.location = (120, -520)
        nt.links.new(normal_tex.outputs["Color"], normal.inputs["Color"])
        nt.links.new(normal.outputs["Normal"], _bsdf_input(bsdf, "Normal", 5))

    surface = bsdf.outputs[0]
    if spec.alpha_map is not None:
        alpha_tex = _texture_node(
            nt, spec.alpha_map, resolver, image_cache, -520, -920
        )
        alpha_channels = _separate(
            nt, alpha_tex, "External Alpha: R", -240, -900
        )
        opacity = alpha_channels.outputs["Red"]
        if spec.alpha_mode == "CLIP":
            clip = nt.nodes.new("ShaderNodeMath")
            clip.name = "Forza Alpha Test"
            clip.operation = "GREATER_THAN"
            clip.inputs[1].default_value = spec.alpha_threshold
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
        _configure_transparency(material, spec.alpha_mode, spec.alpha_threshold)

    nt.links.new(surface, output.inputs["Surface"])
    return material


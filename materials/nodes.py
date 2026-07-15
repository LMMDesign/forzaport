"""MaterialSpec -> Blender node tree (the only bpy module in materials/).

Reproduces the proven generic node graph (base-color x AO multiply into Base Color, gloss
smoothness inverted into Roughness, Normal Map node, per-slot UVMap + tiling), and adds the
data-driven extras the descriptor unlocked: exact per-texture UV channel, 2-channel normal
Z-reconstruction, and image extension (wrap/clamp) from the sampler address mode.
"""

import hashlib
import os

import bpy

MATERIAL_GRAPH_VERSION = 13

from ..parsing.disk_cache import dds_cache_dir
from ..parsing.texture import Texture
from ..parsing.material import image_name

_DDS_CACHE = dds_cache_dir()

_BSDF = "Principled BSDF"


def _bsdf_input(node, name, index):
    """Named Principled input with positional fallback (robust across Blender versions)."""
    if name in node.inputs:
        return node.inputs[name]
    return node.inputs[index]


def _bsdf_alpha(bsdf):
    return _bsdf_input(bsdf, "Alpha", 4)


def _link_bsdf_alpha(nt, bsdf, from_socket):
    if from_socket is None:
        return False
    alpha_in = _bsdf_alpha(bsdf)
    if alpha_in is None:
        return False
    for link in list(alpha_in.links):
        nt.links.remove(link)
    nt.links.new(from_socket, alpha_in)
    return True


def _find_normal_tex_image(nt):
    """Image Texture feeding the Normal Map (walks through Combine Color for 2ch normals)."""
    nmap = next((n for n in nt.nodes if n.type == "NORMAL_MAP"), None)
    if nmap is None:
        return None
    color_in = nmap.inputs.get("Color") if nmap.inputs else None
    if color_in is None and len(nmap.inputs) > 1:
        color_in = nmap.inputs[1]
    if color_in is None or not color_in.is_linked:
        return None
    node = color_in.links[0].from_node
    seen = set()
    while node is not None and node not in seen:
        if node.type == "TEX_IMAGE":
            return node
        seen.add(node)
        upstream = None
        for sock in node.inputs:
            if sock.is_linked:
                upstream = sock.links[0].from_node
                break
        node = upstream
    return None


def _image_alpha_output(image_node):
    if image_node is None:
        return None
    out = image_node.outputs.get("Alpha")
    if out is not None:
        return out
    return image_node.outputs[1] if len(image_node.outputs) > 1 else None


def _wire_normal_alpha(nt, bsdf, spec, slots, alpha_linked):
    """Connect normal-map image Alpha -> Principled Alpha (ext_grille / xyw packing)."""
    if alpha_linked or slots.get("alpha"):
        return alpha_linked
    normal = slots.get("normal")
    should = (
        getattr(normal, "packs_opacity", False)
        or getattr(spec, "opacity_from_normal", False)
    )
    if not should:
        return alpha_linked
    img = _find_normal_tex_image(nt)
    if img is None:
        return alpha_linked
    if _link_bsdf_alpha(nt, bsdf, _image_alpha_output(img)):
        return True
    return alpha_linked


def _packed_opacity_socket(nt, image_node, components):
    """Opacity from mined PS comps xyw — image Alpha output, else Separate Color W."""
    if image_node is None:
        return None
    alpha_out = image_node.outputs.get("Alpha")
    if alpha_out is not None and getattr(alpha_out, "is_available", True):
        return alpha_out
    comps_s = (components or "").lower()
    if "w" in comps_s:
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(image_node.outputs[0], sep.inputs[0])
        return sep.outputs[3]
    return image_node.outputs[1] if len(image_node.outputs) > 1 else None


def _channel_socket(nt, image_node, components, scalar=False):
    """Sample the mined PS channel (x/y/z/w) from an image texture node.

    scalar=True always returns one float channel (gloss/roughness/alpha). Full RGB is
    only used for diffuse/normal color paths."""
    if image_node is None:
        return None
    comps_s = (components or "xyzw").lower()
    ch = comps_s[0] if comps_s else "x"
    if ch in ("x", "y", "z", "w"):
        idx = {"x": 0, "y": 1, "z": 2, "w": 3}[ch]
        if not scalar and idx == 0 and comps_s in ("", "xy", "xyz", "xyzw"):
            return image_node.outputs[0]
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(image_node.outputs[0], sep.inputs[0])
        return sep.outputs[idx]
    return image_node.outputs[0]


def _scalar_to_color(nt, scalar_socket):
    """Replicate a single scalar into an RGB color (BC4 / single-channel masks)."""
    comb = nt.nodes.new("ShaderNodeCombineColor")
    for i in range(3):
        nt.links.new(scalar_socket, comb.inputs[i])
    return comb.outputs[0]


def _spec_is_tire(spec):
    if getattr(spec, "is_tire", False):
        return True
    n = (spec.name or "").lower()
    return "scaling_text" in n or "sidewall" in n or "tiretread" in n


def _rubber_rgba(spec):
    from .builder import TIRE_RUBBER_RGBA

    c = spec.base_color
    if c and (c[0] + c[1] + c[2]) < 2.95:
        return (c[0], c[1], c[2], 1.0 if c[3] == 0 else c[3])
    return TIRE_RUBBER_RGBA


def _uv_chain_tire_mask(nt, image_node, texcoord, tiling, v_offset):
    """TEXCOORD2 mask sample with game scaling_text V' = V - rubber_scalar."""
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = texcoord or "TEXCOORD2"
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    comb = nt.nodes.new("ShaderNodeCombineXYZ")
    nt.links.new(uv.outputs[0], sep.inputs[0])
    nt.links.new(sep.outputs[0], comb.inputs[0])
    if abs(v_offset) > 1e-6:
        vsub = nt.nodes.new("ShaderNodeMath")
        vsub.operation = "SUBTRACT"
        nt.links.new(sep.outputs[1], vsub.inputs[0])
        vsub.inputs[1].default_value = v_offset
        nt.links.new(vsub.outputs[0], comb.inputs[1])
    else:
        nt.links.new(sep.outputs[1], comb.inputs[1])
    chain_out = comb.outputs[0]
    if tiling and (abs(tiling[0] - 1.0) > 1e-6 or abs(tiling[1] - 1.0) > 1e-6):
        mul = nt.nodes.new("ShaderNodeVectorMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = (tiling[0], tiling[1], 1.0)
        nt.links.new(chain_out, mul.inputs[0])
        chain_out = mul.outputs[0]
    nt.links.new(chain_out, image_node.inputs[0])


def _mix_rgb_by_scalar(nt, factor_sock, color_a, color_b):
    """Linear blend between two RGBA tuples using a 0..1 factor socket."""
    a = nt.nodes.new("ShaderNodeRGB")
    a.outputs[0].default_value = color_a
    b = nt.nodes.new("ShaderNodeRGB")
    b.outputs[0].default_value = color_b
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.label = "Tread"
    fac = mix.inputs.get("Factor") or mix.inputs[0]
    a_in = mix.inputs.get("A") or mix.inputs[6]
    b_in = mix.inputs.get("B") or mix.inputs[7]
    out = mix.outputs.get("Result") or mix.outputs[2]
    nt.links.new(factor_sock, fac)
    nt.links.new(a.outputs[0], a_in)
    nt.links.new(b.outputs[0], b_in)
    return out


def _wire_tire_albedo(nt, bsdf, spec, slots, load, image_cache):
    """Mask-driven tread albedo — dark groove/crest (max ~0.18 linear), standard TEXCOORD2 UVs."""
    # Fixed dark rubber range; do not use full rubber (~0.91 / #F5F6F6 in the hex picker).
    groove = (0.035, 0.035, 0.036, 1.0)
    crest = (0.18, 0.18, 0.185, 1.0)

    diff = slots.get("diffuse")
    if _is_rtint_slot(diff):
        diff = None
    tex = load(diff) if diff else None
    diff_node = None
    if tex is not None:
        img = _get_or_create_image(tex, non_color=True, cache=image_cache)
        node = _image_node(nt, img, diff.address, tiling=diff.tiling)
        v_offset = getattr(spec, "tire_rubber_v", None)
        if v_offset is None:
            v_offset = _rubber_rgba(spec)[1]
        _uv_chain_tire_mask(
            nt, node, diff.texcoord or "TEXCOORD2", diff.tiling, v_offset
        )
        mask = _channel_socket(nt, node, diff.components or "x", scalar=True)
        base_out = _mix_rgb_by_scalar(nt, mask, groove, crest)
        diff_node = node
    else:
        rgb = nt.nodes.new("ShaderNodeRGB")
        rgb.label = "Tread fallback"
        rgb.outputs[0].default_value = groove
        base_out = rgb.outputs[0]

    nt.links.new(base_out, _bsdf_input(bsdf, "Base Color", 0))
    bc_in = _bsdf_input(bsdf, "Base Color", 0)
    bc_in.default_value = (groove[0], groove[1], groove[2], 1.0)
    return diff, diff_node


def _dds_cache_path(texture):
    name = image_name(texture.path)
    guid = getattr(texture, "guid", None)
    if not guid:
        guid = hashlib.md5(texture.buffer).hexdigest()[:16]
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return os.path.join(_DDS_CACHE, f"{guid}_{safe}.dds")


def _ensure_principled_output(nt):
    """Principled + output after clearing a material node tree."""
    bsdf = nt.nodes.get(_BSDF)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.name = _BSDF
        bsdf.location = (300, 300)
    out = nt.nodes.get("Material Output")
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (600, 300)
    surf = out.inputs.get("Surface") or out.inputs[0]
    if not surf.is_linked:
        nt.links.new(bsdf.outputs[0], surf)
    return bsdf


def _material_for_spec(spec):
    """Reuse the material datablock so re-import updates every mesh using it."""
    mat = bpy.data.materials.get(spec.name)
    if mat is None:
        mat = bpy.data.materials.new(spec.name)
        mat.use_nodes = True
    else:
        mat.use_nodes = True
        nt = mat.node_tree
        for link in list(nt.links):
            nt.links.remove(link)
        for node in list(nt.nodes):
            nt.nodes.remove(node)
    mat["forza_graph_v"] = MATERIAL_GRAPH_VERSION
    return mat


def _finalize_image(img, name, non_color):
    img.name = name
    img.alpha_mode = "CHANNEL_PACKED"
    if non_color:
        try:
            img.colorspace_settings.is_data = True
        except (TypeError, AttributeError):
            img.colorspace_settings.name = "Non-Color"
    return img


def _new_generated_image(name, w, h, px, non_color):
    """Create a GENERATED image with colorspace set before pixels (Blender 5.1 safe)."""
    img = bpy.data.images.new(name, w, h, alpha=True)
    if non_color:
        try:
            img.colorspace_settings.is_data = True
        except (TypeError, AttributeError):
            pass
    img.source = "GENERATED"
    img.pixels.foreach_set(px)
    return img


def _try_load_dds_file(texture, name, non_color):
    """Load swatch DDS as a FILE image when Blender can decode it (reliable in viewport)."""
    os.makedirs(_DDS_CACHE, exist_ok=True)
    path = _dds_cache_path(texture)
    if not os.path.isfile(path):
        with open(path, "wb") as f:
            f.write(texture.buffer)
    try:
        img = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return None
    if img.size[0] < 1 or img.size[1] < 1:
        if img.users <= 1:
            bpy.data.images.remove(img)
        return None
    img.filepath = path
    img.source = "FILE"
    return _finalize_image(img, name, non_color)


def _load_via_dds_fallback(texture, name, non_color):
    """Last resort: keep DDS on disk as FILE even when decode looks empty."""
    img = _try_load_dds_file(texture, name, non_color)
    if img is not None:
        return img
    os.makedirs(_DDS_CACHE, exist_ok=True)
    path = _dds_cache_path(texture)
    img = bpy.data.images.load(path, check_existing=True)
    img.filepath = path
    img.source = "FILE"
    return _finalize_image(img, name, non_color)


def _get_or_create_image(texture, non_color, cache):
    name = image_name(texture.path)
    cache_key = f"{texture.guid}:{name}"
    img = cache.get(cache_key)
    if img is not None:
        return img
    w = max(1, int(getattr(texture, "width", 1) or 1))
    h = max(1, int(getattr(texture, "height", 1) or 1))
    try:
        # FILE DDS first — copying decoded DDS into GENERATED breaks viewport GPU uploads.
        img = _try_load_dds_file(texture, name, non_color)
        if img is None and getattr(texture, "has_decoded_pixels", lambda: False)():
            npx = w * h * 4
            px = list(texture.rgba_pixels[:npx])
            if len(px) < npx:
                px.extend([0.0] * (npx - len(px)))
            img = _new_generated_image(name, w, h, px, non_color)
        elif img is None:
            img = _load_via_dds_fallback(texture, name, non_color)
    except Exception as e:
        print(f"  texture '{name}': load failed ({e!r}), using placeholder")
        img = bpy.data.images.new(name, w, h)
        img.source = "GENERATED"
        gray = 0.5
        img.pixels.foreach_set([gray, gray, gray, 1.0] * (w * h))
    img = _finalize_image(img, name, non_color)
    cache[cache_key] = img
    return img


_EXT_MAP = {"REPEAT": "REPEAT", "EXTEND": "EXTEND", "CLIP": "CLIP", "MIRROR": "MIRROR"}


def _image_node(nt, image, address, extension_default="REPEAT", tiling=None):
    n = nt.nodes.new("ShaderNodeTexImage")
    n.image = image
    ext = extension_default
    if tiling and (abs(tiling[0] - 1.0) > 1e-6 or abs(tiling[1] - 1.0) > 1e-6):
        # PS-side UV scale expects WRAP/REPEAT; CLIP/CLAMP samplers would freeze at the edge
        ext = "REPEAT"
    elif address:
        # Blender's image node has a single extension for both axes; prefer a clamp/mirror if
        # either axis uses one (badges/letters), else repeat.
        for axis in ("U", "V"):
            m = _EXT_MAP.get(address.get(axis))
            if m and m != "REPEAT":
                ext = m
                break
    try:
        n.extension = ext
    except (TypeError, AttributeError):
        pass
    return n


def _uv_flip_v(spec):
    """Wheel badge/emblem decals need a V flip in Blender vs game TEXCOORD0."""
    n = (spec.name or "").lower()
    return "wheel_badge" in n or "wheel_emblem" in n


def _uv_chain(nt, image_node, texcoord, tiling, flip_v=False):
    """UVMap -> (optional V flip) -> (tiling multiply) -> image node vector input."""
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = texcoord or "TEXCOORD0"
    chain_out = uv.outputs[0]
    if flip_v:
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        comb = nt.nodes.new("ShaderNodeCombineXYZ")
        vinv = nt.nodes.new("ShaderNodeMath")
        vinv.operation = "SUBTRACT"
        vinv.inputs[0].default_value = 1.0
        nt.links.new(chain_out, sep.inputs[0])
        nt.links.new(sep.outputs[0], comb.inputs[0])
        nt.links.new(sep.outputs[1], vinv.inputs[1])
        nt.links.new(vinv.outputs[0], comb.inputs[1])
        nt.links.new(sep.outputs[2], comb.inputs[2])
        chain_out = comb.outputs[0]
    if tiling and (abs(tiling[0] - 1.0) > 1e-6 or abs(tiling[1] - 1.0) > 1e-6):
        mul = nt.nodes.new("ShaderNodeVectorMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = (tiling[0], tiling[1], 1.0)
        nt.links.new(chain_out, mul.inputs[0])
        chain_out = mul.outputs[0]
    nt.links.new(chain_out, image_node.inputs[0])


def _reconstruct_normal_z(nt, color_socket):
    """Rebuild B from a 2-channel (BC5) normal: nz = sqrt(1 - nx^2 - ny^2), feed RGB on."""
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(color_socket, sep.inputs[0])

    def remap(ch):  # c*2 - 1
        m = nt.nodes.new("ShaderNodeMath")
        m.operation = "MULTIPLY_ADD"
        m.inputs[1].default_value = 2.0
        m.inputs[2].default_value = -1.0
        nt.links.new(sep.outputs[ch], m.inputs[0])
        return m

    nx, ny = remap(0), remap(1)

    def sq(src):
        m = nt.nodes.new("ShaderNodeMath")
        m.operation = "MULTIPLY"
        nt.links.new(src.outputs[0], m.inputs[0])
        nt.links.new(src.outputs[0], m.inputs[1])
        return m

    nx2, ny2 = sq(nx), sq(ny)
    sub = nt.nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    sub.inputs[0].default_value = 1.0
    add = nt.nodes.new("ShaderNodeMath")
    add.operation = "ADD"
    nt.links.new(nx2.outputs[0], add.inputs[0])
    nt.links.new(ny2.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], sub.inputs[1])  # 1 - (nx^2+ny^2)
    clamp = nt.nodes.new("ShaderNodeMath")
    clamp.operation = "MAXIMUM"
    clamp.inputs[1].default_value = 0.0
    nt.links.new(sub.outputs[0], clamp.inputs[0])
    sqrt = nt.nodes.new("ShaderNodeMath")
    sqrt.operation = "SQRT"
    nt.links.new(clamp.outputs[0], sqrt.inputs[0])
    bz = nt.nodes.new("ShaderNodeMath")  # nz*0.5 + 0.5
    bz.operation = "MULTIPLY_ADD"
    bz.inputs[1].default_value = 0.5
    bz.inputs[2].default_value = 0.5
    nt.links.new(sqrt.outputs[0], bz.inputs[0])
    comb = nt.nodes.new("ShaderNodeCombineColor")
    nt.links.new(sep.outputs[0], comb.inputs[0])
    nt.links.new(sep.outputs[1], comb.inputs[1])
    nt.links.new(bz.outputs[0], comb.inputs[2])
    return comb.outputs[0]


def _is_rtint_slot(slot):
    if slot is None:
        return False
    path = (slot.path or "").lower()
    name = (slot.name or "").lower()
    return "rtint" in path or "reflectiontint" in path or "rtint" in name or "reflectiontint" in name


def _prune_unused_nodes(nt):
    """Drop nodes that do not feed the material output (leftover from refactors)."""
    output = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        return
    used = set()
    stack = [output]
    while stack:
        node = stack.pop()
        if node in used:
            continue
        used.add(node)
        for socket in node.inputs:
            for link in socket.links:
                stack.append(link.from_node)
    for node in list(nt.nodes):
        if node not in used:
            nt.nodes.remove(node)


def _layout_nodes(nt):
    from collections import defaultdict
    output = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        return
    depth = {output: 0}
    frontier = [output]
    while frontier:
        nxt = []
        for node in frontier:
            d = depth[node]
            for socket in node.inputs:
                for link in socket.links:
                    src = link.from_node
                    if src not in depth or depth[src] < d + 1:
                        depth[src] = d + 1
                        nxt.append(src)
        frontier = nxt
    max_depth = max(depth.values()) if depth else 0
    for node in nt.nodes:
        depth.setdefault(node, max_depth + 1)
    levels = defaultdict(list)
    for node in nt.nodes:
        levels[depth[node]].append(node)
    for d, level_nodes in levels.items():
        level_nodes.sort(key=lambda n: n.name)
        offset = (len(level_nodes) - 1) * 340 / 2.0
        for i, node in enumerate(level_nodes):
            node.location = (-d * 340, offset - i * 340)


def _apply_alpha_cutout(material, spec, bsdf):
    """Alpha-tested / masked materials (grilles, decals) — not glass transmission."""
    if not spec.alpha_cutout or spec.is_glass:
        return
    try:
        material.blend_method = "CLIP"
    except (TypeError, AttributeError):
        pass
    try:
        material.alpha_threshold = 0.5
    except (TypeError, AttributeError):
        pass
    try:
        material.surface_render_method = "DITHERED"
    except (TypeError, AttributeError):
        pass
    material.use_backface_culling = True


def _apply_glass_material(material, spec, bsdf):
    """EEVEE/Cycles transparency for mined glass shaders."""
    if not spec.is_glass:
        return
    try:
        material.surface_render_method = "BLENDED"
    except (TypeError, AttributeError):
        pass
    try:
        material.blend_method = "BLEND"
    except (TypeError, AttributeError):
        pass
    try:
        material.alpha_threshold = 0.0
    except (TypeError, AttributeError):
        pass
    material.use_backface_culling = False

    transmission = spec.transmission
    if transmission <= 0.0 and spec.alpha_value < 0.99 and spec.metallic < 0.5:
        transmission = max(0.0, 1.0 - spec.alpha_value)
    if transmission > 0.0:
        trans = _bsdf_input(bsdf, "Transmission Weight", 17)
        if trans is not None:
            trans.default_value = transmission
    rough = _bsdf_input(bsdf, "Roughness", 2)
    if rough is not None and not rough.is_linked:
        if spec.roughness_value is not None:
            rough.default_value = spec.roughness_value
        else:
            rough.default_value = 0.05
    if spec.alpha_value < 0.99:
        _bsdf_input(bsdf, "Alpha", 4).default_value = spec.alpha_value


def build_material(spec, resolver, image_cache):
    """Create a bpy material from a MaterialSpec. image_cache dedupes textures across the import."""
    material = _material_for_spec(spec)
    nt = material.node_tree
    bsdf = _ensure_principled_output(nt)
    _bsdf_input(bsdf, "Metallic", 1).default_value = spec.metallic
    if "IOR" in bsdf.inputs:
        bsdf.inputs["IOR"].default_value = spec.ior

    slots = {t.role: t for t in spec.textures}
    packed_opacity_node = None
    packed_opacity_slot = None
    tire = _spec_is_tire(spec)
    flip_v = _uv_flip_v(spec)
    use_gloss_graph = not tire and (slots.get("gloss") or slots.get("gloss_variation"))
    rmao_node = None

    def load(slot):
        try:
            return Texture.from_path(slot.path, resolver)
        except Exception as e:
            print(f"  material '{spec.name}': could not load '{slot.path}': {e!r}")
            return None

    diff = None
    diff_node = None
    if tire:
        diff, diff_node = _wire_tire_albedo(nt, bsdf, spec, slots, load, image_cache)
    else:
        # base color x AO multiply -> Base Color (matches old graph)
        base_mul = nt.nodes.new("ShaderNodeVectorMath")
        base_mul.operation = "MULTIPLY"
        base_mul.inputs[0].default_value = (1, 1, 1)
        base_mul.inputs[1].default_value = (1, 1, 1)
        nt.links.new(base_mul.outputs[0], _bsdf_input(bsdf, "Base Color", 0))

        diff = slots.get("diffuse")
        if _is_rtint_slot(diff):
            diff = None
        tex = load(diff) if diff else None
        diff_node = None
        tint = spec.base_color
        use_tint = tint and (
            abs(tint[0] - 1.0) > 1e-4
            or abs(tint[1] - 1.0) > 1e-4
            or abs(tint[2] - 1.0) > 1e-4
        )
        if tex is not None:
            img = _get_or_create_image(tex, non_color=diff.colorspace_data, cache=image_cache)
            node = _image_node(nt, img, diff.address, tiling=diff.tiling)
            comps_s = (getattr(diff, "components", None) or "xyzw").lower()
            if comps_s in ("x", "y", "z", "w"):
                color_socket = _scalar_to_color(
                    nt, _channel_socket(nt, node, diff.components, scalar=True)
                )
            else:
                color_socket = node.outputs[0]
            if use_tint:
                tint_mul = nt.nodes.new("ShaderNodeVectorMath")
                tint_mul.operation = "MULTIPLY"
                rgb = nt.nodes.new("ShaderNodeRGB")
                rgb.outputs[0].default_value = tint
                nt.links.new(color_socket, tint_mul.inputs[0])
                nt.links.new(rgb.outputs[0], tint_mul.inputs[1])
                color_socket = tint_mul.outputs[0]
            nt.links.new(color_socket, base_mul.inputs[0])
            _uv_chain(nt, node, diff.texcoord, diff.tiling, flip_v=flip_v)
            diff_node = node
        else:
            rgb = nt.nodes.new("ShaderNodeRGB")
            rgb.outputs[0].default_value = tint if use_tint else spec.base_color
            nt.links.new(rgb.outputs[0], base_mul.inputs[0])

        lcao = slots.get("lcao")
        tex = load(lcao) if lcao else None
        if tex is not None:
            img = _get_or_create_image(tex, non_color=True, cache=image_cache)
            node = _image_node(nt, img, lcao.address, tiling=lcao.tiling)
            ch = _channel_socket(nt, node, lcao.components, scalar=True)
            if (lcao.components or "x").lower() in ("x", "y", "z", "w"):
                ch = _scalar_to_color(nt, ch)
            nt.links.new(ch, base_mul.inputs[1])
            _uv_chain(nt, node, lcao.texcoord, lcao.tiling, flip_v=flip_v)

        # FH6 RMAO: sample once; B feeds AO when no lcao, R/G feed roughness/metal later.
        rmao = slots.get("rmao")
        if rmao is not None:
            rtex = load(rmao)
            if rtex is not None:
                img = _get_or_create_image(rtex, non_color=True, cache=image_cache)
                rmao_node = _image_node(nt, img, rmao.address, tiling=rmao.tiling)
                _uv_chain(nt, rmao_node, rmao.texcoord, rmao.tiling, flip_v=flip_v)
                if tex is None:
                    ao = _scalar_to_color(nt, _channel_socket(nt, rmao_node, "z", scalar=True))
                    nt.links.new(ao, base_mul.inputs[1])

    alpha = slots.get("alpha")
    tex = load(alpha) if alpha else None
    alpha_linked = False
    if tex is not None:
        img = _get_or_create_image(tex, non_color=True, cache=image_cache)
        node = _image_node(nt, img, alpha.address)
        _uv_chain(nt, node, alpha.texcoord, alpha.tiling, flip_v=flip_v)
        alpha_linked = _link_bsdf_alpha(
            nt, bsdf, _channel_socket(nt, node, alpha.components, scalar=True)
        )
    elif spec.is_glass and spec.alpha_value < 0.99:
        _bsdf_alpha(bsdf).default_value = spec.alpha_value
        alpha_linked = True
    elif (
        diff
        and getattr(diff, "packs_opacity", False)
        and diff_node is not None
        and not getattr(spec, "opacity_from_normal", False)
    ):
        # badge_ch1difnormglossao etc.: opacity from normal alpha, not diffuse W.
        alpha_linked = _link_bsdf_alpha(
            nt, bsdf, _packed_opacity_socket(nt, diff_node, diff.components)
        )

    gloss_var = slots.get("gloss_variation")
    gloss = slots.get("gloss")
    rough_in = _bsdf_input(bsdf, "Roughness", 2)

    def _link_roughness(from_socket):
        if from_socket is not None:
            if tire:
                floor = nt.nodes.new("ShaderNodeMath")
                floor.operation = "MAXIMUM"
                floor.inputs[1].default_value = 0.85
                nt.links.new(from_socket, floor.inputs[0])
                from_socket = floor.outputs[0]
            nt.links.new(from_socket, rough_in)

    def _smoothness_to_roughness(smooth_socket):
        sub = nt.nodes.new("ShaderNodeMath")
        sub.operation = "SUBTRACT"
        sub.inputs[0].default_value = 1.0
        nt.links.new(smooth_socket, sub.inputs[1])
        return sub.outputs[0]

    def _mul_channels(a, b):
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        nt.links.new(a, mul.inputs[0])
        nt.links.new(b, mul.inputs[1])
        return mul.outputs[0]

    def _lerp_scalar(a, b, t):
        """a + (b - a) * t — gloss_variation modulates between GlossA and gloss map."""
        sub = nt.nodes.new("ShaderNodeMath")
        sub.operation = "SUBTRACT"
        nt.links.new(b, sub.inputs[0])
        nt.links.new(a, sub.inputs[1])
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        nt.links.new(sub.outputs[0], mul.inputs[0])
        nt.links.new(t, mul.inputs[1])
        add = nt.nodes.new("ShaderNodeMath")
        add.operation = "ADD"
        nt.links.new(a, add.inputs[0])
        nt.links.new(mul.outputs[0], add.inputs[1])
        return add.outputs[0]

    base_rough = spec.roughness_value if spec.roughness_value is not None else 0.5
    base_smooth = 1.0 - base_rough
    smooth_sock = None
    base_smooth_sock = None
    gloss_map_sock = None
    gloss_var_sock = None
    if spec.roughness_value is not None and use_gloss_graph:
        bs = nt.nodes.new("ShaderNodeValue")
        bs.outputs[0].default_value = base_smooth
        base_smooth_sock = bs.outputs[0]

    if gloss and not tire:
        tex = load(gloss)
        if tex is not None:
            img = _get_or_create_image(tex, non_color=True, cache=image_cache)
            node = _image_node(nt, img, gloss.address, tiling=gloss.tiling)
            gloss_map_sock = _channel_socket(nt, node, gloss.components, scalar=True)
            _uv_chain(nt, node, gloss.texcoord, gloss.tiling, flip_v=flip_v)

    if gloss_var and not tire:
        tex = load(gloss_var)
        if tex is not None:
            img = _get_or_create_image(tex, non_color=True, cache=image_cache)
            node = _image_node(nt, img, gloss_var.address, tiling=gloss_var.tiling)
            gloss_var_sock = _channel_socket(nt, node, gloss_var.components, scalar=True)
            _uv_chain(nt, node, gloss_var.texcoord, gloss_var.tiling, flip_v=flip_v)

    # Game: smoothness = lerp(GlossA, gloss_map, gloss_variation); gloss-only *= GlossA.
    if gloss_map_sock is not None and gloss_var_sock is not None:
        if base_smooth_sock is not None:
            smooth_sock = _lerp_scalar(base_smooth_sock, gloss_map_sock, gloss_var_sock)
        else:
            smooth_sock = gloss_map_sock
    elif gloss_map_sock is not None:
        if base_smooth_sock is not None:
            smooth_sock = _mul_channels(gloss_map_sock, base_smooth_sock)
        else:
            smooth_sock = gloss_map_sock
    elif gloss_var_sock is not None:
        if base_smooth_sock is not None:
            smooth_sock = _mul_channels(gloss_var_sock, base_smooth_sock)
        else:
            smooth_sock = gloss_var_sock

    if smooth_sock is not None:
        _link_roughness(_smoothness_to_roughness(smooth_sock))
    elif tire:
        val = nt.nodes.new("ShaderNodeValue")
        val.outputs[0].default_value = 1.0
        _link_roughness(val.outputs[0])
    elif rmao_node is not None:
        # FH6 RMAO R channel is roughness (not FH5 smoothness).
        _link_roughness(_channel_socket(nt, rmao_node, "x", scalar=True))
    elif spec.roughness_value is not None:
        val = nt.nodes.new("ShaderNodeValue")
        val.outputs[0].default_value = spec.roughness_value
        _link_roughness(val.outputs[0])

    if rmao_node is not None and not tire:
        metal_in = _bsdf_input(bsdf, "Metallic", 1)
        if metal_in is not None and not metal_in.is_linked:
            nt.links.new(_channel_socket(nt, rmao_node, "y", scalar=True), metal_in)
    if tire:
        spec_in = _bsdf_input(bsdf, "Specular IOR Level", 12)
        if spec_in is not None and not spec_in.is_linked:
            spec_in.default_value = 0.0
        spec_in = _bsdf_input(bsdf, "Specular", 7)
        if spec_in is not None and not spec_in.is_linked:
            spec_in.default_value = 0.0
        material.diffuse_color = (0.08, 0.08, 0.08, 1.0)

    normal = slots.get("normal")
    tex = load(normal) if normal else None
    if tex is not None:
        img = _get_or_create_image(tex, non_color=True, cache=image_cache)
        node = _image_node(nt, img, normal.address, tiling=normal.tiling)
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.uv_map = normal.texcoord
        if tire:
            strength = nmap.inputs.get("Strength")
            if strength is not None:
                strength.default_value = 0.12
        nt.links.new(nmap.outputs[0], _bsdf_input(bsdf, "Normal", 5))
        if normal.normal_two_channel:
            nt.links.new(_reconstruct_normal_z(nt, node.outputs[0]), nmap.inputs[1])
        else:
            nt.links.new(node.outputs[0], nmap.inputs[1])
        _uv_chain(nt, node, normal.texcoord, normal.tiling, flip_v=flip_v and not tire)
        if getattr(normal, "packs_opacity", False):
            packed_opacity_node = node
            packed_opacity_slot = normal

    if not alpha_linked and packed_opacity_node is not None and packed_opacity_slot is not None:
        sock = _packed_opacity_socket(
            nt, packed_opacity_node, packed_opacity_slot.components
        )
        alpha_linked = _link_bsdf_alpha(nt, bsdf, sock)

    alpha_linked = _wire_normal_alpha(nt, bsdf, spec, slots, alpha_linked)

    _prune_unused_nodes(nt)
    _layout_nodes(nt)
    _apply_alpha_cutout(material, spec, bsdf)
    _apply_glass_material(material, spec, bsdf)
    return material

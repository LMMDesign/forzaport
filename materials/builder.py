"""MaterialBuilder: parsed material -> MaterialSpec (a pure, Blender-free description).

Selects textures by comparing each instance param to the mined shader default (material_table.json),
mapping PBR roles from descriptor role_token / channel usage, and gating alpha/normal slots on the
material's bool switches. UV channel, packing, and sampler address come from the same descriptor.
"""

import os
from dataclasses import dataclass, field

from ..parsing.material import ShaderParameterName as SPN
from ..parsing.material import classify_texture_role, image_name
from . import material_table


@dataclass
class TextureSlot:
    role: str
    path: str
    name: str
    texcoord: str = "TEXCOORD0"
    tiling: tuple = (1.0, 1.0)
    components: str = None          # packing, e.g. 'xy' / 'xyzw'
    normal_two_channel: bool = False
    address: dict = None            # {'U':..,'V':..,'W':..}
    colorspace_data: bool = True    # True => Non-Color (normals/gloss/ao/alpha)
    invert: bool = False            # gloss smoothness -> roughness
    output_channel: int = 0
    packs_opacity: bool = False     # PS reads W from this sample (normal/diffuse packed mask)


# tiretread rubber scalar @0x2AC75B56 (mined default)
TIRE_RUBBER_RGBA = (0.9131829738616943, 0.9214940071105957, 0.9245240092277527, 1.0)


@dataclass
class MaterialSpec:
    name: str
    valid: bool = False
    base_color: tuple = (1.0, 1.0, 1.0, 1.0)
    metallic: float = 0.0
    ior: float = 1.45
    roughness_value: float = None   # already inverted (1 - smoothness) when no gloss texture
    textures: list = field(default_factory=list)
    is_glass: bool = False
    alpha_value: float = 1.0
    transmission: float = 0.0
    alpha_cutout: bool = False      # masked / alpha-test (grilles, badges, decals)
    opacity_from_normal: bool = False # mined PS comps xyw on normal slot
    is_tire: bool = False           # tiretread family — matte rubber + compound tread mask
    tire_rubber_v: float = None     # scaling_text mask V offset (game rubber scalar)


def _float_param(params, hash_int):
    p = params.get(hash_int)
    v = getattr(p, "value", None) if p is not None else None
    return v if isinstance(v, (int, float)) else None


_GLASS_SHADER_MARKERS = (
    "glass", "window", "windshield", "windsheild", "gls_", "rad_gls",
    "limotint", "rearglass", "driverglass", "bumpglass", "illuminatingglass",
    "mirrors", "carmirror",
)


def _is_glass_shader(shader_name):
    if not shader_name:
        return False
    s = shader_name.lower()
    return any(m in s for m in _GLASS_SHADER_MARKERS)


def _is_mirror_shader(shader_name):
    if not shader_name:
        return False
    s = shader_name.lower()
    return "mirror" in s or "mirrors" in s


def _apply_glass_surface(spec, shader_name, params):
    """Transparent / reflective glass from mined cbuffer params (not opaque white)."""
    if not _is_glass_shader(shader_name):
        return

    spec.is_glass = True
    spec.valid = True
    spec.metallic = 0.0

    surface = _color(params, SPN.GlassSurfaceColorParam)
    tint = _color(params, SPN.GlassTintColorParam)
    interior = _color(params, SPN.GlassInteriorTintColorParam)
    if surface is not None:
        spec.base_color = (surface[0], surface[1], surface[2], 1.0)
    elif interior is not None:
        spec.base_color = (interior[0], interior[1], interior[2], 1.0)
    elif tint is not None:
        spec.base_color = (tint[0], tint[1], tint[2], 1.0)

    alpha = spec.base_color[3]
    if surface is not None and 0.0 < surface[3] < 0.99:
        alpha = surface[3]

    for key in (SPN.GlassOpacityFloat, SPN.GlassOpacityAltFloat):
        v = _float_param(params, key)
        if v is not None and 0.0 <= v < 0.99:
            alpha = v if alpha >= 0.99 else min(alpha, v)

    if _is_mirror_shader(shader_name):
        spec.metallic = 1.0
        spec.alpha_value = 1.0
        spec.transmission = 0.0
        if tint is not None:
            spec.base_color = (tint[0], tint[1], tint[2], 1.0)
        spec.roughness_value = spec.roughness_value if spec.roughness_value is not None else 0.05
        return

    spec.alpha_value = max(0.02, min(1.0, alpha))
    if spec.alpha_value >= 0.99 and "interior" in (shader_name or "").lower():
        spec.alpha_value = 0.12
    spec.transmission = max(0.0, min(1.0, 1.0 - spec.alpha_value))

    smooth = _float_param(params, SPN.GlassSmoothnessFloat)
    if smooth is not None and not any(t.role in ("gloss", "gloss_variation") for t in spec.textures):
        rough = 1.0 - smooth
        if rough > 0.4:
            rough = smooth
        spec.roughness_value = max(0.02, min(1.0, rough))

    ior = _float_param(params, SPN.GlassIORFloat)
    if ior is not None and 1.0 <= ior <= 1.8:
        spec.ior = ior


def _color(params, name):
    p = params.get(name)
    if p is not None and getattr(p, "type", 0) in (0, 1):
        v = p.value
        if v is not None:
            return (v[0], v[1], v[2], 1.0 if v[3] == 0 else v[3])
    return None


def _switch(params, name):
    """Tri-state: True/False if the bool param is present, else None."""
    p = params.get(name)
    return bool(p.value) if (p is not None and getattr(p, "type", None) == 3) else None


def _paint_color(params):
    """Carpaint base colour follows the shader's colour-selector switches, not a fixed priority:
      ColorGroupSwitch    -> PaintColorGroup (group/livery colour, e.g. black rim faces)
      UseUnique == False  -> this part's own PaintColor (e.g. black hubs/inner rims)
      otherwise           -> UniqueBaseColor (the car's body colour), then PaintColor.
    This keeps the body its unique colour while wheels/hubs/accents paint independently
    (black rims with a red accent ring instead of body-red everywhere)."""
    if _switch(params, SPN.ColorGroupSwitchBool):
        c = _color(params, SPN.PaintColorGroupColorParam)
        if c is not None:
            return c
    if _switch(params, SPN.UseUniqueBaseColorSwitchBool) is False:
        c = _color(params, SPN.PaintColorColorParam)
        if c is not None:
            return c
    for name in (SPN.UniqueBaseColorColorParam, SPN.PaintColorColorParam):
        c = _color(params, name)
        if c is not None:
            return c
    return None


def _diffuse_color(params):
    for name in (SPN.DiffuseColorGroupColorParam, SPN.DiffuseColorAColorParam,
                 SPN.DiffuseColorColorParam,
                 SPN.UniqueBaseColorColorParam, SPN.PaintColorColorParam, SPN.ColorColorParam):
        p = params.get(name)
        if p is not None and getattr(p, "type", 0) == 1:
            return p.value
    return None


def _base_color_without_diffuse(params, desc, is_paint, stock_paint=None):
    if is_paint:
        c = _paint_color(params)
        if c is not None:
            return c
        return stock_paint
    if desc is not None:
        c = desc.primary_base_color(params)
        if c is not None:
            return c
    return _diffuse_color(params)


def _gloss_smoothness(params, desc=None):
    """Base gloss/smoothness scalar (GlossA etc.); instance value or mined shader default."""
    for name in (SPN.GlossA_floatVal, SPN.GlossSimple_floatVal, SPN.GlossB_floatVal):
        v = _float_param(params, name)
        if v is None and desc is not None:
            meta = desc.scalar(name)
            if meta and meta.get("type") == 2 and isinstance(meta.get("default"), (int, float)):
                v = meta["default"]
        if isinstance(v, (int, float)):
            return v
    return None


def _param_tiling(params, *named_hashes, use_utile=False):
    for h in named_hashes:
        p = params.get(h)
        v = getattr(p, "value", None) if p is not None else None
        if isinstance(v, (tuple, list)) and len(v) >= 2:
            return (v[0], v[1])
        if isinstance(v, (int, float)):
            return (v, v)
    if use_utile:
        u = params.get(SPN.uTile_floatVal)
        v = params.get(SPN.vTile_floatVal)
        if u is not None and v is not None:
            return (u.value, v.value)
    return None


_OPACITY_SWITCHES = (
    SPN.CH1OpacityMaskSwitchBool, SPN.CH1OpacitySwitchBool, SPN.CH1AlphaSwitchBool,
    SPN.CH2OpacityMaskSwitchBool, SPN.UseDiffuseAlphaBool,
)

_GRILLE_SHADERS = frozenset({"ext_grille", "int_grille"})

_NORMAL_SWITCHES = (
    SPN.CH1NormalMapSwitchBool, SPN.CH2NormalMapSwitchBool, SPN.CH2NormalSwitchBool,
)

_DIFFUSE_SWITCHES = (
    SPN.CH1DiffuseTextureSwitchBool, SPN.DiffuseTextureSwitchBool,
)

_LOCAL_AO_SWITCHES = (
    SPN.LocalAOSwitchBool, SPN.CH1LocalAOSwitchBool,
)


def _uv_scale_factor(params, desc, hash_int):
    """Per-hash UV scale from instance params or mined shader default; None if unresolved."""
    p = params.get(hash_int)
    if p is not None:
        typ = getattr(p, "type", None)
        val = getattr(p, "value", None)
    elif desc is not None:
        meta = desc.scalar(hash_int)
        if not meta:
            return None
        typ = meta.get("type")
        val = meta.get("default")
    else:
        return None

    if typ == 11:
        if isinstance(val, (tuple, list)) and len(val) >= 2:
            return (float(val[0]), float(val[1]))
        return None
    if typ == 2:
        if not isinstance(val, (int, float)):
            return None
        f = float(val)
        if hash_int == SPN.uTile_floatVal:
            return (f, 1.0)
        if hash_int == SPN.vTile_floatVal:
            return (1.0, f)
        # Other type-2 entries in uv_scale (e.g. optional flags with null defaults) are not
        # separate U/V tile factors — only uTile/vTile apply as axis scales.
        return None
    return None


def _descriptor_uv_tiling(tex_hash, params, desc):
    """PS-side UV frequency scale from mined per-texture uv_scale hashes.

    Each texture slot lists the scalar hashes the pixel shader multiplies into its UVs
    (vec2 tilings and/or separate uTile/vTile floats). Instance params win; otherwise
    mined defaults from material_table.json are used.

    Distinct from mesh uv_transform (baked in geometry decode): the game applies this multiply
    in the pixel shader after the vertex shader has already transformed the mesh UVs."""
    if desc is None:
        return None
    keys = desc.uv_scale_hashes(tex_hash)
    if not keys:
        return None
    u = v = 1.0
    any_scale = False
    for k in keys:
        h = int(k, 16) if isinstance(k, str) else k
        contrib = _uv_scale_factor(params, desc, h)
        if contrib is None:
            continue
        any_scale = True
        u *= contrib[0]
        v *= contrib[1]
    if not any_scale:
        return None
    return (u, v)


def _is_rtint_path(path):
    base = os.path.basename(path).lower()
    return "rtint" in base or "reflectiontint" in base


def _feature_enabled(params, switch_names):
    """Feature is on when any switch is True; on by default when no switch param is present."""
    seen = False
    for name in switch_names:
        p = params.get(name)
        if p is not None and getattr(p, "type", None) == 3:
            seen = True
            if p.value:
                return True
    return not seen


def _normal_packed_opacity_enabled(params, desc, shader_name, tex_hash):
    """Whether PS routes this normal sample's W channel into output alpha (mined per shader)."""
    if desc is not None and desc.feature("normal_w_opacity") is not None:
        return desc.normal_w_opacity_enabled(params, tex_hash)
    # Table not yet mined: grille shaders gate W→alpha on GrilleNormalOpacitySwitchBool
    sn = (shader_name or "").lower()
    if sn in _GRILLE_SHADERS:
        p = params.get(SPN.GrilleNormalOpacitySwitchBool)
        if p is not None and getattr(p, "type", None) == 3:
            return bool(p.value)
        return False
    return False


def _opacity_enabled(params, desc=None):
    if desc is not None:
        gated = desc.gate_enabled(params, "opacity_mask", default_when_unknown=None)
        if gated is not None:
            return gated
    return _feature_enabled(params, _OPACITY_SWITCHES)


def _normal_map_enabled(params, desc=None):
    if desc is not None:
        gated = desc.gate_enabled(params, "normal_map", default_when_unknown=None)
        if gated is not None:
            return gated
    return _feature_enabled(params, _NORMAL_SWITCHES)


def _diffuse_map_enabled(params, desc=None):
    if desc is not None:
        gated = desc.gate_enabled(params, "diffuse_map", default_when_unknown=None)
        if gated is not None:
            return gated
    return _feature_enabled(params, _DIFFUSE_SWITCHES)


def _local_ao_enabled(params, desc=None):
    if desc is not None:
        gated = desc.gate_enabled(params, "local_ao", default_when_unknown=None)
        if gated is not None:
            return gated
    return _feature_enabled(params, _LOCAL_AO_SWITCHES)


def _resolve_pbr_role(desc, param_hash, path, shader_roles):
    path_role = classify_texture_role(path)
    if desc is not None:
        role = desc.pbr_role(param_hash)
        if role is not None:
            # Path naming beats descriptor slot when the bound swatch is a different map type.
            if path_role in ("alpha", "gloss_variation", "rtint", "rmao") and path_role != role:
                return path_role
            # Packed CH1 slots (normao_diffopac_*) may bind lcao/mask swatches to the diffuse hash.
            if role == "diffuse" and path_role in (
                "lcao", "ao", "alpha", "normal", "gloss", "gloss_variation", "rmao"
            ):
                return path_role
            return role
    if path_role is not None:
        return path_role
    return shader_roles.get(param_hash)


def _texture_is_active(desc, param_hash, path, defaults):
    """Active when overridden to a real swatch, not when still on placeholder defaults."""
    if path and material_table.is_inactive_shader_slot(image_name(path)):
        return False
    if desc is not None:
        if not desc.is_still_default(param_hash, path):
            return True
        token = desc.role_token(param_hash)
        if material_table.is_inactive_shader_slot(token):
            return False
        return desc.pbr_role(param_hash) is not None
    return path not in defaults


def _is_tire_shader(shader_name):
    return bool(shader_name and shader_name.lower().startswith("tire"))


def _is_tire_material_name(name):
    n = (name or "").lower()
    return n in ("scaling_text", "sidewall") or "tire" in n


def _is_tire_mask_name(slot):
    label = f"{slot.name or ''} {slot.path or ''}".lower()
    return "tire" in label and "mask" in label


def _is_tire_mask_slot(slot):
    """Car-compound tread mask (tire_c_mask / tire_ao_standard_mask) on TEXCOORD2."""
    if slot.role not in ("lcao", "ao", "diffuse"):
        return False
    return _is_tire_mask_name(slot)


def _finalize_tire_spec(spec, material_name, shader_name, desc, params):
    """Tire tread/sidewall: compound mask drives albedo on TEXCOORD2 (V offset by rubber scalar)."""
    if not (_is_tire_shader(shader_name) or _is_tire_material_name(material_name)):
        return
    spec.is_tire = True
    rubber = None
    if desc is not None:
        rubber = desc.primary_base_color(params)
    if rubber is None:
        rubber = TIRE_RUBBER_RGBA
    # Rubber scalar (~0.91) is a UV offset only — keep viewport/base_color dark.
    spec.tire_rubber_v = rubber[1]
    spec.base_color = (0.06, 0.06, 0.06, 1.0)
    if spec.roughness_value is None:
        spec.roughness_value = 0.88
    for slot in spec.textures:
        if _is_tire_mask_slot(slot):
            slot.role = "diffuse"
            slot.colorspace_data = True
            slot.texcoord = "TEXCOORD2"
            slot.components = slot.components or "x"


def _detect_metalness(params, desc, defaults, overrides, shader_name=None):
    sn = (shader_name or "").lower()
    if "simplemetal" in sn:
        return 1.0
    metal = params.get(SPN.MetalnessSwitchBool)
    if metal is not None and getattr(metal, "type", None) == 3:
        return 1.0 if metal.value else 0.0
    nonmetal = params.get(SPN.NonMetalnessSwitchBool)
    if nonmetal is not None and getattr(nonmetal, "type", None) == 3:
        return 0.0 if nonmetal.value else 1.0
    for h, p in params.items():
        if getattr(p, "type", 0) != 6:
            continue
        path = getattr(p, "path", "")
        if not path or not _texture_is_active(desc, h, path, defaults):
            continue
        if _is_rtint_path(path) or (desc is not None and desc.pbr_role(h) == "rtint"):
            return 1.0
    return 0.0


class MaterialBuilder:
    """Turns a parsed MaterialSystemObject (parameters + shader_name + default roles) into a MaterialSpec."""

    def __init__(self):
        self.stock_paint_rgba = None

    def build(self, name, material):
        spec = MaterialSpec(name=name)
        params = material.parameters
        defaults = getattr(material, "default_texture_paths", ())
        overrides = getattr(material, "override_hashes", set())
        shader_roles = getattr(material, "default_texture_roles", {})
        desc = material_table.lookup(getattr(material, "shader_name", None))
        shader_name = getattr(material, "shader_name", None)

        # --- one active (non-default) texture per PBR role, descriptor role first ---
        chosen = {}
        active_slots = []
        for h, p in params.items():
            if getattr(p, "type", 0) != 6:
                continue
            path = getattr(p, "path", "")
            if not path:
                continue
            role = _resolve_pbr_role(desc, h, path, shader_roles)
            if role is None or role == "rtint" or _is_rtint_path(path):
                continue
            if "mfr" in os.path.basename(path).lower() and role != "gloss":
                continue
            if not _texture_is_active(desc, h, path, defaults):
                continue
            active_slots.append((h, path, role))

        for h, path, role in sorted(active_slots, key=lambda row: (0 if h in overrides else 1, row[2])):
            chosen.setdefault(role, (h, path))

        if not chosen and len(active_slots) == 1:
            h, path, role = active_slots[0]
            if role != "rtint":
                chosen[role] = (h, path)

        if "alpha" in chosen and not _opacity_enabled(params, desc) and not _is_glass_shader(shader_name):
            chosen.pop("alpha")
        if "normal" in chosen and not _normal_map_enabled(params, desc):
            nh, npath = chosen["normal"]
            if desc is None or desc.is_still_default(nh, npath):
                chosen.pop("normal")
        if "diffuse" in chosen and not _diffuse_map_enabled(params, desc):
            chosen.pop("diffuse")
        if "lcao" in chosen and not _local_ao_enabled(params, desc):
            lh, lp = chosen["lcao"]
            keep = (
                (_is_tire_shader(shader_name) or _is_tire_material_name(name))
                and "tire" in image_name(lp).lower()
                and "mask" in image_name(lp).lower()
            )
            if not keep:
                chosen.pop("lcao")

        # ao folds into the lcao slot (matches old behavior)
        if "lcao" not in chosen and "ao" in chosen:
            chosen["lcao"] = chosen.pop("ao")

        # --- build slots, enriched from the descriptor ---
        for role, (h, path) in chosen.items():
            if role == "ao":
                continue
            slot = TextureSlot(role=role, path=path, name=image_name(path))
            slot.colorspace_data = (role != "diffuse")
            slot.invert = role == "gloss"

            uv = desc.uv_texcoord(h) if desc else None
            if uv:
                slot.texcoord = uv
            else:
                # descriptor miss -> old tiling heuristic (detail maps go to TEXCOORD1)
                slot.texcoord = self._heuristic_texcoord(role, params, slot, h, desc)

            if desc:
                slot.components = desc.used_components(h)
                slot.output_channel = material_table.channel_index_from_comps(slot.components)
                slot.address = desc.address_mode(h)
                if role == "normal":
                    slot.normal_two_channel = desc.is_two_channel_normal(h)
                opacity_ok = (
                    _normal_packed_opacity_enabled(params, desc, shader_name, h)
                    if role == "normal"
                    else _opacity_enabled(params, desc)
                )
                if (
                    role in ("normal", "diffuse")
                    and "alpha" not in chosen
                    and not _is_glass_shader(shader_name)
                    and opacity_ok
                    and desc.packs_opacity_channel(h)
                ):
                    slot.packs_opacity = True
                    spec.alpha_cutout = True
                    if role == "normal":
                        spec.opacity_from_normal = True

            # FH6 basecoloralpha: opacity in A even when descriptor comps aren't mined yet.
            if (
                role == "diffuse"
                and "alpha" not in chosen
                and not _is_glass_shader(shader_name)
                and "basecoloralpha" in image_name(path).lower()
            ):
                slot.packs_opacity = True
                spec.alpha_cutout = True
                if slot.components is None:
                    slot.components = "xyzw"

            tiling = _descriptor_uv_tiling(h, params, desc)
            if tiling is None:
                tiling = self._role_tiling(role, params)
            if tiling is not None:
                slot.tiling = tiling
            spec.textures.append(slot)

        if "alpha" in chosen and _opacity_enabled(params, desc) and not _is_glass_shader(shader_name):
            spec.alpha_cutout = True

        _finalize_tire_spec(spec, name, shader_name, desc, params)

        spec.metallic = _detect_metalness(params, desc, defaults, overrides, shader_name)

        has_diffuse = any(t.role == "diffuse" for t in spec.textures)

        is_paint = "paint" in (shader_name or "").lower()
        color = None
        if not has_diffuse:
            color = _base_color_without_diffuse(
                params, desc, is_paint, stock_paint=self.stock_paint_rgba
            )
            if color is not None:
                spec.base_color = (color[0], color[1], color[2], 1.0 if color[3] == 0 else color[3])
        elif not is_paint and desc is not None and not _is_glass_shader(shader_name) and not spec.is_tire:
            if spec.metallic < 0.5:
                tint = desc.diffuse_tint(params)
                if tint is not None:
                    spec.base_color = (tint[0], tint[1], tint[2], 1.0 if tint[3] == 0 else tint[3])
            elif (shader_name or "").lower() == "rotor":
                mod = desc.primary_base_color(params)
                if mod is not None:
                    spec.base_color = (mod[0], mod[1], mod[2], 1.0 if mod[3] == 0 else mod[3])
        smoothness = _gloss_smoothness(params, desc)
        if smoothness is not None:
            spec.roughness_value = max(0.0, min(1.0, 1.0 - smoothness))

        _apply_glass_surface(spec, shader_name, params)

        if spec.opacity_from_normal:
            for slot in spec.textures:
                if slot.role == "diffuse":
                    slot.packs_opacity = False

        spec.valid = bool(spec.textures) or color is not None or spec.is_glass
        return spec

    @staticmethod
    def _role_tiling(role, params):
        if role == "diffuse":
            return _param_tiling(params, SPN.CH2DiffuseTextureTiling, SPN.DiffuseTilingB)
        if role == "normal":
            return _param_tiling(params, SPN.NormalTilingB, SPN.NormalTiling, use_utile=True)
        if role == "gloss" or role == "gloss_variation":
            return _param_tiling(params, use_utile=True)
        return None

    @staticmethod
    def _heuristic_texcoord(role, params, slot, tex_hash=None, desc=None):
        """Fallback only when the descriptor lacks UV: detail maps (those with a tiling param)
        repeat on TEXCOORD1, everything else stays on TEXCOORD0 (the old behavior)."""
        if role in ("lcao", "ao") and _is_tire_mask_slot(slot):
            return "TEXCOORD2"
        if role in ("diffuse", "normal", "gloss", "gloss_variation", "rmao"):
            tiling = _descriptor_uv_tiling(tex_hash, params, desc)
            if tiling is None:
                tiling = MaterialBuilder._role_tiling(role, params)
            if tiling is not None:
                return "TEXCOORD1"
        return "TEXCOORD0"

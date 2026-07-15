"""Loader + lookup for data/material_table.json (mined from the FH5 shader library).

Pure module: no bpy. The table is keyed by shader name -> {textures, samplers, scalars}; texture
entries carry the authoritative per-param signals recovered from the compiled shaders:
  uv (TEXCOORD index), comps (used channels = packing), normal_enc (2ch/rgb),
  sN (sampler register), address (U/V/W wrap/clamp), role_token, default (swatchbin).
Texture activation and PBR role come from comparing instance paths to these mined defaults and
mapping role_token / channel usage — not from filename heuristics on the instance path.
"""

import json
import os
import re

_TABLE = None

# Documented cbuffer bools that gate normal-W → SV_Target alpha (see parsing/material.py).
_KNOWN_NORMAL_W_SWITCH_HASHES = ("0x7487EB77", "0xBD65D78D")

_GUID_SUFFIX = re.compile(r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _load():
    global _TABLE
    if _TABLE is None:
        env_path = os.environ.get("FORZA_TABLE_PATH")
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        if env_path:
            paths = [env_path]
        else:
            paths = [
                os.path.join(data_dir, "material_table.json"),
                os.path.join(data_dir, "material_table_fh6.json"),
            ]
        _TABLE = {}
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    chunk = json.load(f).get("shaders", {})
                if chunk:
                    _TABLE.update(chunk)
            except (OSError, ValueError) as e:
                if p == paths[0]:
                    print(f"Forza: material_table unavailable ({e}); falling back to filename roles.")
    return _TABLE


# FH6 car_* shader names -> FH5-table aliases (paint/glass scalar mining where still valid).
_SHADER_ALIASES = {
    "car_automotive_paint": "carpaint_standard",
    "car_window": "glass",
    "car_glass": "glass",
    "car_glass_detailed": "detail_glass",
    "car_mirror": "mirrors",
    "car_blackhole": "blackhole",
}


def _key(hash_int):
    return f"0x{hash_int & 0xFFFFFFFF:08X}"


def swatch_key(path):
    """Normalised swatch identity (strip folder, extension, trailing GUID)."""
    if not path:
        return ""
    name = os.path.basename(path).rsplit(".", 1)[0]
    return _GUID_SUFFIX.sub("", name).lower()


def _pbr_role_from_token(role_token, comps=None):
    """Map mined role_token (+ optional PS channel mask) to a PBR slot."""
    if not role_token:
        return None
    rl = role_token.lower()
    if "rtint" in rl or "reflectiontint" in rl:
        return "rtint"
    if "glossvariation" in rl or "grungegloss" in rl:
        return "gloss_variation"
    if "gradient" in rl and "mask" in rl:
        return "gloss_variation"
    if "whitemask" in rl:
        return "alpha"
    tokens = rl.split("_")
    last = tokens[-1] if tokens else ""
    token_set = set(tokens)

    def has(*opts):
        return not token_set.isdisjoint(opts)

    if last in ("nrml", "nrm", "norm", "normal") or has("nrml", "nrm", "norm", "normal"):
        return "normal"
    if last == "lcao" or has("lcao"):
        return "lcao"
    if last == "rmao" or has("rmao") or "rmao" in rl or "roughmetalao" in rl:
        return "rmao"
    if last == "ao" or has("ao"):
        return "ao"
    if last == "basecoloralpha" or has("basecoloralpha") or "basecoloralpha" in rl:
        return "diffuse"
    if last in ("opac", "opc", "opacity", "alpha") or has("opac", "opacity", "alpha"):
        return "alpha"
    if last in ("higt", "height") or has("higt"):
        return "gloss"
    if last in ("glos", "gloss", "rgh", "rough", "roughness") or has("glos", "gloss", "rgh", "rough"):
        return "gloss"
    if last in ("diff", "dif", "diffuse", "albedo", "basecolor", "col", "color") or has(
        "diff", "diffuse", "albedo", "basecolor"
    ):
        return "diffuse"
    if last == "mask" or has("mask"):
        if has("ao") or "lcao" in rl:
            return "ao"
        if any(p in rl for p in ("mudfx", "snowfx", "dmg_", "whitemask", "flatcolor")):
            return None
        return "gloss_variation"
    comps_s = (comps or "").lower()
    if comps_s in ("x",) or (comps_s and comps_s not in ("xyz", "xyzw", "xy")):
        return "gloss"
    return None


def channel_index_from_comps(comps):
    """First PS-sampled channel letter -> Blender Image Texture output index."""
    if not comps:
        return 0
    return {"x": 0, "y": 1, "z": 2, "w": 3}.get(comps.lower()[0], 0)


_INACTIVE_WHEN_DEFAULT_PREFIXES = (
    "defaultshader", "defaultch", "default_nrm", "mudfx_", "snowfx_", "dmg_",
    "mask_default", "ch1_normao", "flatcolor_", "paint_flake", "flatwhite_",
    "testa_", "testb_", "letters_", "whitemask",
)


def is_inactive_shader_slot(role_token):
    """Shader-family slots that are never used for static import while still on the mined default."""
    if not role_token:
        return True
    rl = role_token.lower()
    return any(rl.startswith(p) or p in rl for p in _INACTIVE_WHEN_DEFAULT_PREFIXES)


class ShaderDescriptor:
    """Per-shader view over the table with hash-keyed lookups."""

    def __init__(self, name, raw):
        self.name = name
        self._tex = raw.get("textures", {})
        self._samp = raw.get("samplers", {})
        self._scalar = raw.get("scalars", {})
        self._features = raw.get("features", {})

    def texture(self, hash_int):
        """{treg, uv, comps, normal_enc, sN, address, role_token, default} or None."""
        return self._tex.get(_key(hash_int))

    def sampler(self, hash_int):
        return self._samp.get(_key(hash_int))

    def scalar(self, hash_int):
        return self._scalar.get(_key(hash_int))

    def role_token(self, hash_int):
        rec = self.texture(hash_int)
        return (rec or {}).get("role_token") or ""

    def mined_default_path(self, hash_int):
        rec = self.texture(hash_int)
        return (rec or {}).get("default")

    def is_still_default(self, hash_int, path):
        """True when the instance still binds the shader's default swatch for this param hash."""
        mined = self.mined_default_path(hash_int)
        if not mined or not path:
            return False
        return swatch_key(path) == swatch_key(mined)

    def pbr_role(self, hash_int):
        rec = self.texture(hash_int)
        if not rec:
            return None
        return _pbr_role_from_token(rec.get("role_token"), rec.get("comps"))

    def uv_texcoord(self, hash_int):
        """'TEXCOORD<n>' — mesh vertex semantic index from VS-linked shader mining."""
        rec = self.texture(hash_int)
        if not rec:
            return None
        uv = rec.get("uv")
        return f"TEXCOORD{uv[0]}" if uv else None

    def uv_scale_hashes(self, hash_int):
        """Scalar param hashes that the PS multiplies into this texture's sample UV."""
        rec = self.texture(hash_int)
        return rec.get("uv_scale") if rec else None

    def is_two_channel_normal(self, hash_int):
        rec = self.texture(hash_int)
        return bool(rec) and rec.get("normal_enc") == "2ch"

    def address_mode(self, hash_int):
        rec = self.texture(hash_int)
        return rec.get("address") if rec else None

    def used_components(self, hash_int):
        rec = self.texture(hash_int)
        return rec.get("comps") if rec else None

    def packs_opacity_channel(self, hash_int):
        """Texture PS comps include W on a normal/diffuse sample (necessary but not sufficient for BSDF alpha)."""
        rec = self.texture(hash_int)
        if not rec:
            return False
        comps = (rec.get("comps") or "").lower()
        if "w" not in comps:
            return False
        role = self.pbr_role(hash_int)
        return role in ("normal", "diffuse")

    def feature(self, name):
        return self._features.get(name)

    def bool_param_value(self, params, hash_ref):
        h = int(hash_ref, 16) if isinstance(hash_ref, str) else hash_ref
        p = params.get(h)
        if p is not None and getattr(p, "type", None) == 3:
            return bool(p.value)
        meta = self.scalar(h)
        default = meta.get("default") if meta else None
        if isinstance(default, bool):
            return default
        return None

    def _known_normal_w_switch_value(self, params):
        """Instance bool for a documented normal-W opacity switch, if present in shader cb."""
        for hx in _KNOWN_NORMAL_W_SWITCH_HASHES:
            meta = self._scalar.get(hx)
            if meta is None or meta.get("type") != 3:
                continue
            val = self.bool_param_value(params, int(hx, 16))
            return bool(val) if val is not None else False
        return None

    def normal_w_opacity_enabled(self, params, tex_hash):
        """Mined PS trace: normal map W channel feeds SV_Target alpha (grilles), not merely xyw comps."""
        feat = self.feature("normal_w_opacity")
        if not feat:
            return False
        mode = feat.get("mode", "never")
        targets = feat.get("targets") or []
        hx = _key(tex_hash)
        if targets and hx not in targets:
            return False
        if mode == "always":
            # Safety net when miner emitted always but shader has a documented opacity switch.
            sw_val = self._known_normal_w_switch_value(params)
            if sw_val is not None:
                return sw_val
            return True
        if mode == "never":
            return False
        if mode == "switch":
            sw = feat.get("switch")
            if not sw:
                return False
            val = self.bool_param_value(params, sw)
            return bool(val) if val is not None else False
        return False

    def gate_enabled(self, params, gate_name, default_when_unknown=False):
        """Bool switch for a mined gate name (opacity_mask, normal_map, …)."""
        feat = self.feature(gate_name)
        if feat:
            mode = feat.get("mode")
            if mode == "always":
                return True
            if mode == "never":
                return False
            if mode == "switch":
                sw = feat.get("switch")
                if sw:
                    val = self.bool_param_value(params, sw)
                    return bool(val) if val is not None else False
        for key, meta in self._scalar.items():
            if meta.get("gate") == gate_name and meta.get("type") == 3:
                val = self.bool_param_value(params, int(key, 16))
                return bool(val) if val is not None else False
        return default_when_unknown

    def color_scalars(self):
        """Cbuffer colour params (type 0 vec4 or type 1 rgba), sorted by cb_off."""
        out = []
        for key, meta in self._scalar.items():
            if meta.get("type") not in (0, 1):
                continue
            out.append((meta.get("cb_off", 9999), int(key, 16), meta.get("type"), meta.get("default")))
        out.sort(key=lambda row: row[0])
        return out

    def _resolve_color(self, params, off, h, typ, default):
        p = params.get(h)
        if p is not None and getattr(p, "type", None) in (0, 1):
            v = getattr(p, "value", None)
            if v is not None:
                return (v[0], v[1], v[2], 1.0 if v[3] == 0 else v[3])
        if isinstance(default, list) and len(default) >= 3:
            a = default[3] if len(default) > 3 else 1.0
            return (default[0], default[1], default[2], 1.0 if a == 0 else a)
        return None

    def primary_base_color(self, params, color_group_hash=0x73A9E2DF):
        """Albedo when there is no diffuse map.

        car_cf_*: type-0 vec4 @0 (~0.04). Leather/suede: type-1 @0 (~0.007).
        tiretread: type-0 only (~0.91 rubber gray). When type-1 exists, bright
        type-0 vec4s are lighting constants and are skipped in favour of type-1."""
        colors = self.color_scalars()
        if not colors:
            return None
        has_type1 = any(typ == 1 for _, _, typ, _ in colors)
        type0 = [(off, h, typ, default) for off, h, typ, default in colors if typ == 0]
        for off, h, typ, default in colors:
            if h == color_group_hash and type0 and off > type0[0][0]:
                continue
            if typ == 0:
                c = self._resolve_color(params, off, h, typ, default)
                if c is None:
                    continue
                if has_type1:
                    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
                    if lum > 0.5:
                        continue
                return c
            if typ == 1:
                c = self._resolve_color(params, off, h, typ, default)
                if c is None:
                    continue
                if c[0] > 0.98 and c[1] > 0.98 and c[2] > 0.98:
                    continue
                return c
        return None

    def diffuse_tint(self, params, color_group_hash=0x73A9E2DF):
        """Dark type-1 multiply for diffuse maps (velvet/alcantara). Never type-0 lighting constants."""
        for off, h, typ, default in self.color_scalars():
            if typ != 1 or h == color_group_hash:
                continue
            c = self._resolve_color(params, off, h, typ, default)
            if c is not None:
                if c[0] > 0.98 and c[1] > 0.98 and c[2] > 0.98:
                    continue
                return c
        return None


def lookup(shader_name):
    """ShaderDescriptor for a shader name, or None if the shader isn't in the table."""
    if not shader_name:
        return None
    table = _load()
    raw = table.get(shader_name)
    if raw is None:
        raw = table.get(shader_name.lower())
    if raw is None:
        alias = _SHADER_ALIASES.get(shader_name.lower())
        if alias:
            raw = table.get(alias)
    return ShaderDescriptor(shader_name, raw) if raw is not None else None


def has_table():
    return bool(_load())


def material_instance_key(pm):
    """Unique key for a parsed modelbin material (short names are reused across shaders)."""
    mobj = getattr(pm, "obj", None)
    if mobj is None:
        return getattr(pm, "name", None) or "material"
    base = getattr(pm, "name", None) or "material"
    if "|" in base:
        return base
    overrides = getattr(mobj, "override_hashes", None) or set()
    if overrides:
        tags = []
        for h in sorted(overrides):
            p = mobj.parameters.get(h)
            path = getattr(p, "path", "") if p else ""
            tag = swatch_key(path)
            tags.append(tag[:28] if tag else f"{h & 0xFFFFFFFF:08X}")
        if tags:
            return f"{base}|{','.join(tags[:2])}"
    return f"{base}|{mobj.shader_name or 'shader'}"

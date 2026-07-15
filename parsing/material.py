"""Material parsing: shader parameters, the material-system object, and role classification.

Pure module: no bpy. Parses materialbin/shaderbin parameter blobs into a hash->ShaderParameter
map, recovers the shader's default texture roles from swatchbin filename tokens, and exposes the
known CRC32 parameter-name table. Resolver is injected so parent-material resolution has no
module globals. (Names are only a labelling aid - the FH5 shaders ship without param names; the
data-driven roles/UV/packing come from data/material_table.json.)
"""

import os
import re
import struct

from .binary import BinaryStream, Bundle, Version, Tag


class ShaderParameterName:
    # bumperF
    CH1DiffuseTextureTexture = 0x10350BBC
    UseDiffuseAlphaBool = 0xD047A271
    CH1DiffuseTextureSwitchBool = 0x7169AC81
    CH1AlphaSwitchBool = 0xE159D67B
    CH1AlphaTextureTexture = 0x66E53F62
    UniqueBaseColorSwitchBool = 0xE1D827FD
    UniqueBaseTextureSwitchBool = 0x9615CAAA
    ColorGroupSwitchBool = 0x78004A9C
    UniqueBaseColorColorParam = 0xEA718FBE
    PaintColorGroupColorParam = 0x0014A502
    PaintColorColorParam = 0xC0CB2820
    FlakeGloss_floatVal = 0x99CC69B1
    # carpaint colour selector: True -> the car's unique/body colour, False -> this part's PaintColor
    # (lets wheels/hubs paint black while the body keeps its unique colour).
    UseUniqueBaseColorSwitchBool = 0xFF73057F

    # badge
    DiffuseTextureSwitchBool = 0x05A401E7
    CH1MaskSwitchBool = 0x08B2C17F
    DiffuseColorColorParam = 0x63040D89

    # emblem
    DiffuseATexture = 0x6DD98CD9
    CH1DiffColTextureSwitchBool = 0x04F8F9FA
    NormalTexture = 0x8C658791
    CH1OpacityMaskSwitchBool = 0xA6BF15E8
    CH1OpacitySwitchBool = 0xCBB3D988
    CH1GlossMaskSwitchBool = 0x5A0DA36A

    # radiator
    DiffuseColorGroupColorParam = 0xF51639BE
    GlossTexture = 0x7E4A41E1
    AlphaTexture = 0x57D9D49E
    GlossA_floatVal = 0x52E99DA3
    CH1NormalMapSwitchBool = 0x553D641D
    CH1LocalAOSwitchBool = 0xE876DDCC

    # clc_metalch2normalch1mask
    CH2DiffuseTextureTexture = 0x294DA6FC
    ColorGroupColorParam = 0x73A9E2DF
    CH2DiffuseTextureTiling = 0x519B26A1
    NormalTiling = 0x730F2086
    LocalAOSwitchBool = 0x6C03F944
    CH2NormalSwitchBool = 0x255EF28A

    # int_ch2_simplenormalao_glossvar (detail normal/diffuse tiling)
    NormalTilingB = 0x942CA044
    DiffuseTilingB = 0x1C77B084

    # CH2 normal-map W channel as opacity (fabric/leather family)
    CH2NormalOpacitySwitchBool = 0xBD65D78D
    # ext_grille / int_grille — instance bool gates whether W feeds cutout alpha
    GrilleNormalOpacitySwitchBool = 0x7487EB77

    # Metalness switches (data-driven, complementary across shader families)
    MetalnessSwitchBool = 0x989B026F
    NonMetalnessSwitchBool = 0x0BF3318B

    # Glossiness (smoothness) floats; high = shiny. roughness = 1 - smoothness.
    GlossSimple_floatVal = 0x5FF94E67
    GlossB_floatVal = 0xB9DE26A0

    # ch2diffnormglossalphaemissive
    uTile_floatVal = 0xB0B8947E
    vTile_floatVal = 0xCCD9B1A5
    DiffuseColorAColorParam = 0xEF5CCE09
    CH2GlossMaskSwitchBool = 0xF5A4EEA0
    CH2NormalMapSwitchBool = 0xFA9429D7
    CH2OpacityMaskSwitchBool = 0x9FC7B8A8

    # simplediffuse
    ColorColorParam = 0x57C321A6

    # ch1ch2normlerpch1glossdiff
    CH1GlossDiffMaskTexture = 0x022DF609

    # glass / window shaders (mined from car_window, int_simpleglass, glm_ch1bumpglass)
    GlassSurfaceColorParam = 0x8467AAA4   # rgba; .w = opacity on car_window
    GlassTintColorParam = 0x1925D9BF
    GlassInteriorTintColorParam = 0x1F30F777  # car_windsheild_interior
    GlassOpacityFloat = 0xC20EBA8D         # ~0.02 on int_simpleglass / glm bumpglass
    GlassSmoothnessFloat = 0x40CCF359
    GlassIORFloat = 0x09A23168
    GlassOpacityAltFloat = 0x07C3F168       # gls_ch1norm_alphaknockout family


_GUID_SUFFIX = re.compile(r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def classify_texture_role(path):
    """Map a swatchbin path to 'diffuse'/'normal'/'gloss'/'alpha'/'ao'/'lcao' or None."""
    name = os.path.basename(path).lower()
    name = name.rsplit(".", 1)[0]
    name = _GUID_SUFFIX.sub("", name)
    tokens = name.split("_")
    last = tokens[-1] if tokens else ""
    token_set = set(tokens)

    def has(*opts):
        return not token_set.isdisjoint(opts)

    if last in ("nrml", "nrm", "norm", "normal") or has("nrml", "nrm", "norm", "normal"):
        return "normal"
    if "rtint" in name or "reflectiontint" in name:
        return "rtint"
    if last == "lcao" or has("lcao"):
        return "lcao"
    # FH6 packed ORM (R=roughness, M=metalness, AO=ambient) — before bare "ao" / "alpha" heuristics.
    if last == "rmao" or has("rmao") or "rmao" in name or "roughmetalao" in name:
        return "rmao"
    if last == "ao" or has("ao"):
        return "ao"
    if last == "basecoloralpha" or has("basecoloralpha") or "basecoloralpha" in name:
        return "diffuse"
    if last in ("opac", "opc", "opacity", "alpha") or has("opac", "opacity", "alpha"):
        return "alpha"
    if "whitemask" in name:
        return "alpha"
    if "glossvariation" in name or "grungegloss" in name or (last == "mask" and has("glossvariation", "glossvar")):
        return "gloss_variation"
    if last in ("diff", "dif", "diffuse", "albedo", "basecolor", "col", "color") or has(
        "diff", "diffuse", "albedo", "basecolor"
    ):
        return "diffuse"
    if last in ("higt", "height") or has("higt"):
        return "gloss"
    if last in ("glos", "gloss", "rgh", "rough", "roughness", "spec") or has("glos", "gloss", "rgh", "rough", "roughness"):
        return "gloss"
    return None


def image_name(path):
    """Readable image name: drop folder, extension and trailing _<guid>."""
    name = os.path.basename(path)
    name = name.rsplit(".", 1)[0]
    return _GUID_SUFFIX.sub("", name) or name


class ShaderParameter:
    def __init__(self):
        self.hash = 0
        self.guid = None
        self.type = 0

    def deserialize(self, stream):
        version = Version()
        version.deserialize(stream)
        if version.major is None or version.minor is None or version.major > 10:
            raise ValueError(f"shader parameter stream desync (version={version})")
        # FH5: <=3.1; FH6 car materials commonly ship 3.4 with the same core layout.
        if not version.is_at_most(3, 4):
            print(f"Warning: Unsupported ShaderParameter version. Found: {version}. Max supported: 3.4")
        if not version.is_at_least(2, 0):
            print(f"Warning: Unsupported ShaderParameter version. Found: {version}. Min supported: 2.0")
        self.hash = stream.read_u32()
        if version.is_at_least(3, 1) and stream.read_u8() != 0:
            stream.seek(4, 1)
        self.type = stream.read_u8()
        if version.is_at_least(3, 0):
            self.guid = stream.read(16)
        self.value_stream = stream
        match self.type:
            case 0:
                self.value = (stream.read_f32(), stream.read_f32(), stream.read_f32(), stream.read_f32())
            case 5 | 9:
                stream.seek(16, 1)
            case 1:
                self.value = (stream.read_f32(), stream.read_f32(), stream.read_f32(), stream.read_f32())
            case 2:
                self.value = stream.read_f32()
            case 4:
                stream.seek(4, 1)
            case 3:
                self.value = stream.read_u32() != 0
            case 6:
                self.path = stream.read_7bit_string()
                stream.seek(4, 1)
            case 7:
                stream.seek(4 * 2, 1)
                if version.is_at_least(1, 1):
                    stream.seek(4, 1)
            case 8:
                length = stream.read_u32()
                stream.seek(4 * length, 1)
            case 11:
                self.value = (stream.read_f32(), stream.read_f32())
                if not version.is_at_least(2, 0):
                    stream.seek(8, 1)


class MaterialSystemObject:
    def __init__(self):
        self.parameters = {}
        self.shader_name = None
        self.default_texture_paths = set()
        self.default_texture_roles = {}
        self.override_hashes = set()

    def _ingest_parameter_blob(self, parameters_blob, mark_overrides=False):
        ver = parameters_blob.version
        if ver is None or getattr(ver, "major", None) is None:
            print("Warning: DFPR/MTPR blob missing version; skipping parameters.")
            return
        if not ver.is_at_most(2, 1):
            print(f"Warning: Unsupported 'DFPR' blob version. Found: {ver}. Max supported: 2.1")
        if not ver.is_at_least(2, 0):
            print(f"Warning: Unsupported 'DFPR' blob version. Found: {ver}. Min supported: 2.0")
        if ver.is_at_least(2, 1):
            parameters_length = parameters_blob.stream.read_u16()
        else:
            parameters_length = parameters_blob.stream.read_u8()
        for _ in range(parameters_length):
            try:
                parameter = ShaderParameter()
                parameter.deserialize(parameters_blob.stream)
            except (TypeError, struct.error, ValueError, EOFError) as e:
                print(f"Warning: stopped reading shader parameters early ({e})")
                break
            if getattr(parameter, "hash", None) is None:
                break
            self.parameters[parameter.hash] = parameter
            if mark_overrides:
                self.override_hashes.add(parameter.hash)

    def _load_shaderbin_defaults(self, path, resolver):
        """FH6 materialbins often parent directly to a .shaderbin (defaults live in DFPR)."""
        f_path = resolver.resolve(path) if not os.path.isfile(path) else path
        if not f_path or not os.path.isfile(f_path):
            print(f"Warning: shaderbin missing: {path!r} -> {f_path!r}")
            return
        self.shader_name = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]
        with open(f_path, "rb", 0) as f:
            s = BinaryStream(memoryview(f.read()))
        bundle = Bundle()
        bundle.deserialize(s)
        blobs = bundle.blobs[Tag.DFPR] or bundle.blobs[Tag.MTPR]
        if not blobs:
            print(f"Warning: no DFPR/MTPR in shaderbin {self.shader_name}")
            return
        self._ingest_parameter_blob(blobs[0], mark_overrides=False)
        self.override_hashes = set()
        self.default_texture_paths = {
            p.path for p in self.parameters.values()
            if getattr(p, "type", 0) == 6 and getattr(p, "path", "")
        }
        self.default_texture_roles = {
            h: classify_texture_role(p.path)
            for h, p in self.parameters.items()
            if getattr(p, "type", 0) == 6 and getattr(p, "path", "")
        }

    def deserialize(self, stream, resolver):
        bundle = Bundle()
        bundle.deserialize(stream)

        parent_blobs = bundle.blobs[Tag.MATI]
        if len(parent_blobs) == 0:
            parent_blobs = bundle.blobs[Tag.MATL]
        if len(parent_blobs) != 0:
            parent_blob = parent_blobs[0]
            parent_path = parent_blob.stream.read_7bit_string()
            low = parent_path.lower().replace("/", "\\")
            if low.endswith(".shaderbin"):
                self._load_shaderbin_defaults(parent_path, resolver)
            else:
                f_path = resolver.resolve(parent_path)
                if not f_path or not os.path.isfile(f_path):
                    print(f"Warning: parent material missing: {parent_path!r} -> {f_path!r}")
                else:
                    with open(f_path, "rb", 0) as f:
                        s = BinaryStream(memoryview(f.read()))
                    parent = MaterialSystemObject()
                    parent.deserialize(s, resolver)
                    self.shader_name = parent.shader_name
                    if self.shader_name is None:
                        name_meta = parent_blob.metadata.get(Tag.Name)
                        if name_meta is not None:
                            self.shader_name = name_meta.read_string()
                    self.parameters = parent.parameters
                    self.default_texture_paths = parent.default_texture_paths
                    self.default_texture_roles = parent.default_texture_roles

        shader_parameters_blobs = bundle.blobs[Tag.MTPR]
        if len(shader_parameters_blobs) == 0:
            shader_parameters_blobs = bundle.blobs[Tag.DFPR]
        if not shader_parameters_blobs:
            return
        had_parent = len(parent_blobs) != 0
        self._ingest_parameter_blob(shader_parameters_blobs[0], mark_overrides=had_parent)
        if not had_parent:
            self.override_hashes = set()
            self.default_texture_paths = {
                p.path for p in self.parameters.values()
                if getattr(p, "type", 0) == 6 and getattr(p, "path", "")
            }
            self.default_texture_roles = {
                h: classify_texture_role(p.path)
                for h, p in self.parameters.items()
                if getattr(p, "type", 0) == 6 and getattr(p, "path", "")
            }

"""Material parsing: MatI / materialbin / shaderbin -> named parameters + maps.

Fail-closed: missing parent materialbin/shaderbin raises MaterialParseError.
Local (library/shader defaults) and Instance (MatI overrides) are kept distinct.
TXMP/CBMP/SPMP come from the owning shaderbin.
"""

from __future__ import annotations

import os
import re
import struct

from .binary import BinaryStream, Bundle, Version, Tag


class MaterialParseError(RuntimeError):
    """Parent shaderbin/materialbin missing or unreadable."""


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
    # FTS: 0xFF73057F is LiverySwitchBool (not UseUniqueBaseColor).
    LiverySwitchBool = 0xFF73057F
    UseUniqueBaseColorSwitchBool = 0xFF73057F  # legacy alias → LiverySwitchBool
    UniqueLiverySwitchBool = 0xF17A77BF
    UserLiverySwitchBool = 0x8A88DE17
    WeaveColorTintA = 0xB0338A61
    WeaveColorTintB = 0x29D1EC60

    DiffuseTextureSwitchBool = 0x05A401E7
    CH1MaskSwitchBool = 0x08B2C17F
    DiffuseColorColorParam = 0x63040D89

    DiffuseATexture = 0x6DD98CD9
    CH1DiffColTextureSwitchBool = 0x04F8F9FA
    NormalTexture = 0x8C658791
    CH1OpacityMaskSwitchBool = 0xA6BF15E8
    CH1OpacitySwitchBool = 0xCBB3D988
    CH1GlossMaskSwitchBool = 0x5A0DA36A

    DiffuseColorGroupColorParam = 0xF51639BE
    GlossTexture = 0x7E4A41E1
    AlphaTexture = 0x57D9D49E
    GlossA_floatVal = 0x52E99DA3
    CH1NormalMapSwitchBool = 0x553D641D
    CH1LocalAOSwitchBool = 0xE876DDCC

    CH2DiffuseTextureTexture = 0x294DA6FC
    ColorGroupColorParam = 0x73A9E2DF
    CH2DiffuseTextureTiling = 0x519B26A1
    NormalTiling = 0x730F2086
    LocalAOSwitchBool = 0x6C03F944
    CH2NormalSwitchBool = 0x255EF28A

    NormalTilingB = 0x942CA044
    DiffuseTilingB = 0x1C77B084

    CH2NormalOpacitySwitchBool = 0xBD65D78D
    GrilleNormalOpacitySwitchBool = 0x7487EB77

    MetalnessSwitchBool = 0x989B026F
    NonMetalnessSwitchBool = 0x0BF3318B

    GlossSimple_floatVal = 0x5FF94E67
    GlossB_floatVal = 0xB9DE26A0

    uTile_floatVal = 0xB0B8947E
    vTile_floatVal = 0xCCD9B1A5
    DiffuseColorAColorParam = 0xEF5CCE09
    CH2GlossMaskSwitchBool = 0xF5A4EEA0
    CH2NormalMapSwitchBool = 0xFA9429D7
    CH2OpacityMaskSwitchBool = 0x9FC7B8A8

    ColorColorParam = 0x57C321A6

    CH1GlossDiffMaskTexture = 0x022DF609

    # FTS NameHashService: glass colors (0x8467AAA4 is g_CarUserColor0 — not glass).
    GlassSurfaceColorParam = 0x1925D9BF  # GlassColor
    GlassTintColorParam = 0x1925D9BF  # GlassColor
    GlassInteriorTintColorParam = 0x1F30F777  # GlassColor0ColorParam
    GlassRoughnessFloat = 0x80D4CB8B
    GlassSwitchBool = 0xA3E54BDF
    # Legacy aliases kept for callers; prefer GlassRoughnessFloat / GlassSwitchBool.
    GlassOpacityFloat = 0xC20EBA8D
    GlassSmoothnessFloat = 0x40CCF359
    GlassIORFloat = 0x09A23168
    GlassOpacityAltFloat = 0x07C3F168

    # Label / badge / decal alpha modes (FTS NameHashService).
    AlphaTransparencyBool = 0x5D3E6F2D
    UseAlphaTestBool = 0x265C042F
    UseAlphaBlendBool = 0x6CD3F5FE
    AlphaTestSwitchBool = 0xBC10D27C
    AlphaTestBool = 0xD34F21FE
    AlphaBlendBool = 0xF825D247


_GUID_SUFFIX = re.compile(
    r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def image_name(path):
    """Readable image name: drop folder, extension and trailing _<guid>."""
    name = os.path.basename(path)
    name = name.rsplit(".", 1)[0]
    return _GUID_SUFFIX.sub("", name) or name


def parse_register_map(blob) -> dict[int, int]:
    """TXMP/SPMP/CBMP: hash -> register or byte offset (u16). Entry = hash+u16+guid(16)."""
    if blob is None:
        return {}
    stream = blob.stream
    stream.seek(0)
    d = bytes(stream.read())
    if not d or len(d) < 2:
        return {}
    n = struct.unpack_from("<H", d, 0)[0]
    off = 2
    out: dict[int, int] = {}
    for _ in range(n):
        if off + 6 > len(d):
            break
        h = struct.unpack_from("<I", d, off)[0]
        v = struct.unpack_from("<H", d, off + 4)[0]
        out[h] = v
        off += 22
    return out


class ShaderParameter:
    def __init__(self):
        self.hash = 0
        self.guid = None
        self.type = 0
        self.name = None  # filled by NameHash when available

    def deserialize(self, stream):
        version = Version()
        version.deserialize(stream)
        if version.major is None or version.minor is None or version.major > 10:
            raise ValueError(f"shader parameter stream desync (version={version})")
        if not version.is_at_most(3, 4):
            raise MaterialParseError(
                f"unsupported ShaderParameter version {version} (max 3.4)"
            )
        if not version.is_at_least(2, 0):
            raise MaterialParseError(
                f"unsupported ShaderParameter version {version} (min 2.0)"
            )
        self.hash = stream.read_u32()
        if version.is_at_least(3, 1) and stream.read_u8() != 0:
            stream.seek(4, 1)
        self.type = stream.read_u8()
        if version.is_at_least(3, 0):
            self.guid = stream.read(16)
        self.value_stream = stream
        match self.type:
            case 0 | 1 | 5 | 9 | 12:
                # 0/1 vector/color; 5/9 swizzle/functionRange; 12 = FH6 vec4-sized slot
                self.value = (
                    stream.read_f32(),
                    stream.read_f32(),
                    stream.read_f32(),
                    stream.read_f32(),
                )
            case 2:
                self.value = stream.read_f32()
            case 4:
                self.value = stream.read_u32()
            case 3:
                self.value = stream.read_u32() != 0
            case 6:
                self.path = stream.read_7bit_string()
                if version.is_at_least(2, 0):
                    stream.seek(4, 1)
            case 7:
                # Sampler: AddressU/V (8). v1.1+ adds UnkType (4). FH6 v3+ adds 4 more.
                self.samp = bytes(stream.read(8))
                if version.is_at_least(3, 0):
                    self.samp4 = bytes(stream.read(8))
                elif version.is_at_least(1, 1):
                    self.samp4 = bytes(stream.read(4))
                else:
                    self.samp4 = b""
            case 8:
                length = stream.read_u32()
                stream.seek(16 * (length or 0), 1)
            case 11:
                self.value = (stream.read_f32(), stream.read_f32())
                if not version.is_at_least(2, 0):
                    stream.seek(8, 1)
            case _:
                raise MaterialParseError(
                    f"unsupported ShaderParameter type {self.type} "
                    f"(hash 0x{self.hash:08X}, ver {version})"
                )


class MaterialSystemObject:
    def __init__(self):
        # Merged view (Instance over Local) for callers that need a single map.
        self.parameters = {}
        self.parameters_local = {}
        self.parameters_instance = {}
        self.shader_name = None
        self.shader_game_path = None
        self.shaderbin_path = None  # resolved filesystem path
        self.txmp = {}  # hash -> texture register
        self.cbmp = {}  # hash -> cbuffer byte offset
        self.spmp = {}  # hash -> sampler register
        self.default_texture_paths = set()
        self.override_hashes = set()

    def _ingest_parameter_blob(self, parameters_blob, *, into: dict, mark_overrides=False):
        ver = parameters_blob.version
        if ver is None or getattr(ver, "major", None) is None:
            raise MaterialParseError("DFPR/MTPR blob missing version")
        if not ver.is_at_most(2, 1):
            raise MaterialParseError(f"unsupported DFPR/MTPR version {ver} (max 2.1)")
        if not ver.is_at_least(2, 0):
            raise MaterialParseError(f"unsupported DFPR/MTPR version {ver} (min 2.0)")
        stream = parameters_blob.stream
        if ver.is_at_least(2, 1):
            parameters_length = stream.read_u16()
        else:
            parameters_length = stream.read_u8()
        for _ in range(parameters_length or 0):
            # FH6 DFPR inserts zero padding between some parameters.
            while True:
                pos = stream.tell()
                b0 = stream.read_u8()
                if b0 is None:
                    return
                if b0 != 0:
                    stream.seek(pos)
                    break
            parameter = ShaderParameter()
            parameter.deserialize(stream)
            if getattr(parameter, "hash", None) is None:
                break
            into[parameter.hash] = parameter
            if mark_overrides:
                self.override_hashes.add(parameter.hash)

    def _label_names(self, params: dict):
        try:
            from ..materials.name_hashes import name_for_hash
        except ImportError:
            return
        for h, p in params.items():
            p.name = name_for_hash(h)

    def _rebuild_merged(self):
        self.parameters = dict(self.parameters_local)
        self.parameters.update(self.parameters_instance)
        self._label_names(self.parameters)
        self.default_texture_paths = {
            p.path
            for p in self.parameters_local.values()
            if getattr(p, "type", 0) == 6 and getattr(p, "path", "")
        }

    def _load_shader_maps(self, bundle: Bundle):
        tx = bundle.blobs[Tag.TXMP]
        cb = bundle.blobs[Tag.CBMP]
        sp = bundle.blobs[Tag.SPMP]
        if tx:
            self.txmp = parse_register_map(tx[0])
        if cb:
            self.cbmp = parse_register_map(cb[0])
        if sp:
            self.spmp = parse_register_map(sp[0])

    def _load_shaderbin(self, path, resolver):
        """Load DFPR defaults + TXMP/CBMP/SPMP from a .shaderbin. Fail if missing."""
        f_path = resolver.resolve(path) if not os.path.isfile(path) else path
        if not f_path or not os.path.isfile(f_path):
            raise MaterialParseError(f"shaderbin missing: {path!r} -> {f_path!r}")
        self.shader_game_path = path
        self.shaderbin_path = f_path
        self.shader_name = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]
        with open(f_path, "rb", 0) as f:
            s = BinaryStream(memoryview(f.read()))
        bundle = Bundle()
        bundle.deserialize(s)
        self._load_shader_maps(bundle)
        blobs = bundle.blobs[Tag.DFPR] or bundle.blobs[Tag.MTPR]
        if not blobs:
            raise MaterialParseError(f"no DFPR/MTPR in shaderbin {self.shader_name}")
        self.parameters_local.clear()
        self._ingest_parameter_blob(blobs[0], into=self.parameters_local, mark_overrides=False)
        self.override_hashes = set()
        self._rebuild_merged()

    def deserialize(self, stream, resolver):
        bundle = Bundle()
        bundle.deserialize(stream)

        parent_blobs = bundle.blobs[Tag.MATI]
        if len(parent_blobs) == 0:
            parent_blobs = bundle.blobs[Tag.MATL]
        if not parent_blobs:
            raise MaterialParseError("material has no MATI/MATL parent path")

        parent_blob = parent_blobs[0]
        parent_path = parent_blob.stream.read_7bit_string()
        low = parent_path.lower().replace("/", "\\")
        if low.endswith(".shaderbin"):
            self._load_shaderbin(parent_path, resolver)
        else:
            f_path = resolver.resolve(parent_path)
            if not f_path or not os.path.isfile(f_path):
                raise MaterialParseError(
                    f"parent material missing: {parent_path!r} -> {f_path!r}"
                )
            with open(f_path, "rb", 0) as f:
                s = BinaryStream(memoryview(f.read()))
            parent = MaterialSystemObject()
            parent.deserialize(s, resolver)
            self.shader_name = parent.shader_name
            self.shader_game_path = parent.shader_game_path
            self.shaderbin_path = parent.shaderbin_path
            self.txmp = dict(parent.txmp)
            self.cbmp = dict(parent.cbmp)
            self.spmp = dict(parent.spmp)
            self.parameters_local = dict(parent.parameters)
            if self.shader_name is None:
                name_meta = parent_blob.metadata.get(Tag.Name)
                if name_meta is not None:
                    self.shader_name = name_meta.read_string()
            if not self.shader_name or not self.shaderbin_path:
                raise MaterialParseError(
                    f"parent material {parent_path!r} did not resolve a shaderbin"
                )

        shader_parameters_blobs = bundle.blobs[Tag.MTPR]
        if len(shader_parameters_blobs) == 0:
            shader_parameters_blobs = bundle.blobs[Tag.DFPR]
        self.parameters_instance.clear()
        self.override_hashes = set()
        if shader_parameters_blobs:
            self._ingest_parameter_blob(
                shader_parameters_blobs[0],
                into=self.parameters_instance,
                mark_overrides=True,
            )
        self._rebuild_merged()

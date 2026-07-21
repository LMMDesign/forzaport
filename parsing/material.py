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
from .fts_mapping import (
    ShaderParameterMapping,
    parse_register_map,
    parse_shader_parameter_mapping,
)


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
    MaskedLiveryBool = 0xBEAA2F7C
    WeaveColorTintA = 0xB0338A61
    WeaveColorTintB = 0x29D1EC60
    WeaveMask = 0xBAA9FAA5
    WeaveNormal = 0xEC13FF23
    WeaveNormalIntensity = 0x4D033DB0
    # car_carbonfiber weave UV (CB reg14): proven NameHashes on this shaderbin.
    # Order in DXIL: TEXCOORD1 → rotate(UV_Orientation°) → * (U_Tiling, V_Tiling).
    # Do NOT use angleInDegrees_UVtransformationRef / scaler_UVtransformationRef —
    # those hashes are absent from car_carbonfiber MatI/CBMP.
    UVOrientation = 0x8B7343AB
    UTiling = 0x19A7D8F1
    VTiling = 0x4A3D8375
    # car_standard Base Color tint (CB reg1 / reg2 / reg19.x) — DXIL proven.
    BaseColorTint = 0x53A946B6
    BaseColorTintMode = 0x5EA395A8
    BaseColorTintMultiplier = 0x6B242133
    # Legacy / other-family UV-transform refs (not used by car_carbonfiber contract).
    AngleInDegreesUVTransformationRef = 0x48486772
    ScalerUVTransformationRef = 0x64F05F40
    XPanningUVTransformationRef = 0x45321B65
    YPanningUVTransformationRef = 0xC5C20C7A

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


# parse_register_map re-exported from fts_mapping (FTS version-aware).


class ShaderParameter:
    def __init__(self):
        self.hash = 0
        self.guid = None
        self.type = 0
        self.name = None  # filled by NameHash when available
        self.param_version = None
        self.path_hash = None  # Texture2D PathHash (param v2+)
        self.address_u = None
        self.address_v = None
        self.filter_or_unk_type = None  # FTS SamplerParameter.UnkType (v1.1+)
        self.gradient_stops = None  # ColorGradient stop count (type 8)

    def deserialize(self, stream):
        version = Version()
        version.deserialize(stream)
        self.param_version = version
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
                # (type 12 is ForzaPort-only relative to FTS enum; layout matches Vector4)
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
                # Texture2D — keep PathHash (FTS TextureParameter.PathHash)
                self.path = stream.read_7bit_string()
                if version.is_at_least(2, 0):
                    self.path_hash = stream.read_u32()
            case 7:
                # Sampler: AddressU/V (i32 each). v1.1+ UnkType/filter (i32).
                # FH6 v3+ may carry 4 extra bytes after UnkType (ForzaPort extension).
                self.address_u = stream.read_s32()
                self.address_v = stream.read_s32()
                self.samp = struct.pack(
                    "<ii", self.address_u or 0, self.address_v or 0
                )
                if version.is_at_least(3, 0):
                    self.filter_or_unk_type = stream.read_s32()
                    extra = bytes(stream.read(4))
                    self.samp4 = struct.pack("<i", self.filter_or_unk_type or 0) + extra
                elif version.is_at_least(1, 1):
                    self.filter_or_unk_type = stream.read_s32()
                    self.samp4 = struct.pack("<i", self.filter_or_unk_type or 0)
                else:
                    self.filter_or_unk_type = 1  # FTS default Linear
                    self.samp4 = b""
            case 8:
                length = stream.read_u32() or 0
                self.gradient_stops = length
                # Preserve stop data for schema dumps (FTS ColorGradientParameter).
                stops = []
                for _ in range(length):
                    stops.append(
                        (
                            stream.read_f32(),
                            stream.read_f32(),
                            stream.read_f32(),
                            stream.read_f32(),
                        )
                    )
                self.value = stops
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
        self.txmp = {}  # hash -> texture register (effective)
        self.cbmp = {}  # hash -> cbuffer byte offset (effective; legacy ×16 applied)
        self.spmp = {}  # hash -> sampler register (effective)
        self.txmp_mapping: ShaderParameterMapping | None = None
        self.cbmp_mapping: ShaderParameterMapping | None = None
        self.spmp_mapping: ShaderParameterMapping | None = None
        self.mtpr_trailer = None  # (unk1, unk2, unk3) CRC/footer for MTPR ≥2.0
        self.default_texture_paths = set()
        self.override_hashes = set()
        self.parent_material_path = None
        self.parent_path_v1_1 = None
        self.parent_path_v1_2 = None

    def _ingest_parameter_blob(self, parameters_blob, *, into: dict, mark_overrides=False):
        ver = parameters_blob.version
        if ver is None or getattr(ver, "major", None) is None:
            raise MaterialParseError("DFPR/MTPR blob missing version")
        if not ver.is_at_most(2, 1):
            raise MaterialParseError(f"unsupported DFPR/MTPR version {ver} (max 2.1)")
        if not ver.is_at_least(2, 0):
            raise MaterialParseError(f"unsupported DFPR/MTPR version {ver} (min 2.0)")
        stream = parameters_blob.stream
        stream.seek(0)
        if ver.is_at_least(2, 1):
            parameters_length = stream.read_u16()
        else:
            parameters_length = stream.read_u8()
        for _ in range(parameters_length or 0):
            # FH6 DFPR inserts zero padding between some parameters
            # (ForzaPort extension; FTS does not skip padding).
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
        # FTS: MTPR (not DFPR) ≥2.0 may append three uint32 footers.
        tag = int(getattr(parameters_blob, "tag", 0) or 0)
        if ver.is_at_least(2, 0) and tag == Tag.MTPR:
            remaining = (
                getattr(parameters_blob, "data_size", None)
                or (stream._stream.getbuffer().nbytes if hasattr(stream, "_stream") else 0)
            )
            # Prefer absolute remaining in this blob stream.
            try:
                blob_len = stream._stream.getbuffer().nbytes
                left = blob_len - stream.tell()
            except Exception:
                left = 0
            if left >= 12:
                self.mtpr_trailer = (
                    stream.read_u32(),
                    stream.read_u32(),
                    stream.read_u32(),
                )

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
            self.txmp_mapping = parse_shader_parameter_mapping(tx[0])
            self.txmp = self.txmp_mapping.as_hash_map()
        if cb:
            self.cbmp_mapping = parse_shader_parameter_mapping(cb[0])
            self.cbmp = self.cbmp_mapping.as_hash_map()
        if sp:
            self.spmp_mapping = parse_shader_parameter_mapping(sp[0])
            self.spmp = self.spmp_mapping.as_hash_map()

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
        parent_blob.stream.seek(0)
        parent_path = parent_blob.stream.read_7bit_string()
        self.parent_material_path = parent_path
        # MATL multi-path (FTS MatLBlob): PathV1_1 / PathV1_2
        pver = parent_blob.version or Version()
        if int(getattr(parent_blob, "tag", 0) or 0) == Tag.MATL:
            if pver.is_at_least(1, 1):
                self.parent_path_v1_1 = parent_blob.stream.read_7bit_string()
            if pver.is_at_least(1, 2):
                self.parent_path_v1_2 = parent_blob.stream.read_7bit_string()
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
            self.txmp_mapping = parent.txmp_mapping
            self.cbmp_mapping = parent.cbmp_mapping
            self.spmp_mapping = parent.spmp_mapping
            self.mtpr_trailer = parent.mtpr_trailer
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

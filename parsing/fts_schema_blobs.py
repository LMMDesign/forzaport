"""FTS-parity parsers for LSCE / TRGT / VERS / VARS and BLEN / VDCL / ARTX metadata.

Adapted from ForzaTools.Bundles (MIT, Copyright (c) 2023 Nenkai).
See ``THIRD_PARTY.md`` and ForzaTechStudio LICENSE. UI/viewport heuristics are
intentionally not ported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .binary import BinaryStream, Tag, Version


@dataclass
class PlatformHash:
    platform: int
    hash_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "hash_hex": self.hash_bytes.hex() if self.hash_bytes else "",
        }


@dataclass
class VertexShaderEntry:
    anim_scenario_flags: int = 0
    path: str = ""
    vs_hash_dummy: bytes | None = None
    vs_hash: bytes | None = None
    vs_platform_hashes: list[PlatformHash] = field(default_factory=list)
    instanced_path: str | None = None
    instanced_vs_hash_dummy: bytes | None = None
    instanced_vs_hash: bytes | None = None
    instanced_platform_hashes: list[PlatformHash] = field(default_factory=list)
    skipped_instanced_ps_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "anim_scenario_flags": self.anim_scenario_flags,
            "path": self.path,
            "vs_platform_hashes": [p.to_dict() for p in self.vs_platform_hashes],
            "vs_hash_hex": self.vs_hash.hex() if self.vs_hash else None,
            "instanced_path": self.instanced_path,
            "instanced_platform_hashes": [
                p.to_dict() for p in self.instanced_platform_hashes
            ],
            "instanced_vs_hash_hex": (
                self.instanced_vs_hash.hex() if self.instanced_vs_hash else None
            ),
        }


@dataclass
class LightScenarioBlob:
    version: Version
    is_inline: bool = False
    scenarios: list[LightScenario] = field(default_factory=list)
    source: str = "ForzaTools.Bundles.LightScenarioBlob (MIT/Nenkai)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": str(self.version),
            "is_inline": self.is_inline,
            "scenario_count": len(self.scenarios),
            "scenarios": [s.to_dict() for s in self.scenarios],
            "source": self.source,
        }


def _read_platform_hashes(stream: BinaryStream) -> list[PlatformHash]:
    count = stream.read_u8() or 0
    out: list[PlatformHash] = []
    for _ in range(count):
        platform = stream.read_u8() or 0
        out.append(PlatformHash(platform=platform, hash_bytes=bytes(stream.read(32))))
    return out


@dataclass
class LightScenario:
    name: str = ""
    secondary_name: str | None = None  # FH6 DXR hit-group name when present
    version: int = 0
    has_instanced_data: bool = False
    anim_count: int = 1
    vertex_shaders: list[VertexShaderEntry] = field(default_factory=list)
    geometry_pixel_shader: str = ""
    shader_stage_bits: int = 0x11  # VS|PS default (FTS)
    fh6_anim0_unk: int | None = None  # u32 after animCount==0 (FH6 extension)
    is_dxr_stub: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "secondary_name": self.secondary_name,
            "version": self.version,
            "has_instanced_data": self.has_instanced_data,
            "anim_count": self.anim_count,
            "fh6_anim0_unk": self.fh6_anim0_unk,
            "is_dxr_stub": self.is_dxr_stub,
            "shader_stage_bits": self.shader_stage_bits,
            "geometry_pixel_shader": self.geometry_pixel_shader,
            "vertex_shaders": [v.to_dict() for v in self.vertex_shaders],
        }


def _peek_hit_group_name(stream: BinaryStream) -> bool:
    """True if next 7-bit string is an FH6 DXR hit-group name (before Version u32)."""
    pos = stream.tell()
    try:
        first = stream.read_u8()
        stream.seek(pos)
        if first is None or first == 0 or first > 200:
            return False
        # Version u32 for normal scenarios is a small integer (bytes like 07 00 00 00).
        # A hit-group name starts with a length byte then ASCII (e.g. 'S' of SimpleHit).
        s = stream.read_7bit_string() or ""
        stream.seek(pos)
        return bool(
            s.startswith("SimpleHit")
            or s.startswith("AnyHit")
            or s.startswith("ClosestHit")
            or "Hit_" in s
        )
    except Exception:
        stream.seek(pos)
        return False


def _peek_starts_with_game_path(stream: BinaryStream) -> bool:
    pos = stream.tell()
    try:
        s = stream.read_7bit_string() or ""
        stream.seek(pos)
        return s.startswith("Game:") or s.startswith("game:")
    except Exception:
        stream.seek(pos)
        return False


def _peek_scenario_name(stream: BinaryStream) -> bool:
    """True if next field looks like an LSCE scenario name string."""
    pos = stream.tell()
    try:
        first = stream.read_u8()
        stream.seek(pos)
        if first is None or first < 4 or first > 120:
            return False
        s = stream.read_7bit_string() or ""
        stream.seek(pos)
        return "Scenario" in s or s.startswith("DXR_")
    except Exception:
        stream.seek(pos)
        return False


def parse_light_scenario_blob(blob) -> LightScenarioBlob | None:
    """Parse LSCE / DBLS (FTS LightScenarioBlob + FH6 extensions).

    FTS baseline: ForzaTools.Bundles.LightScenarioBlob (MIT, Copyright (c) 2023 Nenkai).

    FH6 extensions confirmed on real ``*.shaderbin`` LSCE v1.10:
      - AnimCount==0 still emits ``u32 unk`` + one VS path + platform hashes + stage + GPS,
        without AnimScenarioFlags and without the instanced VS block.
      - Stub rows (DXR hit-groups and some F++ variants): Name [+ hit-group] + version + u32,
        with no VS/GPS payload.
    """
    if blob is None:
        return None
    import struct

    ver = blob.version or Version()
    stream = blob.stream
    stream.seek(0)
    is_inline = False
    if ver.is_at_least(1, 1):
        is_inline = bool(stream.read_u8())
    count = stream.read_u8() or 0
    scenarios: list[LightScenario] = []
    for _ in range(count):
        ls = LightScenario()
        ls.name = stream.read_7bit_string() or ""
        if _peek_hit_group_name(stream):
            ls.secondary_name = stream.read_7bit_string() or ""
        ls.version = stream.read_u32() or 0

        # Stub detection: after version, only a trailing u32 before the next scenario name.
        pos_after_version = stream.tell()
        trail = stream.read(4)
        if trail and len(trail) == 4:
            at_eof = False
            try:
                at_eof = stream.tell() >= stream._stream.getbuffer().nbytes
            except Exception:
                at_eof = False
            if (at_eof or _peek_scenario_name(stream)) and not _peek_starts_with_game_path(
                stream
            ):
                ls.is_dxr_stub = True
                ls.fh6_anim0_unk = struct.unpack("<I", trail)[0]
                ls.anim_count = 0
                scenarios.append(ls)
                continue
        stream.seek(pos_after_version)

        if ver.is_at_least(1, 4):
            # FTS: bool HasInstancedData. FH6 stores a small integer: 1 = one
            # VS/GPS pair, 2 = primary + extra pair (often DXR / alternate path).
            ls.has_instanced_data = bool(stream.read_u8())
            stream.seek(stream.tell() - 1)
            path_pair_count = stream.read_u8() or 1
        else:
            path_pair_count = 1
        anim_count = 1
        if ver.is_at_least(1, 2):
            anim_count = stream.read_u32() or 0
        ls.anim_count = anim_count

        def _read_vs_plat() -> VertexShaderEntry:
            vs = VertexShaderEntry()
            vs.path = stream.read_7bit_string() or ""
            if ver.is_at_least(1, 6):
                vs.vs_platform_hashes = _read_platform_hashes(stream)
            elif ver.is_at_least(1, 5):
                vs.vs_hash_dummy = bytes(stream.read(32))
                vs.vs_hash = bytes(stream.read(32))
            return vs

        def _read_stage_gps() -> None:
            ls.shader_stage_bits = 0x11
            if ver.is_at_least(1, 3):
                raw_bits = stream.read(4)
                if raw_bits and len(raw_bits) == 4:
                    ls.shader_stage_bits = struct.unpack("<i", raw_bits)[0]
            ls.geometry_pixel_shader = stream.read_7bit_string() or ""

        if anim_count == 0 and ver.is_at_least(1, 6):
            unk_raw = stream.read(4)
            if unk_raw and len(unk_raw) == 4:
                ls.fh6_anim0_unk = struct.unpack("<I", unk_raw)[0]
            ls.vertex_shaders.append(_read_vs_plat())
            _read_stage_gps()
            primary_gps = ls.geometry_pixel_shader
            primary_stage = ls.shader_stage_bits
            # Extra path pairs when the FH6 count byte is 2+ (FTS bool was 0/1 only).
            # Not every scenario with count==2 actually emits extras (e.g. ray-tracing
            # rows may jump straight to the next scenario / DXR stub).
            for extra_i in range(max(0, int(path_pair_count) - 1)):
                # Ray-tracing stage bit patterns often omit the extra pair payload.
                if (primary_stage & 0x20) != 0:
                    break
                if _peek_scenario_name(stream) or _peek_hit_group_name(stream):
                    break
                pos_extra = stream.tell()
                extra_flags = stream.read_u8() or 0
                stream.read(4)
                if not _peek_starts_with_game_path(stream):
                    stream.seek(pos_extra)
                    break
                vs = _read_vs_plat()
                vs.anim_scenario_flags = extra_flags
                _read_stage_gps()
                vs.instanced_path = ls.geometry_pixel_shader
                ls.vertex_shaders.append(vs)
            ls.geometry_pixel_shader = primary_gps
            ls.shader_stage_bits = primary_stage
        else:
            for _v in range(anim_count):
                vs = VertexShaderEntry()
                if ver.is_at_least(1, 2):
                    vs.anim_scenario_flags = stream.read_u8() or 0
                vs.path = stream.read_7bit_string() or ""
                if ver.is_at_least(1, 6):
                    vs.vs_platform_hashes = _read_platform_hashes(stream)
                elif ver.is_at_least(1, 5):
                    vs.vs_hash_dummy = bytes(stream.read(32))
                    vs.vs_hash = bytes(stream.read(32))
                if ls.has_instanced_data:
                    vs.instanced_path = stream.read_7bit_string() or ""
                    if ver.is_at_least(1, 6):
                        vs.instanced_platform_hashes = _read_platform_hashes(stream)
                    elif ver.is_at_least(1, 5):
                        vs.instanced_vs_hash_dummy = bytes(stream.read(32))
                        vs.instanced_vs_hash = bytes(stream.read(32))
                        vs.skipped_instanced_ps_path = stream.read_7bit_string() or ""
                        if ver.is_at_least(1, 6):
                            stream.read_u8()
                ls.vertex_shaders.append(vs)
            _read_stage_gps()

        scenarios.append(ls)
    return LightScenarioBlob(version=ver, is_inline=is_inline, scenarios=scenarios)


@dataclass
class RenderTargetEntry:
    vertex_shader_name: str = ""
    pixel_shader_name: str = ""
    vs_platform_count: int = 2
    ps_platform_count: int = 2
    dxbc_bytes_vs: bytes = b""
    dxbc_bytes_ps: bytes = b""

    def to_dict(self) -> dict[str, Any]:
        return {
            "vertex_shader_name": self.vertex_shader_name,
            "pixel_shader_name": self.pixel_shader_name,
            "vs_platform_count": self.vs_platform_count,
            "ps_platform_count": self.ps_platform_count,
            "dxbc_vs_bytes": len(self.dxbc_bytes_vs),
            "dxbc_ps_bytes": len(self.dxbc_bytes_ps),
            "dxbc_vs_sha256_prefix": (
                __import__("hashlib").sha256(self.dxbc_bytes_vs).hexdigest()[:16]
                if self.dxbc_bytes_vs
                else None
            ),
            "dxbc_ps_sha256_prefix": (
                __import__("hashlib").sha256(self.dxbc_bytes_ps).hexdigest()[:16]
                if self.dxbc_bytes_ps
                else None
            ),
        }


@dataclass
class RenderTargetBlob:
    version: Version
    is_inline: bool = False
    entry_count_raw: int = 0
    entries: list[RenderTargetEntry] = field(default_factory=list)
    source: str = "ForzaTools.Bundles.RenderTargetBlob (MIT/Nenkai)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": str(self.version),
            "is_inline": self.is_inline,
            "entry_count_raw": self.entry_count_raw,
            "entries": [e.to_dict() for e in self.entries],
            "source": self.source,
        }


def _read_dxbc_block(stream: BinaryStream) -> bytes:
    import struct

    magic = stream.read_u32()
    if magic != 0x43425844:  # 'DXBC'
        raise ValueError(f"Expected DXBC magic, got 0x{(magic or 0):08X}")
    checksum = bytes(stream.read(16))
    version = stream.read_u32() or 0
    total_size = stream.read_u32() or 0
    remaining = max(0, int(total_size) - 28)
    body = bytes(stream.read(remaining)) if remaining else b""
    return (
        struct.pack("<I", magic)
        + checksum
        + struct.pack("<II", version, total_size)
        + body
    )


def parse_render_target_blob(blob) -> RenderTargetBlob | None:
    """Parse TRGT (FTS RenderTargetBlob)."""
    if blob is None:
        return None
    ver = blob.version or Version()
    stream = blob.stream
    stream.seek(0)
    is_inline = False
    if ver.is_at_least(1, 1):
        is_inline = bool(stream.read_u8())
    entry_count = stream.read_u8() or 0
    out = RenderTargetBlob(
        version=ver, is_inline=is_inline, entry_count_raw=entry_count
    )
    if entry_count == 0:
        return out
    try:
        for _ in range(entry_count):
            vs_name = stream.read_7bit_string() or ""
            vs_plat = 2
            if ver.is_at_least(1, 3):
                vs_plat = stream.read_u8() or 0
            dxbc_vs = _read_dxbc_block(stream) if is_inline else b""
            ps_name = stream.read_7bit_string() or ""
            ps_plat = 2
            if ver.is_at_least(1, 3):
                ps_plat = stream.read_u8() or 0
            dxbc_ps = _read_dxbc_block(stream) if is_inline else b""
            out.entries.append(
                RenderTargetEntry(
                    vertex_shader_name=vs_name,
                    pixel_shader_name=ps_name,
                    vs_platform_count=vs_plat,
                    ps_platform_count=ps_plat,
                    dxbc_bytes_vs=dxbc_vs,
                    dxbc_bytes_ps=dxbc_ps,
                )
            )
    except Exception:
        # FTS swallows parse failures and keeps partial entries.
        pass
    return out


@dataclass
class VersBlob:
    version: Version
    unk: int = 0
    path: str = ""
    source: str = "ForzaTools.Bundles.VersBlob (MIT/Nenkai)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": str(self.version),
            "unk": self.unk,
            "path": self.path,
            "source": self.source,
        }


def parse_vers_blob(blob) -> VersBlob | None:
    if blob is None:
        return None
    stream = blob.stream
    stream.seek(0)
    return VersBlob(
        version=blob.version or Version(),
        unk=stream.read_u32() or 0,
        path=stream.read_7bit_string() or "",
    )


@dataclass
class VarsBlob:
    version: Version
    data: bytes = b""
    source: str = "ForzaTools.Bundles.VarsBlob (MIT/Nenkai) — opaque"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": str(self.version),
            "byte_length": len(self.data),
            "opaque": True,
            "source": self.source,
        }


def parse_vars_blob(blob) -> VarsBlob | None:
    if blob is None:
        return None
    stream = blob.stream
    stream.seek(0)
    return VarsBlob(version=blob.version or Version(), data=bytes(stream.read()))


# --- Metadata (on blob.metadata) -------------------------------------------------


@dataclass
class BlendMetadata:
    version: int
    unk1: bool = False
    unk2: bool = False
    source: str = "ForzaTools.Bundles.BlendMetadata (MIT/Nenkai)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "unk1": self.unk1,
            "unk2": self.unk2,
            "source": self.source,
        }


def parse_blend_metadata(meta) -> BlendMetadata | None:
    if meta is None:
        return None
    ver = int(getattr(meta, "version", 0) or 0)
    stream = meta.stream
    stream.seek(0)
    unk1 = unk2 = False
    if ver == 1:
        unk1 = bool(stream.read_u8())
        unk2 = bool(stream.read_u8())
    return BlendMetadata(version=ver, unk1=unk1, unk2=unk2)


@dataclass
class VdclEntry:
    name_hash: int
    vertex_input_flags: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name_hash": f"0x{self.name_hash & 0xFFFFFFFF:08X}",
            "vertex_input_flags": self.vertex_input_flags,
            # Bitfield per FTS VDCLEntry comments:
            # 0..4 TEXCOORD0..4, 5 TEXCOORD5/TANGENT0, 6..9 TANGENT1..4, 10 COLOR0
        }


@dataclass
class VdclMetadata:
    version: int
    entries: list[VdclEntry] = field(default_factory=list)
    source: str = "ForzaTools.Bundles.VDCLMetadata (MIT/Nenkai)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entries": [e.to_dict() for e in self.entries],
            "source": self.source,
        }


def parse_vdcl_metadata(meta) -> VdclMetadata | None:
    if meta is None:
        return None
    ver = int(getattr(meta, "version", 0) or 0)
    stream = meta.stream
    stream.seek(0)
    entries: list[VdclEntry] = []
    if ver >= 2:
        outer_count = 1
        if ver >= 3:
            outer_count = stream.read_s32() or 0
        for _o in range(outer_count):
            if ver >= 4:
                stream.read_u16()  # permutation index
            inner = stream.read_s32() or 0
            for _i in range(inner):
                nh = stream.read_u32() or 0
                flags = stream.read_s32() or 0
                entries.append(
                    VdclEntry(name_hash=nh, vertex_input_flags=flags & 0xFFFFFFFF)
                )
    return VdclMetadata(version=ver, entries=entries)


@dataclass
class ArtxMetadata:
    version: int
    unknown_v1: int = 0
    unused_v2: int = 0
    enable_after_pixel_depth: int = 0
    flags_v3: int = 0
    unknown_v4: int = 0
    unknown_v5: int = 0
    source: str = "ForzaTools.Bundles.ARTXMetadata (MIT/Nenkai)"

    @property
    def has_flags_v3(self) -> bool:
        return self.version >= 3 and self.enable_after_pixel_depth != 0

    @property
    def force_after_pixel_depth(self) -> bool:
        return (self.flags_v3 & 0x08) != 0

    @property
    def mode_code_v3(self) -> int:
        has_bit1 = (self.flags_v3 & 0x02) != 0
        has_bit2 = (self.flags_v3 & 0x04) != 0
        if has_bit1:
            return 33 if has_bit2 else 35
        return 34 if has_bit2 else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "unknown_v1": self.unknown_v1,
            "unused_v2": self.unused_v2,
            "enable_after_pixel_depth": self.enable_after_pixel_depth,
            "flags_v3": self.flags_v3,
            "unknown_v4": self.unknown_v4,
            "unknown_v5": self.unknown_v5,
            "force_after_pixel_depth": self.force_after_pixel_depth,
            "mode_code_v3": self.mode_code_v3,
            "source": self.source,
        }


def parse_artx_metadata(meta) -> ArtxMetadata | None:
    if meta is None:
        return None
    ver = int(getattr(meta, "version", 0) or 0)
    stream = meta.stream
    stream.seek(0)
    out = ArtxMetadata(version=ver)
    if ver >= 1:
        out.unknown_v1 = stream.read_u8() or 0
    if ver >= 2:
        out.unused_v2 = stream.read_u8() or 0
        out.enable_after_pixel_depth = stream.read_u8() or 0
    if out.has_flags_v3:
        out.flags_v3 = stream.read_u8() or 0
    if ver >= 4:
        out.unknown_v4 = stream.read_u8() or 0
    if ver >= 5:
        out.unknown_v5 = stream.read_u8() or 0
    return out


def collect_blob_metadata(blob) -> dict[str, Any]:
    """Parse known FTS metadata tags attached to a bundle blob."""
    if blob is None:
        return {}
    md = getattr(blob, "metadata", None) or {}
    out: dict[str, Any] = {}
    if Tag.BLEN in md:
        b = parse_blend_metadata(md[Tag.BLEN])
        if b:
            out["blen"] = b.to_dict()
    if Tag.VDCL in md:
        v = parse_vdcl_metadata(md[Tag.VDCL])
        if v:
            out["vdcl"] = v.to_dict()
    if Tag.ARTX in md:
        a = parse_artx_metadata(md[Tag.ARTX])
        if a:
            out["artx"] = a.to_dict()
    return out

"""FTS-parity register/parameter mapping parsers (TXMP / CBMP / SPMP).

Adapted from ForzaTools.Bundles ``ShaderParameterMappingBlob`` (MIT,
Copyright (c) 2023 Nenkai). See ``THIRD_PARTY.md`` / ForzaTechStudio LICENSE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .binary import Tag, Version


@dataclass
class MappingEntry:
    name_hash: int | None = None
    name: str | None = None  # v1.0 only
    id_or_offset: int = 0
    guid: bytes | None = None
    is_legacy_register_offset: bool = False

    @property
    def effective_byte_offset(self) -> int:
        """CBMP v≤1.0 stores float4 register units → multiply by 16 (FTS/engine)."""
        if self.is_legacy_register_offset:
            return int(self.id_or_offset) * 16
        return int(self.id_or_offset)


@dataclass
class ShaderParameterMapping:
    tag: int
    version: Version
    entries: list[MappingEntry] = field(default_factory=list)
    cbuffer_byte_size: int = 0
    source: str = "ForzaTools.Bundles.ShaderParameterMappingBlob (MIT/Nenkai)"

    def as_hash_map(self) -> dict[int, int]:
        """hash → effective value (register or byte offset)."""
        out: dict[int, int] = {}
        for e in self.entries:
            if e.name_hash is not None:
                out[int(e.name_hash) & 0xFFFFFFFF] = e.effective_byte_offset
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": f"0x{self.tag:08X}",
            "version": str(self.version),
            "entry_count": len(self.entries),
            "cbuffer_byte_size": self.cbuffer_byte_size,
            "legacy_cbmp_scale": any(e.is_legacy_register_offset for e in self.entries),
            "source": self.source,
            "entries": [
                {
                    "name_hash": (
                        f"0x{e.name_hash & 0xFFFFFFFF:08X}" if e.name_hash is not None else None
                    ),
                    "name": e.name,
                    "id_or_offset": e.id_or_offset,
                    "effective_byte_offset": e.effective_byte_offset,
                    "is_legacy_register_offset": e.is_legacy_register_offset,
                    "has_guid": e.guid is not None,
                }
                for e in self.entries
            ],
        }


def parse_shader_parameter_mapping(blob) -> ShaderParameterMapping:
    """Parse one TXMP/CBMP/SPMP blob with FTS version rules.

    Rules (from FTS ShaderParameterMappingBlob):
      - count: uint16 if blob ≥3.1 else uint8
      - entry ≥2.0: NameHash u32 + IdOrOffset u16; + Guid(16) if ≥3.0
      - entry <2.0: Name 7-bit string + IdOrOffset u8
      - CBMP (and all maps) at blob ≤1.0: IdOrOffset is float4 units → *16 bytes
    """
    if blob is None:
        return ShaderParameterMapping(tag=0, version=Version())
    ver = blob.version or Version()
    stream = blob.stream
    stream.seek(0)
    if ver.is_at_least(3, 1):
        count = stream.read_u16() or 0
    else:
        count = stream.read_u8() or 0

    legacy = not ver.is_at_least(1, 1)
    entries: list[MappingEntry] = []
    for _ in range(count):
        entry = MappingEntry(is_legacy_register_offset=legacy)
        if ver.is_at_least(2, 0):
            entry.name_hash = stream.read_u32()
            entry.id_or_offset = stream.read_u16() or 0
            if ver.is_at_least(3, 0):
                entry.guid = bytes(stream.read(16))
        else:
            entry.name = stream.read_7bit_string()
            entry.id_or_offset = stream.read_u8() or 0
        entries.append(entry)

    cbuf = 0
    if entries:
        max_off = max(e.effective_byte_offset for e in entries)
        raw = max_off + 16
        cbuf = (raw + 15) & ~15

    return ShaderParameterMapping(
        tag=int(getattr(blob, "tag", 0) or 0),
        version=ver,
        entries=entries,
        cbuffer_byte_size=cbuf,
    )


def parse_register_map(blob) -> dict[int, int]:
    """Back-compat: hash → effective register/byte offset (FTS-parity)."""
    return parse_shader_parameter_mapping(blob).as_hash_map()

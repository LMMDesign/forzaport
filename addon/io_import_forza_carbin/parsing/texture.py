"""Swatchbin -> DDS decoder and RGBA decompressor.

Pure module: no bpy. Reads a ForzaTech swatchbin or multi-texture .pb (TXCB blobs),
detects PC vs Durango platform, decodes block-compressed mip0 to RGBA8, and can still
build an in-memory DDS buffer for Blender fallback (BC6H / Durango without xg.dll).
"""

import os
import re
import struct
from uuid import UUID

from .binary import BinaryStream, Bundle, Tag, Version
from .bcdecode import decode_to_rgba8, rgba8_to_float_pixels

_GUID_IN_PATH = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

_DURANGO_WARNED: set[str] = set()


def _guid_from_path(path: str) -> str | None:
    m = _GUID_IN_PATH.search(os.path.basename(path))
    if not m:
        return None
    return "{" + m.group(1).upper() + "}"


def _read_txch_guid(header_stream: BinaryStream) -> str:
    header_stream.seek(0)
    header_stream.seek(4 + 4, 1)
    raw = header_stream.read(16)
    if len(raw) != 16:
        return ""
    return "{" + str(UUID(bytes_le=raw)).upper() + "}"


def _parse_txch(blob) -> dict:
    header_stream = blob.metadata[Tag.TXCH].stream
    header_stream.seek(0)
    header_stream.seek(4 + 4, 1)
    guid = "{" + str(UUID(bytes_le=header_stream.read(16))).upper() + "}"
    width = struct.unpack("<I", header_stream.read(4))[0]
    height = struct.unpack("<I", header_stream.read(4))[0]
    header_stream.seek(4 + 2, 1)
    mip_levels = header_stream.read_u8()
    header_stream.seek(1, 1)
    transcoding = header_stream.read_u32()
    header_stream.seek(4, 1)
    color_profile = header_stream.read_u32()
    header_stream.seek(4 + 8, 1)
    encoding = header_stream.read_u32()
    header_stream.seek(8, 1)
    linear_size = header_stream.read(4)
    format_encoded = encoding if transcoding <= 1 else transcoding - 2
    match format_encoded:
        case 0:
            fmt = 72 if color_profile else 71
        case 1:
            fmt = 75 if color_profile else 74
        case 2:
            fmt = 78 if color_profile else 77
        case 3:
            fmt = 80
        case 4:
            fmt = 81
        case 5:
            fmt = 83
        case 6:
            fmt = 84
        case 7:
            fmt = 95
        case 8:
            fmt = 96
        case 9:
            fmt = 99 if color_profile else 98
        case 13:
            fmt = 29 if color_profile else 28
        case _:
            fmt = 0
            print(f"Warning: Unknown texture format (encoding={format_encoded}).")
    return {
        "guid": guid,
        "width": width,
        "height": height,
        "mip_levels": mip_levels,
        "encoding": format_encoded,
        "format": fmt,
        "color_profile": color_profile,
        "linear_size": linear_size,
    }


def _pick_txcb_blob(blobs: list, path: str) -> object:
    """Select the TXCB blob matching the path GUID, or the sole / first blob."""
    if not blobs:
        raise ValueError("bundle has no TXCB blobs")
    if len(blobs) == 1:
        return blobs[0]
    want = _guid_from_path(path)
    if want:
        for blob in blobs:
            try:
                if _read_txch_guid(blob.metadata[Tag.TXCH].stream) == want:
                    return blob
            except (KeyError, AttributeError):
                continue
        print(f"Warning: no TXCB blob matches GUID {want} in {os.path.basename(path)}; using first blob.")
    else:
        print(f"Warning: multi-texture bundle {os.path.basename(path)} has {len(blobs)} TXCB blobs; using first.")
    return blobs[0]


def _warn_durango(path: str, blob_version: Version) -> bool:
    if not blob_version.is_at_least(2, 0):
        return False
    key = os.path.basename(path)
    if key not in _DURANGO_WARNED:
        _DURANGO_WARNED.add(key)
        print(
            f"Warning: Durango/Xbox texture (TXCB v{blob_version}) in {key}. "
            "Pixel data is GPU-tiled; decode may be wrong without xg.dll detiling. "
            "Convert to PC format with ForzaTech Studio if textures look corrupted."
        )
    return True


def _build_dds(header: dict, pixel_data: bytes) -> bytes:
    height = struct.pack("<I", header["height"])
    width = struct.pack("<I", header["width"])
    linear_size = header["linear_size"]
    if isinstance(linear_size, int):
        linear_size = struct.pack("<I", linear_size)
    mip_levels = bytes([header["mip_levels"] or 1])
    fmt = header["format"]
    return b"".join([
        b"\x44\x44\x53\x20\x7C\x00\x00\x00\x07\x10\x0A\x00", height,
        width, linear_size, b"\x01\x00\x00\x00", mip_levels, b"\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x20\x00\x00\x00",
        b"\x04\x00\x00\x00\x44\x58\x31\x30\x00\x00\x00\x00\x00\x00\x00\x00",
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\x10\x40\x00",
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
        struct.pack("I", fmt), b"\x03\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00",
        b"\x03\x00\x00\x00", pixel_data])


class Texture:
    def __init__(self, path):
        self.path = path
        self.buffer = None
        self.guid = ""
        self.width = 1
        self.height = 1
        self.format = 0
        self.encoding = 0
        self.color_profile = 0
        self.is_durango = False
        self.blob_version = None
        self.rgba_pixels: list[float] | None = None
        self._pixel_data: bytes | None = None

    @staticmethod
    def from_path(path, resolver, *, decode_pixels: bool = False):
        """Load swatchbin → DDS. Pixel BC decode is opt-in (slow); Blender prefers DDS FILE."""
        p = resolver.resolve(path)
        if p is None or not os.path.isfile(p):
            if p:
                print(f"Warning: texture not found: {p}")
            return None
        t = Texture(p)
        t.deserialize(decode_pixels=decode_pixels)
        return t

    def deserialize(self, *, decode_pixels: bool = False):
        s = BinaryStream.from_path(self.path)
        bundle = Bundle()
        bundle.deserialize(s)

        blobs = bundle.blobs.get(Tag.TXCB, [])
        blob = _pick_txcb_blob(blobs, self.path)
        self.blob_version = Version()
        self.blob_version.major = blob.version.major
        self.blob_version.minor = blob.version.minor
        self.is_durango = _warn_durango(self.path, blob.version)

        header = _parse_txch(blob)
        self.guid = header["guid"]
        self.width = max(1, header["width"])
        self.height = max(1, header["height"])
        self.format = header["format"]
        self.encoding = header["encoding"]
        self.color_profile = header["color_profile"]

        pixel_data = bytes(blob.stream.read())
        self._pixel_data = pixel_data
        self.buffer = _build_dds(header, pixel_data)

        if decode_pixels:
            self.ensure_rgba_pixels()

    def ensure_rgba_pixels(self) -> bool:
        """Lazy BC decode — only when DDS FILE load is unavailable."""
        if self.has_decoded_pixels():
            return True
        if self.is_durango or not self._pixel_data:
            return False
        try:
            rgba = decode_to_rgba8(
                self._pixel_data, self.width, self.height, self.encoding
            )
            if rgba is not None:
                self.rgba_pixels = rgba8_to_float_pixels(rgba)
                return self.has_decoded_pixels()
        except Exception as e:
            print(
                f"Warning: Python BC decode failed for {os.path.basename(self.path)} "
                f"(encoding={self.encoding}): {e!r}; using DDS fallback."
            )
        return False

    def has_decoded_pixels(self) -> bool:
        n = self.width * self.height * 4
        return self.rgba_pixels is not None and len(self.rgba_pixels) >= n

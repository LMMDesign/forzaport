"""Low-level binary reading + the common ForzaTech container primitives.

Pure module: no bpy. Ported verbatim (behavior-preserving) from the original core.py
IOSys block - BinaryStream, Version, Metadata, the Tag enum - plus Blob/Bundle which every
ForzaTech file (model/carbin/material/shader) is built from.
"""

import io
import os
import struct
from collections import defaultdict


class BinaryStream:
    def __init__(self, buffer):  # buffer: memoryview | bytes
        self._stream = io.BytesIO(buffer)

    @staticmethod
    def from_path(path):
        with open(path, "rb", 0) as f:
            return BinaryStream(memoryview(f.read()))

    def __getitem__(self, key):
        return self._stream.getbuffer()[key]

    def tell(self):
        return self._stream.tell()

    def seek(self, offset, whence=0):
        return self._stream.seek(offset, whence)

    def read(self, size=None):
        return self._stream.read(size)

    def read_list(self, _VT):
        length = self.read_u32()
        return [_VT() for _ in range(length)]

    def read_string(self):
        length = self.read_u32()
        return self._stream.read(length).decode("utf-8")

    def read_7bit_string(self):
        length = self.read_7bit()
        return self._stream.read(length).decode("utf-8")

    def read_s16(self):
        v = self._stream.read(2)
        return struct.unpack("h", v)[0] if v else None

    def read_u8(self):
        v = self._stream.read(1)
        return struct.unpack("B", v)[0] if v else None

    def read_u16(self):
        v = self._stream.read(2)
        return struct.unpack("H", v)[0] if v else None

    def read_s32(self):
        v = self._stream.read(4)
        return struct.unpack("i", v)[0] if v else None

    def read_u32(self):
        v = self._stream.read(4)
        return struct.unpack("I", v)[0] if v else None

    def read_f16(self):
        v = self._stream.read(2)
        return struct.unpack("e", v)[0] if v else None

    def read_f32(self):
        v = self._stream.read(4)
        return struct.unpack("f", v)[0] if v else None

    def read_sn16(self):
        return self.read_s16() / 32767

    def read_un8(self):
        return self.read_u8() / 255

    def read_un16(self):
        return self.read_u16() / 65535

    def read_7bit(self):
        value = 0
        shift = 0
        while True:
            value_byte = self.read_u8()
            value |= (value_byte & 0x7F) << shift
            shift += 7
            if value_byte & 0x80 == 0:
                break
        return value


class Tag:
    # bundle
    Grub = 0x47727562  # 'Grub'

    # metadata
    Id = 0x49642020    # 'Id  '
    Name = 0x4E616D65  # 'Name'
    TXCH = 0x54584348  # 'TXCH'

    # blob
    Modl = 0x4D6F646C  # 'Modl'
    Skel = 0x536B656C  # 'Skel'
    MatI = 0x4D617449  # 'MatI'
    Mesh = 0x4D657368  # 'Mesh'
    VLay = 0x564C6179  # 'VLay'
    IndB = 0x496E6442  # 'IndB'
    VerB = 0x56657242  # 'VerB'
    MBuf = 0x4D427566  # 'MBuf'

    MATI = 0x4D415449
    MATL = 0x4D41544C
    MTPR = 0x4D545052  # 'MTPR'
    DFPR = 0x44465052

    TXCB = 0x54584342  # 'TXCB'


class Version:
    def __init__(self):
        self.major = 0
        self.minor = 0

    def deserialize(self, stream):
        self.major = stream.read_u8()
        self.minor = stream.read_u8()

    def is_at_least(self, major, minor):
        if self.major is None or self.minor is None:
            return False
        return self.major > major or self.major == major and self.minor >= minor

    def is_at_most(self, major, minor):
        if self.major is None or self.minor is None:
            return False
        return self.major < major or self.major == major and self.minor <= minor

    def is_equal(self, major, minor):
        return self.major == major and self.minor == minor

    def __str__(self):
        return f"{self.major}.{self.minor}"


class Metadata:
    def __init__(self):
        self.tag = 0
        self.version = 0

    def deserialize(self, stream):
        self.tag = stream.read_u32()
        version_and_size = stream.read_u16()
        self.version = version_and_size & 0xF
        size = version_and_size >> 4
        offset = stream.read_u16()
        self.stream = BinaryStream(stream[offset:offset + size])

    def read_string(self):
        if self.version > 0:
            print(f"Warning: Unsupported 'Name' metadata version. Found: {self.version}. Max supported: 0")
        return self.stream.read().decode("utf-8")

    def read_s32(self):
        if self.version > 0:
            print(f"Warning: Unsupported 'Id  ' metadata version. Found: {self.version}. Max supported: 0")
        return self.stream.read_s32()


class Blob:
    def __init__(self):
        self.tag = 0
        self.version = Version()
        self.metadata_length = 0
        self.metadata_offset = 0
        self.data_offset = 0
        self.data_size = 0
        self.metadata = {}

    def deserialize(self, stream):
        self.tag = stream.read_u32()
        self.version.deserialize(stream)
        self.metadata_length = stream.read_u16()
        self.metadata_offset = stream.read_u32()
        self.data_offset = stream.read_u32()
        self.data_size = stream.read_u32()
        stream.seek(4, os.SEEK_CUR)
        self.metadata = {}
        for i in range(self.metadata_length):
            metadata = Metadata()
            metadata.deserialize(BinaryStream(stream[self.metadata_offset + i * 8:]))
            self.metadata[metadata.tag] = metadata
        self.stream = BinaryStream(stream[self.data_offset:self.data_offset + self.data_size])

    def get_tag(self):
        return (chr((self.tag >> 24) & 0xFF) + chr((self.tag >> 16) & 0xFF)
                + chr((self.tag >> 8) & 0xFF) + chr(self.tag & 0xFF))


class Bundle:
    def __init__(self):
        self.tag = 0
        self.version = Version()
        self.blobs_length = 0
        self.blobs = defaultdict(list)

    def deserialize(self, stream):
        self.tag = stream.read_u32()
        if self.tag != Tag.Grub:
            print("Warning: Bundle has invalid tag. Expected 'Grub'.")
        self.version.deserialize(stream)
        if not self.version.is_at_most(1, 1):
            print(f"Warning: Unsupported Bundle version. Found: {self.version}. Max supported: 1.1")
        if not self.version.is_at_least(1, 0):
            print(f"Warning: Unsupported Bundle version. Found: {self.version}. Min supported: 1.0")
        self.blobs_length = stream.read_u16()
        stream.seek(4 * 2, os.SEEK_CUR)
        if self.version.is_at_least(1, 1):
            self.blobs_length = stream.read_u32()
        for _ in range(self.blobs_length):
            blob = Blob()
            blob.deserialize(stream)
            self.blobs[blob.tag].append(blob)

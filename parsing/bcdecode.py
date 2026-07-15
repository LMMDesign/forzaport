"""Pure-Python BC block decompressor (BC1–BC7 + raw RGBA8).

Decodes mip0 linear PC-layout payloads into RGBA8 bytes. Ported from the public-domain
Pillow BcnDecode.c logic (BC1–BC5, BC7). BC6H is not supported here — callers should fall
back to Blender DDS loading for HDR formats.
"""

from __future__ import annotations

import struct
from typing import Optional


def _load16(src: bytes, off: int = 0) -> int:
    return src[off] | (src[off + 1] << 8)


def _decode_565(x: int) -> tuple[int, int, int, int]:
    r = (x & 0xF800) >> 8
    r |= r >> 5
    g = (x & 0x07E0) >> 3
    g |= g >> 6
    b = (x & 0x001F) << 3
    b |= b >> 5
    return r, g, b, 0xFF


def _decode_bc1_color(dst: list[tuple[int, int, int, int]], src: bytes, separate_alpha: bool) -> None:
    c0 = _load16(src, 0)
    c1 = _load16(src, 2)
    p = [_decode_565(c0), _decode_565(c1), (0, 0, 0, 0xFF), (0, 0, 0, 0xFF)]
    r0, g0, b0, _ = p[0]
    r1, g1, b1, _ = p[1]
    if c0 > c1 or separate_alpha:
        p[2] = ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 0xFF)
        p[3] = ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 0xFF)
    else:
        p[2] = ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 0xFF)
        p[3] = (0, 0, 0, 0)
    for n in range(4):
        row = src[4 + n]
        for o in range(4):
            cw = 3 & (row >> (2 * o))
            dst[n * 4 + o] = p[cw]


def _set_channel(px: tuple[int, int, int, int], channel: int, val: int) -> tuple[int, int, int, int]:
    r, g, b, a = px
    if channel == 0:
        return val, g, b, a
    if channel == 1:
        return r, val, b, a
    if channel == 2:
        return r, g, val, a
    return r, g, b, val


def _decode_bc3_alpha(dst: list, src: bytes, channel: int, signed: bool) -> None:
    if signed:
        a0 = (struct.unpack("b", src[0:1])[0] + 128) & 0xFF
        a1 = (struct.unpack("b", src[1:2])[0] + 128) & 0xFF
        lut1 = src[2] | (src[3] << 8) | (src[4] << 16)
        lut2 = src[5] | (src[6] << 8) | (src[7] << 16)
    else:
        a0 = src[0]
        a1 = src[1]
        lut1 = src[2] | (src[3] << 8) | (src[4] << 16)
        lut2 = src[5] | (src[6] << 8) | (src[7] << 16)
    a = [0] * 8
    a[0] = a0
    a[1] = a1
    if a0 > a1:
        a[2] = (6 * a0 + a1) // 7
        a[3] = (5 * a0 + 2 * a1) // 7
        a[4] = (4 * a0 + 3 * a1) // 7
        a[5] = (3 * a0 + 4 * a1) // 7
        a[6] = (2 * a0 + 5 * a1) // 7
        a[7] = (a0 + 6 * a1) // 7
    else:
        a[2] = (4 * a0 + a1) // 5
        a[3] = (3 * a0 + 2 * a1) // 5
        a[4] = (2 * a0 + 3 * a1) // 5
        a[5] = (a0 + 4 * a1) // 5
        a[6] = 0
        a[7] = 0xFF
    for n in range(8):
        aw = 7 & (lut1 >> (3 * n))
        dst[n] = _set_channel(dst[n], channel, a[aw])
    for n in range(8):
        aw = 7 & (lut2 >> (3 * n))
        dst[8 + n] = _set_channel(dst[8 + n], channel, a[aw])


def _decode_bc1_block(src: bytes) -> list[tuple[int, int, int, int]]:
    col = [(0, 0, 0, 0xFF)] * 16
    _decode_bc1_color(col, src, False)
    return col


def _decode_bc2_block(src: bytes) -> list[tuple[int, int, int, int]]:
    col = [(0, 0, 0, 0xFF)] * 16
    _decode_bc1_color(col, src[8:], True)
    for n in range(16):
        bit_i = n * 4
        by_i = bit_i >> 3
        av = 0xF & (src[by_i] >> (bit_i & 7))
        av = (av << 4) | av
        r, g, b, _ = col[n]
        col[n] = (r, g, b, av)
    return col


def _decode_bc3_block(src: bytes) -> list[tuple[int, int, int, int]]:
    col = [(0, 0, 0, 0xFF)] * 16
    _decode_bc1_color(col, src[8:], True)
    _decode_bc3_alpha(col, src, 3, False)
    return col


def _decode_bc4_block(src: bytes, signed: bool) -> list[tuple[int, int, int, int]]:
    col = [(0, 0, 0, 0xFF)] * 16
    _decode_bc3_alpha(col, src, 0, signed)
    for i, (r, _, _, _) in enumerate(col):
        col[i] = (r, r, r, 0xFF)
    return col


def _decode_bc5_block(src: bytes, signed: bool) -> list[tuple[int, int, int, int]]:
    col = [(0, 0, 0, 0xFF)] * 16
    _decode_bc3_alpha(col, src, 0, signed)
    _decode_bc3_alpha(col, src[8:], 1, signed)
    for i, (r, g, _, _) in enumerate(col):
        col[i] = (r, g, 0xFF, 0xFF)
    return col


# --- BC7 tables (Pillow BcnDecode.c) ---
_BC7_MODES = [
    (3, 4, 0, 0, 4, 0, 1, 0, 3, 0),
    (2, 6, 0, 0, 6, 0, 0, 1, 3, 0),
    (3, 6, 0, 0, 5, 0, 0, 0, 2, 0),
    (2, 6, 0, 0, 7, 0, 1, 0, 2, 0),
    (1, 0, 2, 1, 5, 6, 0, 0, 2, 3),
    (1, 0, 2, 0, 7, 8, 0, 0, 2, 2),
    (1, 0, 0, 0, 7, 7, 1, 0, 4, 0),
    (2, 6, 0, 0, 5, 5, 1, 0, 2, 0),
]
_BC7_SI2 = [
    0xCCCC, 0x8888, 0xEEEE, 0xECC8, 0xC880, 0xFEEC, 0xFEC8, 0xEC80, 0xC800, 0xFFEC,
    0xFE80, 0xE800, 0xFFE8, 0xFF00, 0xFFF0, 0xF000, 0xF710, 0x008E, 0x7100, 0x08CE,
    0x008C, 0x7310, 0x3100, 0x8CCE, 0x088C, 0x3110, 0x6666, 0x366C, 0x17E8, 0x0FF0,
    0x718E, 0x399C, 0xAAAA, 0xF0F0, 0x5A5A, 0x33CC, 0x3C3C, 0x55AA, 0x9696, 0xA55A,
    0x73CE, 0x13C8, 0x324C, 0x3BDC, 0x6996, 0xC33C, 0x9966, 0x0660, 0x0272, 0x04E4,
    0x4E40, 0x2720, 0xC936, 0x936C, 0x39C6, 0x639C, 0x9336, 0x9CC6, 0x817E, 0xE718,
    0xCCF0, 0x0FCC, 0x7744, 0xEE22,
]
_BC7_SI3 = [
    0xAA685050, 0x6A5A5040, 0x5A5A4200, 0x5450A0A8, 0xA5A50000, 0xA0A05050, 0x5555A0A0,
    0x5A5A5050, 0xAA550000, 0xAA555500, 0xAAAA5500, 0x90909090, 0x94949494, 0xA4A4A4A4,
    0xA9A59450, 0x2A0A4250, 0xA5945040, 0x0A425054, 0xA5A5A500, 0x55A0A0A0, 0xA8A85454,
    0x6A6A4040, 0xA4A45000, 0x1A1A0500, 0x0050A4A4, 0xAAA59090, 0x14696914, 0x69691400,
    0xA08585A0, 0xAA821414, 0x50A4A450, 0x6A5A0200, 0xA9A58000, 0x5090A0A8, 0xA8A09050,
    0x24242424, 0x00AA5500, 0x24924924, 0x24499224, 0x50A50A50, 0x500AA550, 0xAAAA4444,
    0x66660000, 0xA5A0A5A0, 0x50A050A0, 0x69286928, 0x44AAAA44, 0x66666600, 0xAA444444,
    0x54A854A8, 0x95809580, 0x96969600, 0xA85454A8, 0x80959580, 0xAA141414, 0x96960000,
    0xAAAA1414, 0xA05050A0, 0xA0A5A5A0, 0x96000000, 0x40804080, 0xA9A8A9A8, 0xAAAAAA44,
    0x2A4A5254,
]
_BC7_AI0 = [15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 2, 8, 2, 2, 8, 8, 15, 2, 8, 2, 2, 8, 8, 2, 2, 15, 15, 6, 8, 2, 8, 15, 15, 2, 8, 2, 2, 2, 15, 15, 6, 6, 2, 6, 8, 15, 15, 2, 2, 15, 15, 15, 15, 15, 2, 2, 15]
_BC7_AI1 = [3, 3, 15, 15, 8, 3, 15, 15, 8, 8, 6, 6, 6, 5, 3, 3, 3, 3, 8, 15, 3, 3, 6, 10, 5, 8, 8, 6, 8, 5, 15, 15, 8, 15, 3, 5, 6, 10, 8, 15, 15, 3, 15, 5, 15, 15, 15, 15, 15, 3, 15, 5, 5, 5, 8, 5, 10, 5, 10, 8, 13, 15, 12, 3, 3]
_BC7_AI2 = [15, 8, 8, 3, 15, 15, 3, 8, 15, 15, 15, 15, 15, 15, 15, 8, 15, 8, 15, 3, 15, 8, 15, 8, 3, 15, 6, 10, 15, 15, 10, 8, 15, 3, 15, 10, 10, 8, 9, 10, 6, 15, 8, 15, 3, 6, 6, 8, 15, 3, 15, 15, 15, 15, 15, 15, 15, 15, 3, 15, 15, 8]
_BC7_W2 = [0, 21, 43, 64]
_BC7_W3 = [0, 9, 18, 27, 37, 46, 55, 64]
_BC7_W4 = [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64]


def _get_bits(src: bytes, bit: int, count: int) -> int:
    if not count:
        return 0
    by = bit >> 3
    bit &= 7
    if bit + count <= 8:
        return (src[by] >> bit) & ((1 << count) - 1)
    x = src[by] | (src[by + 1] << 8)
    return (x >> bit) & ((1 << count) - 1)


def _bc7_get_subset(ns: int, partition: int, n: int) -> int:
    if ns == 2:
        return 1 & (_BC7_SI2[partition] >> n)
    if ns == 3:
        return 3 & (_BC7_SI3[partition] >> (2 * n))
    return 0


def _expand_quantized(v: int, bits: int) -> int:
    v = v << (8 - bits)
    return v | (v >> bits)


def _bc7_lerp(e0: tuple[int, int, int, int], e1: tuple[int, int, int, int], s0: int, s1: int) -> tuple[int, int, int, int]:
    t0 = 64 - s0
    t1 = 64 - s1
    return (
        (t0 * e0[0] + s0 * e1[0] + 32) >> 6,
        (t0 * e0[1] + s0 * e1[1] + 32) >> 6,
        (t0 * e0[2] + s0 * e1[2] + 32) >> 6,
        (t1 * e0[3] + s1 * e1[3] + 32) >> 6,
    )


def _decode_bc7_block(src: bytes) -> list[tuple[int, int, int, int]]:
    col = [(0, 0, 0, 255)] * 16
    mode_byte = src[0]
    if not mode_byte:
        return col
    bit = 0
    while not (mode_byte & (1 << bit)):
        bit += 1
    mode = bit
    info = _BC7_MODES[mode]
    ns, pb, rb, isb, cb, ab, epb, spb, ib, ib2 = info
    bit = mode + 1
    partition = _get_bits(src, bit, pb)
    bit += pb
    rotation = _get_bits(src, bit, rb)
    bit += rb
    index_sel = _get_bits(src, bit, isb)
    bit += isb
    numep = ns << 1
    endpoints: list[list[int]] = [[0, 0, 0, 255] for _ in range(numep)]
    for i in range(numep):
        endpoints[i][0] = _get_bits(src, bit, cb)
        bit += cb
    for i in range(numep):
        endpoints[i][1] = _get_bits(src, bit, cb)
        bit += cb
    for i in range(numep):
        endpoints[i][2] = _get_bits(src, bit, cb)
        bit += cb
    for i in range(numep):
        if ab:
            endpoints[i][3] = _get_bits(src, bit, ab)
            bit += ab
    if epb:
        cb += 1
        if ab:
            ab += 1
        for i in range(numep):
            val = _get_bits(src, bit, 1)
            bit += 1
            for ch in range(3 + (1 if ab else 0)):
                endpoints[i][ch] = (endpoints[i][ch] << 1) | val
    if spb:
        cb += 1
        if ab:
            ab += 1
        for i in range(0, numep, 2):
            val = _get_bits(src, bit, 1)
            bit += 1
            for j in range(2):
                for ch in range(3 + (1 if ab else 0)):
                    endpoints[i + j][ch] = (endpoints[i + j][ch] << 1) | val
    ep_tuples = []
    for i in range(numep):
        r = _expand_quantized(endpoints[i][0], cb)
        g = _expand_quantized(endpoints[i][1], cb)
        b = _expand_quantized(endpoints[i][2], cb)
        a = _expand_quantized(endpoints[i][3], ab) if ab else 255
        ep_tuples.append((r, g, b, a))
    cw = _BC7_W2 if ib == 2 else (_BC7_W3 if ib == 3 else _BC7_W4)
    aw = _BC7_W2 if (ab and ib2) and ib2 == 2 else (_BC7_W3 if (ab and ib2) and ib2 == 3 else (_BC7_W4 if ab and ib2 else cw))
    cibit = bit
    aibit = cibit + 16 * ib - ns
    for i in range(16):
        s = _bc7_get_subset(ns, partition, i) << 1
        use_ib = ib
        if i == 0:
            use_ib -= 1
        elif ns == 2 and i == _BC7_AI0[partition]:
            use_ib -= 1
        elif ns == 3:
            if i == _BC7_AI1[partition] or i == _BC7_AI2[partition]:
                use_ib -= 1
        i0 = _get_bits(src, cibit, use_ib)
        cibit += use_ib
        e0 = ep_tuples[s]
        e1 = ep_tuples[s + 1]
        if ab and ib2:
            use_ib2 = ib2 - (1 if i == 0 else 0)
            i1 = _get_bits(src, aibit, use_ib2)
            aibit += use_ib2
            if index_sel:
                col[i] = _bc7_lerp(e0, e1, aw[i1], cw[i0])
            else:
                col[i] = _bc7_lerp(e0, e1, cw[i0], aw[i1])
        else:
            col[i] = _bc7_lerp(e0, e1, cw[i0], cw[i0])
        if rotation == 1:
            r, g, b, a = col[i]
            col[i] = (a, g, b, r)
        elif rotation == 2:
            r, g, b, a = col[i]
            col[i] = (r, a, b, g)
        elif rotation == 3:
            r, g, b, a = col[i]
            col[i] = (r, g, a, b)
    return col


_BLOCK_DECODERS = {
    0: lambda b, s: _decode_bc1_block(b),
    1: lambda b, s: _decode_bc2_block(b),
    2: lambda b, s: _decode_bc3_block(b),
    3: lambda b, s: _decode_bc4_block(b, False),
    4: lambda b, s: _decode_bc4_block(b, True),
    5: lambda b, s: _decode_bc5_block(b, False),
    6: lambda b, s: _decode_bc5_block(b, True),
    9: lambda b, s: _decode_bc7_block(b),
}


def _block_bytes(encoding: int) -> int:
    if encoding in (0, 3, 4):
        return 8
    return 16


def _mip0_size(width: int, height: int, encoding: int) -> int:
    if encoding == 13:
        return width * height * 4
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    return bw * bh * _block_bytes(encoding)


def decode_to_rgba8(data: bytes, width: int, height: int, encoding: int) -> Optional[bytes]:
    """Decode mip0 block-compressed or raw RGBA8 data to width*height*4 bytes."""
    if width < 1 or height < 1:
        return None
    if encoding == 13:
        need = width * height * 4
        if len(data) < need:
            return None
        return bytes(data[:need])
    if encoding in (7, 8):
        return None
    decoder = _BLOCK_DECODERS.get(encoding)
    if decoder is None:
        return None
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    need = bw * bh * _block_bytes(encoding)
    if len(data) < need:
        return None
    out = bytearray(width * height * 4)
    offset = 0
    for by in range(bh):
        for bx in range(bw):
            block = data[offset:offset + _block_bytes(encoding)]
            if len(block) < _block_bytes(encoding):
                return None
            offset += _block_bytes(encoding)
            try:
                pixels = decoder(block, encoding)
            except (IndexError, ValueError):
                return None
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    continue
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    r, g, b, a = pixels[py * 4 + px]
                    i = (y * width + x) * 4
                    out[i:i + 4] = bytes((r, g, b, a))
    return bytes(out)


def rgba8_to_float_pixels(rgba: bytes) -> list[float]:
    inv = 1.0 / 255.0
    return [c * inv for c in rgba]

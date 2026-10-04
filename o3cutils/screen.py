"""Convert O3C framebuffers (RGB565) to RGB888 and write PNG files.

The package is dependency-free, so PNGs are emitted by hand: IHDR/IDAT/IEND
chunks with zlib-compressed scanlines, per the PNG specification.
"""

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def rgb565_to_rgb888(data: bytes) -> bytes:
    """Expand a little-endian RGB565 byte stream into 3-bytes-per-pixel RGB."""
    if len(data) % 2:
        raise ValueError(f"odd-length RGB565 data: {len(data)} bytes")
    out = bytearray()
    for i in range(0, len(data), 2):
        value = data[i] | (data[i + 1] << 8)
        r = (value >> 11) & 0x1F
        g = (value >> 5) & 0x3F
        b = value & 0x1F
        out += bytes(
            (round(r * 255 / 31), round(g * 255 / 63), round(b * 255 / 31))
        )
    return bytes(out)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + tag
        + payload
        + zlib.crc32(tag + payload).to_bytes(4, "big")
    )


def encode_png(rgb: bytes, width: int, height: int) -> bytes:
    """Encode an RGB888 image (row-major) as a PNG file image."""
    if len(rgb) != width * height * 3:
        raise ValueError(f"pixel data is {len(rgb)} bytes, want {width*height*3}")
    stride = width * 3
    scanlines = b"".join(
        b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height)
    )
    return b"".join(
        [
            PNG_SIGNATURE,
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(scanlines)),
            _chunk(b"IEND", b""),
        ]
    )


def save_png(rgb: bytes, width: int, height: int, path) -> None:
    """Encode an RGB888 image and write it to ``path``."""
    with open(path, "wb") as f:
        f.write(encode_png(rgb, width, height))

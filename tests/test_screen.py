import struct
import zlib

from o3cutils.screen import (
    PNG_SIGNATURE,
    encode_png,
    rgb565_to_rgb888,
    save_png,
)


class TestRgb565:
    def test_expands_pixels(self):
        assert rgb565_to_rgb888(b"\x00\x00") == b"\x00\x00\x00"
        assert rgb565_to_rgb888(b"\xff\xff") == b"\xff\xff\xff"
        assert rgb565_to_rgb888(b"\x00\xf8") == b"\xff\x00\x00"  # pure red
        assert rgb565_to_rgb888(b"\xe0\x07") == b"\x00\xff\x00"  # pure green
        assert rgb565_to_rgb888(b"\x1f\x00") == b"\x00\x00\xff"  # pure blue

    def test_rejects_odd_length(self):
        import pytest

        with pytest.raises(ValueError, match="odd-length"):
            rgb565_to_rgb888(b"\x00")


def parse_png(png):
    assert png.startswith(PNG_SIGNATURE)
    pos = len(PNG_SIGNATURE)
    chunks = {}
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        tag = png[pos + 4 : pos + 8]
        payload = png[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", png[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(tag + payload)
        chunks[tag] = chunks.get(tag, b"") + payload
        pos += 12 + length
    return chunks


class TestEncodePng:
    def test_roundtrips_pixels(self):
        rgb = bytes(range(12))  # 2x2 image
        png = encode_png(rgb, 2, 2)
        chunks = parse_png(png)
        width, height, depth, color = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
        assert (width, height, depth, color) == (2, 2, 8, 2)
        scanlines = zlib.decompress(chunks[b"IDAT"])
        assert scanlines == b"\x00" + rgb[0:6] + b"\x00" + rgb[6:12]
        assert chunks[b"IEND"] == b""

    def test_rejects_size_mismatch(self):
        import pytest

        with pytest.raises(ValueError, match="want 12"):
            encode_png(b"\x00" * 3, 2, 2)


class TestSavePng:
    def test_writes_file(self, tmp_path):
        path = tmp_path / "out.png"
        save_png(b"\xff\x00\x00", 1, 1, path)
        assert path.read_bytes().startswith(PNG_SIGNATURE)

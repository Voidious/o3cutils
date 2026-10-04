import pytest

from o3cutils.packets import (
    Cmd,
    PacketError,
    decode_cmd,
    decode_packet,
    encode_cmd,
    encode_packet,
    packet_checksum,
    v2_checksum,
)


class TestChecksum:
    def test_even_length(self):
        assert v2_checksum(b"\x01\x02\x03\x04") == 0x0604

    def test_odd_length_pads(self):
        assert v2_checksum(b"\x01") == 1

    def test_wraps_at_16_bits(self):
        # 0xFFFF + 0xFFFF + 0x0003 = 0x20001 -> truncated to 16 bits.
        assert v2_checksum(b"\xff\xff\xff\xff\x03") == 0x0001


class TestEncodeCmd:
    def test_no_padding_needed(self):
        assert encode_cmd(Cmd(1, 2, b"abcd")) == b"\x08\x00\x01\x02abcd"

    def test_pads_to_multiple_of_four(self):
        assert encode_cmd(Cmd(1, 2, b"a")) == b"\x05\x00\x01\x02a\x00\x00\x00"

    def test_empty_data(self):
        assert encode_cmd(Cmd(0, 0, b"")) == b"\x04\x00\x00\x00"

    def test_rejects_oversized(self):
        with pytest.raises(PacketError, match="too large"):
            encode_cmd(Cmd(1, 2, b"x" * 64))


class TestDecodeCmd:
    def test_roundtrip(self):
        cmd = Cmd(0x25, 7, b"\xde\xad\xbe\xef")
        decoded, offset = decode_cmd(encode_cmd(cmd))
        assert decoded == cmd
        assert offset == 8

    def test_truncated_length(self):
        with pytest.raises(PacketError, match="truncated"):
            decode_cmd(b"\x08")

    def test_length_below_header(self):
        with pytest.raises(PacketError, match="invalid command length"):
            decode_cmd(b"\x02\x00\x00\x00")

    def test_length_past_buffer(self):
        with pytest.raises(PacketError, match="invalid command length"):
            decode_cmd(b"\x40\x00\x00\x00")

    def test_length_flags_are_stripped(self):
        # live responses set flag bits above the low-10-bit length
        cmd, offset = decode_cmd(b"\x04\x4c\x11\x00")
        assert cmd == Cmd(0x11, 0x00, b"")
        assert offset == 4


class TestPacket:
    def test_roundtrip(self):
        cmds = [Cmd(0, 1, b"hi"), Cmd(0x22, 2, b"\x00\x01\x02")]
        packet = encode_packet(cmds)
        assert len(packet) == 64
        assert packet[0] == 0x21
        assert packet[1] == 0x03
        assert decode_packet(packet) == cmds

    def test_body_too_large(self):
        with pytest.raises(PacketError, match="too large"):
            encode_packet([Cmd(1, 2, b"x" * 64)])

    def test_packet_too_short(self):
        with pytest.raises(PacketError, match="too short"):
            decode_packet(b"\x21\x03\x00")

    def test_wrong_report_id(self):
        with pytest.raises(PacketError, match="report id"):
            decode_packet(b"\x99\x03\x00\x00" + b"\x00" * 60)

    def test_checksum_mismatch(self):
        packet = bytearray(encode_packet([Cmd(0, 1, b"hi")]))
        packet[2] ^= 0xFF
        with pytest.raises(PacketError, match="checksum mismatch"):
            decode_packet(bytes(packet))

    def test_high_speed_checksum_mismatch_tolerated(self):
        # live 8000 Hz firmware sometimes computes the 0x22 checksum
        # before filling the body; mismatch must not reject the report
        packet = bytearray(
            encode_packet([Cmd(0x1E, 0, b"\x3f\x00")], report_id=0x22, packet_size=1024)
        )
        packet[2] ^= 0xFF  # corrupt the checksum
        assert decode_packet(bytes(packet)) == [Cmd(0x1E, 0, b"\x3f\x00")]


class TestHighSpeedPacket:
    def test_roundtrip_1024(self):
        cmds = [Cmd(0x25, 0, b"\x00\x00\x00\x00" + b"\xab" * 1004)]
        packet = encode_packet(
            cmds, report_id=0x22, packet_size=1024
        )
        assert len(packet) == 1024
        assert packet[0] == 0x22
        assert decode_packet(packet) == cmds

    def test_decode_accepts_both_report_ids(self):
        for rid in (0x21, 0x22):
            packet = encode_packet([Cmd(0, 1, b"hi")], report_id=rid)
            assert decode_packet(packet) == [Cmd(0, 1, b"hi")]

    def test_checksum_uses_report_id(self):
        body = encode_cmd(Cmd(0, 1, b"hi"))
        assert packet_checksum(3, body, 0x22) != packet_checksum(3, body, 0x21)


class TestUnpaddedBody:
    def test_decodes_body_with_no_trailing_pad(self):
        cmd = Cmd(0, 1, b"hi")
        body = encode_cmd(cmd)
        header = bytes([0x21, 0x03]) + packet_checksum(0x03, body).to_bytes(2, "little")
        assert decode_packet(header + body) == [cmd]

    def test_body_too_large_across_cmds(self):
        with pytest.raises(PacketError, match="too large"):
            encode_packet([Cmd(1, 1, b"a" * 28), Cmd(2, 2, b"b" * 28)])

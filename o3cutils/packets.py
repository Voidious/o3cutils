"""Encoding and decoding of SayoDevice API v2 HID packets.

Packet format (from khang06's O3C reverse-engineering notes):

    struct hid_packet_v2_t {
        uint8_t report_id;      // 0x21 (0x22 for 8000hz high-speed mode)
        uint8_t echo;           // always 3 in the web UI
        uint16_t checksum;      // sum of the cmds reinterpreted as 16-bit words
        hid_cmd_v2_t cmds[];    // padded to 64 bytes total
    };

    struct hid_cmd_v2_t {
        uint16_t length;            // counts the whole cmd, including this field
        uint8_t id;
        uint8_t index;
        uint8_t data[length - 4];   // padded to a multiple of 4 bytes
    };
"""

from dataclasses import dataclass

REPORT_ID = 0x21
ECHO = 0x03
PACKET_SIZE = 64
CMD_HEADER_SIZE = 4


class PacketError(ValueError):
    """Raised when a packet cannot be encoded or decoded."""


@dataclass(frozen=True)
class Cmd:
    """A single command (host to device) or response (device to host)."""

    id: int
    index: int
    data: bytes


def v2_checksum(buf: bytes) -> int:
    """Sum ``buf`` as little-endian 16-bit words, as the firmware does."""
    if len(buf) % 2:
        buf = buf + b"\x00"
    return sum(buf[i] | (buf[i + 1] << 8) for i in range(0, len(buf), 2)) & 0xFFFF


def packet_checksum(echo: int, body: bytes) -> int:
    """Checksum of a report: header words (checksum field zeroed) plus cmds.

    Verified against live device responses: the firmware sums the entire
    report as 16-bit words, with the checksum field itself zeroed.
    """
    return (v2_checksum(bytes([REPORT_ID, echo, 0, 0])) + v2_checksum(body)) & 0xFFFF


def encode_cmd(cmd: Cmd) -> bytes:
    """Encode one command, padded to a multiple of 4 bytes."""
    length = CMD_HEADER_SIZE + len(cmd.data)
    if length > PACKET_SIZE:
        raise PacketError(f"command too large: {length} bytes")
    out = length.to_bytes(2, "little") + bytes([cmd.id, cmd.index]) + cmd.data
    pad = -len(out) % 4
    return out + b"\x00" * pad


def decode_cmd(buf: bytes, offset: int = 0) -> tuple[Cmd, int]:
    """Decode one command starting at ``offset``; returns (cmd, next_offset).

    The device sets flag bits in the high bits of the length field in some
    responses (e.g. 0x4004 on bare acks); the low 10 bits are the length.
    """
    if offset + 2 > len(buf):
        raise PacketError("truncated command length field")
    raw = int.from_bytes(buf[offset : offset + 2], "little")
    length = raw & 0x03FF
    if length < CMD_HEADER_SIZE or offset + length > len(buf):
        raise PacketError(f"invalid command length {raw:#06x}")
    cmd = Cmd(
        id=buf[offset + 2],
        index=buf[offset + 3],
        data=bytes(buf[offset + CMD_HEADER_SIZE : offset + length]),
    )
    padded = (length + 3) // 4 * 4
    return cmd, offset + padded


def encode_packet(cmds: list[Cmd]) -> bytes:
    """Encode a full 64-byte report containing ``cmds``."""
    body = b"".join(encode_cmd(c) for c in cmds)
    if len(body) > PACKET_SIZE - 4:
        raise PacketError(f"packet body too large: {len(body)} bytes")
    header = bytes([REPORT_ID, ECHO]) + packet_checksum(ECHO, body).to_bytes(
        2, "little"
    )
    return (header + body).ljust(PACKET_SIZE, b"\x00")


def decode_packet(buf: bytes) -> list[Cmd]:
    """Decode a report into its commands, verifying report id and checksum."""
    if len(buf) < 4:
        raise PacketError("packet too short")
    if buf[0] != REPORT_ID:
        raise PacketError(f"unexpected report id 0x{buf[0]:02x}")
    checksum = int.from_bytes(buf[2:4], "little")
    body = buf[4:]
    want = (v2_checksum(buf[:2] + b"\x00\x00") + v2_checksum(body)) & 0xFFFF
    if want != checksum:
        raise PacketError(f"checksum mismatch: got 0x{checksum:04x}, want 0x{want:04x}")
    cmds = []
    offset = 0
    while offset < len(body) and body[offset : offset + 2] != b"\x00\x00":
        cmd, offset = decode_cmd(body, offset)
        cmds.append(cmd)
    return cmds

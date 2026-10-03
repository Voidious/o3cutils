"""Transport client for talking to an O3C over a hidraw device node."""

import os
import select
import time

from .discover import open_first
from .packets import Cmd, decode_packet, encode_packet

READ_TIMEOUT = 1.0
REPORT_SIZE = 64

CMD_INFO = 0x00
CMD_SYSINFO = 0x02
CMD_DISPLAY = 0x25


class TransportError(OSError):
    """Raised when the device does not answer or answers unintelligibly."""


class O3C:
    """Talks to one O3C device over an already-open hidraw file object."""

    def __init__(self, dev):
        self.dev = dev

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.dev.close()

    def send(self, cmd_id: int, data: bytes = b"", index: int = 0) -> int:
        """Send one command; returns the index used to match its response.

        The device only answers requests with data when ``index`` is 0;
        nonzero indexes get a bare ack, so 0 is the default.
        """
        report = encode_packet([Cmd(id=cmd_id, index=index, data=data)])
        self.dev.write(report)
        return index

    def read(self, timeout: float | None = None) -> list[Cmd]:
        """Read one report and return its decoded commands.

        Blocks up to ``timeout`` seconds (default: forever, like hidraw).
        """
        timeout = READ_TIMEOUT if timeout is None else timeout
        readable, _, _ = select.select([self.dev], [], [], timeout)
        if not readable:
            raise TransportError("device read timed out")
        buf = os.read(self.dev.fileno(), REPORT_SIZE)
        if not buf:
            raise TransportError("device read timed out")
        return decode_packet(bytes(buf))

    def transact(self, cmd_id: int, data: bytes = b"") -> Cmd:
        """Send a command and return its matching response."""
        index = self.send(cmd_id, data)
        deadline = time.monotonic() + READ_TIMEOUT
        while True:
            try:
                cmds = self.read(max(0.01, deadline - time.monotonic()))
            except TransportError:
                cmds = []
            for cmd in cmds:
                if cmd.id == cmd_id and cmd.index == index:
                    return cmd
            if time.monotonic() >= deadline:
                raise TransportError(
                    f"no response for command 0x{cmd_id:02x} index {index}"
                )

    def info(self) -> dict:
        """Query device info (model code, firmware version, uptime).

        Field offsets verified against live firmware: battery is at 7 and
        the supported-command list starts at 11 (khang06's gist notes are
        off by one here).
        """
        raw = self.transact(CMD_INFO).data
        return {
            "model_code": int.from_bytes(raw[0:2], "little"),
            "firmware_version": int.from_bytes(raw[2:4], "little"),
            "battery": raw[7],
            "fn": raw[8],
            "uptime": f"{raw[9]}s{raw[10]}ms",
            "supported_commands": sorted(set(raw[11:])),
        }

    def sysinfo(self) -> dict:
        """Query live system info (display, uptime, clocks)."""
        raw = self.transact(CMD_SYSINFO).data

        def u16(off):
            return int.from_bytes(raw[off : off + 2], "little")

        def u32(off):
            return int.from_bytes(raw[off : off + 4], "little")

        return {
            "width": u16(0x00),
            "height": u16(0x02),
            "refresh_rate": raw[0x04],
            "sys_ms": u16(0x06),
            "sys_s": u32(0x08),
            "vid": u16(0x0C),
            "pid": u16(0x0E),
            "cpu_1m": raw[0x10],
            "cpu_5m": raw[0x11],
            "cpu_freq": u32(0x14),
            "hclk": u32(0x18),
            "pclk_1": u32(0x1C),
            "pclk_2": u32(0x20),
            "adc_0": u32(0x24),
            "adc_1": u32(0x28),
        }

    def display(self, byte_offset: int) -> bytes:
        """Read one chunk of the live framebuffer (52 bytes of RGB565).

        Requests without the 4-byte offset wedge the firmware, so the
        offset is mandatory and validated here.
        """
        if not 0 <= byte_offset < 0x10000 or byte_offset % 4:
            raise ValueError(f"bad display offset: {byte_offset}")
        res = self.transact(CMD_DISPLAY, byte_offset.to_bytes(4, "little"))
        got = int.from_bytes(res.data[0:4], "little")
        if got != byte_offset:
            raise TransportError(
                f"display offset mismatch: requested {byte_offset}, got {got}"
            )
        return res.data[4:]

    def framebuffer(self) -> bytes:
        """Dump the whole framebuffer (RGB565, row-major)."""
        dims = self.sysinfo()
        size = dims["width"] * dims["height"] * 2
        out = bytearray()
        while len(out) < size:
            out += self.display(len(out))
        return bytes(out[:size])


def connect(path: str | None = None) -> O3C:
    """Open the first usable O3C interface (or ``path`` if given)."""
    fd = os.open(path or open_first(), os.O_RDWR)
    return O3C(os.fdopen(fd, "rb+", buffering=0))

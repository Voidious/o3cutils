"""Transport client for talking to an O3C over a hidraw device node."""

import os
import time

from .discover import open_first
from .packets import Cmd, decode_packet, encode_packet

READ_TIMEOUT = 1.0
REPORT_SIZE = 64

CMD_INFO = 0x00
CMD_SYSINFO = 0x02


class TransportError(OSError):
    """Raised when the device does not answer or answers unintelligibly."""


class O3C:
    """Talks to one O3C device over an already-open hidraw file object."""

    def __init__(self, dev, index_seed: int = 0):
        self.dev = dev
        self.index = index_seed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.dev.close()

    def _next_index(self) -> int:
        self.index = (self.index + 1) & 0xFF
        return self.index

    def send(self, cmd_id: int, data: bytes = b"") -> int:
        """Send one command; returns the index used to match its response."""
        index = self._next_index()
        report = encode_packet([Cmd(id=cmd_id, index=index, data=data)])
        self.dev.write(report)
        return index

    def read(self) -> list[Cmd]:
        """Read one report and return its decoded commands."""
        buf = self.dev.read(REPORT_SIZE)
        if not buf:
            raise TransportError("device read timed out")
        return decode_packet(bytes(buf))

    def transact(self, cmd_id: int, data: bytes = b"") -> Cmd:
        """Send a command and return its matching response."""
        index = self.send(cmd_id, data)
        deadline = time.monotonic() + READ_TIMEOUT
        while True:
            for cmd in self.read():
                if cmd.id == cmd_id and cmd.index == index:
                    return cmd
            if time.monotonic() >= deadline:
                raise TransportError(
                    f"no response for command 0x{cmd_id:02x} index {index}"
                )

    def info(self) -> dict:
        """Query device info (model code, firmware version, uptime)."""
        cmd = self.transact(CMD_INFO)
        raw = cmd.data
        return {
            "model_code": int.from_bytes(raw[0:2], "little"),
            "firmware_version": int.from_bytes(raw[2:4], "little"),
            "battery": raw[8],
            "fn": raw[9],
            "uptime": f"{raw[0x0A]}s{raw[0x0B]}ms",
        }


def connect(path: str | None = None) -> O3C:
    """Open the first usable O3C interface (or ``path`` if given)."""
    fd = os.open(path or open_first(), os.O_RDWR)
    return O3C(os.fdopen(fd, "rb+", buffering=0))

"""Transport client for talking to an O3C over a hidraw device node."""

import os
import select
import time

from .discover import open_first
from .packets import (
    PACKET_SIZE_HIGH,
    REPORT_ID_HIGH,
    Cmd,
    PacketError,
    decode_packet,
    encode_packet,
)

READ_TIMEOUT = 1.0
REPORT_SIZE = 64
DISPLAY_CHUNK = 52  # payload bytes per 0x25 chunk (64 - 4 hdr - 4 offset)
DISPLAY_CHUNK_HIGH = 1012  # same for 1024-byte high-speed reports

CMD_INFO = 0x00
CMD_SYSINFO = 0x02
CMD_DISPLAY = 0x25
CMD_KEY_STATUS = 0x1E
CMD_SCREEN_MAIN = 0x22
CMD_SCREEN_SLEEP = 0x23

LAYER_INDEXES = range(16)


class TransportError(OSError):
    """Raised when the device does not answer or answers unintelligibly."""


class O3C:
    """Talks to one O3C device over an already-open hidraw file object.

    ``high_speed`` selects the 8000 Hz interface (report id 0x22, 1024-byte
    reports, ~19x more pixel data per report for streaming). It is only
    available after the device's polling rate is set to 8000 Hz.
    """

    def __init__(self, dev, high_speed: bool = False):
        self.dev = dev
        self.report_id = REPORT_ID_HIGH if high_speed else 0x21
        self.report_size = PACKET_SIZE_HIGH if high_speed else REPORT_SIZE
        self.display_chunk = DISPLAY_CHUNK_HIGH if high_speed else DISPLAY_CHUNK
        self._pending = b""

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
        report = encode_packet(
            [Cmd(id=cmd_id, index=index, data=data)],
            report_id=self.report_id,
            packet_size=self.report_size,
        )
        self.dev.write(report)
        return index

    def read(self, timeout: float | None = None) -> list[Cmd]:
        """Read one report and return its decoded commands.

        Blocks up to ``timeout`` seconds (default: forever, like hidraw).
        """
        timeout = READ_TIMEOUT if timeout is None else timeout
        while not self._pending:
            readable, _, _ = select.select([self.dev], [], [], timeout)
            if not readable:
                raise TransportError("device read timed out")
            buf = os.read(self.dev.fileno(), PACKET_SIZE_HIGH)
            if not buf:
                raise TransportError("device read timed out")
            # hidraw returns exactly one report per read; if a byte
            # stream ever hands us more, keep the tail for next time.
            size = PACKET_SIZE_HIGH if buf[0] == REPORT_ID_HIGH else REPORT_SIZE
            self._pending = bytes(buf)
        size = PACKET_SIZE_HIGH if self._pending[0] == REPORT_ID_HIGH else REPORT_SIZE
        buf, self._pending = self._pending[:size], self._pending[size:]
        return decode_packet(buf)

    def transact(self, cmd_id: int, data: bytes = b"") -> Cmd:
        """Send a command and return its matching response."""
        index = self.send(cmd_id, data)
        deadline = time.monotonic() + READ_TIMEOUT
        while True:
            try:
                cmds = self.read(max(0.01, deadline - time.monotonic()))
            except (TransportError, PacketError):
                # timeout or an undecodable report (e.g. a mid-stream
                # broadcast): skip it and keep waiting for the match
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

    def write_framebuffer(self, rgb565: bytes, offset: int = 0) -> None:
        """Write raw RGB565 pixels into the framebuffer (streams to screen).

        This is the vendor streaming path: Display (0x25) carries the
        4-byte byte offset plus up to 52 pixel bytes per report, and each
        write renders immediately — no refresh command needed.
        """
        if len(rgb565) % 2 or not 0 <= offset < 0x10000 or offset % 4:
            raise ValueError(f"bad framebuffer write: offset={offset}, len={len(rgb565)}")
        pos = 0
        while pos < len(rgb565):
            chunk = rgb565[pos : pos + self.display_chunk]
            self.send(CMD_DISPLAY, (offset + pos).to_bytes(4, "little") + chunk)
            pos += len(chunk)

    def keepalive(self, pixel: bytes = b"\x00\x00") -> None:
        """Hold the screen awake with a minimal display write.

        The device repaints the framebuffer with the sleep-screen image
        about 1.5s after the *last* Display write (streaming resets the
        idle timer; input also wakes it). Writing one pixel every ~0.4s
        keeps streamed content on screen indefinitely. Pass the pixel
        bytes already at framebuffer offset 0 to make the write invisible.
        """
        if len(pixel) != 2:
            raise ValueError(f"keepalive needs exactly 2 pixel bytes, got {len(pixel)}")
        self.send(CMD_DISPLAY, (0).to_bytes(4, "little") + pixel)

    def null_layers(
        self, stacks: tuple[int, ...] = (CMD_SCREEN_SLEEP, CMD_SCREEN_MAIN)
    ) -> None:
        """Replace every element on the given layer stacks with Null.

        Element type 0 (Null) renders nothing, so after this the raw
        framebuffer is the whole visible image. Any non-null element on
        the main stack composits over the framebuffer within ~1s of a
        streaming write, blacking it out — so this must run before (and
        after any prior clear that painted) framebuffer streaming.
        """
        for cmd in stacks:
            for idx in LAYER_INDEXES:
                self.send(cmd, bytes(56), index=idx)

    def clear_screen(self) -> None:
        """Blank the display, including the persistent layer stacks.

        The visible image is a composite of up to 16 element layers on the
        sleep (0x23) and main (0x22) stacks rendered over the framebuffer,
        so a black framebuffer write alone leaves layered content visible.
        This nulls every layer on both stacks (type-0 elements render
        nothing and let the framebuffer through) and then black-fills the
        framebuffer. Never paint black *Color* elements to clear: they sit
        on top of the framebuffer and suppress later streaming writes.
        Layer content survives re-attachment; only a power cycle resets
        it harder than this.
        """
        self.null_layers()
        dims = self.sysinfo()
        black = b"\x00\x00" * (dims["width"] * dims["height"])
        self.write_framebuffer(black)

    def key_status(self) -> int:
        """Return the active-low button/knob bitmask from KeyStatu (0x1E).

        Bit clear means pressed: bit 0..2 = buttons 1-3, bit 3 = knob
        click, bits 4/5 = knob left/right.
        """
        res = self.transact(CMD_KEY_STATUS)
        if not res.data:
            raise TransportError("empty key status response")
        return res.data[0]

    def busy_count(self, window: float) -> list[float]:
        """Poll KeyStatu for ``window`` seconds; return the offset (in
        seconds) of every BUSY (0xff) reply.

        The firmware pushes each framebuffer write to the panel a few
        tens of ms after the write (up to ~200ms right after input
        activity), replying BUSY to KeyStatu for the few ms the push
        takes. Hits therefore mean the panel-push path is alive; a
        full-frame write followed by zero hits over >= 0.5s means the
        panel has stopped presenting streamed updates (the click
        freeze). Live-verified: healthy pushes hit at ~30-60ms.
        """
        hits = []
        start = time.monotonic()
        end = start + window
        while time.monotonic() < end:
            try:
                mask = self.key_status()
            except TransportError:
                break
            if mask == 0xFF:
                hits.append(time.monotonic() - start)
        return hits


def _probe_high_speed(dev: O3C) -> bool:
    """Try one 1024-byte 0x22 report; True if the device answers it."""
    dev.report_id, dev.report_size, dev.display_chunk = (
        REPORT_ID_HIGH,
        PACKET_SIZE_HIGH,
        DISPLAY_CHUNK_HIGH,
    )
    try:
        dev.key_status()
        return True
    except (TransportError, PacketError, OSError):
        dev.report_id, dev.report_size, dev.display_chunk = (
            0x21,
            REPORT_SIZE,
            DISPLAY_CHUNK,
        )
        return False


def connect(path: str | None = None, high_speed: bool | None = None) -> O3C:
    """Open the first usable O3C interface (or ``path`` if given).

    With ``high_speed=None`` (default), the 1024-byte high-speed report is
    probed once and used when the device answers it; pass ``False`` to
    force the 64-byte path (e.g. at 1000 Hz polling).
    """
    fd = os.open(path or open_first(), os.O_RDWR)
    dev = O3C(os.fdopen(fd, "rb+", buffering=0))
    if high_speed is None:
        high_speed = _probe_high_speed(dev)
    elif high_speed:
        dev.report_id, dev.report_size, dev.display_chunk = (
            REPORT_ID_HIGH,
            PACKET_SIZE_HIGH,
            DISPLAY_CHUNK_HIGH,
        )
    return dev

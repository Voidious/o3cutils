"""Freeze-reproduction and recovery probe for the SayoDevice O3C.

The menu demo's panel stops presenting new frames shortly after knob
clicks, even though the framebuffer keeps accepting writes (verified by
readback). This probe reproduces the conditions while logging two
headless-visible signals -- KeyStatu masks (including 0xff busy values)
and the undocumented ~1 Hz 0xFF Broadcast packets the device emits --
then runs a scripted recovery sequence so we can see which (if any)
display path still reaches the panel after the freeze:

    uv run python examples/freeze_probe.py [seconds]

Click the knob slowly until the screen stops updating, then let go:
8 s after the last click (or immediately with --selftest) the probe
draws numbered test screens. Note which numbers/colors you see and
paste the console output. Button 3 exits early.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from menu_demo import BLACK, CYAN, WHITE, YELLOW, Frame, H, W

from o3cutils.input import BUTTON3, KNOB_CLICK
from o3cutils.packets import PacketError
from o3cutils.transport import TransportError, connect

CMD_SCREEN_START = 0x21
RED = 0xF800

KEEPALIVE_S = 0.4
QUIET_BEFORE_RECOVERY_S = 8.0
STEP_GAP_S = 4.0


def build_element(etype: int, w: int, h: int, x: int, y: int, color: int) -> bytes:
    """56-byte screen element (type u32@0, w u16@4, h u16@6, x u16@8,
    y u16@10, RGB565 color u16@12, rest zero)."""
    buf = bytearray(56)
    buf[0:4] = etype.to_bytes(4, "little")
    buf[4:6] = w.to_bytes(2, "little")
    buf[6:8] = h.to_bytes(2, "little")
    buf[8:10] = x.to_bytes(2, "little")
    buf[10:12] = y.to_bytes(2, "little")
    buf[12:14] = color.to_bytes(2, "little")
    return bytes(buf)


class Probe:
    def __init__(self, dev):
        self.dev = dev
        self.t0 = time.monotonic()
        self.broadcasts = 0
        self.busy_masks = 0
        self.last_broadcast_at = self.t0

    def log(self, msg: str) -> None:
        print(f"{(time.monotonic() - self.t0) * 1000:9.1f}ms {msg}", flush=True)

    def read_all(self, timeout: float) -> int | None:
        """Read pending reports; log broadcasts, return a KeyStatu mask
        if one arrives (else None)."""
        mask = None
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            try:
                cmds = self.dev.read(left)
            except (TransportError, PacketError, OSError):
                continue
            for cmd in cmds:
                if cmd.id == 0xFF:
                    self.broadcasts += 1
                    self.last_broadcast_at = time.monotonic()
                    self.log(f"bcast seq=0x{cmd.data[1]:02x} {cmd.data.hex()}")
                elif cmd.id == 0x1E:
                    if cmd.data and cmd.data[0] == 0xFF:
                        self.busy_masks += 1
                        self.log("keystatu BUSY 0xff")
                    mask = cmd.data[0] if cmd.data else mask
        return mask

    def poll_keys(self) -> int:
        self.dev.send(0x1E)
        mask = self.read_all(0.03)
        return 0x3F if mask is None else mask

    def count_busy(self, window: float) -> int:
        """Poll KeyStatu rapidly; count 0xff BUSY replies.

        A healthy device reports BUSY for a few ms while it pushes a
        framebuffer write to the panel, so busy>0 right after a write
        means the panel push path is alive (headless freeze detector).
        """
        busy = 0
        end = time.monotonic() + window
        while time.monotonic() < end:
            if self.poll_keys() == 0xFF:
                busy += 1
        return busy

    def draw_label(self, f: Frame, text: str, bg=YELLOW) -> None:
        f.rect(0, 0, W, H, BLACK)
        band = H // 4
        f.rect(0, band, W, band * 2, bg)
        f.text(8, band + band // 2 - 4, text, BLACK, scale=2)
        f.dirty_from, f.dirty_to = 0, H
        f.flush(self.dev)


def recovery_sequence(probe: Probe, f: Frame) -> None:
    dev = probe.dev
    def r1():
        probe.draw_label(f, "1", WHITE)

    def r2():
        # genuine partial write: cyan band across the top rows only
        f.rect(0, 0, W, 14, CYAN)
        f.flush(dev)

    def r3():
        for idx in range(16):
            dev.send(CMD_SCREEN_START, bytes(56), index=idx)
        probe.draw_label(f, "3", WHITE)

    def r4():
        dev.send(0x22, build_element(1, W, H, 0, 0, RED), index=15)
        probe.log("R4 sent RED color element on main layer 15")

    def r5():
        dev.send(0x22, bytes(56), index=15)
        probe.draw_label(f, "4", WHITE)

    def r6():
        end = time.monotonic() + 4.0
        flip = True
        while time.monotonic() < end:
            f.buf[:] = (b"\xff\xff" if flip else b"\x00\x00") * (W * H)
            flip = not flip
            f.dirty_from, f.dirty_to = 0, H
            f.flush(dev)
            time.sleep(0.25)
        probe.draw_label(f, "5", WHITE)

    steps = [
        ("R1 full-frame stream", r1),
        ("R2 partial-row stream", r2),
        ("R3 ScreenStart-null + stream", r3),
        ("R4 red color element", r4),
        ("R5 null element + stream", r5),
        ("R6 continuous streaming", r6),
    ]
    for name, step in steps:
        probe.log(name)
        step()
        probe.log(f"{name}: push busy={probe.count_busy(0.3)} "
                  f"(0 = panel push path dead)")
        deadline = time.monotonic() + STEP_GAP_S
        while time.monotonic() < deadline:
            probe.poll_keys()
            time.sleep(0.01)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", nargs="?", type=float, default=120.0)
    ap.add_argument("--selftest", action="store_true",
                    help="run the recovery sequence immediately (headless check)")
    args = ap.parse_args()

    dev = connect()
    probe = Probe(dev)
    f = Frame()
    dev.null_layers()
    probe.draw_label(f, "CLICK KNOB", WHITE)

    clicks = 0
    prev_click_up = True
    last_click_at = 0.0
    last_debounced = float("-inf")
    recovered = False
    prev_mask = 0x3F
    probe.log(f"start clicks=0 selftest={args.selftest}")
    if args.selftest:
        probe.log("selftest: running recovery sequence immediately")
        recovered = True
        recovery_sequence(probe, f)
    try:
        while True:
            now = time.monotonic()
            if now - probe.t0 > args.seconds:
                break
            if not recovered and clicks >= 2 and now - last_click_at >= QUIET_BEFORE_RECOVERY_S:
                probe.log(f"quiet {QUIET_BEFORE_RECOVERY_S:.0f}s after {clicks} clicks: recovering")
                recovered = True
                recovery_sequence(probe, f)
            if now - getattr(main, "_last_ping", 0.0) >= KEEPALIVE_S:
                dev.keepalive(bytes(f.buf[0:2]))
                main._last_ping = now
            mask = probe.poll_keys()
            if mask != prev_mask:
                probe.log(f"raw=0x{mask:02x}")
                prev_mask = mask
            click_down = not (mask & KNOB_CLICK)
            if click_down and prev_click_up and now - last_debounced >= 0.2:
                clicks += 1
                last_debounced = now
                last_click_at = now
                # Alternate flash / no-flash to bisect the trigger.
                if clicks % 2:
                    probe.log(f"click {clicks}: yellow flash + redraw")
                    probe.draw_label(f, f"CLICK {clicks}", YELLOW)
                    probe.log(f"click {clicks}: push busy="
                              f"{probe.count_busy(0.25)}")
                else:
                    probe.log(f"click {clicks}: no redraw")
            prev_click_up = not click_down
            if not (mask & BUTTON3):
                probe.log("button3: exit")
                break
            time.sleep(0.002)
    finally:
        if recovered:
            dev.clear_screen()
        dev.close()
    probe.log(f"done clicks={clicks} bcasts={probe.broadcasts} busy={probe.busy_masks}")


if __name__ == "__main__":
    main()

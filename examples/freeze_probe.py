"""O3C panel-freeze probe v2: catch the freeze with reliable telemetry,
find the minimal action that un-freezes the panel push, and test whether
delaying redraws until the knob is fully idle avoids the trigger.

v1 lesson: the firmware pushes a framebuffer write to the panel ~200ms
AFTER the write (KeyStatu replies 0xff BUSY for a few ms during the
push), so a 250ms busy-count window straddling that lag gives random
0s on a healthy device. v2 counts busy hits over 700ms, logs their
offsets, and only calls the push path dead after two consecutive
full-frame writes with zero hits.

Run it and click slowly until the yellow flash stops appearing on the
screen (that is the freeze), then let go and wait ~60s. Everything else
is automatic and the probe exits on its own; paste the console output.

    uv run python examples/freeze_probe.py [--selftest]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from menu_demo import BLACK, WHITE, YELLOW, Frame, H, W

from o3cutils.input import BUTTON3, KNOB_CLICK, KNOB_LEFT, KNOB_RIGHT
from o3cutils.packets import PacketError
from o3cutils.transport import TransportError, connect

CMD_SCREEN_START = 0x21
CMD_SCREEN_MAIN = 0x22
RED = 0xF800

KEEPALIVE_S = 0.4
BUSY_WINDOW_S = 0.7  # cover the ~200ms write->push lag with margin
DEAD_STREAK = 2  # dead = this many consecutive zero-hit full writes
WATCH_S = 10.0  # keepalive-only observation after the freeze
IDLE_AFTER_CLICK_S = 0.3  # knob quiet time before a delayed redraw
MAX_RUNTIME_S = 240.0


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
                    self.log(f"bcast seq=0x{cmd.data[1]:02x} {cmd.data.hex()}")
                elif cmd.id == 0x1E:
                    if cmd.data and cmd.data[0] == 0xFF:
                        self.busy_masks += 1
                    mask = cmd.data[0] if cmd.data else mask
        return mask

    def poll_keys(self) -> int:
        self.dev.send(0x1E)
        mask = self.read_all(0.03)
        return 0x3F if mask is None else mask

    def busy_offsets(self, window: float = BUSY_WINDOW_S) -> list[float]:
        """Poll KeyStatu for `window` seconds; return the offset (s) of
        every 0xff BUSY reply. A healthy device pushes each framebuffer
        write to the panel a few hundred ms after the write, so any hit
        within the window means the panel push path is alive."""
        hits = []
        start = time.monotonic()
        end = start + window
        while time.monotonic() < end:
            if self.poll_keys() == 0xFF:
                hits.append(time.monotonic() - start)
        return hits

    def draw_label(self, f: Frame, text: str, bg=YELLOW) -> None:
        f.rect(0, 0, W, H, BLACK)
        band = H // 4
        f.rect(0, band, W, band * 2, bg)
        f.text(8, band + band // 2 - 4, text, BLACK, scale=2)
        f.dirty_from, f.dirty_to = 0, H
        f.flush(self.dev)

    def stream_and_verify(self, label: str, bg=WHITE) -> bool:
        """One full-frame stream followed by a busy-window verdict."""
        f = self._frame
        self.draw_label(f, label, bg)
        hits = self.busy_offsets()
        offsets = " ".join(f"{h * 1000:.0f}ms" for h in hits)
        alive = bool(hits)
        self.log(f"push '{label}': busy at [{offsets}] -> "
                 f"{'ALIVE' if alive else 'DEAD'}")
        return alive

    _frame: Frame = None


def wait_click(probe: Probe, prev_mask: int,
               timeout: float | None = None) -> tuple[int, int]:
    """Poll until a knob click fires (or BUTTON3); return (mask, clicks).
    With ``timeout``, return (prev_mask, 0) if no click arrives in time.

    Keeps the screen awake while waiting: pixel (0,0) is black in every
    probe screen, so the keepalive write is invisible."""
    prev_up = True
    last_debounced = float("-inf")
    last_ping = 0.0
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            probe.log("no click within timeout: skipping")
            return prev_mask, 0
        if now - last_ping >= KEEPALIVE_S:
            probe.dev.keepalive(b"\x00\x00")
            last_ping = now
        mask = probe.poll_keys()
        if mask != prev_mask:
            probe.log(f"raw=0x{mask:02x}")
            prev_mask = mask
        if not (mask & BUTTON3):
            probe.log("button3: exit")
            return mask, -1
        click_down = not (mask & KNOB_CLICK)
        if click_down and prev_up and now - last_debounced >= 0.2:
            last_debounced = now
            return mask, 1
        prev_up = not click_down
        time.sleep(0.002)


def phase_clicks(probe: Probe, f: Frame) -> bool:
    """Immediate-redraw clicks until the push path dies (or 12 clicks).

    Returns True if the freeze was detected (DEAD_STREAK zero-hit
    writes in a row)."""
    probe.log("PHASE 1: click slowly until the yellow flash stops "
              "appearing on screen, then let go")
    probe.draw_label(f, "CLICK KNOB", WHITE)
    prev_mask = 0x3F
    dead_streak = 0
    clicks = 0
    while clicks < 12 and dead_streak < DEAD_STREAK:
        mask, got = wait_click(probe, prev_mask)
        if got < 0:
            return False
        prev_mask = mask
        clicks += 1
        probe.log(f"click {clicks}: immediate redraw")
        alive = probe.stream_and_verify(f"CLICK {clicks}", YELLOW)
        dead_streak = 0 if alive else dead_streak + 1
    probe.log(f"PHASE 1 done clicks={clicks} "
              f"{'FROZEN (push dead)' if dead_streak >= DEAD_STREAK else 'still alive'}")
    return dead_streak >= DEAD_STREAK


def phase_watch(probe: Probe) -> None:
    """Keepalive-only observation: does the push path self-heal?"""
    probe.log(f"PHASE 2: {WATCH_S:.0f}s keepalive-only watch")
    f = probe._frame
    last_ping = 0.0
    end = time.monotonic() + WATCH_S
    while time.monotonic() < end:
        now = time.monotonic()
        if now - last_ping >= KEEPALIVE_S:
            probe.dev.keepalive(bytes(f.buf[0:2]))
            last_ping = now
        probe.poll_keys()
        time.sleep(0.002)
    probe.log("PHASE 2 done (busy hits above = pushes still happening)")


def phase_ladder(probe: Probe) -> bool:
    """Candidate un-freeze actions, cheapest first; after each, one
    full-frame stream + busy verdict. Stops at the first ALIVE."""
    dev = probe.dev

    def silence_then_stream(seconds: float, label: str) -> bool:
        probe.log(f"L{label}: {seconds:.0f}s total silence (no writes)")
        time.sleep(seconds)
        return probe.stream_and_verify(label)

    steps = [
        ("2s silence + stream", lambda: silence_then_stream(2.0, "L1")),
        ("4s more silence + stream", lambda: silence_then_stream(4.0, "L2")),
        ("null layers (0x23+0x22) + stream", lambda: (
            dev.null_layers(),
            probe.stream_and_verify("L3"),
        )[-1]),
        ("ScreenStart nulls (0x21) + stream", lambda: (
            [dev.send(CMD_SCREEN_START, bytes(56), index=i) for i in range(16)],
            probe.stream_and_verify("L4"),
        )[-1]),
        ("red element + null element + stream", lambda: (
            dev.send(CMD_SCREEN_MAIN, build_element(1, W, H, 0, 0, RED), index=15),
            time.sleep(0.5),
            dev.send(CMD_SCREEN_MAIN, bytes(56), index=15),
            probe.stream_and_verify("L5"),
        )[-1]),
    ]
    probe.log("PHASE 3: un-freeze ladder")
    for name, step in steps:
        probe.log(name)
        if step():
            probe.log(f"UN-FROZEN by: {name}")
            return True
    probe.log("LADDER FAILED: no candidate revived the push path")
    return False


def phase_retest(probe: Probe, f: Frame) -> None:
    """4a: immediate redraws again (expect re-freeze). 4b: redraws
    delayed until knob fully idle (candidate avoidance rule)."""
    probe.log("PHASE 4a: 4 more immediate-redraw clicks (expect re-freeze) "
              "-- CLICK SOME MORE")
    prev_mask = 0x3F
    dead = 0
    for i in range(4):
        mask, got = wait_click(probe, prev_mask, timeout=25.0)
        if got < 0:
            return
        if got == 0:
            return
        prev_mask = mask
        alive = probe.stream_and_verify(f"4A-{i + 1}", YELLOW)
        dead = 0 if alive else dead + 1
    probe.log(f"PHASE 4a done dead_writes={dead}")

    probe.log("PHASE 4b: 6 delayed-redraw clicks (redraw only after the "
              "knob is fully idle for 0.3s) -- CLICK SOME MORE")
    dead = 0
    for i in range(6):
        mask, got = wait_click(probe, prev_mask, timeout=25.0)
        if got <= 0:
            return
        prev_mask = mask
        idle_since = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            mask = probe.poll_keys()
            rot_or_click = (not (mask & KNOB_CLICK)
                            or not (mask & KNOB_LEFT)
                            or not (mask & KNOB_RIGHT))
            if not rot_or_click:
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since >= IDLE_AFTER_CLICK_S:
                    break
            else:
                idle_since = None
            time.sleep(0.002)
        alive = probe.stream_and_verify(f"4B-{i + 1}", YELLOW)
        dead = 0 if alive else dead + 1
    probe.log(f"PHASE 4b done dead_writes={dead} "
              "(0 = delayed redraws avoid the freeze)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="one stream+verify round headless, then exit")
    args = ap.parse_args()

    dev = connect()
    probe = Probe(dev)
    f = Frame()
    probe._frame = f
    dev.null_layers()

    try:
        if args.selftest:
            probe.log("selftest: stream + verify on a (hopefully healthy) device")
            probe.stream_and_verify("SELFTEST", WHITE)
        else:
            frozen = phase_clicks(probe, f)
            if frozen:
                phase_watch(probe)
                healed = phase_ladder(probe)
                if healed:
                    phase_retest(probe, f)
            else:
                probe.log("no freeze reproduced; done")
    finally:
        dev.clear_screen()
        dev.close()
    probe.log(f"DONE bcasts={probe.broadcasts} busy={probe.busy_masks}")


if __name__ == "__main__":
    main()

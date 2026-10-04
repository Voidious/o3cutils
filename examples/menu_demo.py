"""Knob-driven menu demo for the SayoDevice O3C.

Scroll with the knob, select with a knob click, exit with button 3.
Renders a list menu to the screen via raw framebuffer streaming, with
dirty-row updates so scrolling only repaints what changed.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from o3cutils.input import (
    BUTTON1,
    BUTTON2,
    BUTTON3,
    KNOB_CLICK,
    KNOB_LEFT,
    KNOB_RIGHT,
    ButtonState,
)
from o3cutils.transport import connect

W, H = 160, 80

# 5x7 font: each glyph is 7 rows of 5 bits (MSB left), one byte per row.
FONT = {
    "A": [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "B": [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
    "C": [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
    "D": [0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C],
    "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
    "F": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
    "G": [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
    "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "I": [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "J": [0x07, 0x02, 0x02, 0x02, 0x12, 0x12, 0x0C],
    "K": [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
    "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
    "M": [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
    "N": [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
    "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "P": [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
    "Q": [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
    "R": [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
    "S": [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
    "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "V": [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
    "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
    "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
    "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
    "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
    "3": [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
    "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    "5": [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
    "6": [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
    " ": [0, 0, 0, 0, 0, 0, 0],
    "-": [0, 0, 0, 0x1F, 0, 0, 0],
    ".": [0, 0, 0, 0, 0, 0x0C, 0x0C],
    ":": [0, 0x0C, 0x0C, 0, 0x0C, 0x0C, 0],
    ">": [0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08],
    "!": [0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04],
    "?": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04],
}

WHITE = 0xFFFF
BLACK = 0x0000
CYAN = 0x07FF
YELLOW = 0xFFE0

TITLE_H = 14  # title bar height
ITEM_H = 16  # one menu row
VISIBLE = (H - TITLE_H) // ITEM_H  # 4 visible items

KEEPALIVE_S = 0.4  # sleep repaint fires ~1.5s after the last display write


def _gray(pressed: int) -> int:
    """Map rotation bits to gray-code position (see KnobDecoder)."""
    return {0: 0, KNOB_RIGHT: 1, KNOB_RIGHT | KNOB_LEFT: 2,
            KNOB_LEFT: 3}[pressed & (KNOB_LEFT | KNOB_RIGHT)]


def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


class Frame:
    """In-memory RGB565 framebuffer with dirty-row tracking."""

    def __init__(self):
        self.buf = bytearray(BLACK.to_bytes(2, "little") * (W * H))
        self.dirty_from = H
        self.dirty_to = 0

    def px(self, x, y, color):
        if 0 <= x < W and 0 <= y < H:
            off = (y * W + x) * 2
            self.buf[off:off + 2] = color.to_bytes(2, "little")
            self.dirty_from = min(self.dirty_from, y)
            self.dirty_to = max(self.dirty_to, y + 1)

    def rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.px(xx, yy, color)

    def text(self, x, y, s, color, scale=1):
        cx = x
        for ch in s.upper():
            glyph = FONT.get(ch, FONT["?"])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        if scale == 1:
                            self.px(cx + col, y + row, color)
                        else:
                            self.rect(cx + col * scale, y + row * scale,
                                      scale, scale, color)
            cx += 6 * scale

    def flush(self, dev):
        if self.dirty_to <= self.dirty_from:
            return
        y0, y1 = self.dirty_from, self.dirty_to
        self.dirty_from, self.dirty_to = H, 0
        rows = bytes(self.buf[y0 * W * 2:y1 * W * 2])
        dev.write_framebuffer(rows, offset=y0 * W * 2)


class Menu:
    """A scrollable list menu driven by the knob."""

    def __init__(self, dev, title, items):
        self.dev = dev
        self.title = title
        self.items = items
        self.selected = 0
        self.top = 0  # first visible item index
        self.frame = Frame()
        self.draw(full=True)

    def draw(self, full=False):
        f = self.frame
        if full:
            f.dirty_from, f.dirty_to = 0, H
        # title bar
        f.rect(0, 0, W, TITLE_H - 2, CYAN)
        f.text(2, 3, self.title, BLACK)
        # items
        for i in range(VISIBLE):
            idx = self.top + i
            y = TITLE_H + i * ITEM_H
            if idx >= len(self.items):
                break
            active = idx == self.selected
            f.rect(0, y, W, ITEM_H - 1, WHITE if active else BLACK)
            f.text(4, y + 4, self.items[idx], BLACK if active else WHITE)
            if active:
                f.text(W - 8, y + 4, ">", BLACK)
        f.flush(self.dev)

    def draw_rows(self, rows):
        """Redraw only the given visible row indexes (cheap scroll update)."""
        f = self.frame
        for i in rows:
            idx = self.top + i
            y = TITLE_H + i * ITEM_H
            if idx >= len(self.items):
                break
            active = idx == self.selected
            f.rect(0, y, W, ITEM_H - 1, WHITE if active else BLACK)
            f.text(4, y + 4, self.items[idx], BLACK if active else WHITE)
            if active:
                f.text(W - 8, y + 4, ">", BLACK)
        f.flush(self.dev)

    def move(self, delta):
        old_sel, old_top = self.selected, self.top
        self.selected = max(0, min(len(self.items) - 1, self.selected + delta))
        self.top = min(self.top, self.selected)
        if self.selected >= self.top + VISIBLE:
            self.top = self.selected - VISIBLE + 1
        if self.selected == old_sel:
            return
        if self.top != old_top:
            self.draw()  # scrolled: everything shifts
        else:
            self.draw_rows({old_sel - old_top, self.selected - old_top})

    def flash_selected(self):
        f = self.frame
        y = TITLE_H + (self.selected - self.top) * ITEM_H
        f.rect(0, y, W, ITEM_H - 1, YELLOW)
        f.text(4, y + 4, self.items[self.selected], BLACK)
        f.flush(self.dev)
        time.sleep(0.15)
        self.draw()
        # Kick: some firmware builds stop presenting streamed framebuffer
        # updates after knob-click input even though the framebuffer keeps
        # accepting them (verified by readback). Re-asserting the empty
        # layer stacks forces a compositor pass, which re-renders the
        # framebuffer onto the panel.
        self.dev.null_layers()
        self.draw(full=True)


class KnobDecoder:
    """Turns raw KeyStatu masks into click/select and knob-step events.

    The knob is a 2-bit gray-code encoder: one detent walks the cycle
    idle -> RIGHT -> BOTH -> LEFT -> idle (mirrored for the other
    direction), and contact bounce repeats adjacent states. So rotation
    is decoded as a quadrature position: adjacent state transitions nudge
    a counter (bounces cancel themselves out), and every net 4 transitions
    = one detent = one step. Clicking wobbles the encoder for up to
    ~200ms (a long RIGHT phase rides the release), so after a click all
    rotation is ignored until knob and click are fully idle.
    """

    # gray-code position of each rotation state: 0=idle, 1=RIGHT only,
    # 2=BOTH, 3=LEFT only (one direction walks 0,1,2,3,0; mirror = 3,2,1,0)
    CLICK_DEBOUNCE_S = 0.2  # click wobble can re-assert within ~150ms

    def __init__(self):
        self.state = 0
        self.pos = 0
        self.rot_lock = False
        self.last_click = float("-inf")
        self.idle_since: float | None = None

    def feed(self, pressed: int, now: float) -> tuple[int, bool]:
        """Feed one sample (bit set = control active); return (step, clicked)."""
        clicked = False
        rot_bits = pressed & (KNOB_LEFT | KNOB_RIGHT)
        if pressed & KNOB_CLICK:
            if not self.rot_lock:
                self.rot_lock = True
                self.pos = 0
                if now - self.last_click >= self.CLICK_DEBOUNCE_S:
                    clicked = True
                    self.last_click = now
        elif self.rot_lock and not rot_bits:
            self.rot_lock = False  # release only once knob and click are idle
            self.state = 0  # resync: the wobble's release edge is not motion
            self.pos = 0
            self.idle_since = now
        rot = _gray(pressed)
        if self.rot_lock:
            self.state = rot  # resync silently while locked
            self.pos = 0
            self.idle_since = None
            return 0, clicked
        step = 0
        if rot == self.state:
            if rot == 0 and self.idle_since is not None and now - self.idle_since >= 0.15:
                self.pos = 0  # partial detent abandoned; drop residue
            return 0, clicked
        if rot == 0:
            self.idle_since = now
        else:
            self.idle_since = None
        delta = ((rot - self.state + 2) % 4) - 2  # -1/+1 adjacent, else illegal
        self.state = rot
        if delta not in (-1, 1):
            self.pos = 0  # skipped a state mid-spin or glitch: resync
            return 0, clicked
        self.pos += delta
        while self.pos >= 4:
            self.pos -= 4
            step += 1
        while self.pos <= -4:
            self.pos += 4
            step -= 1
        return step, clicked


def run(menu_items, title="MENU"):
    dev = connect()
    # Streamed frames must own the whole screen: null out every layer
    # element, or the compositor paints them over the framebuffer.
    dev.null_layers()
    menu = Menu(dev, title, menu_items)
    picked = None
    t0 = time.monotonic()
    polls = 0
    raw = -1
    prev_raw = None
    try:
        knob = KnobDecoder()
        last_ping = 0.0
        while True:
            now = time.monotonic()
            # Any display write resets the sleep timer; rewrite pixel (0,0)
            # (title bar) so the keep-alive is invisible.
            if now - last_ping >= KEEPALIVE_S:
                dev.keepalive(bytes(menu.frame.buf[0:2]))
                last_ping = now
            raw = dev.key_status()
            polls += 1
            if raw != prev_raw:
                print(f"{(now - t0) * 1000:9.1f}ms raw=0x{raw:02x} "
                      f"polls/s={polls / max(now - t0, 1e-9):.0f}", flush=True)
                prev_raw = raw
            if polls % 500 == 0:
                print(f"{(now - t0) * 1000:9.1f}ms alive polls/s="
                      f"{polls / max(now - t0, 1e-9):.0f} "
                      f"sel={menu.selected} lock={knob.rot_lock} "
                      f"pos={knob.pos}", flush=True)
            state = ButtonState.from_mask(raw)
            pressed = 0
            for bit, on in ((BUTTON1, state.button1), (BUTTON2, state.button2),
                            (BUTTON3, state.button3), (KNOB_CLICK, state.knob_click),
                            (KNOB_LEFT, state.knob_left), (KNOB_RIGHT, state.knob_right)):
                if on:
                    pressed |= bit
            step, clicked = knob.feed(pressed, now)
            if step or clicked:
                print(f"{(now - t0) * 1000:9.1f}ms event step={step} "
                      f"click={clicked} sel={menu.selected}", flush=True)
            if clicked:
                menu.flash_selected()
                picked = menu.selected
            if step:
                menu.move(step)
            if pressed & BUTTON3:
                break
            time.sleep(0.002)
    except BaseException:  # diagnostics: never die silently
        import traceback
        traceback.print_exc()
        print(f"died after {(time.monotonic() - t0):.1f}s, {polls} polls "
              f"({polls / max(time.monotonic() - t0, 1e-9):.0f}/s), "
              f"last raw=0x{raw:02x}", flush=True)
        raise
    finally:
        dev.close()
    return picked


if __name__ == "__main__":
    items = sys.argv[1:] or ["VOLUME", "MIC GAIN", "WAKE WORD", "THEME", "ABOUT"]
    choice = run(items)
    print(f"selected: {choice} {items[choice] if choice is not None else '(cancelled)'}")

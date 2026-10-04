"""Raw key/knob diagnostic for the SayoDevice O3C.

Polls KeyStatu (0x1E) as fast as practical and logs every mask change
with a timestamp, so we can see exactly how knob rotation is encoded
(pulse vs held level, quadrature alternation, bounce). Run it, then
twist the knob slowly, click it, and press buttons for the duration:

    uv run python examples/keylog.py 10
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from o3cutils.transport import connect

NAMES = {
    0x01: "B1",
    0x02: "B2",
    0x04: "B3",
    0x08: "CLICK",
    0x10: "LEFT",
    0x20: "RIGHT",
}


def describe(active_mask: int) -> str:
    names = [name for bit, name in NAMES.items() if active_mask & bit]
    return "+".join(names) if names else "-"


def main(seconds: float) -> None:
    dev = connect()
    print(f"polling key status for {seconds:.0f}s (twist, click, press)")
    prev = 0x3F
    t0 = time.monotonic()
    changes = 0
    polls = 0
    try:
        while True:
            now = time.monotonic()
            if now - t0 > seconds:
                break
            mask = dev.key_status()
            polls += 1
            if mask != prev:
                active = 0x3F & ~mask
                dt = (now - t0) * 1000
                print(f"{dt:9.1f}ms  {describe(active)}  (raw 0x{mask:02x})")
                prev = mask
                changes += 1
    finally:
        dev.close()
    print(f"{polls} polls ({polls / seconds:.0f}/s), {changes} changes")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)

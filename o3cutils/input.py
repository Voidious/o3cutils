"""Decoded button and knob state for the O3C.

The KeyStatu (0x1E) response carries an active-low bitmask: a cleared bit
means the control is active (pressed / rotating). Bit 0..2 are buttons
1-3, bit 3 is the knob click, bits 4 and 5 are knob rotation left/right.
"""

from dataclasses import dataclass

from .transport import O3C

BUTTON1 = 0x01
BUTTON2 = 0x02
BUTTON3 = 0x04
KNOB_CLICK = 0x08
KNOB_LEFT = 0x10
KNOB_RIGHT = 0x20


@dataclass
class ButtonState:
    """Instantaneous state of every physical control (True = active)."""

    button1: bool = False
    button2: bool = False
    button3: bool = False
    knob_click: bool = False
    knob_left: bool = False
    knob_right: bool = False

    @classmethod
    def from_mask(cls, mask: int) -> "ButtonState":
        """Decode an active-low KeyStatu bitmask."""
        return cls(
            button1=not mask & BUTTON1,
            button2=not mask & BUTTON2,
            button3=not mask & BUTTON3,
            knob_click=not mask & KNOB_CLICK,
            knob_left=not mask & KNOB_LEFT,
            knob_right=not mask & KNOB_RIGHT,
        )

    @property
    def any_pressed(self) -> bool:
        """True if any button or knob control is active."""
        return (
            self.button1
            or self.button2
            or self.button3
            or self.knob_click
            or self.knob_left
            or self.knob_right
        )


def buttons(dev: O3C) -> ButtonState:
    """Poll the device once and return the decoded control state."""
    return ButtonState.from_mask(dev.key_status())

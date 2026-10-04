from test_transport import FakeDev

from o3cutils.input import ButtonState, buttons
from o3cutils.packets import Cmd, encode_packet
from o3cutils.transport import CMD_KEY_STATUS, O3C


class TestFromMask:
    def test_all_released(self):
        state = ButtonState.from_mask(0x3F)
        assert not state.any_pressed

    def test_button1_pressed(self):
        state = ButtonState.from_mask(0x3E)
        assert state.button1
        assert state.any_pressed
        assert not state.button2

    def test_knob_events(self):
        state = ButtonState.from_mask(0x3F & ~0x10)
        assert state.knob_left
        assert not state.knob_right
        state = ButtonState.from_mask(0x3F & ~0x20)
        assert state.knob_right

    def test_everything_active(self):
        state = ButtonState.from_mask(0x00)
        assert state.any_pressed
        assert all(
            [state.button1, state.button2, state.button3,
             state.knob_click, state.knob_left, state.knob_right]
        )


class TestButtons:
    def test_polls_device(self):
        dev = FakeDev(
            [encode_packet([Cmd(id=CMD_KEY_STATUS, index=0, data=b"\x3d\x00")])]
        )
        state = buttons(O3C(dev))
        assert state.button2
        assert not state.button1

import os

import pytest

from o3cutils.packets import Cmd, encode_packet
from o3cutils.transport import (
    CMD_DISPLAY,
    CMD_INFO,
    CMD_KEY_STATUS,
    CMD_SYSINFO,
    O3C,
    TransportError,
)
from o3cutils.transport import connect as transport_connect


class FakeDev:
    """Records written reports and replays canned reads through a pipe."""

    def __init__(self, responses=()):
        self.written = []
        self.responses = list(responses)
        self.closed = False
        self._r, self._w = os.pipe()
        os.set_blocking(self._w, False)

    def fileno(self):
        return self._r

    def write(self, report):
        self.written.append(bytes(report))
        if self.responses:
            os.write(self._w, self.responses.pop(0))
        return len(report)

    def close(self):
        os.close(self._r)
        os.close(self._w)
        self.closed = True


def info_response(data=b"\x04\x00\x0c\x09\x00\x00\x00\x55\x00\x00\x0a\x00\x01\x02"):
    return encode_packet([Cmd(id=CMD_INFO, index=0, data=data)])


class TestSend:
    def test_defaults_to_index_zero(self):
        dev = FakeDev()
        assert O3C(dev).send(0x00) == 0
        report = dev.written[0]
        assert len(report) == 64
        assert report[4:8] == b"\x04\x00\x00\x00"

    def test_written_report_carries_data(self):
        dev = FakeDev()
        O3C(dev).send(0x02, b"\x01")
        assert dev.written[0][4:9] == b"\x05\x00\x02\x00\x01"


class TestRead:
    def test_decodes_response(self):
        dev = FakeDev([info_response()])
        dev.write(info_response())  # feed the pipe
        cmds = O3C(dev).read()
        assert cmds[0].id == CMD_INFO
        assert cmds[0].index == 0

    def test_empty_read_raises(self, monkeypatch):
        monkeypatch.setattr("o3cutils.transport.READ_TIMEOUT", 0.01)
        with pytest.raises(TransportError, match="timed out"):
            O3C(FakeDev()).read()

    def test_read_after_device_disconnect_raises(self):
        dev = FakeDev()
        os.close(dev._w)  # EOF: selectable but empty
        with pytest.raises(TransportError, match="timed out"):
            O3C(dev).read()


class TestTransact:
    def test_matches_response_by_id_and_index(self):
        broadcast = encode_packet([Cmd(id=0xFF, index=0, data=b"\xc0\xf0")])
        dev = FakeDev([info_response()])
        os.write(dev._w, broadcast)  # unsolicited broadcast arrives first
        cmd = O3C(dev).transact(CMD_INFO)
        assert cmd.id == CMD_INFO

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setattr("o3cutils.transport.READ_TIMEOUT", 0)
        dev = FakeDev([info_response(b"\x01")])  # wrong id, never matches
        with pytest.raises(TransportError, match="no response"):
            O3C(dev).transact(CMD_SYSINFO)

    def test_no_response_at_all_raises(self, monkeypatch):
        monkeypatch.setattr("o3cutils.transport.READ_TIMEOUT", 0.01)
        with pytest.raises(TransportError, match="no response"):
            O3C(FakeDev()).transact(CMD_INFO)


class TestInfo:
    def test_parses_fields(self):
        data = bytes(
            [0x04, 0x00, 0x0C, 0x09, 0x00, 0x00, 0x00, 0x55, 0x00, 0x00, 0x0A]
        ) + bytes([0x06, 0x01, 0x02])
        dev = FakeDev([info_response(data)])
        info = O3C(dev).info()
        assert info["model_code"] == 4
        assert info["firmware_version"] == 0x090C
        assert info["battery"] == 0x55
        assert info["uptime"] == "0s10ms"
        assert info["supported_commands"] == [0x01, 0x02, 0x06]
        # duplicates in the raw list are dropped


class TestSysinfo:
    def test_parses_fields(self):
        data = (
            b"\xa0\x00"  # width
            b"\x50\x00"  # height
            b"\x3c\x00"  # refresh rate + pad
            b"\xd7\x07"  # sys_ms
            b"\x11\x00\x00\x00"  # sys_s
            b"\x89\x80"  # vid
            b"\x09\x00"  # pid
            b"\x01\x05\x00\x00"  # cpu_1m, cpu_5m + pad
            b"\xfc\xac\x06\x00"  # cpu_freq
            b"\x00\x01\x00\x00"  # hclk
            b"\x00\x02\x00\x00"  # pclk_1
            b"\x00\x03\x00\x00"  # pclk_2
            b"\x00\x04\x00\x00"  # adc_0
            b"\x00\x05\x00\x00"  # adc_1
        )
        assert len(data) == 0x2C
        dev = FakeDev([encode_packet([Cmd(id=CMD_SYSINFO, index=0, data=data)])])
        sysinfo = O3C(dev).sysinfo()
        assert sysinfo["width"] == 160
        assert sysinfo["height"] == 80
        assert sysinfo["refresh_rate"] == 60
        assert sysinfo["sys_ms"] == 0x07D7
        assert sysinfo["sys_s"] == 0x11
        assert sysinfo["vid"] == 0x8089
        assert sysinfo["pid"] == 9
        assert sysinfo["cpu_1m"] == 1
        assert sysinfo["cpu_5m"] == 5
        assert sysinfo["cpu_freq"] == 0x06ACFC
        assert sysinfo["hclk"] == 0x100
        assert sysinfo["pclk_1"] == 0x200
        assert sysinfo["pclk_2"] == 0x300
        assert sysinfo["adc_0"] == 0x400
        assert sysinfo["adc_1"] == 0x500


class TestLifecycle:
    def test_context_manager_closes(self):
        dev = FakeDev()
        with O3C(dev):
            pass
        assert dev.closed


class TestDisplay:
    def test_reads_chunk_at_offset(self):
        payload = (0x34).to_bytes(4, "little") + b"\x11" * 52
        dev = FakeDev([encode_packet([Cmd(id=CMD_DISPLAY, index=0, data=payload)])])
        chunk = O3C(dev).display(0x34)
        assert chunk == b"\x11" * 52
        assert dev.written[0][4:12] == b"\x08\x00\x25\x00" + (0x34).to_bytes(4, "little")

    def test_rejects_bad_offsets(self):
        o3c = O3C(FakeDev())
        with pytest.raises(ValueError, match="bad display offset"):
            o3c.display(-4)
        with pytest.raises(ValueError, match="bad display offset"):
            o3c.display(2)
        with pytest.raises(ValueError, match="bad display offset"):
            o3c.display(0x10000)

    def test_offset_mismatch_raises(self):
        payload = (99).to_bytes(4, "little") + b"\x00" * 52
        dev = FakeDev([encode_packet([Cmd(id=CMD_DISPLAY, index=0, data=payload)])])
        with pytest.raises(TransportError, match="offset mismatch"):
            O3C(dev).display(0)


def sysinfo_response():
    data = (
        b"\x02\x00"  # width
        b"\x01\x00"  # height
        b"\x3c\x00\xd7\x07\x00\x00\x00\x00"
        b"\x89\x80\x09\x00\x01\x05\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00"
    )
    return encode_packet([Cmd(id=CMD_SYSINFO, index=0, data=data)])


class TestFramebuffer:
    def test_dumps_whole_framebuffer(self):
        # 2x1 screen = 4 bytes; one 52-byte chunk covers it
        chunk = b"\x11\x22\x33\x44" + b"\x00" * 48
        responses = [sysinfo_response()]
        for i, off in enumerate((0,)):
            responses.append(
                encode_packet(
                    [Cmd(id=CMD_DISPLAY, index=0, data=off.to_bytes(4, "little") + chunk)]
                )
            )
        dev = FakeDev(responses)
        assert O3C(dev).framebuffer() == b"\x11\x22\x33\x44"


class TestConnect:
    def test_connect_opens_path(self, tmp_path):
        node = tmp_path / "devnode"
        node.write_bytes(b"")
        o3c = transport_connect(str(node))
        o3c.close()

    def test_probe_succeeds_with_high_speed_response(self):
        from o3cutils.transport import _probe_high_speed

        dev = FakeDev(
            [
                encode_packet(
                    [Cmd(id=CMD_KEY_STATUS, index=0, data=b"\x3f\x00")],
                    report_id=0x22,
                    packet_size=1024,
                )
            ]
        )
        o3c = O3C(dev)
        assert _probe_high_speed(o3c) is True
        assert o3c.report_id == 0x22 and o3c.report_size == 1024

    def test_probe_falls_back_to_64_byte(self):
        from o3cutils.transport import _probe_high_speed

        dev = FakeDev()  # no response at all
        o3c = O3C(dev)
        o3c.report_id, o3c.report_size = 0x22, 1024
        import o3cutils.transport as tr

        orig = tr.READ_TIMEOUT
        tr.READ_TIMEOUT = 0.01
        try:
            assert _probe_high_speed(o3c) is False
        finally:
            tr.READ_TIMEOUT = orig
        assert o3c.report_id == 0x21 and o3c.report_size == 64

    def test_connect_forced_high_speed(self, tmp_path):
        node = tmp_path / "devnode"
        node.write_bytes(b"")
        o3c = transport_connect(str(node), high_speed=True)
        assert o3c.report_id == 0x22
        assert o3c.report_size == 1024
        assert o3c.display_chunk == 1012
        o3c.close()

    def test_connect_forced_low_speed(self, tmp_path):
        node = tmp_path / "devnode"
        node.write_bytes(b"")
        o3c = transport_connect(str(node), high_speed=False)
        assert o3c.report_id == 0x21 and o3c.report_size == 64
        o3c.close()


class TestHighSpeed:
    def test_send_uses_1024_byte_report_id_0x22(self):
        dev = FakeDev()
        O3C(dev, high_speed=True).send(0x00)
        report = dev.written[0]
        assert len(report) == 1024
        assert report[0] == 0x22

    def test_write_framebuffer_chunks_1012(self):
        dev = FakeDev()
        pixels = b"\xab" * 2024  # two chunks
        O3C(dev, high_speed=True).write_framebuffer(pixels)
        assert len(dev.written) == 2
        assert len(dev.written[0]) == 1024
        assert dev.written[0][4:12] == (
            (1020).to_bytes(2, "little") + b"\x25\x00" + b"\x00\x00\x00\x00"
        )
        assert dev.written[0][12:1024] == pixels[:1012]
        assert dev.written[1][8:12] == (1012).to_bytes(4, "little")
        assert dev.written[1][12:1024] == pixels[1012:]


class TestClearScreen:
    def test_blanks_all_layers_both_stacks(self):
        dev = FakeDev([sysinfo_response()])
        O3C(dev).clear_screen()
        assert len(dev.written) == 33  # sysinfo + 32 layer elements
        cmds = [(w[6], w[7]) for w in dev.written[1:]]  # cmd id, index
        assert set(cmds) == {
            (0x22, i) for i in range(16)
        } | {(0x23, i) for i in range(16)}
        first = dev.written[1][8:64]  # data begins after hid + v2 header
        assert first[0:4] == b"\x01\x00\x00\x00"  # etype: rectangle
        assert first[4:6] == (2).to_bytes(2, "little")  # width from sysinfo
        assert first[6:8] == (1).to_bytes(2, "little")  # height from sysinfo
        assert first[12:14] == b"\x00\x00"  # black


class TestWriteFramebuffer:
    def test_writes_chunked_reports(self):
        dev = FakeDev()
        pixels = bytes(range(104))  # two chunks
        O3C(dev).write_framebuffer(pixels)
        assert len(dev.written) == 2
        assert dev.written[0][4:12] == b"\x3c\x00\x25\x00" + b"\x00\x00\x00\x00"
        assert dev.written[0][12:64] == pixels[:52]
        assert dev.written[1][8:12] == (52).to_bytes(4, "little")
        assert dev.written[1][12:64] == pixels[52:]

    def test_single_small_write(self):
        dev = FakeDev()
        O3C(dev).write_framebuffer(b"\x11\x22", offset=8)
        assert dev.written[0][8:14] == b"\x08\x00\x00\x00\x11\x22"

    def test_partial_final_chunk(self):
        dev = FakeDev()
        pixels = b"\xab" * (52 + 10)
        O3C(dev).write_framebuffer(pixels)
        assert len(dev.written) == 2
        assert dev.written[1][4:6] == b"\x12\x00"
        assert dev.written[1][12:22] == pixels[52:]

    def test_rejects_bad_args(self):
        o3c = O3C(FakeDev())
        with pytest.raises(ValueError, match="bad framebuffer write"):
            o3c.write_framebuffer(b"\x01")  # odd length
        with pytest.raises(ValueError, match="bad framebuffer write"):
            o3c.write_framebuffer(b"\x01\x02", offset=2)  # unaligned
        with pytest.raises(ValueError, match="bad framebuffer write"):
            o3c.write_framebuffer(b"\x01\x02", offset=0x10000)


class TestKeyStatus:
    def test_returns_mask(self):
        dev = FakeDev([encode_packet([Cmd(id=CMD_KEY_STATUS, index=0, data=b"\x3f\x00")])])
        assert O3C(dev).key_status() == 0x3F

    def test_empty_response_raises(self):
        dev = FakeDev([encode_packet([Cmd(id=CMD_KEY_STATUS, index=0, data=b"")])])
        with pytest.raises(TransportError, match="empty key status"):
            O3C(dev).key_status()


class TestTransactSkipsBadPackets:
    def test_undecodable_report_is_skipped(self):
        dev = FakeDev([info_response()])
        os.write(dev._w, b"\xff" * 64)  # garbage report arrives first
        cmd = O3C(dev).transact(CMD_INFO)
        assert cmd.id == CMD_INFO

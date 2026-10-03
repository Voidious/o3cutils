import pytest

from o3cutils.packets import Cmd, encode_packet
from o3cutils.transport import CMD_INFO, O3C, TransportError
from o3cutils.transport import connect as transport_connect


class FakeDev:
    """Records written reports and replays canned reads."""

    def __init__(self, responses=()):
        self.written = []
        self.responses = list(responses)
        self.closed = False

    def write(self, report):
        self.written.append(bytes(report))
        return len(report)

    def read(self, size):
        if self.responses:
            return self.responses.pop(0)
        return b""

    def close(self):
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
        cmds = O3C(dev).read()
        assert cmds[0].id == CMD_INFO
        assert cmds[0].index == 0

    def test_empty_read_raises(self):
        with pytest.raises(TransportError, match="timed out"):
            O3C(FakeDev()).read()


class TestTransact:
    def test_matches_response_by_id_and_index(self):
        broadcast = encode_packet([Cmd(id=0xFF, index=0, data=b"\xc0\xf0")])
        dev = FakeDev([broadcast, info_response()])
        cmd = O3C(dev).transact(CMD_INFO)
        assert cmd.id == CMD_INFO

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setattr("o3cutils.transport.READ_TIMEOUT", 0)
        dev = FakeDev([info_response(b"\x01")])  # short data, no match needed
        with pytest.raises(TransportError, match="no response"):
            O3C(dev).transact(CMD_SYSINFO)


CMD_SYSINFO = 0x02


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


class TestConnect:
    def test_connect_opens_path(self, tmp_path):
        node = tmp_path / "devnode"
        node.write_bytes(b"")
        o3c = transport_connect(str(node))
        o3c.close()

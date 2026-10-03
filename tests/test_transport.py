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


def info_response(index, data=b"\x09\x00\x02\x01\x00\x00\x00\x00\x00\x00\x05\xea"):
    return encode_packet([Cmd(id=CMD_INFO, index=index, data=data)])


class TestSend:
    def test_assigns_sequential_indexes(self):
        dev = FakeDev()
        o3c = O3C(dev, index_seed=41)
        assert o3c.send(0x00) == 42
        assert o3c.send(0x00) == 43
        assert len(dev.written) == 2

    def test_written_report_is_well_formed(self):
        dev = FakeDev()
        O3C(dev).send(0x02, b"\x01")
        report = dev.written[0]
        assert len(report) == 64
        assert report[4:8] == b"\x05\x00\x02\x01"


class TestRead:
    def test_decodes_response(self):
        dev = FakeDev([info_response(1)])
        cmds = O3C(dev).read()
        assert cmds[0].id == CMD_INFO
        assert cmds[0].index == 1

    def test_empty_read_raises(self):
        with pytest.raises(TransportError, match="timed out"):
            O3C(FakeDev()).read()


class TestTransact:
    def test_matches_response_by_id_and_index(self):
        other = info_response(9)
        mine = info_response(10)
        dev = FakeDev([other, mine])
        o3c = O3C(dev, index_seed=9)
        cmd = o3c.transact(CMD_INFO)
        assert cmd.index == 10

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setattr("o3cutils.transport.READ_TIMEOUT", 0)
        dev = FakeDev([info_response(1)])
        o3c = O3C(dev, index_seed=5)
        with pytest.raises(TransportError, match="no response"):
            o3c.transact(CMD_INFO)


class TestInfo:
    def test_parses_fields(self):
        data = b"\x09\x00\x02\x01\x00\x00\x00\x00\x00\x00\x05\xea"
        dev = FakeDev([info_response(1, data)])
        info = O3C(dev, index_seed=0).info()
        assert info["model_code"] == 9
        assert info["firmware_version"] == 0x0102
        assert info["battery"] == 0
        assert info["uptime"] == "5s234ms"


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

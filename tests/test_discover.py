import pytest

from o3cutils.discover import (
    DeviceError,
    find_o3c_devices,
    open_first,
    parse_report_descriptor,
)

O3C_UEVENT = (
    "DRIVER=hid-generic\n"
    "HID_ID=0003:00008089:00000009\n"
    "HID_NAME=SayoDevice SayoDevice O3C\n"
)

# Minimal descriptor: usage page 0xFF11, usage 1, end collection padding.
V2_DESCRIPTOR = bytes([0x06, 0x11, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0xC0])
KEYBOARD_DESCRIPTOR = bytes([0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0xC0])


def make_sys(tmp_path, name, uevent, descriptor):
    node = tmp_path / "sys" / name / "device"
    node.mkdir(parents=True)
    (node / "uevent").write_text(uevent)
    (node / "report_descriptor").write_bytes(descriptor)


class TestParseReportDescriptor:
    def test_collects_usage_pages(self):
        assert parse_report_descriptor(V2_DESCRIPTOR) == {0xFF11}

    def test_multiple_pages(self):
        data = bytes([0x06, 0x00, 0xFF, 0x06, 0x11, 0xFF, 0x91])
        assert parse_report_descriptor(data) == {0xFF00, 0xFF11}

    def test_four_byte_item(self):
        data = bytes([0x07, 0x01, 0x00, 0x00, 0x00, 0x05, 0x01])
        assert parse_report_descriptor(data) == {1}


class TestFindDevices:
    def test_finds_vendor_interface_only(self, tmp_path):
        make_sys(tmp_path, "hidraw0", O3C_UEVENT, KEYBOARD_DESCRIPTOR)
        make_sys(tmp_path, "hidraw1", O3C_UEVENT, V2_DESCRIPTOR)
        devices = find_o3c_devices(str(tmp_path / "sys"))
        assert [d["path"] for d in devices] == ["/dev/hidraw1"]
        assert devices[0]["usage_pages"] == {0xFF11}

    def test_skips_other_devices(self, tmp_path):
        make_sys(
            tmp_path,
            "hidraw0",
            "HID_ID=0003:0000046D:0000C52B\n",
            KEYBOARD_DESCRIPTOR,
        )
        assert find_o3c_devices(str(tmp_path / "sys")) == []

    def test_skips_unreadable_nodes(self, tmp_path):
        make_sys(tmp_path, "hidraw0", O3C_UEVENT, V2_DESCRIPTOR)
        (tmp_path / "sys" / "hidraw0" / "device" / "uevent").unlink()
        assert find_o3c_devices(str(tmp_path / "sys")) == []

    def test_skips_malformed_hid_id(self, tmp_path):
        make_sys(tmp_path, "hidraw0", "HID_ID=nonsense\n", V2_DESCRIPTOR)
        assert find_o3c_devices(str(tmp_path / "sys")) == []

    def test_open_first_raises_when_missing(self, tmp_path):
        with pytest.raises(DeviceError, match="no O3C vendor interface"):
            open_first(str(tmp_path / "sys"))

    def test_open_first_returns_path(self, tmp_path):
        make_sys(tmp_path, "hidraw1", O3C_UEVENT, V2_DESCRIPTOR)
        assert open_first(str(tmp_path / "sys")) == "/dev/hidraw1"


class TestUnreadableDescriptor:
    def test_skips_missing_descriptor(self, tmp_path):
        make_sys(tmp_path, "hidraw0", O3C_UEVENT, V2_DESCRIPTOR)
        (tmp_path / "sys" / "hidraw0" / "device" / "report_descriptor").unlink()
        assert find_o3c_devices(str(tmp_path / "sys")) == []

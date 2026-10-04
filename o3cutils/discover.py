"""Discovery of O3C hidraw devices under /sys/class/hidraw.

The O3C exposes two USB HID interfaces with VID 0x8089 / PID 0x0009: a
standard keyboard interface and the vendor interface used for configuration
(usage page 0xFF00 for API v1 firmware, 0xFF11/0xFF12 for API v2). We find
candidate devices by matching ``HID_ID`` in each hidraw node's uevent, then
parse the report descriptor to see which usage pages each interface speaks.
"""

import glob
import os

VENDOR_ID = 0x8089
PRODUCT_ID = 0x0009

USAGE_PAGE_V1 = 0xFF00
USAGE_PAGE_V2 = 0xFF11
USAGE_PAGE_V2_HIGH = 0xFF12


class DeviceError(OSError):
    """Raised when no usable O3C interface can be found."""


def parse_report_descriptor(data: bytes) -> set[int]:
    """Return the set of usage pages declared in a HID report descriptor."""
    pages = set()
    i = 0
    while i < len(data):
        item = data[i]
        i += 1
        kind = item & 0x03
        size = {0: 0, 1: 1, 2: 2, 3: 4}[kind]
        value = int.from_bytes(data[i : i + size], "little")
        i += size
        if item & 0xFC == 0x04:  # global item: usage page (any size)
            pages.add(value)
    return pages


def read_usage_pages(descriptor_path: str) -> set[int]:
    """Read a report_descriptor sysfs file and return its usage pages."""
    with open(descriptor_path, "rb") as f:
        return parse_report_descriptor(f.read())


def find_o3c_devices(sys_class: str = "/sys/class/hidraw") -> list[dict]:
    """Return O3C interfaces as ``{"path", "usage_pages"}`` dicts.

    Only interfaces carrying a vendor usage page are returned; the plain
    keyboard interface is skipped.
    """
    devices = []
    for link in sorted(glob.glob(os.path.join(sys_class, "hidraw*"))):
        uevent = os.path.join(link, "device", "uevent")
        try:
            with open(uevent) as f:
                fields = dict(line.strip().split("=", 1) for line in f if "=" in line)
        except OSError:
            continue
        hid_id = fields.get("HID_ID", "")
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        _, vid, pid = (int(p, 16) for p in parts)
        if (vid, pid) != (VENDOR_ID, PRODUCT_ID):
            continue
        try:
            pages = read_usage_pages(os.path.join(link, "device", "report_descriptor"))
        except OSError:
            continue
        if not pages & {USAGE_PAGE_V1, USAGE_PAGE_V2, USAGE_PAGE_V2_HIGH}:
            continue
        devices.append(
            {"path": os.path.join("/dev", os.path.basename(link)), "usage_pages": pages}
        )
    return devices


def open_first(sys_class: str = "/sys/class/hidraw") -> str:
    """Return the device path of the first usable O3C interface."""
    devices = find_o3c_devices(sys_class)
    if not devices:
        raise DeviceError(
            f"no O3C vendor interface found under {sys_class} (is the device attached?)"
        )
    return devices[0]["path"]

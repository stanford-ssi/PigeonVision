"""Offline provisioning guards: no authopen, disk writes, or real device queries."""
import importlib.util
import json
from pathlib import Path
import plistlib

import pytest


PLATFORM = Path(__file__).resolve().parents[1] / "platform"


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"pigeonvision_test_{name}", PLATFORM / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def camera_config():
    return load_script("configure_cameras")


def test_camera_configuration_preserves_unrelated_sections_and_is_idempotent(camera_config):
    original = (
        "# Existing board configuration\n"
        "camera_auto_detect=1\n"
        "[pi4]\n"
        "dtoverlay=vc4-kms-v3d\n"
        "max_framebuffers=2\n"
        "[cm5]\n"
        "dtparam=pciex1\n"
        "[all]\n"
        "dtparam=audio=on\n"
        "dtoverlay=fr_imx900,cam1\n"
    )
    rendered = camera_config.render(original)
    assert "[pi4]\ndtoverlay=vc4-kms-v3d\nmax_framebuffers=2\n[cm5]\ndtparam=pciex1\n[all]\ndtparam=audio=on" in rendered
    assert "# superseded by PigeonVision: camera_auto_detect=1" in rendered
    assert "# superseded by PigeonVision: dtoverlay=fr_imx900,cam1\n" in rendered
    assert rendered.count("\ndtoverlay=fr_imx900,cam0\n") == 1
    assert rendered.count("\ndtoverlay=fr_imx900,cam1-compute\n") == 1
    assert camera_config.render(rendered) == rendered


@pytest.mark.parametrize("layout", ["missing_end", "missing_start", "duplicate", "reversed"])
def test_camera_configuration_rejects_malformed_managed_blocks(camera_config, layout):
    malformed = {
        "missing_end": camera_config.START + "\n[all]\n",
        "missing_start": camera_config.END + "\n",
        "duplicate": camera_config.BLOCK + camera_config.BLOCK,
        "reversed": camera_config.END + "\n[pi4]\n" + camera_config.START + "\n",
    }[layout]
    with pytest.raises(ValueError, match="Malformed"):
        camera_config.render(malformed)


def section_for(text, setting):
    section = "all"
    matches = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif line == setting:
            matches.append(section)
    return matches


def test_managed_block_update_preserves_scope_of_following_global_settings(camera_config):
    # Operators can add unrelated settings after the generated block. Removing
    # its [all] line must not move those settings into the previous [pi4] scope.
    original = "[pi4]\ngpu_mem=128\n" + camera_config.BLOCK + "dtparam=audio=on\n[cm5]\ndtparam=pciex1\n"
    rendered = camera_config.render(original)
    for setting in ("gpu_mem=128", "dtparam=audio=on", "dtparam=pciex1"):
        assert section_for(rendered, setting) == section_for(original, setting)
    assert camera_config.render(rendered) == rendered


@pytest.fixture
def disk_guard(monkeypatch):
    flasher = load_script("flash_cm5_macos")
    disk = "disk7"
    serial = "test-cm5-serial"
    capacity = 64_000_000_000
    info = {"WholeDisk": True, "Internal": False, "BusProtocol": "USB",
            "VirtualOrPhysical": "Physical", "MediaName": "mmcblk0 Media",
            "TotalSize": capacity, "Writable": True}
    usb_device = {"serial_num": serial, "manufacturer": "Raspberry Pi",
                  "Media": [{"bsd_name": disk, "size_in_bytes": capacity}]}
    devices = {"SPUSBDataType": [{"_name": "USB Bus", "_items": [usb_device]}]}
    calls = []

    def fake_check_output(args):
        calls.append(args)
        if args == ["diskutil", "info", "-plist", f"/dev/{disk}"]:
            return plistlib.dumps(info)
        if args == ["system_profiler", "SPUSBDataType", "-json"]:
            return json.dumps(devices).encode()
        raise AssertionError(f"Unexpected external command: {args}")

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline guard tests must never authorize or access a block device")

    monkeypatch.setattr(flasher.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(flasher.subprocess, "Popen", forbidden)
    monkeypatch.setattr(flasher.subprocess, "run", forbidden)
    monkeypatch.setattr(flasher, "authorized_fd", forbidden)
    return flasher, info, usb_device, calls, disk, serial, capacity


def test_flasher_accepts_only_matching_mock_emmc_identity(disk_guard):
    flasher, info, _, calls, disk, serial, capacity = disk_guard
    assert flasher.identify(disk, serial, capacity) == info
    assert calls == [["diskutil", "info", "-plist", "/dev/disk7"],
                     ["system_profiler", "SPUSBDataType", "-json"]]


@pytest.mark.parametrize("change", [
    {"Internal": True},
    {"WholeDisk": False},
    {"VirtualOrPhysical": "Virtual"},
    {"BusProtocol": "PCI-Express"},
])
def test_flasher_refuses_internal_partition_virtual_and_nonusb(disk_guard, change):
    flasher, info, _, calls, disk, serial, capacity = disk_guard
    info.update(change)
    with pytest.raises(RuntimeError, match="whole external physical USB"):
        flasher.identify(disk, serial, capacity)
    assert len(calls) == 1  # Rejection occurs before any USB identity query.


def test_flasher_refuses_changed_capacity(disk_guard):
    flasher, info, _, _, disk, serial, capacity = disk_guard
    info["TotalSize"] -= 512
    with pytest.raises(RuntimeError, match="capacity changed"):
        flasher.identify(disk, serial, capacity)


@pytest.mark.parametrize("media_name", ["nvme0n1 Media", "NVMe SSD Media", "External SSD Media"])
def test_flasher_refuses_nvme_and_other_media_names(disk_guard, media_name):
    flasher, info, _, _, disk, serial, capacity = disk_guard
    info["MediaName"] = media_name
    with pytest.raises(RuntimeError, match="eMMC device mmcblk0"):
        flasher.identify(disk, serial, capacity)


@pytest.mark.parametrize("change", [
    {"serial_num": "another-pi"},
    {"manufacturer": "Another Manufacturer"},
    {"Media": [{"bsd_name": "disk8"}]},
])
def test_flasher_refuses_wrong_usb_identity_or_disk_association(disk_guard, change):
    flasher, _, usb_device, _, disk, serial, capacity = disk_guard
    usb_device.update(change)
    with pytest.raises(RuntimeError, match="expected Raspberry Pi USB serial"):
        flasher.identify(disk, serial, capacity)


def test_flasher_refuses_read_only_target(disk_guard):
    flasher, info, _, _, disk, serial, capacity = disk_guard
    info["Writable"] = False
    with pytest.raises(RuntimeError, match="read-only"):
        flasher.identify(disk, serial, capacity)

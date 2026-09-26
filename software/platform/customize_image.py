#!/usr/bin/env python3
"""Customize a *regular image file*, never a device, for key-only first boot.

Requires pyfatfs==1.1.0 and setuptools<81 in a provisioning virtualenv.
Raspberry Pi OS 2025-11-24 supports cloudinit-rpi on its FAT boot partition.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import struct


def customize(image: Path, public_key: Path, hostname: str, username: str):
    from pyfatfs.PyFatFS import PyFatFS

    if not stat.S_ISREG(image.stat().st_mode):
        raise ValueError("Only a regular .img file can be customized; device paths are forbidden")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", hostname):
        raise ValueError("Invalid hostname")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", username):
        raise ValueError("Invalid username")
    key = public_key.read_text().strip()
    if "\n" in key or not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise ValueError("Expected one OpenSSH public key; private keys are never accepted")
    with image.open("rb") as stream:
        mbr = stream.read(512)
    if mbr[510:512] != b"\x55\xaa" or mbr[450] not in (0x0b, 0x0c):
        raise ValueError("Expected the pinned Raspberry Pi image's first FAT32 MBR partition")
    offset = struct.unpack_from("<I", mbr, 454)[0] * 512
    config = "#cloud-config\n" + json.dumps({
        "hostname": hostname, "manage_etc_hosts": True,
        "manage_resolv_conf": False, "timezone": "America/Los_Angeles",
        "user": {"name": username, "shell": "/bin/bash", "lock_passwd": True,
                 "ssh_authorized_keys": [key], "sudo": "ALL=(ALL) NOPASSWD:ALL"},
        "ssh_pwauth": False, "disable_root": True,
        "package_update": False, "package_upgrade": False,
        "runcmd": [["systemctl", "enable", "--now", "ssh"],
                   ["systemctl", "enable", "--now", "avahi-daemon"]],
    }, indent=2) + "\n"
    metadata = json.dumps({"dsmode": "local", "instance-id": f"pigeonvision-{hostname}-20251124"}) + "\n"
    # Ethernet DHCP is provided by the stock NetworkManager configuration.
    # Preserve network-config so a direct-link or Wi-Fi policy can be explicit.
    with PyFatFS(str(image), offset=offset) as fs:
        for required in ("user-data", "meta-data", "kernel_2712.img"):
            if not fs.exists(required):
                raise ValueError(f"Image does not match cloudinit-rpi layout: missing {required}")
        fs.writetext("user-data", config)
        fs.writetext("meta-data", metadata)
    with PyFatFS(str(image), offset=offset, read_only=True) as fs:
        if fs.readtext("user-data") != config or fs.readtext("meta-data") != metadata:
            raise IOError("Cloud-init data verification failed")
    with image.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    evidence = {"image": str(image.resolve()), "sha256": digest, "hostname": hostname,
                "username": username, "public_key_sha256": hashlib.sha256(key.encode()).hexdigest(),
                "fat_offset_bytes": offset, "ssh_password_auth": False}
    print(json.dumps(evidence, indent=2))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--hostname", default="pigeonvision")
    parser.add_argument("--username", default="pigeon")
    args = parser.parse_args()
    customize(args.image, args.public_key, args.hostname, args.username)

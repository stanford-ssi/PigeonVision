#!/usr/bin/env python3
"""Write and read-back verify one explicitly identified CM5 USB eMMC on macOS.

Uses macOS authopen for a narrowly scoped device descriptor. The operator enters
administrator authentication in the native macOS dialog, never in this script.
"""
import argparse
import array
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import subprocess
import sys
import time


def usb_matches(node, disk, serial):
    if isinstance(node, dict):
        if node.get('serial_num') == serial:
            return (node.get('manufacturer') == 'Raspberry Pi'
                    and any(media.get('bsd_name') == disk for media in node.get('Media', [])))
        return any(usb_matches(v, disk, serial) for v in node.values())
    if isinstance(node, list):
        return any(usb_matches(v, disk, serial) for v in node)
    return False


def identify(disk, serial, expected_size):
    info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', f'/dev/{disk}']))
    if not info.get('WholeDisk') or info.get('Internal') or info.get('BusProtocol') != 'USB' or info.get('VirtualOrPhysical') != 'Physical':
        raise RuntimeError('Target must be a whole external physical USB disk')
    if info.get('MediaName') != 'mmcblk0 Media':
        raise RuntimeError('Expected the USB gadget eMMC device mmcblk0, not NVMe or other media')
    if info.get('TotalSize') != expected_size:
        raise RuntimeError(f'Target capacity changed: {info.get("TotalSize")} != {expected_size}')
    if info.get('Writable') is False:
        raise RuntimeError('Target is read-only')
    devices = json.loads(subprocess.check_output(['system_profiler', 'SPUSBDataType', '-json']))
    if not usb_matches(devices, disk, serial):
        raise RuntimeError('Disk is not associated with the expected Raspberry Pi USB serial')
    return info


def authorized_fd(path):
    parent, child = socket.socketpair()
    process = subprocess.Popen(['/usr/libexec/authopen', '-stdoutpipe', '-o', str(os.O_RDWR), path], stdout=child)
    child.close()
    try:
        _, ancillary, _, _ = parent.recvmsg(16, socket.CMSG_SPACE(array.array('i').itemsize))
        result = process.wait()
        if result:
            raise RuntimeError(f'macOS device authorization failed ({result})')
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors = array.array('i')
                descriptors.frombytes(data[:descriptors.itemsize])
                return descriptors[0]
        raise RuntimeError('macOS did not provide an authorized device descriptor')
    finally:
        parent.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--disk', required=True)
    p.add_argument('--usb-serial', required=True)
    p.add_argument('--capacity-bytes', type=int, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    args = p.parse_args()
    if sys.platform != 'darwin' or not re.fullmatch(r'disk[1-9][0-9]*', args.disk):
        p.error('Expected macOS and an explicit diskN identifier other than disk0')
    if not stat.S_ISREG(args.image.stat().st_mode):
        p.error('Image must be a regular file')
    size = args.image.stat().st_size
    if size % 512 or size > args.capacity_bytes:
        p.error('Image must fit the disk and contain whole sectors')
    with args.image.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != args.sha256:
            p.error('Image SHA-256 does not match the approved customized image')
    info = identify(args.disk, args.usb_serial, args.capacity_bytes)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    if args.evidence.exists():
        p.error('Use a new evidence path; existing provisioning evidence is preserved')
    subprocess.run(['diskutil', 'unmountDisk', f'/dev/{args.disk}'], check=True)
    identify(args.disk, args.usb_serial, args.capacity_bytes)
    print(f'Authorizing exact CM5 device /dev/r{args.disk}; image {size} bytes.', flush=True)
    fd = authorized_fd(f'/dev/r{args.disk}')
    try:
        identify(args.disk, args.usb_serial, args.capacity_bytes)
        if os.fstat(fd).st_rdev != os.stat(f'/dev/r{args.disk}').st_rdev:
            raise RuntimeError('Authorized descriptor no longer matches the identified disk')
    except BaseException:
        os.close(fd)
        raise
    evidence = {'disk': args.disk, 'usb_serial': args.usb_serial, 'capacity_bytes': args.capacity_bytes,
                'image': str(args.image.resolve()), 'image_bytes': size, 'sha256': args.sha256,
                'started_unix': time.time(), 'verified': False, 'device_info': info}
    # JSON-compatible subset: diskutil may return plist dates/data on other hosts.
    def save():
        args.evidence.write_text(json.dumps(evidence, indent=2, default=str) + '\n')
    save()
    try:
        with args.image.open('rb') as image:
            written = 0
            while chunk := image.read(4 * 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise IOError('Device write made no progress')
                    view = view[count:]
                written += len(chunk)
                if written % (128 * 1024 * 1024) == 0 or written == size:
                    print(f'Written {written}/{size} bytes', flush=True)
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            chunk = os.read(fd, min(4 * 1024 * 1024, remaining))
            if not chunk:
                raise IOError('Unexpected end of device during read-back')
            digest.update(chunk)
            remaining -= len(chunk)
            if (size - remaining) % (128 * 1024 * 1024) == 0 or remaining == 0:
                print(f'Verified read {size - remaining}/{size} bytes', flush=True)
        evidence['readback_sha256'] = digest.hexdigest()
        if evidence['readback_sha256'] != args.sha256:
            raise IOError('Read-back SHA-256 mismatch; do not boot this image')
        evidence['verified'] = True
        print('Full image read-back SHA-256 matches.', flush=True)
    except BaseException as error:
        evidence['error'] = str(error)
        raise
    finally:
        evidence['finished_unix'] = time.time()
        try:
            save()
        finally:
            os.close(fd)
    subprocess.run(['diskutil', 'eject', f'/dev/{args.disk}'], check=True)
    print('CM5 disk ejected. Power off before removing the boot-mode jumper.', flush=True)


if __name__ == '__main__':
    main()

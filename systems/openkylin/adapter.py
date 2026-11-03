#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare a pinned openKylin ARM64 base without accessing a device."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import struct
import tarfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4194304), b''):
            h.update(block)
    return h.hexdigest()


def identity(archive):
    with tarfile.open(archive) as tf:
        members = {m.name.removeprefix('./'): m for m in tf}
        release = members.get('usr/lib/os-release') or members.get('etc/os-release')
        if release is None or not release.isfile():
            raise ValueError('Base archive lacks a regular os-release')
        text = tf.extractfile(release).read().decode()
        fields = dict(re.findall(r'^([A-Z_]+)=[\"\']?([^\"\'\n]+)', text, re.M))
        if fields.get('ID', '').lower() != 'openkylin' or fields.get('VERSION_ID') != '3.0':
            raise ValueError('Expected openKylin 3.0')
        status = members.get('var/lib/dpkg/status')
        if status is None:
            raise ValueError('Base archive lacks dpkg status')
        packages = tf.extractfile(status).read().decode()
        core = [s for s in packages.split('\n\n') if s.startswith('Package: libc6\n')]
        if not any('\nArchitecture: arm64\n' in s for s in core):
            raise ValueError('Expected native arm64 libc6')
        return fields


def extract_iso(source, output):
    squash = output / 'filesystem.squashfs'
    checksums = output / 'ISO-SHA256SUMS'
    subprocess.run(['xorriso', '-osirrox', 'on', '-indev', str(source),
                    '-extract', '/casper/filesystem.squashfs', str(squash),
                    '-extract', '/SHA256SUMS', str(checksums)], check=True)
    entries = [line.split(maxsplit=1) for line in checksums.read_text().splitlines() if line.strip()]
    expected = [h for h, name in entries if name.lstrip('./') == 'casper/filesystem.squashfs']
    if len(expected) != 1:
        raise ValueError('ISO must contain a unique SHA-256 for its squashfs')
    with squash.open('rb') as f:
        header = f.read(96)
    if header[:4] != b'hsqs':
        raise ValueError('Invalid squashfs header')
    used = struct.unpack_from('<Q', header, 40)[0]
    if used > squash.stat().st_size:
        # Some official 3.0 ARM64 ISOs wrap the ISO9660 length at 4 GiB.
        # Recover the contiguous extent ONLY with an internal checksum match.
        layout = subprocess.check_output(['xorriso', '-indev', str(source),
            '-find', '/casper/filesystem.squashfs', '-exec', 'report_lba', '--'], text=True)
        rows = re.findall(r'File data lba:\s+0\s*,\s*(\d+)\s*,\s*\d+\s*,\s*(\d+)', layout)
        if len(rows) != 1:
            raise ValueError('Cannot recover a non-contiguous squashfs extent')
        offset, length = int(rows[0][0]) * 2048, int(rows[0][1])
        length += ((used - length + (1 << 32) - 1) // (1 << 32)) * (1 << 32)
        if used > length or length - used >= 4096 or offset + length > source.stat().st_size:
            raise ValueError('Squashfs extent is outside the ISO')
        squash.chmod(0o644)
        with source.open('rb') as src, squash.open('wb') as dst:
            src.seek(offset)
            remaining = length
            while remaining:
                block = src.read(min(4194304, remaining))
                if not block:
                    raise ValueError('Truncated ISO extent')
                dst.write(block)
                remaining -= len(block)
    if digest(squash) != expected[0]:
        raise ValueError('ISO squashfs SHA-256 mismatch')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--sha256')
    a = p.parse_args()
    a.output = a.output.resolve()
    a.source = a.source.resolve()
    if a.output.exists() or not a.source.is_file():
        p.error('Use an existing ISO or tar archive and a new output directory')
    sidecar = a.source.with_name(a.source.name + '.sha256')
    expected = a.sha256 or (sidecar.read_text().split()[0] if sidecar.exists() else None)
    if not expected or not re.fullmatch('[0-9a-fA-F]{64}', expected):
        p.error('Provide --sha256 or a source .sha256 sidecar')
    if digest(a.source) != expected.lower():
        p.error('Source checksum mismatch')
    a.output.mkdir(parents=True)
    archive = a.output / 'rootfs.tar'
    with a.source.open('rb') as f:
        f.seek(32769)
        iso = f.read(5) == b'CD001'
    if iso:
        extract_iso(a.source, a.output)
        # Convert directly: no host chown/mknod and no loss of unreadable files,
        # numeric owners, symlinks, hard links, capabilities or other xattrs.
        with archive.open('wb') as target:
            subprocess.run(['sqfs2tar', '--no-skip', '--root-becomes', '.',
                            str(a.output / 'filesystem.squashfs')], stdout=target, check=True)
    else:
        shutil.copyfile(a.source, archive)
    release = identity(archive)
    result = {'system': 'openkylin', 'version': '3.0', 'architecture': 'arm64',
              'source_format': 'iso9660' if iso else 'tar', 'source_sha256': expected.lower(),
              'base_sha256': digest(archive), 'os_release': release,
              'stage': 'official live filesystem; board configuration required',
              'device_tested': False, 'device_write_performed': False}
    if iso:
        result['squashfs_sha256'] = digest(a.output / 'filesystem.squashfs')
    (a.output / 'prepare.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

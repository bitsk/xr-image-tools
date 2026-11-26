#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the public A081 ramdisk from the clean ARM64 base and locked board firmware."""
import argparse
import hashlib
import json
import lzma
import subprocess
import tempfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
for arg in ('root', 'vendor-cache', 'vendor-lock', 'gen-init-cpio', 'output'):
    p.add_argument('--' + arg, required=True, type=Path)
a = p.parse_args()
root = a.root.resolve()
files = {
    '/init': (Path(__file__).with_name('init').resolve(), '0755'),
    '/shutdown': (Path(__file__).with_name('shutdown').resolve(), '0755'),
    '/bin/busybox': (root / 'usr/bin/busybox', '0755'),
    '/lib/ld-linux-aarch64.so.1': (root / 'usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1', '0755'),
}
for lib in ('libc.so.6', 'libresolv.so.2'):
    files['/lib/' + lib] = (root / 'usr/lib/aarch64-linux-gnu' / lib, '0755')
for item in json.loads(a.vendor_lock.read_text())['bundles']['firmware']['files']:
    name = Path(item['install_path']).relative_to('usr/lib/firmware/xr/a081')
    src = (a.vendor_cache / 'firmware' / item['path']).resolve()
    if not src.is_relative_to(a.vendor_cache.resolve()) or hashlib.sha256(src.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('Firmware digest mismatch: ' + str(name))
    files['/lib/firmware/' + str(name)] = (src, '0644')
dirs = {'/dev', '/sys', '/proc', '/run', '/store', '/newroot'}
for name in files:
    dirs.update(str(parent) for parent in Path(name).parents if parent != Path('/'))
entries = ['dir ' + d + ' 0755 0 0' for d in sorted(dirs)]
entries += ['nod /dev/console 0600 0 0 c 5 1', 'slink /bin/sh busybox 0777 0 0']
with tempfile.TemporaryDirectory() as temp:
    for index, (name, (source, mode)) in enumerate(sorted(files.items())):
        # gen_init_cpio uses whitespace as its input delimiter.
        alias = Path(temp) / str(index)
        alias.symlink_to(source)
        entries.append(f'file {name} {alias} {mode} 0 0')
    spec = Path(temp) / 'spec'
    spec.write_text('\n'.join(entries) + '\n')
    archive = subprocess.check_output([a.gen_init_cpio, '-t', '0', spec])
with a.output.open('xb') as output:
    output.write(lzma.compress(archive, check=lzma.CHECK_CRC32))

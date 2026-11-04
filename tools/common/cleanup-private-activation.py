#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Retire only activated portal/GVfs resources in one owned development session."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time


def process(pid):
    root = Path('/proc') / str(pid)
    try:
        if root.stat().st_uid != os.getuid():
            return None
        fields = (root / 'stat').read_text().rsplit(') ', 1)[1].split()
        if fields[0] == 'Z':
            return None
        env = {}
        for item in (root / 'environ').read_bytes().split(b'\0'):
            if b'=' in item:
                key, value = item.split(b'=', 1)
                env[key.decode(errors='replace')] = value.decode(errors='replace')
        return {'pid': int(pid), 'birth': fields[19],
                'executable': os.readlink(root / 'exe'), 'environment': env}
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None


def session_processes(private, bus):
    result = []
    for root in Path('/proc').glob('[0-9]*'):
        item = process(root.name)
        if not item:
            continue
        env = item['environment']
        if (bus and env.get('XDG_RUNTIME_DIR') == str(private / 'runtime')
                and env.get('XDG_CONFIG_HOME') == str(private / 'config')
                and env.get('DBUS_SESSION_BUS_ADDRESS') == bus):
            item['environment'] = {k: env[k] for k in
                                   ['XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'DBUS_SESSION_BUS_ADDRESS']}
            result.append(item)
    return result


def activated(item):
    name = Path(item['executable']).name
    return name.startswith(('xdg-desktop-portal', 'gvfs')) or name in {
        'xdg-document-portal', 'xdg-permission-store',
        'kylin-status-manager', 'peony-intelligent-data-management-service'}


def same_process(item, private, bus):
    current = process(item['pid'])
    if not current or current['birth'] != item['birth']:
        return False
    env = current['environment']
    return (env.get('XDG_RUNTIME_DIR') == str(private / 'runtime')
            and env.get('XDG_CONFIG_HOME') == str(private / 'config')
            and env.get('DBUS_SESSION_BUS_ADDRESS') == bus)


def unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), value)


def mounts(private):
    result = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        first, tail = line.split(' - ', 1)
        fields, details = first.split(), tail.split()
        target = Path(unescape(fields[4]))
        if target == private or private in target.parents:
            result.append({'target': str(target), 'fstype': details[0],
                           'source': unescape(details[1]), 'options': details[2]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private', required=True, type=Path)
    parser.add_argument('--runtime-root', required=True, type=Path)
    args = parser.parse_args()
    private = args.private
    report = {'private': str(private), 'processes': [], 'signals': [],
              'mounts_before': [], 'unmounts': [], 'errors': []}
    try:
        if (private.is_symlink() or private.parent.resolve() != args.runtime_root.resolve()
                or not private.name.startswith('xr-desktop-dev.')
                or private.stat().st_uid != os.getuid()
                or stat.S_IMODE(private.stat().st_mode) != 0o700):
            raise RuntimeError('Refusing an unowned/non-private session directory')
        private = private.resolve()
        bus_file = private / 'session-bus-address'
        bus = bus_file.read_text().strip() if bus_file.is_file() and not bus_file.is_symlink() else ''
        report['processes'] = session_processes(private, bus)
        report['mounts_before'] = mounts(private)
        owned = [item for item in report['processes'] if activated(item)]
        for item in owned:
            if same_process(item, private, bus):
                try:
                    os.kill(item['pid'], signal.SIGTERM)
                    report['signals'].append({'pid': item['pid'], 'birth': item['birth'], 'signal': 'TERM'})
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(same_process(p, private, bus) for p in owned):
            time.sleep(0.05)
        for item in owned:
            if same_process(item, private, bus):
                try:
                    os.kill(item['pid'], signal.SIGKILL)
                    report['signals'].append({'pid': item['pid'], 'birth': item['birth'], 'signal': 'KILL'})
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and any(same_process(p, private, bus) for p in owned):
            time.sleep(0.05)
        fuse_tool = shutil.which('fusermount3') or shutil.which('fusermount')
        deadline = time.monotonic() + 10
        for mount in sorted(mounts(private), key=lambda m: len(Path(m['target']).parts), reverse=True):
            target = Path(mount['target'])
            allowed_path = any(target == private / 'runtime' / name
                               or private / 'runtime' / name in target.parents
                               for name in ['doc', 'gvfs'])
            owner = f'user_id={os.getuid()}' in mount['options'].split(',')
            if (not bus or not allowed_path or not owner
                    or mount['fstype'] not in {'fuse.portal', 'fuse.gvfsd-fuse'}):
                report['errors'].append('Unverified mount preserved: ' + str(target))
                continue
            if not fuse_tool or time.monotonic() >= deadline:
                report['errors'].append('No bounded FUSE unmount available: ' + str(target))
                continue
            try:
                p = subprocess.run([fuse_tool, '-u', str(target)], capture_output=True,
                                   text=True, timeout=min(3, max(0.1, deadline-time.monotonic())))
                report['unmounts'].append({'target': str(target), 'status': p.returncode,
                                          'stdout': p.stdout.strip(), 'stderr': p.stderr.strip()})
                if p.returncode:
                    report['errors'].append('FUSE unmount failed: ' + str(target))
            except subprocess.TimeoutExpired:
                report['errors'].append('FUSE unmount timed out: ' + str(target))
        report['processes_after'] = session_processes(private, bus)
        report['mounts_after'] = mounts(private)
        if report['processes_after'] or report['mounts_after']:
            report['errors'].append('Private resources remain; directory must be preserved')
    except Exception as error:
        report['errors'].append(str(error))
    report['success'] = not report['errors']
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    sys.exit(main())

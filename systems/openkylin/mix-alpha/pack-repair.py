#!/usr/bin/env python3
"""Export the four installed ARM64 package updates and first-boot files for MIX Alpha."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--packages', type=Path, required=True, help='Verified locked build package directory')
parser.add_argument('--old-recovery', type=Path, required=True)
parser.add_argument('--new-recovery', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
recipe = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def normalize(member):
    member.uid = member.gid = member.mtime = 0
    member.uname = member.gname = ''
    return member


args.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=args.output) as work:
    stage = Path(work)
    packages = stage / 'packages'
    packages.mkdir()
    for name, arch in [('xr-head-tracking', 'arm64'), ('xr-head-tracking-tools', 'arm64'),
                       ('xr-session-manager', 'all'), ('xr-diagnostic-tools', 'all')]:
        deb, = args.packages.glob(f'{name}_*_{arch}.deb')
        if subprocess.check_output(['dpkg-deb', '-f', deb, 'Package'], text=True).strip() != name:
            raise ValueError(f'Wrong package: {deb.name}')
        shutil.copy2(deb, packages / deb.name)
    for name in ('apply-alpha-update', 'make-tracker-config.py', 'mix-alpha-calibration',
                 'mix-alpha-account', 'start-xr', 'mix-alpha-xr.service', '10-mix-alpha.conf'):
        shutil.copy2(recipe / name, stage / name)
    sums = [f'{digest(p)}  {p.relative_to(stage)}\n' for p in sorted(stage.rglob('*')) if p.is_file()]
    (stage / 'payload.sha256').write_text(''.join(sums))
    archive = args.output / 'mix-alpha-repair.tar'
    with tarfile.open(archive, 'w', format=tarfile.GNU_FORMAT) as tar:
        for path in sorted(stage.rglob('*')):
            tar.add(path, arcname=str(path.relative_to(stage)), recursive=False, filter=normalize)
    unit = args.output / 'mix-alpha-repair.service'
    shutil.copyfile(recipe / unit.name, unit)
    manifest = dict(payload_sha256=digest(archive), unit_sha256=digest(unit),
                    previous_recovery_sha256=digest(args.old_recovery),
                    recovery_sha256=digest(args.new_recovery))
    (args.output / 'mix-alpha-repair.json').write_text(json.dumps(manifest, indent=2) + '\n')

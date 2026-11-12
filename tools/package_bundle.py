# SPDX-License-Identifier: GPL-3.0-or-later
"""Carry checksum-locked Debian packages into a system composition bundle."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import shlex
import subprocess
import tarfile


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def export(manifest_path, output, selected, lock_digest, requested, expected_build_order):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest['component_lock_sha256'] != lock_digest:
        raise ValueError('Package manifest and composition use different component locks')
    if set(expected_build_order) - set(manifest.get('completed', [])):
        raise ValueError('The selected runtime source components have not all been built')
    for name, entry in selected.items():
        if manifest['components'].get(name, {}).get('commit') != entry['commit']:
            raise ValueError('Package source commit mismatch: ' + name)
    repository = output / 'packages'
    repository.mkdir()
    payload_owners = {}
    packages = {}
    for item in manifest['packages']:
        name = item['file']
        if Path(name).name != name or not name.endswith('.deb'):
            raise ValueError('Invalid package filename')
        source = manifest_path.parent / 'packages' / name
        if not source.is_file() or source.is_symlink() or digest(source) != item['sha256']:
            raise ValueError('Package payload digest mismatch: ' + name)
        if item['architecture'] not in ('all', 'arm64'):
            raise ValueError('Foreign package in ARM64 composition: ' + name)
        package = item['package']
        if package in packages:
            raise ValueError('Multiple versions of a selected package: ' + package)
        packages[package] = item['version']
        process = subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(source)], stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                for member in archive:
                    if member.isdir():
                        continue
                    path = str(Path(member.name.removeprefix('./')))
                    if path.startswith('/') or '..' in Path(path).parts:
                        raise ValueError('Invalid package member: ' + path)
                    previous = payload_owners.setdefault(path, package)
                    if previous != package:
                        raise ValueError('Competing package owners: ' + path + ': ' + previous + '/' + package)
            if process.wait() != 0:
                raise ValueError('Cannot read Debian package: ' + name)
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        shutil.copyfile(source, repository / name)
    pins = []
    for name in requested:
        if name not in packages:
            raise ValueError('Profile requests an unbuilt package: ' + name)
        pins.append(name + '=' + packages[name])
    index = subprocess.check_output(['dpkg-scanpackages', '--multiversion', '.', '/dev/null'], cwd=repository)
    (repository / 'Packages').write_bytes(index)
    (repository / 'Packages.gz').write_bytes(gzip.compress(index, mtime=0))
    (repository / 'SHA256SUMS').write_text(''.join(
        item['sha256'] + '  ' + item['file'] + '\n' for item in manifest['packages']))
    (output / 'install-packages.sh').write_text(
        '#!/bin/sh\nset -eu\n'
        'repo=$(CDPATH= cd -- "$(dirname -- "$0")/packages" && pwd)\n'
        '(cd "$repo" && sha256sum --quiet -c SHA256SUMS)\n'
        'list=$(mktemp)\ntrap \'rm -f "$list"\' EXIT HUP INT TERM\n'
        'printf "deb [trusted=yes] file:%s ./\\n" "$repo" > "$list"\n'
        'apt-get -o Dir::Etc::sourcelist="$list" update\n'
        'apt-get -o Dir::Etc::sourcelist="$list" install --no-install-recommends '
        + ' '.join(shlex.quote(pin) for pin in pins) + '\n')
    (output / 'install-packages.sh').chmod(0o755)
    return dict(packages=manifest['packages'], requested=pins,
                source_lock_sha256=lock_digest, payload_ownership_checked=True,
                installed_into_base=False, activation_performed=False)

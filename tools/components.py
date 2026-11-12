#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build distribution packages from exact, independently owned Git snapshots."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def command(argv, **options):
    return subprocess.run([str(value) for value in argv], check=True, **options)


def package_info(path):
    fields = subprocess.check_output(
        ['dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture'], text=True)
    info = dict(line.split(': ', 1) for line in fields.splitlines())
    return dict(package=info['Package'], version=info['Version'],
                architecture=info['Architecture'], file=path.name,
                sha256=digest(path), bytes=path.stat().st_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'checkout', 'build', 'index'])
    parser.add_argument('--profile', default='vr-hands')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-cache', type=Path,
                        help='Optional canonical Git object cache; dirty files are never read')
    parser.add_argument('--environment', type=Path,
                        help='Explicit toolchain/sysroot/cache configuration; never a source overlay')
    parser.add_argument('--only', action='append', default=[])
    parser.add_argument('--builder-commit', help='Exact builder source commit when running an installed tool')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not args.profile or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in args.profile):
        parser.error('Invalid profile identifier')
    lock = read(ROOT / 'components.lock.json')
    profile = read(ROOT / 'profiles' / (args.profile + '.json'))
    recipes = read(ROOT / 'build-recipes.json')
    selected = list(profile['components'])
    registry = {r['repository']: r['url'] for r in read(ROOT / 'repositories.json')['repositories']}
    builder_commit = args.builder_commit
    if not builder_commit and (ROOT/'builder-commit.txt').is_file():
        builder_commit=(ROOT/'builder-commit.txt').read_text().strip()
    if not builder_commit:
        result = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True)
        if result.returncode == 0 and (ROOT / '.git').exists():
            builder_commit = result.stdout.strip()
    if 'xr-image-tools' not in selected:
        if not builder_commit or len(builder_commit) != 40:
            parser.error('Supply --builder-commit for the exact installed builder source')
        # A build system cannot include its own commit in its committed lock.
        # Record it independently, then build its runtime/session package too.
        selected.append('xr-image-tools')
        lock['components']['xr-image-tools'] = dict(repository=registry['xr-image-tools'],
                                                   commit=builder_commit)
    order = [name for name in recipes['order'] if name in selected]
    if args.only:
        unknown = set(args.only) - set(order)
        if unknown:
            parser.error('Components are not selected by this profile: ' + ', '.join(sorted(unknown)))
        order = [name for name in order if name in args.only]
    out = args.output.resolve()
    if args.command != 'index' and out.exists() and not args.resume:
        parser.error('Choose a new output directory, or explicitly resume the same locked build')
    out.mkdir(parents=True, exist_ok=True)
    receipt = dict(schema=1, profile=args.profile,
                   component_lock_sha256=digest(ROOT / 'components.lock.json'),
                   recipes_sha256=digest(ROOT / 'build-recipes.json'),
                   components={name: lock['components'][name] for name in selected},
                   build_order=order, packages=[], hardware_validated=False,
                   device_write_performed=False)
    receipt_path = out / 'build-manifest.json'
    if args.resume:
        before = read(receipt_path)
        for field in ['component_lock_sha256', 'recipes_sha256', 'profile', 'components']:
            if before[field] != receipt[field]:
                parser.error('Resume would mix different build inputs: ' + field)
        receipt = before
    if args.command == 'index':
        receipt = read(receipt_path)
        rows = []
        for item in sorted((out / 'packages').glob('*.deb')):
            rows.append(package_info(item))
        text = subprocess.check_output(['dpkg-scanpackages', '--multiversion', '.', '/dev/null'],
                                       cwd=out / 'packages')
        (out / 'packages/Packages').write_bytes(text)
        (out / 'packages/Packages.gz').write_bytes(gzip.compress(text, mtime=0))
        receipt['packages'] = rows
        write(receipt_path, receipt)
        print(json.dumps(dict(packages=len(rows), manifest=str(receipt_path))))
        return
    write(receipt_path, receipt)
    if args.command == 'plan':
        print(json.dumps(receipt, indent=2))
        return
    sources = out / 'sources'
    sources.mkdir(exist_ok=True)
    # Fetch each selected repository before building any package. Relative Cargo
    # source dependencies consequently refer to a locked sibling, never a
    # checkout selected by cwd, a historical workspace, or a build timestamp.
    for name in selected:
        entry = lock['components'][name]
        repository = entry['repository']
        if repository != registry.get(name):
            raise ValueError('Noncanonical component repository: ' + name)
        destination = sources / name
        if not destination.exists():
            cache = args.source_cache / name if args.source_cache else None
            origin = cache if cache is not None else repository + '.git'
            command(['git', 'clone', '--no-checkout', origin, destination], stdout=subprocess.DEVNULL)
            command(['git', '-C', destination, 'remote', 'set-url', 'origin', repository + '.git'])
            command(['git', '-C', destination, 'checkout', '--detach', entry['commit']],
                    stdout=subprocess.DEVNULL)
        head = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip()
        if head != entry['commit']:
            raise ValueError('Source commit changed: ' + name)
        for file, expected in entry.get('public_headers_sha256', {}).items():
            if digest(destination / file) != expected:
                raise ValueError('Public interface digest mismatch: ' + name + '/' + file)
    if args.command == 'checkout':
        return
    config = read(args.environment) if args.environment else {}
    if args.environment:
        if (receipt.get('environment_sha256') is not None and
                receipt['environment_sha256'] != digest(args.environment)):
            raise ValueError('Resume would mix different toolchain or SDK inputs')
        receipt['environment_sha256'] = digest(args.environment)
    env = dict(os.environ)
    env.update(config.get('environment', {}))
    env.update(DEB_BUILD_OPTIONS='nocheck', SOURCE_DATE_EPOCH='0')
    env['XR_OPENVR_INCLUDE_DIR'] = str(sources / 'xr-monado/src/external/openvr_includes')
    sysroot = out / 'sysroot'
    sysroot.mkdir(exist_ok=True)
    prefix = sysroot / 'usr'
    env.update(XR_DEVICE_PREFIX=str(prefix), XR_DEVICE_INCLUDE_DIR=str(prefix / 'include'),
               XR_KERNEL_UAPI_DIR=str(prefix / 'include/xr-kernel'),
               XR_HEXAGONRPC_SOURCE=str(prefix / 'share/xr-hexagonrpc/source'),
               CMAKE_PREFIX_PATH=str(prefix) + (':' + env['CMAKE_PREFIX_PATH'] if env.get('CMAKE_PREFIX_PATH') else ''),
               DEB_CFLAGS_MAINT_APPEND='-I' + str(prefix / 'include'),
               DEB_CXXFLAGS_MAINT_APPEND='-I' + str(prefix / 'include'))
    packages = out / 'packages'
    packages.mkdir(exist_ok=True)
    logs = out / 'logs'
    logs.mkdir(exist_ok=True)
    receipt.setdefault('completed', [])
    receipt.setdefault('external_inputs', [])
    for item in config.get('external_packages', []):
        path = Path(item['path'])
        if digest(path) != item['sha256']:
            raise ValueError('Native dependency package changed: ' + path.name)
        owner = sources / item['source_component']
        for field in ['source_recipe', 'source_lock']:
            if digest(owner / item[field]) != item[field + '_sha256']:
                raise ValueError('Native dependency source recipe changed: ' + path.name)
        destination = packages / path.name
        shutil.copy2(path, destination)
        command(['dpkg-deb', '-x', destination, sysroot], stdout=subprocess.DEVNULL)
        receipt['external_inputs'].append(dict(name=path.name, sha256=item['sha256'],
                                              source_component=item['source_component'],
                                              source_lock_sha256=item['source_lock_sha256']))
    for item in config.get('inputs', []):
        path = Path(item['path'])
        if digest(path) != item['sha256']:
            raise ValueError('Build dependency changed: ' + path.name)
        receipt['external_inputs'].append(dict(name=path.name, sha256=item['sha256']))
    for name in order:
        if name in receipt['completed']:
            continue
        recipe = recipes['components'][name]
        component_env = dict(env)
        component_env.update(config.get('components', {}).get(name, {}))
        if config.get('architecture'):
            component_env.setdefault('XR_CARGO_TARGET', 'aarch64-unknown-linux-gnu')
            component_env.setdefault('CARGO_BUILD_TARGET', 'aarch64-unknown-linux-gnu')
        component_env['XR_CARGO_TARGET_DIR'] = str(out / 'cargo-target' / name)
        repo = sources / name
        with (logs / (name + '.log')).open('w') as log:
            cargo_manifest = repo / recipe.get('cargo_manifest', 'Cargo.toml')
            if config.get('cargo_fetch', False) and cargo_manifest.is_file():
                command(['cargo', 'fetch', '--locked', '--manifest-path', cargo_manifest],
                        env=component_env, stdout=log, stderr=subprocess.STDOUT)
            if recipe['kind'] == 'kernel-uapi':
                argv = [sys.executable, repo / 'packaging/build-uapi-deb.py', '--output', packages,
                        '--version', recipe['version']]
                command(argv, env=component_env, stdout=log, stderr=subprocess.STDOUT)
            elif recipe['kind'] == 'debian':
                builds = [repo / p for p in recipe.get('directories', ['.'])]
                for work in builds:
                    argv = ['dpkg-buildpackage', '-b', '-us', '-uc']
                    if config.get('architecture'):
                        argv.append('-a' + config['architecture'])
                    if config.get('external_dependency_check') == 'explicit-cross-sysroot':
                        argv.append('-d')
                    command(argv, cwd=work, env=component_env, stdout=log, stderr=subprocess.STDOUT)
                    for item in work.parent.glob('*.deb'):
                        info = package_info(item)
                        destination = packages / item.name
                        if destination.exists() and digest(destination) != info['sha256']:
                            raise ValueError('Conflicting package payload: ' + item.name)
                        shutil.copy2(item, destination)
                        command(['dpkg-deb', '-x', destination, sysroot], stdout=log)
            elif recipe['kind'] == 'vendor':
                cache = component_env.get('XR_VENDOR_CACHE')
                if not cache:
                    raise ValueError('Provide the verified XR_VENDOR_CACHE for OEM package inputs')
                work = out / 'vendor-packages'
                command([sys.executable, repo / 'tools/build-debs.py', '--cache', cache,
                         '--lock', sources / 'openkylin-xr-integration/devices/a081/vendor.lock.json',
                         '--output', work], env=component_env, stdout=log, stderr=subprocess.STDOUT)
                for item in work.glob('*.deb'):
                    shutil.copy2(item, packages / item.name)
                    command(['dpkg-deb', '-x', packages / item.name, sysroot], stdout=log)
            else:
                raise ValueError('Unknown recipe kind for ' + name)
        for item in packages.glob('xr-kernel-uapi-dev_*.deb'):
            command(['dpkg-deb', '-x', item, sysroot], stdout=subprocess.DEVNULL)
        receipt['completed'].append(name)
        write(receipt_path, receipt)
        print('Built ' + name, flush=True)
    command([sys.executable, __file__, 'index', '--output', out])


if __name__ == '__main__':
    main()

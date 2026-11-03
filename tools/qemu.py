#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Manage an isolated openKylin x86_64 XR development VM; no physical devices."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def qmp(work, command, arguments=None):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(15)
        sock.connect(str(work / 'qmp.sock'))
        stream = sock.makefile('rwb')
        json.loads(stream.readline())
        for request in [{'execute': 'qmp_capabilities'},
                        {'execute': command, 'arguments': arguments or {}}]:
            stream.write(json.dumps(request).encode() + b'\n')
            stream.flush()
            while True:
                response = json.loads(stream.readline())
                if 'error' in response:
                    raise RuntimeError(response['error'])
                if 'return' in response:
                    break
        return response['return']


def ssh_args(work, port, user):
    return ['ssh', '-F', '/dev/null', '-i', str(work / 'id_ed25519'),
            '-p', str(port), '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'UserKnownHostsFile=' + str(work / 'known_hosts'),
            '-o', 'ConnectTimeout=5', user + '@127.0.0.1']


def wait_ssh(command, deadline=180):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if subprocess.run(command + ['true'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return
        time.sleep(2)
    raise TimeoutError('Guest SSH did not become ready')


def prepare(a):
    work = a.workspace
    if (work / 'desktop.qcow2').exists():
        raise ValueError('Existing VM preserved; choose a new workspace')
    lock = json.loads((ROOT / 'systems/openkylin/qemu-source.lock.json').read_text())
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with a.iso.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            sha.update(block)
            md5.update(block)
    if (a.iso.stat().st_size != lock['bytes'] or sha.hexdigest() != lock['sha256']
            or md5.hexdigest() != lock['download_page_md5']):
        raise ValueError('ISO differs from pinned official source')
    work.mkdir(parents=True, exist_ok=True)
    (work / 'source').mkdir(exist_ok=True)
    (work / 'source.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    spec = importlib.util.spec_from_file_location(
        'openkylin_adapter', ROOT / 'systems/openkylin/adapter.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    (work / 'iso').mkdir()
    adapter.extract_iso(a.iso, work / 'iso')
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', work / 'id_ed25519'])
    helper = a.helper.resolve()
    run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b',
         helper / 'desktop.qcow2', work / 'provision-helper.qcow2'])
    run(['qemu-img', 'create', '-f', 'qcow2', work / 'desktop.qcow2', '64G'])
    run(['qemu-system-x86_64', '-name', 'openkylin-provision-helper', '-machine', 'q35',
         '-accel', 'kvm', '-cpu', 'host', '-smp', '4', '-m', '6144',
         '-kernel', helper / 'vmlinuz', '-initrd', helper / 'initrd',
         '-append', 'root=/dev/vda1 rw console=ttyS0 quiet systemd.mask=mix-xr-simulator.service',
         '-drive', f'file={work}/provision-helper.qcow2,if=virtio,format=qcow2',
         '-drive', f'file={work}/desktop.qcow2,if=virtio,format=qcow2',
         '-netdev', f'user,id=n0,hostfwd=tcp:127.0.0.1:{a.helper_port}-:22',
         '-device', 'virtio-net-pci,netdev=n0', '-virtfs',
         f'local,path={work},mount_tag=devshare,security_model=none,id=devshare',
         '-display', 'none', '-serial', f'file:{work}/provision-serial.log',
         '-monitor', f'unix:{work}/provision-monitor.sock,server=on,wait=off',
         '-pidfile', work / 'provision.pid', '-daemonize'])
    command = ssh_args(work, a.helper_port, 'root')
    # The existing helper's key is consumed explicitly, never copied to source.
    command[command.index('-i') + 1] = str(helper / 'id_ed25519')
    wait_ssh(command)
    run(command + ['mkdir -p /mnt/devshare; mount -t 9p -o trans=virtio,version=9p2000.L devshare /mnt/devshare; '
                   'apt-get update && apt-get install -y squashfs-tools xorriso'])
    with (ROOT / 'tools/qemu/prepare-guest.sh').open('rb') as script:
        run(command + ['cat > /tmp/prepare-xr-guest.sh; systemd-run --unit=xr-vm-prepare '
                       '--property=Type=exec /bin/bash -c '
                       + shlex.quote('bash /tmp/prepare-xr-guest.sh /mnt/devshare /dev/vdb '
                                     '> /mnt/devshare/prepare-guest.log 2>&1')], stdin=script)
    print('Provisioning in helper. Follow prepare-guest.log; when complete run helper poweroff.', flush=True)


def start(a):
    work = a.workspace
    for name in ['desktop.qcow2', 'vmlinuz', 'initrd', 'id_ed25519']:
        if not (work / name).is_file():
            raise ValueError('Missing prepared input: ' + name)
    if (work / 'qmp.sock').exists():
        try:
            print(json.dumps(qmp(work, 'query-status')))
            raise ValueError('VM already running')
        except (ConnectionRefusedError, FileNotFoundError):
            (work / 'qmp.sock').unlink(missing_ok=True)
    env = os.environ.copy()
    gpu = 'virtio-vga,xres=1600,yres=1000'
    display = 'none'
    if a.graphics != 'software':
        if not Path(a.rendernode).exists():
            raise ValueError('GPU node unavailable; run outside a device-hiding sandbox')
        gpu = 'virtio-vga-gl,xres=1600,yres=1000'
        if a.graphics == 'venus':
            gpu += ',hostmem=4G,blob=true,venus=true'
        display = 'egl-headless,rendernode=' + a.rendernode
        vendor = Path('/sys/class/drm') / Path(a.rendernode).name / 'device/vendor'
        if vendor.read_text().strip() == '0x8086':
            env['VK_DRIVER_FILES'] = '/usr/share/vulkan/icd.d/intel_icd.json'
            env['__EGL_VENDOR_LIBRARY_FILENAMES'] = '/usr/share/glvnd/egl_vendor.d/50_mesa.json'
    private_qemu = work / 'host-tools/qemu/bin/qemu-system-x86_64'
    binary = a.qemu_binary or (str(private_qemu) if private_qemu.exists() else 'qemu-system-x86_64')
    version = subprocess.check_output([binary, '--version'], text=True).splitlines()[0]
    accel = a.accelerator
    if accel == 'kvm' and int(version.split('version ')[1].split('.')[0]) >= 11:
        accel += ',honor-guest-pat=on'
    command = [binary, '-name', 'openkylin-ukui-xr-dev',
               '-machine', 'q35', '-accel', accel, '-cpu',
               'host' if a.accelerator == 'kvm' else 'max',
               '-smp', str(a.cpus), '-m', str(a.memory),
               '-object', f'memory-backend-memfd,id=mem,size={a.memory}M,share=on',
               '-machine', 'memory-backend=mem',
               '-kernel', str(work / 'vmlinuz'), '-initrd', str(work / 'initrd'),
               '-append', 'root=LABEL=ukui-xr-dev rw console=ttyS0 quiet',
               '-drive', f'file={work}/desktop.qcow2,if=virtio,format=qcow2,discard=unmap',
               '-netdev', f'user,id=n0,hostfwd=tcp:127.0.0.1:{a.ssh_port}-:22',
               '-device', 'virtio-net-pci,netdev=n0', '-device', gpu,
               '-virtfs', f'local,path={work}/source,mount_tag=devsrc,security_model=none,readonly=on,id=devsrc',
               '-device', 'qemu-xhci', '-device', 'usb-tablet',
               '-display', display, '-vnc', f'127.0.0.1:{a.vnc_display}',
               '-serial', f'file:{work}/serial.log',
               '-qmp', f'unix:{work}/qmp.sock,server=on,wait=off',
               '-pidfile', str(work / 'qemu.pid'), '-daemonize',
               '-D', str(work / 'qemu-debug.log')]
    receipt = {'qemu_version': version, 'graphics_requested': a.graphics, 'accelerator': a.accelerator,
               'rendernode': a.rendernode, 'ssh_port': a.ssh_port,
               'vnc_port': 5900 + a.vnc_display, 'command': command,
               'hardware_acceleration_validated': False}
    (work / 'launch.json').write_text(json.dumps(receipt, indent=2) + '\n')
    with (work / 'qemu-stderr.log').open('ab') as log:
        run(command, env=env, stdout=log, stderr=log)
    print(json.dumps(qmp(work, 'query-status')))


def stop(a):
    # UKUI may consume an ACPI power-button event with a confirmation dialog.
    # Request normal shutdown in the guest first, without force-stopping QEMU.
    result = subprocess.run(ssh_args(a.workspace, a.ssh_port, 'root')
                            + ['systemctl poweroff'], timeout=15)
    if result.returncode:
        try:
            qmp(a.workspace, 'system_powerdown')
        except (OSError, json.JSONDecodeError):
            return
    print(json.dumps({'status': 'shutdown-requested'}), flush=True)
    if not a.wait_seconds:
        return
    end = time.monotonic() + a.wait_seconds
    while time.monotonic() < end:
        try:
            qmp(a.workspace, 'query-status')
        except (OSError, json.JSONDecodeError):
            return
        time.sleep(.5)
    raise TimeoutError('Guest has not shut down; no process was force-stopped')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--ssh-port', type=int, default=22244)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--iso', type=Path, required=True)
    prep.add_argument('--helper', type=Path, required=True)
    prep.add_argument('--helper-port', type=int, default=22243)
    launch = sub.add_parser('run')
    launch.add_argument('--graphics', choices=['venus', 'virgl', 'software'], default='venus')
    launch.add_argument('--accelerator', choices=['kvm', 'tcg'], default='kvm')
    launch.add_argument('--rendernode', default='/dev/dri/renderD128')
    launch.add_argument('--cpus', type=int, default=6)
    launch.add_argument('--memory', type=int, default=8192)
    launch.add_argument('--vnc-display', type=int, default=94)
    launch.add_argument('--qemu-binary', help='Explicit QEMU executable; private workspace build preferred when present')
    remote = sub.add_parser('ssh')
    remote.add_argument('--user', default='xr')
    remote.add_argument('--tty', action='store_true', help='Allocate a terminal for interactive XR tools')
    remote.add_argument('remote_command', nargs=argparse.REMAINDER)
    sub.add_parser('status')
    shutdown = sub.add_parser('stop')
    shutdown.add_argument('--wait-seconds', type=int, default=0,
                          help='Optionally wait for shutdown (0-60); never force-stop')
    shot = sub.add_parser('screenshot')
    shot.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    a.workspace = a.workspace.resolve()
    if ',' in str(a.workspace):
        parser.error('QEMU workspace path cannot contain commas')
    if a.command == 'stop' and not 0 <= a.wait_seconds <= 60:
        parser.error('--wait-seconds must be between 0 and 60')
    if a.command == 'prepare':
        prepare(a)
    elif a.command == 'run':
        start(a)
    elif a.command == 'ssh':
        command = ssh_args(a.workspace, a.ssh_port, a.user)
        if a.tty:
            command.insert(1, '-t')
        os.execvp('ssh', command + a.remote_command)
    elif a.command == 'status':
        try:
            status = qmp(a.workspace, 'query-status')
        except (ConnectionRefusedError, FileNotFoundError):
            status = {'status': 'stopped', 'running': False}
        print(json.dumps(status))
    elif a.command == 'stop':
        stop(a)
    elif a.command == 'screenshot':
        try:
            qmp(a.workspace, 'screendump', {'filename': str(a.output.resolve()), 'format': 'png'})
        except RuntimeError as error:
            if 'no surface' not in str(error):
                raise
            # GL dma-buf scanout can have no CPU surface for QMP screendump.
            # Capture the actual composed guest desktop instead.
            command = ('p=$(mktemp --suffix=.png); trap \'rm -f "$p"\' EXIT; '
                       'DISPLAY=:0 scrot -o "$p" && cat "$p"')
            with a.output.open('wb') as target:
                run(ssh_args(a.workspace, a.ssh_port, 'xr') + [command], stdout=target)


if __name__ == '__main__':
    main()

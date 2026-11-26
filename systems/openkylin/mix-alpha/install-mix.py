#!/usr/bin/env python3
"""Install or restore the MIX Alpha recovery from rooted Android B over USB ADB."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOTFS = 'openKylin-3.0-Spatial-Alpha-MIX-arm64.img'
BOOT = 'openKylin-3.0-Spatial-Alpha-MIX-recovery.img'
RECOVERY = '/dev/block/by-name/recovery_b'
STORE = '/mnt/mix-alpha-install'
TARGET = STORE + '/systems/openkylin-alpha'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('check', 'install', 'restore'))
    p.add_argument('--serial', help='ADB serial when several devices are connected')
    p.add_argument('--backup', type=Path, default=Path('mix-device-backup'))
    a = p.parse_args()
    base = Path(__file__).resolve().parent
    transport = a.serial or subprocess.check_output(['adb', 'get-serialno'], text=True).strip()
    adb = ['adb', '-s', transport]

    def root(script, **kwargs):
        cmd = 'su -mm -c ' + shlex.quote('set -eu\n' + script)
        return subprocess.run(adb + ['shell', '-T', cmd], check=True, **kwargs)

    def query(script):
        return root(script, stdout=subprocess.PIPE).stdout.decode().strip()

    def remote_sha(path):
        return query('sha256sum ' + shlex.quote(path)).split()[0]

    def put(source, path):
        with source.open('rb') as stream:
            root('umask 077; cat > ' + shlex.quote(path) + '; sync', stdin=stream)

    root(f'test "$(id -u)" = 0; test "$(getprop ro.product.model)" = A081; '
         f'test "$(getprop ro.boot.slot_suffix)" = _b; test -b {RECOVERY}')
    serial = query('getprop ro.serialno')
    if not serial:
        sys.exit('无法读取本机身份。')
    recorded = a.backup / 'device.json'
    backup = a.backup / 'recovery_b.original.img'
    pending = a.backup / 'write-pending'
    def same_device():
        if query('getprop ro.serialno') != serial:
            sys.exit('USB 设备身份已变化。')

    old = remote_sha(RECOVERY)
    sums = {}
    for line in (base / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name.strip().lstrip('*')] = digest
    if a.action == 'restore':
        record = json.loads(recorded.read_text())
        if (record['serial'] != serial or sha(backup) != record['sha256']
                or backup.stat().st_size != int(query('blockdev --getsize64 ' + RECOVERY))):
            sys.exit('设备或本机备份校验失败。')
        if old == record['sha256']:
            print('原 recovery 已在位。')
            return
        if old != sums[BOOT] and not (pending.is_file() and pending.read_text() == sums[BOOT]):
            sys.exit('当前 recovery 与此版本不同，请先确认它的来源。')
        if input('输入 RESTORE 恢复这台设备的 recovery_b：') != 'RESTORE':
            sys.exit('已取消。')
        same_device()
        put(backup, RECOVERY)
        if remote_sha(RECOVERY) != record['sha256']:
            sys.exit('恢复读回失败。请留在 Android。')
        pending.unlink(missing_ok=True)
        print('已恢复并读回校验。Alpha 系统文件仍保存在 osstore。')
        return

    root(f"test -b /dev/block/by-name/osstore; test ! -L {STORE}; "
         f'if test -d {STORE}; then test -z "$(ls -A {STORE})"; else mkdir -m 700 {STORE}; fi')
    mounted = False
    try:
        opts = 'ro,noload' if a.action == 'check' else 'rw,noatime'
        # A separate existing mount might belong to another maintenance session.
        root(f'dev=$(readlink -f /dev/block/by-name/osstore); '
             f'num=$(cat /sys/class/block/${{dev##*/}}/dev); '
             f'! grep -q "^[^ ]* [^ ]* $num " /proc/self/mountinfo || exit 1; '
             f'mount -t ext4 -o {opts} /dev/block/by-name/osstore {STORE}')
        mounted = True
        print(query('df -h ' + STORE))
        if a.action == 'check':
            available = int(query(f'df -k {STORE} | tail -1').split()[-3]) * 1024
            if available < (24 * 1024 + 64) * 1024 * 1024:
                sys.exit('osstore 可用空间不足 24 GiB + 64 MiB。')
            print('A081、Android B、root、recovery_b 和 ext4 osstore 已确认。')
            return
        image = base / ROOTFS
        recovery = base / BOOT
        for path in (image, recovery):
            if sha(path) != sums[path.name]:
                sys.exit('下载文件校验失败：' + path.name)
        if recovery.stat().st_size != int(query('blockdev --getsize64 ' + RECOVERY)):
            sys.exit('recovery_b 大小不匹配。')
        staging = TARGET + '.installing'
        root(f'test ! -L {STORE}/systems; test ! -L {TARGET}; test ! -L {staging}')
        existing = query(f'if test -d {TARGET}; then echo yes; fi')
        if existing and (not recorded.is_file() or
                         query(f'cat {TARGET}/release.sha256') != sums[ROOTFS]):
            sys.exit('目标目录已被占用。')
        ready = bool(existing and remote_sha(TARGET + '/rootfs.img') == sums[ROOTFS])
        if existing and not ready:
            sys.exit('现有根镜像已改变，请先备份或移走该目录。')
        available = int(query(f'df -k {STORE} | tail -1').split()[-3]) * 1024
        partial = int(query(f'if test -f {staging}/rootfs.img && test ! -L {staging}/rootfs.img; '
                            f'then du -k {staging}/rootfs.img | cut -f1; else echo 0; fi')) * 1024
        if not ready and available + partial < image.stat().st_size + 64 * 1024 * 1024:
            sys.exit('osstore 空间不足。')
        if old == sums[BOOT]:
            sys.exit('Alpha recovery 已安装，请保管原设备备份。')
        if a.backup.exists():
            record = json.loads(recorded.read_text())
            if (record['serial'] != serial or old != record['sha256']
                    or sha(backup) != old):
                sys.exit('现有备份与当前设备或 recovery 不匹配。')
        else:
            a.backup.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=a.backup.parent) as scratch:
                staged = Path(scratch) / 'backup'
                staged.mkdir(mode=0o700)
                with (staged / backup.name).open('xb') as stream:
                    root('cat ' + RECOVERY, stdout=stream)
                if sha(staged / backup.name) != old:
                    sys.exit('原 recovery 备份校验失败。')
                (staged / recorded.name).write_text(json.dumps(dict(serial=serial, sha256=old)) + '\n')
                staged.rename(a.backup)
        if input('输入 INSTALL 安装到 osstore 并更新 recovery_b：') != 'INSTALL':
            sys.exit('已取消。')
        password = getpass.getpass('为本机 mix 设置密码：')
        if not password or password != getpass.getpass('再次输入：'):
            sys.exit('两次密码须一致且非空。')
        hashed = subprocess.check_output(['openssl', 'passwd', '-6', '-stdin'],
                                         input=password.encode() + b'\n').strip()
        if not ready:
            root(f'umask 077; mkdir -p {STORE}/systems; if test -d {staging}; then '
                 f'test "$(cat {staging}/release.sha256)" = {sums[ROOTFS]}; else mkdir {staging}; '
                 f'echo {sums[ROOTFS]} > {staging}/release.sha256; fi; test ! -L {staging}/rootfs.img')
            put(image, staging + '/rootfs.img')
            if remote_sha(staging + '/rootfs.img') != sums[ROOTFS]:
                sys.exit('系统文件读回失败，原 recovery 仍在位。')
            # These factory values are copied directly on this headset, never through the host.
            root(f'''umask 077
localdir={staging}/local-device
mkdir -p "$localdir"
cal=/mnt/vendor/persist/qvr/device_calibration.xml
if test -s "$cal"; then cp "$cal" "$localdir/"; fi
for name in libsns_low_lat_stream_skel.so fastrpc_shell_2; do
    for dir in /vendor/dsp/sdsp /vendor/dsp/slpi /vendor/lib/rfsa/adsp /system/lib/rfsa/adsp; do
        if test -s "$dir/$name"; then
            mkdir -p "$localdir/slpi"; cp "$dir/$name" "$localdir/slpi/$name"; break
        fi
    done
done
reg=/mnt/vendor/persist/sensors/registry/registry
if test -d "$reg"; then
    mkdir -p "$localdir/sensors/registry" "$localdir/socinfo"
    for file in "$reg"/*; do
        if test -f "$file" && test ! -L "$file"; then cp "$file" "$localdir/sensors/registry/"; fi
    done
    for name in platform_version platform_subtype_id platform_subtype revision hw_platform soc_id; do
        file=/sys/devices/soc0/$name
        if test -r "$file"; then cat "$file" > "$localdir/socinfo/$name"; fi
    done
fi''')
        dest = TARGET if ready else staging
        root(f'umask 077; cat > {dest}/mix-password.sha512', input=hashed + b'\n')
        if not ready:
            root(f'mv {staging} {TARGET}; sync')
        same_device()
        if remote_sha(RECOVERY) != old:
            sys.exit('安装过程中 recovery 发生变化。')
        pending.write_text(sums[BOOT])
        try:
            put(recovery, RECOVERY)
            if remote_sha(RECOVERY) != sums[BOOT]:
                raise ValueError('recovery verification failed')
        except (OSError, subprocess.CalledProcessError, ValueError):
            same_device()
            put(backup, RECOVERY)
            if remote_sha(RECOVERY) != old:
                sys.exit('恢复原 recovery 后仍未通过读回。请留在 Android 并运行 restore。')
            pending.unlink(missing_ok=True)
            sys.exit('已恢复并读回原 recovery。')
        pending.unlink()
        print('系统和 recovery 均已读回校验。可执行 adb reboot recovery。')
    finally:
        if mounted:
            root('sync; umount ' + STORE)
        root('rmdir ' + STORE)


if __name__ == '__main__':
    os.umask(0o077)
    main()

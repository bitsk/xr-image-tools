#!/usr/bin/env python3
"""Install or restore the MIX Alpha recovery from the current rooted Android slot over USB ADB."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOTFS = 'openKylin-3.0-Spatial-Alpha-MIX-arm64.img'
BOOT = 'openKylin-3.0-Spatial-Alpha-MIX-recovery.img'
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
    p.add_argument('action', choices=('check', 'install', 'repair', 'update-recovery', 'restore'))
    p.add_argument('--serial', help='ADB serial when several devices are connected')
    p.add_argument('--backup', type=Path, default=Path('mix-device-backup'))
    a = p.parse_args()
    base = Path(__file__).resolve().parent
    try:
        transport = a.serial or subprocess.check_output(['adb', 'get-serialno'], text=True).strip()
    except FileNotFoundError:
        sys.exit('找不到 adb。请安装 Android Platform Tools，并将 adb 加入 PATH。')
    except subprocess.CalledProcessError:
        sys.exit('请用 adb devices -l 确认设备已授权；多台设备连接时请指定 --serial。')
    adb = ['adb', '-s', transport]

    guard = ''

    def root(script, **kwargs):
        cmd = 'su -mm -c ' + shlex.quote('set -eu\n' + guard + script)
        return subprocess.run(adb + ['shell', '-T', cmd], check=True, **kwargs)

    def query(script):
        return root(script, stdout=subprocess.PIPE).stdout.decode().strip()

    def remote_sha(path):
        return query('sha256sum ' + shlex.quote(path)).split()[0]

    def put(source, path):
        with source.open('rb') as stream:
            root('umask 077; cat > ' + shlex.quote(path) + '; sync', stdin=stream)

    try:
        root('test "$(id -u)" = 0')
    except subprocess.CalledProcessError:
        sys.exit('请在当前 Android 槽位授权 USB 调试和 Magisk root，然后重试。')
    if query('getprop ro.product.model') != 'A081':
        sys.exit('此安装器仅适用于奇遇 MIX A081。')
    serial = query('getprop ro.serialno')
    slot = query('getprop ro.boot.slot_suffix')
    if not serial or slot not in ('_a', '_b'):
        sys.exit('无法确认设备身份或当前 A/B 槽位。')
    # Bind each privileged command (including a write or rollback) to this boot slot.
    guard = (f'test "$(getprop ro.serialno)" = {shlex.quote(serial)} && '
             f'test "$(getprop ro.boot.slot_suffix)" = {slot} || '
             '{ echo "设备或槽位已变化，操作已停止。" >&2; exit 1; }\n')
    part = 'recovery' + slot
    recovery_dev = '/dev/block/by-name/' + part
    root(f'test -b {recovery_dev} || {{ echo "缺少 {part} 分区" >&2; exit 1; }}')
    print(f'当前 Android {slot[-1].upper()} 槽，目标 {part}。')
    recorded = a.backup / 'device.json'
    backup = a.backup / (part + '.original.img')
    pending = a.backup / 'write-pending'

    def backup_record():
        if not recorded.is_file():
            sys.exit('备份目录缺少 device.json，请选择这台头显对应槽位的备份目录。')
        record = json.loads(recorded.read_text())
        # The original installer supported only slot B and recorded no slot field.
        if record.get('slot', '_b') != slot or record.get('serial') != serial:
            sys.exit('备份的设备或槽位与当前 Android 不符。请核对备份目录。')
        return record

    def import_factory(dest):
        # Copy from this Android installation. A retry also refreshes any partial import.
        root(f'''umask 077
localdir={dest}/local-device
mkdir -p "$localdir/sensors/registry" "$localdir/socinfo"
getprop ro.serialno > "$localdir/usb-serial"
cp /mnt/vendor/persist/qvr/device_calibration.xml "$localdir/device_calibration.xml"
for name in libsns_low_lat_stream_skel.so fastrpc_shell_2; do
    for dir in /vendor/dsp/sdsp /vendor/dsp/slpi /vendor/lib/rfsa/adsp /system/lib/rfsa/adsp; do
        if test -s "$dir/$name"; then
            mkdir -p "$localdir/slpi"; cp "$dir/$name" "$localdir/slpi/$name"; break
        fi
    done
done
reg=/mnt/vendor/persist/sensors/registry
for file in "$reg/registry/"*; do
    if test -f "$file" && test ! -L "$file"; then cp "$file" "$localdir/sensors/registry/"; fi
done
if test -s "$reg/sns_reg_version"; then cp "$reg/sns_reg_version" "$localdir/sensors/"; fi
if test -s /vendor/etc/sensors/sns_reg_config; then cp /vendor/etc/sensors/sns_reg_config "$localdir/sensors/sns_reg.conf"; fi
if test -d /vendor/etc/sensors/config; then
    mkdir -p "$localdir/sensors/config"
    cp -R /vendor/etc/sensors/config/. "$localdir/sensors/config/"
fi
for name in platform_version platform_subtype_id platform_subtype revision hw_platform soc_id; do
    file=/sys/devices/soc0/$name
    if test -r "$file"; then cat "$file" > "$localdir/socinfo/$name"; fi
done
''')
        root(f'test -s {dest}/local-device/device_calibration.xml; '
             f'test -n "$(find {dest}/local-device/sensors/registry -type f | head -n 1)"')

    def write_recovery(recovery, new_digest):
        # Bind subsequent restore to this installation, also when reusing a backup.
        record = backup_record()
        record.update(slot=slot, installed_sha256=new_digest)
        with tempfile.NamedTemporaryFile(mode='w', dir=a.backup, delete=False) as updated:
            json.dump(record, updated)
            updated.write('\n')
        os.replace(updated.name, recorded)
        pending.write_text(new_digest)
        try:
            put(recovery, recovery_dev)
            if remote_sha(recovery_dev) != new_digest:
                raise ValueError('recovery verification failed')
        except (OSError, subprocess.CalledProcessError, ValueError):
            put(backup, recovery_dev)
            if remote_sha(recovery_dev) != record['sha256']:
                sys.exit('恢复原 recovery 后仍未通过读回。请留在 Android 并运行 restore。')
            pending.unlink(missing_ok=True)
            sys.exit('已恢复并读回原 recovery。')
        pending.unlink()

    if a.action in ('install', 'restore', 'update-recovery'):
        old = remote_sha(recovery_dev)
        sums = {}
        sums_file = base / 'SHA256SUMS'
        if not sums_file.is_file():
            sys.exit('请将同版本文档脚本包和镜像包解压到同一目录。')
        for line in sums_file.read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            sums[name.strip().lstrip('*')] = digest
        if BOOT not in sums or ROOTFS not in sums:
            sys.exit('SHA256SUMS 缺少本版镜像条目，请重新解压配套文档脚本包。')
    if a.action in ('update-recovery', 'repair'):
        info = base / 'mix-alpha-repair.json'
        if not info.is_file():
            sys.exit('请使用包含修复材料的配套文档脚本包。')
        repair_info = json.loads(info.read_text())
    if a.action == 'update-recovery':
        record = backup_record()
        recovery = base / BOOT
        new = repair_info['recovery_sha256']
        if not recovery.is_file() or sha(recovery) != new:
            sys.exit('请把修订版 recovery IMG 放到安装器目录，核对版本后重试。')
        size = int(query('blockdev --getsize64 ' + recovery_dev))
        if recovery.stat().st_size != size or not backup.is_file() or backup.stat().st_size != size or sha(backup) != record['sha256']:
            sys.exit('本机原 recovery 备份或分区长度不匹配。')
        if old == new:
            print('本槽已使用修订版 recovery。')
            return
        if old not in (record['sha256'], record.get('installed_sha256'), repair_info['previous_recovery_sha256']):
            sys.exit('当前 recovery 来源不明，已停止。')
        if input(f'输入 UPDATE 更新 {part} 并保留原备份：') != 'UPDATE':
            sys.exit('已取消。')
        if remote_sha(recovery_dev) != old:
            sys.exit('确认期间 recovery 已改变。')
        write_recovery(recovery, new)
        print(f'{part} 已更新并读回校验。')
        return
    if a.action == 'restore':
        record = backup_record()
        if (not backup.is_file() or sha(backup) != record['sha256']
                or backup.stat().st_size != int(query('blockdev --getsize64 ' + recovery_dev))):
            sys.exit('设备或本机备份校验失败。')
        if old == record['sha256']:
            pending.unlink(missing_ok=True)
            print('原 recovery 已在位。')
            return
        known = {sums[BOOT], record.get('installed_sha256')}
        repair_manifest = base / 'mix-alpha-repair.json'
        if repair_manifest.is_file():
            releases = json.loads(repair_manifest.read_text())
            known.update((releases['previous_recovery_sha256'], releases['recovery_sha256']))
        if old not in known and not (pending.is_file() and pending.read_text() in known):
            sys.exit('当前 recovery 与此版本不同，请先确认它的来源。')
        if input(f'输入 RESTORE 恢复这台设备的 {part}：') != 'RESTORE':
            sys.exit('已取消。')
        # An interrupted restore can leave a partially written partition too.
        pending.write_text(record.get('installed_sha256') or sums[BOOT])
        put(backup, recovery_dev)
        if remote_sha(recovery_dev) != record['sha256']:
            sys.exit('恢复读回失败。请留在 Android。')
        pending.unlink(missing_ok=True)
        print('已恢复并读回校验。Alpha 系统文件仍保存在 osstore。')
        return

    root(f"test -b /dev/block/by-name/osstore || {{ echo '缺少 osstore 分区。请先按安装指南的「创建 osstore 分区」准备存储，再运行 check。' >&2; exit 1; }}; test ! -L {STORE}; "
         f'if test -d {STORE}; then test -z "$(ls -A {STORE})"; else mkdir -m 700 {STORE}; fi')
    mounted = False
    try:
        opts = 'ro,noload' if a.action == 'check' else 'rw,noatime'
        # A separate existing mount might belong to another maintenance session.
        root(f'dev=$(readlink -f /dev/block/by-name/osstore); '
             f'num=$(cat /sys/class/block/${{dev##*/}}/dev); '
             f'if grep -q "^[^ ]* [^ ]* $num " /proc/self/mountinfo; then echo "osstore 已被挂载，请结束其他安装或维护操作。" >&2; exit 1; fi; '
             f'mount -t ext4 -o {opts} /dev/block/by-name/osstore {STORE} || {{ echo "无法挂载 osstore，请确认其为可读取的 ext4 文件系统。" >&2; exit 1; }}')
        mounted = True
        print(query('df -h ' + STORE))
        root('test -s /mnt/vendor/persist/qvr/device_calibration.xml || '
             '{ echo "当前 Android 缺少本机 QVR 标定文件。" >&2; exit 1; }; '
             'test -n "$(find /mnt/vendor/persist/sensors/registry/registry -type f | head -n 1)" || '
             '{ echo "当前 Android 缺少传感器注册表。" >&2; exit 1; }')
        if a.action == 'check':
            available = int(query(f'df -k {STORE} | tail -1').split()[-3]) * 1024
            if query(f'if test -f {TARGET}/rootfs.img; then echo yes; fi'):
                print('已发现现有 Alpha 系统。保留用户数据进行修复时请使用 repair。')
            elif available < (24 * 1024 + 64) * 1024 * 1024:
                sys.exit('osstore 可用空间不足 24 GiB + 64 MiB。')
            print(f'A081、当前槽位、root、{part}、本机标定和 ext4 osstore 已确认。')
            return
        if a.action == 'repair':
            payload = base / 'mix-alpha-repair.tar'
            unit = base / 'mix-alpha-repair.service'
            for path, key in ((payload, 'payload_sha256'), (unit, 'unit_sha256')):
                if not path.is_file() or sha(path) != repair_info[key]:
                    sys.exit('修复材料不完整，请重新解压文档脚本包。')
            root(f'test ! -L {STORE}/systems; test ! -L {TARGET}; test -f {TARGET}/rootfs.img; test ! -L {TARGET}/rootfs.img')
            if input('输入 REPAIR 更新已有 Alpha 并保留账户、应用和当前槽位：') != 'REPAIR':
                sys.exit('已取消。')
            scratch = query(f'umask 077; mktemp -d {STORE}/.alpha-repair.XXXXXXXX')
            if not scratch.startswith(STORE + '/.alpha-repair.') or '/' in scratch[len(STORE) + 1:]:
                sys.exit('无法创建修复临时目录。')
            try:
                put(payload, scratch + '/payload.tar')
                put(unit, scratch + '/mix-alpha-repair.service')
                if remote_sha(scratch + '/payload.tar') != repair_info['payload_sha256'] or remote_sha(scratch + '/mix-alpha-repair.service') != repair_info['unit_sha256']:
                    sys.exit('修复文件传输校验失败。')
                import_factory(TARGET)
                root(f'''umask 077
work={scratch}
r="$work/root"
mkdir "$r"
mount -t ext4 -o loop,rw,noatime {TARGET}/rootfs.img "$r"
trap 'umount "$r"' EXIT
test "$(cat "$r/etc/a081-rootfs-id")" = a081-openkylin-3.0-arm64
test -f "$r/etc/xr/session.json"
dest="$r/var/lib/mix-alpha-repair"
units="$r/etc/systemd/system"
test ! -L "$dest"
mkdir -p "$dest"
cp "$work/payload.tar" "$dest/incoming.tar"
cp "$work/mix-alpha-repair.service" "$units/mix-alpha-repair.service.tmp"
chmod 0644 "$units/mix-alpha-repair.service.tmp"
mv "$units/mix-alpha-repair.service.tmp" "$units/mix-alpha-repair.service"
ln -sfn ../mix-alpha-repair.service "$units/multi-user.target.wants/mix-alpha-repair.service"
sync
mv "$dest/incoming.tar" "$dest/payload.tar"
sync''')
            finally:
                root(f'rm -f {scratch}/payload.tar {scratch}/mix-alpha-repair.service; rmdir {scratch}/root 2>/dev/null || :; rmdir {scratch}')
            print('已准备本机修复。执行 adb reboot recovery；首次启动会安装更新。')
            return
        image = base / ROOTFS
        recovery = base / BOOT
        for path in (image, recovery):
            if not path.is_file():
                sys.exit(f'缺少 {path.name}，请把两份镜像和安装器解压到同一目录。')
            if sha(path) != sums.get(path.name):
                sys.exit('下载文件校验失败：' + path.name)
        if not shutil.which('openssl'):
            sys.exit('找不到 openssl，安装器需要它为本机用户生成登录密码。')
        if recovery.stat().st_size != int(query('blockdev --getsize64 ' + recovery_dev)):
            sys.exit(f'{part} 分区大小与 recovery 镜像不匹配。')
        staging = TARGET + '.installing'
        root(f'test ! -L {STORE}/systems; test ! -L {TARGET}; test ! -L {staging}')
        existing = query(f'if test -d {TARGET}; then echo yes; fi')
        if existing and (not recorded.is_file() or
                         query(f'cat {TARGET}/release.sha256') != sums[ROOTFS]):
            sys.exit('目标目录已有安装。如需保留现有 Alpha，请使用 repair。')
        if existing and old == sums[BOOT]:
            sys.exit('本槽已有 Alpha。保留可写系统和用户数据，请使用 repair。')
        # A retry before writing recovery can reuse a previously verified pristine transfer.
        ready = bool(existing and remote_sha(TARGET + '/rootfs.img') == sums[ROOTFS])
        if existing and not ready:
            sys.exit('已使用的根镜像包含本机修改。请使用 repair 保留其中的数据。')
        available = int(query(f'df -k {STORE} | tail -1').split()[-3]) * 1024
        partial = int(query(f'if test -f {staging}/rootfs.img && test ! -L {staging}/rootfs.img; '
                            f'then du -k {staging}/rootfs.img | cut -f1; else echo 0; fi')) * 1024
        if not ready and available + partial < image.stat().st_size + 64 * 1024 * 1024:
            sys.exit('osstore 空间不足。')
        if old == sums[BOOT]:
            sys.exit('Alpha recovery 已安装，请保管原设备备份。')
        if a.backup.exists():
            record = backup_record()
            if not backup.is_file() or old != record['sha256'] or sha(backup) != old:
                sys.exit('现有备份与当前设备或 recovery 不匹配。')
        else:
            a.backup.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=a.backup.parent) as scratch:
                staged = Path(scratch) / 'backup'
                staged.mkdir(mode=0o700)
                with (staged / backup.name).open('xb') as stream:
                    root('cat ' + recovery_dev, stdout=stream)
                if sha(staged / backup.name) != old:
                    sys.exit('原 recovery 备份校验失败。')
                (staged / recorded.name).write_text(json.dumps(dict(serial=serial, slot=slot, sha256=old, installed_sha256=sums[BOOT])) + '\n')
                staged.rename(a.backup)
        if input(f'输入 INSTALL 安装到 osstore 并更新 {part}：') != 'INSTALL':
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
        dest = TARGET if ready else staging
        import_factory(dest)
        root(f'umask 077; cat > {dest}/mix-password.sha512', input=hashed + b'\n')
        if not ready:
            root(f'mv {staging} {TARGET}; sync')
        if remote_sha(recovery_dev) != old:
            sys.exit('安装过程中 recovery 发生变化。')
        write_recovery(recovery, sums[BOOT])
        print('系统和 recovery 均已读回校验。可执行 adb reboot recovery。')
    finally:
        if mounted:
            root('sync; umount ' + STORE)
        root('rmdir ' + STORE)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(f'操作中止（命令返回 {error.returncode}）。请查看上方设备提示。')

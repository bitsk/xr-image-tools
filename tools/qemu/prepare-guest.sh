#!/bin/bash
# Run as root ONLY in a disposable provisioning VM, never on the host.
set -euo pipefail
share=${1:?Usage: prepare-guest.sh SHARED_WORKSPACE TARGET_DISK}
disk=${2:?Specify the new virtual disk explicitly}
[[ $(systemd-detect-virt) == qemu || $(systemd-detect-virt) == kvm ]]
[[ $(id -u) == 0 && $disk == /dev/vdb ]]
[[ $(blockdev --getsize64 "$disk") == 68719476736 ]]
[[ -f $share/iso/filesystem.squashfs && -f $share/id_ed25519.pub ]]
r=/mnt/ukui-target
mkdir -p "$r"
if [[ ${3:-} == --resume ]]; then
 [[ $(findmnt -n -o SOURCE "$r") == "$disk" ]]
 grep -qx openkylin-qemu-amd64 "$r/etc/xr-vm-id"
else
 ! findmnt -rn -S "$disk"
 mkfs.ext4 -F -L ukui-xr-dev "$disk"
 mount "$disk" "$r"
 unsquashfs -no-progress -processors 4 -f -d "$r" "$share/iso/filesystem.squashfs"
fi
grep -q '^ID=openkylin' "$r/etc/os-release"
[[ $(chroot "$r" dpkg --print-architecture) == amd64 ]]
printf 'openkylin-qemu-amd64\n' > "$r/etc/xr-vm-id"
printf 'ukui-xr-dev\n' > "$r/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 ukui-xr-dev\n' > "$r/etc/hosts"
printf 'LABEL=ukui-xr-dev / ext4 defaults 0 1\n' > "$r/etc/fstab"
rm -f "$r/etc/machine-id" "$r/var/lib/dbus/machine-id"
touch "$r/etc/machine-id"
printf '#!/bin/sh\nexit 101\n' > "$r/usr/sbin/policy-rc.d"
chmod 755 "$r/usr/sbin/policy-rc.d"
for p in proc sys dev; do
 mountpoint -q "$r/$p" || mount --rbind "/$p" "$r/$p"
 mount --make-rslave "$r/$p"
done
cleanup() { for p in dev sys proc; do umount -R "$r/$p" || true; done; }
trap cleanup EXIT
rm -f "$r/etc/resolv.conf"
printf 'nameserver 10.0.2.3\n' > "$r/etc/resolv.conf"
mkdir -p "$r/etc/apt/sources.list.d"
for p in "$r"/etc/apt/sources.list.d/*.list "$r"/etc/apt/sources.list.d/*.sources; do
 [[ ! -f $p ]] || mv "$p" "$p.disabled"
done
# The official ISO uses this signed immutable build snapshot. The public
# huanghe archive carries older packages incompatible with its newer libraries.
printf 'deb [signed-by=/usr/share/keyrings/openkylin-archive-keyring.gpg] http://archive-proxy.factory.openkylin.top/kif/archive/proxy/build/3172/archive.build.openkylin.top_openkylin huanghe main cross pty\n' > "$r/etc/apt/sources.list"
sed 's,http://archive.build.openkylin.top,https://archive.build.openkylin.top,' \
 "$r/etc/apt/sources.list.d/openkylin.sources.disabled" > "$r/etc/apt/sources.list.d/openkylin.sources"
printf 'deb [signed-by=/usr/share/keyrings/openkylin-archive-keyring.gpg] https://archive.openkylin.top/openkylin huanghe-proposed main cross pty\n' > "$r/etc/apt/sources.list.d/xr-public-proposed.list"
printf 'deb [signed-by=/usr/share/keyrings/openkylin-archive-keyring.gpg] https://archive.build.openkylin.top/openkylin huanghe-proposed main cross pty\n' > "$r/etc/apt/sources.list.d/xr-proposed.list"
export DEBIAN_FRONTEND=noninteractive SYSTEMD_OFFLINE=1
chroot "$r" apt-get update
chroot "$r" apt-get install -y --no-install-recommends openssh-server openssh-sftp-server sudo mesa-utils mesa-vulkan-drivers x11-utils x11-xserver-utils xdotool dbus-x11 build-essential cmake ninja-build pkgconf pkgconf-bin libpkgconf7 libvulkan-dev libeigen3-dev glslang-tools spirv-tools libjsoncpp-dev libvncclient1 libvncserver1 git curl ca-certificates
chroot "$r" apt-cache policy monado-service libopenxr1-monado openxr-utils > "$share/package-candidates.txt"
chroot "$r" apt-get install -y --no-install-recommends scrot wmctrl tigervnc-scraping-server tigervnc-tools python3-dbus python3-gi
chroot "$r" id xr >/dev/null 2>&1 || chroot "$r" useradd -m -s /bin/bash xr
chroot "$r" usermod -aG sudo,video,render xr
chroot "$r" passwd -d xr
install -d -m 700 "$r/home/xr/.ssh" "$r/root/.ssh"
install -m 600 "$share/id_ed25519.pub" "$r/home/xr/.ssh/authorized_keys"
install -m 600 "$share/id_ed25519.pub" "$r/root/.ssh/authorized_keys"
chroot "$r" chown -R xr:xr /home/xr
printf 'xr ALL=(ALL) NOPASSWD: ALL\n' > "$r/etc/sudoers.d/xr-dev"
chmod 440 "$r/etc/sudoers.d/xr-dev"
mkdir -p "$r/etc/ssh/sshd_config.d" "$r/etc/lightdm/lightdm.conf.d"
printf 'PasswordAuthentication no\nPermitEmptyPasswords no\nPermitRootLogin prohibit-password\n' > "$r/etc/ssh/sshd_config.d/20-xr-dev.conf"
cat > "$r/etc/lightdm/lightdm.conf.d/50-xr-dev.conf" <<'EOF'
[Seat:*]
autologin-user=xr
autologin-user-timeout=0
user-session=ukui
autologin-session=ukui
xserver-command=X -core -nolisten tcp
EOF
rm -f "$r/etc/systemd/system/lightdm.service"
chroot "$r" systemctl enable ssh.service NetworkManager.service lightdm.service serial-getty@ttyS0.service
chroot "$r" systemctl disable dnsmasq.service
chroot "$r" systemctl set-default graphical.target
mkdir -p "$r/etc/NetworkManager/conf.d"
printf '[main]\nno-auto-default=\n' > "$r/etc/NetworkManager/conf.d/20-xr-dev.conf"
printf 'MODULES=most\n' > "$r/etc/initramfs-tools/conf.d/xr-dev"
chroot "$r" update-initramfs -u -k all
chroot "$r" ssh-keygen -A
chroot "$r" dpkg --audit
chroot "$r" dpkg-query -W > "$share/packages-initial.tsv"
kernel=$(find "$r/boot" -maxdepth 1 -name 'vmlinuz-*' | sort -V | tail -1)
version=${kernel##*/vmlinuz-}
cp "$kernel" "$share/vmlinuz"
cp "$r/boot/initrd.img-$version" "$share/initrd"
printf '%s\n' "$version" > "$share/kernel-version"
rm -f "$r/usr/sbin/policy-rc.d"
touch "$r/var/lib/xr-vm-prepared"
sync
printf 'PREPARED openKylin amd64 kernel=%s\n' "$version"

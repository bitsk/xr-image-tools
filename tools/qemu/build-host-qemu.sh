#!/bin/bash
# Build a private QEMU with the blob RAM lifetime fix; do not replace host QEMU.
set -euo pipefail
work=$(realpath "${1:?Usage: build-host-qemu.sh VM_WORKSPACE}")
base=$work/host-tools
sdk=$base/sdk
mkdir -p "$sdk" "$base/debs" "$base/qemu-build"
archive=$base/qemu-11.1.3.tar.xz
if [[ ! -f $archive ]]; then
 curl --fail --location --retry 3 -o "$archive" https://download.qemu.org/qemu-11.1.3.tar.xz
fi
printf '5a8289c7940dc77d3873c81a6c29ba3afe1446fc9d36e77fa1658cdbecb84b22  %s\n' "$archive" | sha256sum -c -
[[ -d $base/qemu-11.1.3 ]] || tar -xf "$archive" -C "$base"
cd "$base/debs"
apt-get download libgio-2.0-dev libglib2.0-dev libglib2.0-dev-bin libpixman-1-dev \
 libvirglrenderer-dev libslirp-dev libpcre2-dev libffi-dev libmount-dev libblkid-dev \
 libsysprof-capture-4-dev libselinux-dev
sha256sum ./*.deb > "$base/sdk-debs.sha256"
for p in ./*.deb; do dpkg-deb -x "$p" "$sdk"; done
python3 - "$sdk" <<'PY'
from pathlib import Path
import sys
r=Path(sys.argv[1])
for p in r.rglob('*.pc'):
    p.write_text(p.read_text().replace('/usr', str(r/'usr')))
for p in (r/'usr/lib/x86_64-linux-gnu').glob('*.so*'):
    if p.is_symlink():
        target=Path('/usr/lib/x86_64-linux-gnu')/p.readlink().name
        if target.exists():
            p.unlink();p.symlink_to(target)
PY
export PKG_CONFIG_PATH="$sdk/usr/lib/x86_64-linux-gnu/pkgconfig:$sdk/usr/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export CFLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu"
export CXXFLAGS="$CFLAGS"
cd "$base/qemu-build"
"$base/qemu-11.1.3/configure" --python=/usr/bin/python3 \
 --prefix="$base/qemu" --target-list=x86_64-softmmu --enable-kvm \
 --enable-opengl --enable-virglrenderer --enable-vnc --enable-slirp --enable-virtfs \
 --disable-docs --disable-werror --disable-gtk --disable-sdl --disable-tools \
 --disable-guest-agent --disable-user --disable-linux-user --disable-bsd-user
/usr/bin/ninja -j8
/usr/bin/ninja install
"$base/qemu/bin/qemu-system-x86_64" --version

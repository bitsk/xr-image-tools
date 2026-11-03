#!/bin/bash
# Native openKylin guest only. Keep incomplete distribution development packages
# in a relocatable SDK, preserving the installed UKUI runtime and dpkg health.
set -euo pipefail
grep -qx openkylin-qemu-amd64 /etc/xr-vm-id
[[ $(id -u) == 0 ]]
base=/opt/xr-dev
sdk=$base/sdk
mkdir -p "$base" "$sdk" "$base/debs" "$base/src" /mnt/devsrc
mountpoint -q /mnt/devsrc || mount -t 9p -o trans=virtio,version=9p2000.L devsrc /mnt/devsrc
cd "$base/debs"
packages=(libsdl2-dev libvncserver-dev libgl-dev libglx-dev libopengl-dev libegl-dev libgles-dev
 libx11-dev libx11-xcb-dev libxau-dev libxdmcp-dev libxcb1-dev libxcb-render0-dev libxcb-randr0-dev
 libxcb-keysyms1-dev libxcb-shm0-dev x11proto-dev xtrans-dev libxext-dev libxfixes-dev
 libxi-dev libxrandr-dev libxrender-dev libxxf86vm-dev libxinerama-dev libxcursor-dev
 libxkbcommon-dev libudev-dev libdrm-dev libwayland-dev libjpeg62-turbo-dev)
# apt download checks each .deb against its signed index; no force-depends or
# downgrades. Runtime dependencies already come from the official system.
apt-get download "${packages[@]}"
sha256sum ./*.deb > "$base/sdk-debs.sha256"
for p in ./*.deb; do dpkg-deb -x "$p" "$sdk"; done
python3 - "$sdk" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
# Make the standard /usr-prefixed pkg-config metadata relocatable.
for p in root.rglob('*.pc'):
    p.write_text(p.read_text().replace('/usr',str(root/'usr')))
# Developer symlinks resolve to the genuine installed ABI-compatible runtime.
libroot=Path('/usr/lib/x86_64-linux-gnu')
for p in (root/'usr/lib/x86_64-linux-gnu').glob('*.so*'):
    if p.is_symlink():
        name=p.readlink().name
        target=libroot/name
        if target.exists():
            p.unlink();p.symlink_to(target)
PY
cat > "$base/env.sh" <<'EOF'
export XR_DEV_PREFIX=/opt/xr-dev
export PATH=/opt/xr-dev/bin:$PATH
export LD_LIBRARY_PATH=/opt/xr-dev/lib:/opt/xr-dev/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PKG_CONFIG_PATH=/opt/xr-dev/lib/pkgconfig:/opt/xr-dev/lib/x86_64-linux-gnu/pkgconfig:/opt/xr-dev/sdk/usr/lib/x86_64-linux-gnu/pkgconfig:/opt/xr-dev/sdk/usr/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}
export CMAKE_PREFIX_PATH=/opt/xr-dev:/opt/xr-dev/sdk/usr${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}
EOF
source "$base/env.sh"
for name in openxr-sdk openxr-sdk-source vulkan-tools monado; do
 [[ -d $base/src/$name ]] || cp -a "/mnt/devsrc/$name" "$base/src/"
done
chown -R root:root "$base/src"
cmake -S "$base/src/openxr-sdk" -B "$base/build-openxr" -G Ninja \
 -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$base" \
 -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DBUILD_WITH_SYSTEM_JSONCPP=ON \
 -DCMAKE_C_FLAGS="-I$sdk/usr/include" -DCMAKE_CXX_FLAGS="-I$sdk/usr/include" \
 -DBUILD_TESTS=OFF -DBUILD_API_LAYERS=OFF -DBUILD_CONFORMANCE_TESTS=OFF
cmake --build "$base/build-openxr" -j4
cmake --install "$base/build-openxr"
cmake -S "$base/src/vulkan-tools" -B "$base/build-vulkan-tools" -G Ninja \
 -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$base" \
 -DBUILD_TESTS=OFF -DBUILD_ICD=OFF -DBUILD_WERROR=OFF \
 -DBUILD_WSI_WAYLAND_SUPPORT=OFF \
 -DCMAKE_C_FLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu" \
 -DCMAKE_CXX_FLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu"
cmake --build "$base/build-vulkan-tools" -j4
cmake --install "$base/build-vulkan-tools"
driver_flags=()
while read -r option; do
 case "$option" in
  XRT_BUILD_DRIVER_SIMULATED|XRT_BUILD_DRIVER_QWERTY|XRT_BUILD_DRIVER_REMOTE) ;;
  *) driver_flags+=("-D$option=OFF") ;;
 esac
done < <(sed -n 's/.*\(XRT_BUILD_DRIVER_[A-Z0-9_]*\).*/\1/p' "$base/src/monado/CMakeLists.txt" | sort -u)
cmake -S "$base/src/monado" -B "$base/build-monado" -G Ninja \
 -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$base" \
 -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DXRT_FEATURE_SERVICE=ON \
 -DXRT_FEATURE_SERVICE_SYSTEMD=OFF -DXRT_INSTALL_SYSTEMD_UNIT_FILES=OFF \
 -DXRT_BUILD_DRIVER_SIMULATED=ON -DXRT_BUILD_DRIVER_QWERTY=ON \
 -DBUILD_TESTING=OFF -DXRT_FEATURE_SLAM=OFF -DXRT_FEATURE_STEAMVR_PLUGIN=OFF "${driver_flags[@]}" \
 -DXRT_HAVE_XCB=ON -DXRT_HAVE_XLIB=ON -DXRT_HAVE_XRANDR=ON \
 -DCMAKE_EXE_LINKER_FLAGS="-L$sdk/usr/lib/x86_64-linux-gnu" \
 -DCMAKE_SHARED_LINKER_FLAGS="-L$sdk/usr/lib/x86_64-linux-gnu" \
 -DOPENGL_opengl_LIBRARY=/usr/lib/x86_64-linux-gnu/libOpenGL.so.0 \
 -DOPENGL_glx_LIBRARY=/usr/lib/x86_64-linux-gnu/libGLX.so.0 \
 -DCMAKE_C_FLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu" \
 -DCMAKE_CXX_FLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu"
cmake --build "$base/build-monado" -j4
cmake --install "$base/build-monado"
cmake -S "$base/src/openxr-sdk-source" -B "$base/build-openxr-samples" -G Ninja \
 -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$base" \
 -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DBUILD_WITH_SYSTEM_JSONCPP=ON \
 -DBUILD_TESTS=ON -DBUILD_SDK_TESTS=ON -DBUILD_API_LAYERS=OFF -DBUILD_CONFORMANCE_TESTS=OFF \
 -DOPENGL_opengl_LIBRARY=/usr/lib/x86_64-linux-gnu/libOpenGL.so.0 \
 -DOPENGL_glx_LIBRARY=/usr/lib/x86_64-linux-gnu/libGLX.so.0 \
 -DCMAKE_C_FLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu" \
 -DCMAKE_CXX_FLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu"
cmake --build "$base/build-openxr-samples" --target hello_xr -j4
install -m 755 "$base/build-openxr-samples/src/tests/hello_xr/hello_xr" "$base/bin/hello_xr"
dpkg --audit
printf 'SDK_BUILD_COMPLETE\n'

#!/bin/bash
# Run inside the isolated KasmVNC desktop account after SDK installation.
set -euo pipefail
grep -qx openkylin-qemu-amd64 /etc/xr-vm-id
[[ $(id -un) == kasm-xr && ${DISPLAY:-} == :1 ]]
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/state" "$HOME/.config/autostart"
cat > "$HOME/.local/share/applications/xr-debug.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=OpenXR Debug
Name[zh_CN]=OpenXR 调试面板
Comment=Head and controller sliders with a hardware Vulkan stereo example
Exec=/usr/local/bin/xr-simulator --sliders --remote-port 4243 --wsi-copy --scale 100
Icon=input-gaming
Terminal=false
Categories=Development;
EOF
desktop_dir=$(xdg-user-dir DESKTOP)
mkdir -p "$desktop_dir"
install -m 755 "$HOME/.local/share/applications/xr-debug.desktop" "$desktop_dir/OpenXR-Debug.desktop"
gio set "$desktop_dir/OpenXR-Debug.desktop" metadata::trusted true || true
cat > "$HOME/.config/autostart/xr-dev-awake.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=XR development idle policy
Exec=/usr/local/bin/xr-dev-awake --hold
NoDisplay=true
X-UKUI-Autostart-enabled=true
EOF
cat > "$HOME/.config/autostart/ukui-screensaver.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Screensaver
Hidden=true
X-UKUI-AutoRestart=false
EOF
/usr/local/bin/xr-dev-awake

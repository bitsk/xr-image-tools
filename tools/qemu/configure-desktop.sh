#!/bin/bash
# Run as xr in the development VM after installing the SDK helpers.
set -euo pipefail
grep -qx openkylin-qemu-amd64 /etc/xr-vm-id
[[ $(id -u) != 0 ]]
mkdir -p "$HOME/.config/autostart" "$HOME/.config/systemd/user" "$HOME/.local/share/applications"
cat > "$HOME/.config/autostart/xr-dev-awake.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=XR development idle policy
Exec=systemctl --user start xr-dev-awake.service
NoDisplay=true
X-UKUI-Autostart-enabled=true
EOF
cat > "$HOME/.local/share/applications/xr-debug.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=OpenXR Debug
Name[zh_CN]=OpenXR 调试面板
Comment=Head and controller sliders with a hardware Vulkan stereo example
Exec=systemd-run --user --collect --unit=xr-debug-panel /usr/local/bin/xr-simulator --sliders --scale 100
Icon=input-gaming
Terminal=false
Categories=Development;
EOF
desktop_dir=$(xdg-user-dir DESKTOP)
mkdir -p "$desktop_dir"
install -m 755 "$HOME/.local/share/applications/xr-debug.desktop" "$desktop_dir/OpenXR-Debug.desktop"
gio set "$desktop_dir/OpenXR-Debug.desktop" metadata::trusted true || true
cat > "$HOME/.config/systemd/user/xr-desktop-vnc.service" <<'EOF'
[Unit]
Description=UKUI XR development desktop VNC
After=graphical-session.target
[Service]
ExecStart=/usr/bin/X0tigervnc -display :0 -rfbport 5900 -SecurityTypes VncAuth -PasswordFile %h/.config/tigervnc/passwd -AlwaysShared -FrameRate 60 -desktop openKylin-UKUI-XR
Restart=on-failure
RestartSec=3
[Install]
WantedBy=default.target
EOF
cat > "$HOME/.config/systemd/user/xr-dev-awake.service" <<'EOF'
[Unit]
Description=Keep the XR development desktop awake
After=graphical-session.target
[Service]
ExecStart=/usr/local/bin/xr-dev-awake --hold
Restart=on-failure
RestartSec=3
[Install]
WantedBy=default.target
EOF
cat > "$HOME/.config/autostart/ukui-screensaver.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Screensaver
Hidden=true
X-UKUI-AutoRestart=false
EOF
systemctl --user daemon-reload
# Password setup uses the distribution's interactive tigervncpasswd tool.
if [[ -s $HOME/.config/tigervnc/passwd ]]; then
 systemctl --user enable --now xr-desktop-vnc.service
fi
systemctl --user enable --now xr-dev-awake.service

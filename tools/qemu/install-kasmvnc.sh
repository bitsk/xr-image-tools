#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install a verified upstream KasmVNC in an isolated prefix and X display.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run inside the development guest as root.' >&2; exit 1; }
package=${1:?Usage: install-kasmvnc.sh upstream.deb kasmvnc-ffmpeg-abi.c}
shim_source=${2:?Supply the companion FFmpeg ABI shim source}
expected=caaae8b563570cabed436914187b961f7b563f9e4473e3bb3b09d99e0fd3348d
[[ $(sha256sum "$package" | cut -d' ' -f1) == "$expected" ]] || { echo 'Unexpected package digest' >&2; exit 1; }
account=kasm-xr
prefix=/opt/xr-kasmvnc
[[ ! -S /tmp/.X11-unix/X1 ]] || { echo 'Display :1 already in use' >&2; exit 1; }
apt-get install -y --no-install-recommends xauth vainfo mesa-utils gcc libc6-dev libavcodec62 libavformat62 libavutil60 libswscale9
command -v openssl >/dev/null
[[ -x /usr/local/bin/xr-dev-awake ]] || { echo "Configure the development desktop idle policy first" >&2; exit 1; }
mkdir -p "$prefix"
dpkg-deb -x "$package" "$prefix"
ldd "$prefix/usr/bin/Xkasmvnc" | (! grep 'not found')
id "$account" >/dev/null 2>&1 || useradd -m -s /bin/bash -G video,render "$account"
install -d -m700 -o "$account" -g "$account" /home/$account/.vnc
# Credentials arrive on stdin, and only the standard upstream hash is persisted.
IFS= read -r password
[[ ${#password} -ge 12 ]] || { echo 'Use a password with at least 12 characters' >&2; exit 1; }
printf '%s\n%s\n' "$password" "$password" | runuser -u "$account" -- "$prefix/usr/bin/kasmvncpasswd" -u "$account" -w -o /home/$account/.kasmpasswd
unset password
runuser -u "$account" -- openssl req -x509 -newkey rsa:3072 -nodes -days 365 -subj /CN=xr-development-desktop -keyout /home/$account/.vnc/tls.key -out /home/$account/.vnc/tls.crt >/dev/null 2>&1
chmod 600 /home/$account/.vnc/tls.key /home/$account/.kasmpasswd
install -d "$prefix/libexec"
install -m644 "$shim_source" "$prefix/libexec/kasmvnc-ffmpeg-abi.c"
cc -shared -fPIC -O2 -Wall -Wextra -o "$prefix/libexec/ffmpeg-abi.so" "$prefix/libexec/kasmvnc-ffmpeg-abi.c" -ldl
cat > "$prefix/libexec/start-session" <<'SESSION'
#!/bin/bash
set -euo pipefail
export DISPLAY=:1
export XAUTHORITY="$HOME/.vnc/Xauthority"
export XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=UKUI DESKTOP_SESSION=ukui
export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json
cookie=$(openssl rand -hex 16)
touch "$XAUTHORITY"
chmod 600 "$XAUTHORITY"
xauth -f "$XAUTHORITY" add :1 . "$cookie"
unset cookie
LD_PRELOAD=/opt/xr-kasmvnc/libexec/ffmpeg-abi.so /opt/xr-kasmvnc/usr/bin/Xkasmvnc :1 -auth "$XAUTHORITY" -nolisten tcp \
  -geometry 1600x900 -depth 24 -hw3d -drinode /dev/dri/renderD128 \
  -desktop openKylin-UKUI-XR-Kasm -interface 0.0.0.0 -websocketPort 8444 \
  -rfbport 0 -httpd /opt/xr-kasmvnc/usr/share/kasmvnc/www \
  -KasmPasswordFile "$HOME/.kasmpasswd" -cert "$HOME/.vnc/tls.crt" \
  -key "$HOME/.vnc/tls.key" -sslOnly 1 -SecurityTypes None \
  -publicIP 127.0.0.1 -FrameRate 60 -videoCodec h264 \
  -IdleTimeout 0 -MaxIdleTime 0 -MaxDisconnectionTime 0 \
  -Log '*:stderr:30' &
xserver=$!
trap 'kill "$xserver" 2>/dev/null || true' EXIT
for ((i=0;i<100;i++)); do
  if xdpyinfo >/dev/null 2>&1; then break; fi
  kill -0 "$xserver"
  sleep .1
done
# A fresh bus keeps the existing :0 desktop and its XR services independent.
dbus-run-session -- bash -c '/usr/local/bin/xr-dev-awake; exec ukui-session'
SESSION
chmod 755 "$prefix/libexec/start-session"
cat > /etc/systemd/system/xr-kasmvnc.service <<'SERVICE'
[Unit]
Description=Isolated UKUI KasmVNC GPU development desktop
After=network.target
[Service]
User=kasm-xr
Group=kasm-xr
SupplementaryGroups=render video
Environment=HOME=/home/kasm-xr
Environment=XDG_RUNTIME_DIR=/run/xr-kasmvnc
RuntimeDirectory=xr-kasmvnc
RuntimeDirectoryMode=0700
ExecStart=/opt/xr-kasmvnc/libexec/start-session
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now xr-kasmvnc.service

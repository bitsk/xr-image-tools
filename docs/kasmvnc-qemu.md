# Isolated KasmVNC development desktop

This optional browser desktop runs on display `:1` as a separate `kasm-xr`
account. The existing UKUI display `:0`, its XR sessions and its VNC endpoint
continue independently. KasmVNC uses its own browser protocol; use a browser,
not a traditional VNC viewer.

The upstream package is KasmVNC **1.5.2**, Ubuntu resolute amd64:

- [Release](https://github.com/kasmtech/KasmVNC/releases/tag/v1.5.2)
- [Package](https://github.com/kasmtech/KasmVNC/releases/download/v1.5.2/kasmvncserver_ubuntu_resolute_1.5.2_amd64.deb)
- SHA-256 `caaae8b563570cabed436914187b961f7b563f9e4473e3bb3b09d99e0fd3348d`,
  also provided by the upstream GitHub release asset digest.

The installation extracts the package into `/opt/xr-kasmvnc`; it does not
replace the distribution's VNC server. The companion installer verifies the
package digest. Run it as root inside the development guest, with the package
and companion C source already copied there. Supply a new password on stdin
using a private terminal prompt; do not put passwords in command arguments,
URLs, source files or shell history. The installer expects the development
VM's `/usr/local/bin/xr-dev-awake` idle-policy helper to be configured first.

```sh
read -rsp 'New KasmVNC password: ' kasm_password; printf '\n'
printf '%s\n' "$kasm_password" | bash install-kasmvnc.sh \
  kasmvncserver_ubuntu_resolute_1.5.2_amd64.deb kasmvnc-ffmpeg-abi.c
unset kasm_password
```

The guest service is enabled at boot:

```sh
systemctl start xr-kasmvnc
systemctl stop xr-kasmvnc
systemctl status xr-kasmvnc
journalctl -u xr-kasmvnc
```

Only the new TCP port needs a host NAT mapping. On the host, after starting
the VM, use the existing QMP manager through the companion helper. Choose the
host's LAN IPv4 address explicitly to expose the browser desktop on the LAN.
This mapping is live only and must be added again after a VM restart:

```sh
python3 tools/qemu/kasmvnc-forward.py --workspace VM_WORKSPACE --listen HOST_LAN_IP
# To remove only this mapping:
python3 tools/qemu/kasmvnc-forward.py --workspace VM_WORKSPACE --listen HOST_LAN_IP --remove
```

Open `https://HOST_LAN_IP:8444/?resize=scale` in a browser and authenticate as `kasm-xr` with
the separately supplied password. The installer creates a private self-signed
certificate. Trust it for this development endpoint or install your own
certificate in the account's `.vnc/tls.crt` and `.vnc/tls.key` files. This
account's password file is separate from existing VNC and desktop passwords.
The Unix account remains locked for password login; the supplied credential
is for KasmVNC Basic authentication only. The `resize=scale` option scales the
existing desktop locally, preserving its resolution and application layout.
Without that option, the client defaults to resizing the guest desktop to
match the browser viewport.

Session environment for launching applications:

```sh
DISPLAY=:1
XAUTHORITY=/home/kasm-xr/.vnc/Xauthority
XDG_RUNTIME_DIR=/run/xr-kasmvnc
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json
```

The service starts a separate D-Bus session. Obtain its bus address from that
user's `ukui-session` environment if an application needs desktop D-Bus APIs;
do not reuse the existing desktop user's bus. The configured idle helper
turns off automatic screensaver and idle sleep for this account. Xkasmvnc has
no DPMS extension, so it cannot switch a virtual monitor into DPMS standby.

## Acceleration and streaming

[Upstream DRI3 instructions](https://docs.kasmvnc.com/docs/gpu_acceleration/)
are applied with `-hw3d -drinode /dev/dri/renderD128`. Verify `glxinfo -B` under
the session's display and X authority: the renderer must report VirGL with
the actual host GPU, direct rendering and `Accelerated: yes`.

The server selects H.264, with a maximum frame rate of 60. The browser's
quality preset may impose a lower frame rate; a server ceiling is not a
measurement. The tested client uses `SW H.264/AVC` and WebCodecs. Verify actual
encoded chunks and displayed VideoFrames under animation, rather than relying
on the selected codec label. Still-image compression and H.264 are different
modes. GPU application rendering and video encoding are independent.

The current VirtIO render node advertises only `VAProfileNone / VideoProc`;
`ffmpeg -c:v h264_vaapi` reports no usable encoding profile. Thus H.264 encoding
is software. The tested QEMU initialization does not set
`VIRGL_RENDERER_USE_VIDEO`; no live video-encode switch is available in this
configuration. Enabling a new VM video capability requires separate work and
acceptance. There is no PCI passthrough or host driver change in this setup.

## FFmpeg compatibility

[KasmVNC 1.5.2's loader](https://github.com/kasmtech/KasmVNC/blob/v1.5.2/common/rfb/ffmpeg.cxx)
recursively selects the first filename matching each FFmpeg library stem. On
openKylin, FFmpeg 6 and 8 coexist. Independent selection can mix `AVFrame`
allocation from libavutil 58 with libavcodec 62 and crash on the first encoded
frame. The companion `kasmvnc-ffmpeg-abi.c` shim redirects only Xkasmvnc's
explicit FFmpeg `dlopen` calls to one consistent FFmpeg 8 set:
libavcodec 62, libavformat 62, libavutil 60 and libswscale 9. The installer
builds this small shared library locally and sets `LD_PRELOAD` only on
Xkasmvnc, not the desktop applications. No upstream executable or system
library is overwritten. Verify `/proc/PID/maps` to ensure only this set is
mapped. Revisit this workaround after upstream fixes versioned loading.

Automated Chromium HTTP credentials do not populate WebSocket Basic auth in
the tested Playwright environment. Browser automation therefore used a
short-lived loopback-only authenticated relay to the real TLS LAN endpoint.
The relay existed only for acceptance and is not part of the service. The
LAN listener retains TLS and Basic auth; unauthenticated requests return 401.
Native browser password-dialog behavior must be checked separately by the
interactive user.

## Vulkan window presentation under KasmVNC

With the tested Mesa 26.0.3 Venus/X11 combination, GPU Vulkan applications
can keep rendering while their Kasm windows appear black. A bounded Venus
`vkcube` comparison reproduced this with the default WSI path and with
`MESA_VK_WSI_DEBUG=linear`. The same Venus application became visible using
`MESA_VK_WSI_DEBUG=sw,linear`; the selected GPU remained Intel UHD 770.
This preserves GPU rendering and changes the window image transfer to a CPU
path. It is not a zero-copy presentation path.

Apply this workaround only to the affected application's or XR compositor's
process environment. Do not change the existing display's runtime, all Vulkan
applications, or global Mesa settings. A runtime's frame counters alone do
not establish that its mirror is visible; verify the actual browser image.

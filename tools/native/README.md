# Native openKylin ARM64 development profile

This entry point runs alongside an existing openKylin Wayland/X11 desktop. It
requires `--native-development`, a native aarch64 kernel and arm64 openKylin
userspace. It does not create or modify `/etc/xr-vm-id`, install packages, change
system services, or acquire physical XR hardware. The QEMU entry and guard remain
separate. Keep this directory and `../common` together when staging scripts.

```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u)
# Set DISPLAY, WAYLAND_DISPLAY and XAUTHORITY from the actual logged-in session.
XR_DESKTOP_PREFIX="$HOME/ukui-xr-dev/install" \
  tools/native/run-desktop.sh --native-development \
  --runtime-prefix "$HOME/ukui-xr-dev/runtime" \
  --renderer freedreno --presentation auto --seconds 60 \
  --capture-dir "$HOME/ukui-xr-dev/captures" --debug-input --no-audio --no-ime
```

`XR_DESKTOP_PREFIX` contains `bin/xr-shell`, `xr-window-server`, `xr-dock` and
`xr-system-ui`. The runtime prefix contains `bin/monado-service` and, by default,
`share/openxr/1/openxr_monado.json`. Use `--runtime-json FILE` and repeated
`--runtime-lib-dir DIR` for the actual SDK installation layout; no SDK environment
script from the QEMU installation is sourced. The default native runtime prefix
is `$HOME/ukui-xr-dev/runtime` (overridable with `XR_NATIVE_RUNTIME_PREFIX`).

`--renderer freedreno` selects a real installed `freedreno_icd*.json`. Optional
`--renderer lavapipe` selects `lvp_icd*.json` and is logged as software rendering;
it is separate acceptance evidence. `--icd FILE` selects the exact installed ICD.
Native sessions do not automatically add Mesa software/linear WSI overrides.
`--presentation auto` prefers the original Wayland socket and then a reachable
X11 DISPLAY. Explicit `wayland`/`xcb` selects the corresponding Monado compositor
window. No fullscreen/direct-display mode is requested. The original Wayland
socket is converted to an absolute path before selecting a dedicated runtime.

The full desktop and standalone native simulator use
`$XDG_RUNTIME_DIR/xr-desktop-runtime`, a process lock, and remote input port 4244
(overridable with `--remote-port`). The native Monado candidate must implement
`XRT_REMOTE_BIND_ADDRESS=127.0.0.1`; the profile verifies the actual TCP listener
is exclusively loopback before starting the XR client. An unsupported bind
contract or occupied port fails startup and cleans up owned children.

Component sessions use a fresh D-Bus, 0700 private runtime/config/state/cache,
private Wayland socket, IME and optional PipeWire/Pulse/WirePlumber. The private
PipeWire configuration creates a null sink before startup, and readiness requires
actual sink/default readback. `--no-audio`/`--no-ime` skip those capabilities and
do not certify them. The native desktop, existing Fcitx and existing audio daemons
are not managed by this profile. Original display endpoints remain available only
to the Monado presentation process; component activation receives private Wayland,
Qt/GDK and audio settings.

`run-shell.sh` and `run-simulator.sh` are available for narrower stages and also
require `--native-development`. `run-shell.sh --headless-input` uses the same
remote devices without a sliders GUI. Both variants share the QEMU-tested owned
client-before-runtime cleanup implementation rather than a second session API.
PID birth tokens restrict signals to children created by this session. Runtime
exit, bounded termination and component cleanup are recorded in the user state
logs; no global process kill or native user-service restart is performed.

Install updates via a complete versioned directory and an atomic symlink/rename.
Do not overwrite a Bash script while it is executing. Script/package checks,
native startup, GPU rendering and actual input/IME/audio acceptance are distinct
results. This source change by itself does not establish native runtime success.

#!/bin/bash
# Run as the logged-in UKUI development user inside the x86_64 VM.
set -euo pipefail
grep -qx openkylin-qemu-amd64 /etc/xr-vm-id
[[ $(id -u) != 0 ]]
seconds=0
mode=simulated
renderer=venus
prefix=/opt/xr-dev
scale=30
remote_port=4242
wsi_copy=false
while (($#)); do
 case "$1" in
  --seconds) seconds=${2:?Missing duration}; shift 2 ;;
  --qwerty) mode=qwerty; shift ;;
  --sliders) mode=remote; shift ;;
  --remote-port) remote_port=${2:?Missing remote input port}; shift 2 ;;
  --wsi-copy) wsi_copy=true; shift ;;
  --renderer) renderer=${2:?Missing renderer}; shift 2 ;;
  --runtime-prefix) prefix=${2:?Missing runtime prefix}; shift 2 ;;
  --scale) scale=${2:?Missing scale percentage}; shift 2 ;;
  *) echo 'Usage: run-simulator.sh [--seconds N] [--qwerty|--sliders] [--remote-port PORT] [--wsi-copy] [--renderer venus|lavapipe] [--runtime-prefix DIR] [--scale 1-200]' >&2; exit 2 ;;
 esac
done
[[ $seconds =~ ^[0-9]+$ ]]
[[ $scale =~ ^[0-9]+$ && $scale -ge 1 && $scale -le 200 ]]
[[ $remote_port =~ ^[0-9]+$ && $remote_port -ge 1024 && $remote_port -le 65535 ]]
[[ -x $prefix/bin/monado-service && -f $prefix/share/openxr/1/openxr_monado.json ]]
source /opt/xr-dev/env.sh
export LD_LIBRARY_PATH="$prefix/lib64:$prefix/lib:$prefix/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH"
export DISPLAY=${DISPLAY:-:0}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
exec 9>"$XDG_RUNTIME_DIR/xr-simulator.lock"
flock -n 9 || { echo 'A simulator session is already running' >&2; exit 1; }
logs=${XDG_STATE_HOME:-$HOME/.local/state}/xr-simulator
mkdir -p "$logs"
out=$(mktemp -d "$logs/run-XXXXXX")
printf '%s\n' "$out" > "$logs/latest"
export XR_RUNTIME_JSON="$prefix/share/openxr/1/openxr_monado.json"
case "$renderer" in
 venus) export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json XRT_VK_DRM_MODIFIERS=1 ;;
 lavapipe) export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json LP_NUM_THREADS=4; unset XRT_VK_DRM_MODIFIERS ;;
 *) echo 'Unknown renderer' >&2; exit 2 ;;
esac
export XRT_NO_STDIN=1 XRT_COMPOSITOR_FORCE_XCB=1 XRT_COMPOSITOR_XCB_FULLSCREEN=0
export XRT_COMPOSITOR_COMPUTE=0 XRT_COMPOSITOR_SCALE_PERCENTAGE=$scale
export XRT_METRICS_FILE="$out/metrics.pb" XRT_METRICS_EARLY_FLUSH=1
if [[ $mode == remote ]]; then
 export P_OVERRIDE_ACTIVE_CONFIG=remote XDG_CONFIG_HOME="$out/config"
 mkdir -p "$XDG_CONFIG_HOME/monado"
 printf '{"active":"remote","remote":{"version":0,"port":%d,"view_count":2}}\n' "$remote_port" > "$XDG_CONFIG_HOME/monado/config_v0.json"
 unset SIMULATED_ENABLE QWERTY_ENABLE XRT_DEBUG_GUI
elif [[ $mode == qwerty ]]; then
 export P_OVERRIDE_ACTIVE_CONFIG=none
 export QWERTY_ENABLE=1 XRT_DEBUG_GUI=1
 unset SIMULATED_ENABLE
else
 export P_OVERRIDE_ACTIVE_CONFIG=none
 export SIMULATED_ENABLE=1 SIMULATED_LEFT=simple SIMULATED_RIGHT=simple
 unset QWERTY_ENABLE
fi
service_pid= app_pid= timer_pid= gui_pid= layout_pid=
cleanup() {
 trap - EXIT INT TERM
 for p in "$timer_pid" "$layout_pid" "$app_pid" "$gui_pid" "$service_pid"; do
  [[ -z $p ]] || kill "$p" 2>/dev/null || true
 done
 wait || true
 rm -f "$out/input.fifo"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
present_env=()
if $wsi_copy; then
 # Xkasmvnc needs CPU WSI transport; application/compositor rendering stays GPU.
 present_env=(env MESA_VK_WSI_DEBUG=sw,linear)
fi
"${present_env[@]}" "$prefix/bin/monado-service" > "$out/monado.log" 2>&1 & service_pid=$!
for ((i=0;i<150;i++)); do
 [[ -S $XDG_RUNTIME_DIR/monado_comp_ipc ]] && break
 kill -0 "$service_pid" || { cat "$out/monado.log" >&2; exit 1; }
 sleep .1
done
[[ -S $XDG_RUNTIME_DIR/monado_comp_ipc ]] || { cat "$out/monado.log" >&2; exit 1; }
printf 'XR_RENDERER=%s\nXR_RUNTIME_PREFIX=%s\nXR_LOG_DIRECTORY=%s\n' "$renderer" "$prefix" "$out"
printf 'XR_WSI_CPU_COPY=%s\n' "$wsi_copy"
if [[ $mode == remote ]]; then
 "$prefix/bin/monado-gui" remote "127.0.0.1:$remote_port" > "$out/controls.log" 2>&1 & gui_pid=$!
 if command -v xdotool >/dev/null && command -v wmctrl >/dev/null; then
  (
   for ((attempt=0;attempt<80;attempt++)); do
    gui=$(xdotool search --onlyvisible --pid "$gui_pid" --name '^Monado!' 2>/dev/null | head -1) || true
    mirror=$(xdotool search --onlyvisible --name '^Monado$' 2>/dev/null | head -1) || true
    if [[ -n $gui && -n $mirror ]]; then
     wmctrl -ir "$gui" -b remove,maximized_vert,maximized_horz
     read -r screen_width screen_height < <(xdotool getdisplaygeometry)
     gui_height=$((screen_height - 140))
     ((gui_height >= 700)) || gui_height=700
     mirror_y=280
     ((screen_height >= 960)) || mirror_y=200
     xdotool windowsize "$gui" 600 "$gui_height" windowmove "$gui" 10 40
     xdotool windowmove "$mirror" 630 "$mirror_y"
     break
    fi
    sleep .1
   done
  ) > "$out/layout.log" 2>&1 & layout_pid=$!
 fi
fi
if ((seconds)); then
 mkfifo "$out/input.fifo"
 exec 8<>"$out/input.fifo"
 (sleep "$seconds"; printf '\n' >&8) & timer_pid=$!
 hello_xr -g Vulkan2 -v <&8 > "$out/hello-xr.log" 2>&1 & app_pid=$!
 wait "$app_pid"
 app_pid=
 exec 8>&-
elif [[ $mode == remote ]]; then
 # The GUI owns interactive lifetime; keep stdin open for the sample.
 mkfifo "$out/input.fifo"
 exec 8<>"$out/input.fifo"
 hello_xr -g Vulkan2 -v <&8 > "$out/hello-xr.log" 2>&1 & app_pid=$!
 wait -n "$app_pid" "$gui_pid"
 printf '\n' >&8
 wait "$app_pid"
 app_pid=
 exec 8>&-
else
 hello_xr -g Vulkan2 -v > >(tee "$out/hello-xr.log") 2>&1
fi
printf 'XR_SESSION_EXIT=0\n'

#!/bin/bash
# Run as the logged-in UKUI development user inside the x86_64 VM.
set -euo pipefail
grep -qx openkylin-qemu-amd64 /etc/xr-vm-id
[[ $(id -u) != 0 ]]
seconds=0
mode=simulated
renderer=venus
while (($#)); do
 case "$1" in
  --seconds) seconds=${2:?Missing duration}; shift 2 ;;
  --qwerty) mode=qwerty; shift ;;
  --renderer) renderer=${2:?Missing renderer}; shift 2 ;;
  *) echo 'Usage: run-simulator.sh [--seconds N] [--qwerty] [--renderer venus|lavapipe]' >&2; exit 2 ;;
 esac
done
[[ $seconds =~ ^[0-9]+$ ]]
source /opt/xr-dev/env.sh
export DISPLAY=${DISPLAY:-:0}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
exec 9>"$XDG_RUNTIME_DIR/xr-simulator.lock"
flock -n 9 || { echo 'A simulator session is already running' >&2; exit 1; }
logs=${XDG_STATE_HOME:-$HOME/.local/state}/xr-simulator
mkdir -p "$logs"
out=$(mktemp -d "$logs/run-XXXXXX")
printf '%s\n' "$out" > "$logs/latest"
export XR_RUNTIME_JSON=/opt/xr-dev/share/openxr/1/openxr_monado.json
case "$renderer" in
 venus) export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json ;;
 lavapipe) export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json LP_NUM_THREADS=4 ;;
 *) echo 'Unknown renderer' >&2; exit 2 ;;
esac
export XRT_NO_STDIN=1 XRT_COMPOSITOR_FORCE_XCB=1 XRT_COMPOSITOR_XCB_FULLSCREEN=0
export XRT_COMPOSITOR_COMPUTE=0 XRT_COMPOSITOR_SCALE_PERCENTAGE=30
export XRT_METRICS_FILE="$out/metrics.pb" XRT_METRICS_EARLY_FLUSH=1
if [[ $mode == qwerty ]]; then
 export QWERTY_ENABLE=1 XRT_DEBUG_GUI=1
 unset SIMULATED_ENABLE
else
 export SIMULATED_ENABLE=1 SIMULATED_LEFT=simple SIMULATED_RIGHT=simple
 unset QWERTY_ENABLE
fi
service_pid= app_pid= timer_pid=
cleanup() {
 trap - EXIT INT TERM
 for p in "$timer_pid" "$app_pid" "$service_pid"; do
  [[ -z $p ]] || kill "$p" 2>/dev/null || true
 done
 wait || true
 rm -f "$out/input.fifo"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
monado-service > "$out/monado.log" 2>&1 & service_pid=$!
for ((i=0;i<150;i++)); do
 [[ -S $XDG_RUNTIME_DIR/monado_comp_ipc ]] && break
 kill -0 "$service_pid" || { cat "$out/monado.log" >&2; exit 1; }
 sleep .1
done
[[ -S $XDG_RUNTIME_DIR/monado_comp_ipc ]] || { cat "$out/monado.log" >&2; exit 1; }
printf 'XR_RENDERER=%s\nXR_LOG_DIRECTORY=%s\n' "$renderer" "$out"
if ((seconds)); then
 mkfifo "$out/input.fifo"
 exec 8<>"$out/input.fifo"
 (sleep "$seconds"; printf '\n' >&8) & timer_pid=$!
 hello_xr -g Vulkan2 -v <&8 > "$out/hello-xr.log" 2>&1 & app_pid=$!
 wait "$app_pid"
 app_pid=
 exec 8>&-
else
 hello_xr -g Vulkan2 -v > >(tee "$out/hello-xr.log") 2>&1
fi
printf 'XR_SESSION_EXIT=0\n'

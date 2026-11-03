#!/bin/bash
# Run as the logged-in UKUI development user inside the x86_64 VM.
set -euo pipefail
usage() {
 cat <<'EOF'
Usage: run-simulator.sh [--seconds N] [--qwerty|--sliders|--remote]
       [--remote-port PORT] [--wsi-copy] [--renderer venus|lavapipe]
       [--runtime-prefix DIR] [--scale 1-200]
       [--client PATH [--client-arg ARG ...]]

The default client is hello_xr -g Vulkan2 -v. Custom clients receive only the
explicit --client-arg arguments and must handle SIGTERM for graceful shutdown.
The timer or closing the sliders stops the client before this session's runtime.
--remote enables the remote devices without starting the sliders GUI.
EOF
}
seconds=0
mode=simulated
controls_gui=false
renderer=venus
prefix=/opt/xr-dev
scale=30
remote_port=4242
wsi_copy=false
client_path=
client_args=()
while (($#)); do
 case "$1" in
  --help|-h) usage; exit 0 ;;
  --seconds) seconds=${2:?Missing duration}; shift 2 ;;
  --qwerty) mode=qwerty; controls_gui=false; shift ;;
  --sliders) mode=remote; controls_gui=true; shift ;;
  --remote) mode=remote; controls_gui=false; shift ;;
  --remote-port) remote_port=${2:?Missing remote input port}; shift 2 ;;
  --wsi-copy) wsi_copy=true; shift ;;
  --renderer) renderer=${2:?Missing renderer}; shift 2 ;;
  --runtime-prefix) prefix=${2:?Missing runtime prefix}; shift 2 ;;
  --scale) scale=${2:?Missing scale percentage}; shift 2 ;;
  --client) client_path=${2:?Missing client executable}; shift 2 ;;
  --client-arg)
   (($# >= 2)) || { echo 'Missing client argument' >&2; exit 2; }
   client_args+=("$2"); shift 2 ;;
  *) usage >&2; exit 2 ;;
 esac
done
[[ $seconds =~ ^[0-9]+$ ]]
[[ $scale =~ ^[0-9]+$ && $scale -ge 1 && $scale -le 200 ]]
[[ $remote_port =~ ^[0-9]+$ && $remote_port -ge 1024 && $remote_port -le 65535 ]]
[[ -n $client_path || ${#client_args[@]} == 0 ]] || { echo '--client-arg requires --client' >&2; exit 2; }
grep -qx openkylin-qemu-amd64 /etc/xr-vm-id
[[ $(id -u) != 0 ]]
[[ -x $prefix/bin/monado-service && -f $prefix/share/openxr/1/openxr_monado.json ]]
source /opt/xr-dev/env.sh
export LD_LIBRARY_PATH="$prefix/lib64:$prefix/lib:$prefix/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH"
export DISPLAY=${DISPLAY:-:0}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
exec 9>"$XDG_RUNTIME_DIR/xr-simulator.lock"
flock -n 9 || { echo 'A simulator session is already running' >&2; exit 1; }
[[ ! -e $XDG_RUNTIME_DIR/monado_comp_ipc ]] || {
 echo 'An existing runtime socket is present; stop its owning session first' >&2; exit 1;
}
custom_client=false
if [[ -n $client_path ]]; then
 client_path=$(command -v -- "$client_path")
 [[ -f $client_path && -x $client_path ]] || { echo 'Client must be an executable file' >&2; exit 2; }
 custom_client=true
 client=("$client_path" "${client_args[@]}")
 client_log=client.log
else
 client=(hello_xr -g Vulkan2 -v)
 client_log=hello-xr.log
fi
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
app_status=not-started
fifo_open=false
stop_reason=client-exit
declare -A child_start=()

record() { printf '%s\n' "$*" | tee -a "$out/lifecycle.log"; }
process_start() {
 local stat rest
 [[ -r /proc/$1/stat ]] || return 1
 stat=$(<"/proc/$1/stat")
 rest=${stat##*) }
 local fields=()
 read -r -a fields <<< "$rest"
 [[ ${#fields[@]} -ge 20 ]] || return 1
 printf '%s\n' "${fields[19]}"
}
remember_child() {
 local token
 token=$(process_start "$2") || token=
 child_start[$2]=$token
 record "XR_CHILD_START role=$1 pid=$2 start=$token"
}
child_alive() {
 local token
 [[ -n ${1:-} ]] || return 1
 [[ -n ${child_start[$1]:-} ]] || return 1
 token=$(process_start "$1") || return 1
 [[ $token == "${child_start[$1]}" ]] && kill -0 "$1" 2>/dev/null
}
signal_child() {
 child_alive "${2:-}" || return 0
 kill -s "$1" "$2" 2>/dev/null || true
}
reap_child() {
 local role=$1 pid=$2 result
 if wait "$pid"; then result=0; else result=$?; fi
 record "XR_CHILD_EXIT role=$role pid=$pid status=$result"
}
stop_client() {
 [[ -n $app_pid ]] || return 0
 if child_alive "$app_pid"; then
  record "XR_CLIENT_STOP reason=$stop_reason pid=$app_pid"
  if ! $custom_client && $fifo_open; then
   printf '\n' >&8
  else
   signal_child TERM "$app_pid"
  fi
  local limit=$((SECONDS + 15))
  while child_alive "$app_pid"; do
   if ((SECONDS >= limit)); then
    record "XR_CLIENT_GRACE_TIMEOUT pid=$app_pid"
    signal_child KILL "$app_pid"
    cleanup_status=124
    break
   fi
   sleep .1
  done
  if wait "$app_pid"; then app_status=0; else app_status=$?; fi
 else
  if wait "$app_pid"; then app_status=0; else app_status=$?; fi
 fi
 record "XR_CHILD_EXIT role=client pid=$app_pid status=$app_status"
 app_pid=
}
cleanup() {
 local cleanup_status=$?
 trap - EXIT INT TERM
 set +e
 if [[ -n $timer_pid ]]; then signal_child TERM "$timer_pid"; reap_child timer "$timer_pid"; timer_pid=; fi
 if [[ -n $layout_pid ]]; then signal_child TERM "$layout_pid"; reap_child layout "$layout_pid"; layout_pid=; fi
 stop_client
 if [[ $app_status != not-started && $app_status != 0 && $cleanup_status == 0 ]]; then cleanup_status=$app_status; fi
 if [[ -n $gui_pid ]]; then signal_child TERM "$gui_pid"; reap_child controls "$gui_pid"; gui_pid=; fi
 # The runtime remains alive until the client's graceful exit has completed.
 if [[ -n $service_pid ]]; then signal_child TERM "$service_pid"; reap_child runtime "$service_pid"; service_pid=; fi
 if $fifo_open; then exec 8>&-; fi
 exec 7<&-
 rm -f "$out/input.fifo"
 record "XR_CLIENT_EXIT=$app_status XR_STOP_REASON=$stop_reason"
 record "XR_SESSION_EXIT=$cleanup_status"
 exit "$cleanup_status"
}
trap cleanup EXIT
trap 'stop_reason=signal-int; exit 130' INT
trap 'stop_reason=signal-term; exit 143' TERM
present_env=()
if $wsi_copy; then
 # Xkasmvnc needs CPU WSI transport; application/compositor rendering stays GPU.
 present_env=(env MESA_VK_WSI_DEBUG=sw,linear)
fi
"${present_env[@]}" "$prefix/bin/monado-service" 9>&- > "$out/monado.log" 2>&1 & service_pid=$!
remember_child runtime "$service_pid"
for ((i=0;i<150;i++)); do
 child_alive "$service_pid" || { cat "$out/monado.log" >&2; exit 1; }
 [[ -S $XDG_RUNTIME_DIR/monado_comp_ipc ]] && break
 sleep .1
done
[[ -S $XDG_RUNTIME_DIR/monado_comp_ipc ]] || { cat "$out/monado.log" >&2; exit 1; }
record "XR_RENDERER=$renderer XR_RUNTIME_PREFIX=$prefix"
record "XR_RUNTIME_JSON=$XR_RUNTIME_JSON XR_VK_DRIVER_FILES=$VK_DRIVER_FILES"
record "XR_LOG_DIRECTORY=$out XR_RUNTIME_DIRECTORY=$XDG_RUNTIME_DIR"
record "XR_WSI_CPU_COPY=$wsi_copy XR_DISPLAY=$DISPLAY XR_CLIENT_LOG=$client_log"
record "XR_INPUT_MODE=$mode XR_REMOTE_PORT=$remote_port XR_CONTROLS_GUI=$controls_gui"
printf -v client_description '%q ' "${client[@]}"
record "XR_CLIENT_COMMAND=$client_description"
if [[ $mode == remote ]] && $controls_gui; then
 "$prefix/bin/monado-gui" remote "127.0.0.1:$remote_port" 9>&- > "$out/controls.log" 2>&1 & gui_pid=$!
 remember_child controls "$gui_pid"
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
  ) 9>&- > "$out/layout.log" 2>&1 & layout_pid=$!
  remember_child layout "$layout_pid"
 fi
fi
if ! $custom_client && { ((seconds)) || [[ $mode == remote ]]; }; then
 mkfifo "$out/input.fifo"
 exec 8<>"$out/input.fifo"
 fifo_open=true
 "${client[@]}" <&8 8>&- 9>&- > "$out/$client_log" 2>&1 & app_pid=$!
elif $custom_client; then
 "${client[@]}" </dev/null 9>&- > "$out/$client_log" 2>&1 & app_pid=$!
else
 # Explicitly retain terminal stdin when running the sample in the background.
 exec 7<&0
 "${client[@]}" <&7 7<&- 9>&- > "$out/$client_log" 2>&1 & app_pid=$!
fi
remember_child client "$app_pid"
wait_pids=("$app_pid" "$service_pid")
[[ -z $gui_pid ]] || wait_pids+=("$gui_pid")
if ((seconds)); then
 sleep "$seconds" 9>&- & timer_pid=$!
 remember_child timer "$timer_pid"
 wait_pids+=("$timer_pid")
fi
finished=
# Check owned child identities before waiting. wait -n can overlook a child
# that has already completed while another child is still running.
while [[ -z $finished ]]; do
 for pid in "${wait_pids[@]}"; do
  if ! child_alive "$pid"; then
   finished=$pid
   if wait "$pid"; then result=0; else result=$?; fi
   break
  fi
 done
 [[ -n $finished ]] || sleep .1
done
case ${finished:-} in
 "$app_pid")
  app_status=$result
  record "XR_CHILD_EXIT role=client pid=$app_pid status=$app_status"
  app_pid=
  exit "$result" ;;
 "$service_pid")
  record "XR_CHILD_EXIT role=runtime pid=$service_pid status=$result"
  service_pid=; stop_reason=runtime-exit
  ((result)) || result=1
  exit "$result" ;;
 "$gui_pid")
  record "XR_CHILD_EXIT role=controls pid=$gui_pid status=$result"
  gui_pid=; stop_reason=controls-exit; exit "$result" ;;
 "$timer_pid")
  record "XR_CHILD_EXIT role=timer pid=$timer_pid status=$result"
  timer_pid=; stop_reason=duration; exit "$result" ;;
 *) stop_reason=wait-error; exit "$result" ;;
esac

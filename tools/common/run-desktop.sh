#!/bin/bash
# Isolated development composition; shared simulator owns Shell/Monado.
set -euo pipefail
XR_DEVELOPMENT_BACKEND=${XR_DEVELOPMENT_BACKEND:-qemu}
native_development=false
source "$(dirname -- "$(readlink -f -- "$0")")/development-guard.sh"
usage() {
 cat <<'EOF'
Usage: run-desktop.sh [--seconds N] [--capture-dir DIR] [--validation]
       [--debug-input] [--xwayland] [--no-audio] [--no-ime]
       [--scale 1-200]
       [--environment FILE] [--runtime-prefix DIR] [--remote-port PORT]
       [--renderer venus|freedreno|lavapipe] [--wsi-copy] [--simulator PATH]
       [--presentation auto|wayland|xcb] [--runtime-json FILE]
       [--runtime-lib-dir DIR ...] [--icd FILE] [--native-development]

QEMU requires its development VM marker; native requires explicit opt-in and
an openKylin arm64 system. XR_DESKTOP_PREFIX selects component binaries
(default /usr). Shell/Monado use a dedicated runtime. Components,
Wayland, Fcitx and audio use a fresh private runtime/config and D-Bus session.
EOF
}
original_args=("$@")
seconds=0 capture_dir= environment= simulator= private=
validation=false debug_input=false xwayland=false audio=true ime=true
runtime_args=(--remote-port "${XR_DESKTOP_REMOTE_PORT:-4244}")
while (($#)); do
 case "$1" in
  --help|-h) usage; exit 0 ;;
  --native-development) native_development=true; shift ;;
  --inside-private-bus) private=${2:?Missing private runtime}; shift 2 ;;
  --seconds) seconds=${2:?Missing duration}; shift 2 ;;
  --capture-dir) capture_dir=${2:?Missing capture directory}; shift 2 ;;
  --environment) environment=${2:?Missing environment}; shift 2 ;;
  --simulator) simulator=${2:?Missing simulator}; shift 2 ;;
  --validation) validation=true; shift ;;
  --debug-input) debug_input=true; shift ;;
  --xwayland) xwayland=true; shift ;;
  --no-audio) audio=false; shift ;;
  --no-ime) ime=false; shift ;;
  --runtime-prefix|--remote-port|--renderer|--presentation|--runtime-json|--runtime-lib-dir|--icd|--scale)
   (($# >= 2)) || { usage >&2; exit 2; }
   runtime_args+=("$1" "$2"); shift 2 ;;
  --wsi-copy) runtime_args+=(--wsi-copy); shift ;;
  *) usage >&2; exit 2 ;;
 esac
done
[[ $seconds =~ ^[0-9]+$ ]]
development_guard
original_runtime=${XDG_RUNTIME_DIR:?A logged-in development runtime directory is required}
[[ -d $original_runtime && -O $original_runtime ]]
# Preserve the original desktop's absolute Wayland endpoint before changing
# XDG_RUNTIME_DIR for Monado; a relative wayland-0 would resolve in the wrong place.
if [[ $XR_DEVELOPMENT_BACKEND == native && ${WAYLAND_DISPLAY:-} != /* && -n ${WAYLAND_DISPLAY:-} ]]; then
 export WAYLAND_DISPLAY="$original_runtime/$WAYLAND_DISPLAY"
fi
# A dedicated Monado runtime and port let the earlier panorama/debug session
# keep running. Explicit test sessions may already supply their own directory.
if [[ -z $private ]] && { [[ $XR_DEVELOPMENT_BACKEND == native ]] || [[ ${XR_DESKTOP_DEDICATED_RUNTIME:-1} == 1 ]]; }; then
 original_runtime="$original_runtime/xr-desktop-runtime"
 [[ ! -L $original_runtime ]]
 mkdir -p -m 700 "$original_runtime"
 [[ -d $original_runtime && -O $original_runtime ]]
 export XDG_RUNTIME_DIR="$original_runtime"
fi
prefix=${XR_DESKTOP_PREFIX:-/usr}
shell_socket="$original_runtime/xr-desktop/shell.sock"

birth() {
 local stat rest
 [[ -r /proc/$1/stat ]] || return 1
 stat=$(<"/proc/$1/stat"); rest=${stat##*) }
 local fields=(); read -r -a fields <<< "$rest"
 [[ ${#fields[@]} -ge 20 ]] || return 1
 printf '%s\n' "${fields[19]}"
}
declare -A starts=() pids=()
alive() {
 [[ -n ${1:-} && -n ${starts[${1:-0}]:-} ]] || return 1
 local current
 current=$(birth "$1") || return 1
 [[ $current == "${starts[$1]}" ]] && kill -0 "$1" 2>/dev/null
}
signal_owned() { if alive "${2:-}"; then kill -s "$1" "$2" 2>/dev/null || true; fi; }

if [[ -z $private ]]; then
 [[ ! -e $shell_socket ]] || { echo 'An existing Shell socket is present; stop its owning session first' >&2; exit 1; }
 private=$(mktemp -d "$original_runtime/xr-desktop-dev.XXXXXX")
 chmod 700 "$private"
 outer_child=
 forward() {
  local pid= token=
  if [[ -r $private/helper.pid ]]; then read -r pid token < "$private/helper.pid"; fi
  if [[ -n $pid && -n $token ]]; then
   starts[$pid]=$token; signal_owned TERM "$pid"
  elif [[ -n $outer_child ]]; then signal_owned TERM "$outer_child"; fi
 }
 outer_cleanup() {
  local result=$?
  trap - EXIT INT TERM
  set +e
  forward
  if [[ -n $outer_child ]]; then
   local limit=$((SECONDS+45))
   while alive "$outer_child" && ((SECONDS < limit)); do sleep .1; done
   if alive "$outer_child"; then signal_owned KILL "$outer_child"; result=124; fi
   wait "$outer_child" 2>/dev/null || true
  fi
  rm -rf -- "$private"
  exit "$result"
 }
 trap outer_cleanup EXIT
 trap forward INT TERM
 if [[ -n ${DBUS_SESSION_BUS_ADDRESS:-} ]]; then
  gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
   --method org.freedesktop.DBus.GetId > "$private/original-bus-id"
 else
  : > "$private/original-bus-id"
 fi
 dbus-run-session -- "$0" --inside-private-bus "$private" "${original_args[@]}" & outer_child=$!
 starts[$outer_child]=$(birth "$outer_child")
 result=0
 while :; do
  if wait "$outer_child"; then result=0; else result=$?; fi
  alive "$outer_child" || break
 done
 outer_child=
 exit "$result"
fi

[[ -d $private && -O $private && $private == "$original_runtime"/xr-desktop-dev.* ]]
[[ -n ${DBUS_SESSION_BUS_ADDRESS:-} ]]
printf '%s %s\n' "$$" "$(birth $$)" > "$private/helper.pid"
component_runtime="$private/runtime"
mkdir -p "$component_runtime" "$private/config" "$private/state" "$private/cache"
chmod 700 "$component_runtime" "$private/config" "$private/state" "$private/cache"
logs_root=${XDG_STATE_HOME:-$HOME/.local/state}/xr-desktop-dev
mkdir -p "$logs_root"
logs=$(mktemp -d "$logs_root/run-XXXXXX")
printf '%s\n' "$logs" > "$logs_root/latest"
if [[ -z $capture_dir ]]; then capture_dir="$logs/captures"; fi
mkdir -p "$capture_dir"
record() { printf '%s\n' "$*" | tee -a "$logs/lifecycle.log"; }
record "XR_DESKTOP_HELPER_PID=$$ XR_DESKTOP_PRIVATE=$private XR_DESKTOP_BACKEND=$XR_DEVELOPMENT_BACKEND"
record "XR_DESKTOP_LOG_DIRECTORY=$logs XR_DESKTOP_CAPTURE_DIRECTORY=$capture_dir"
record "XR_DESKTOP_SHELL_RUNTIME=$original_runtime XR_DESKTOP_COMPONENT_RUNTIME=$component_runtime"
record "XR_DESKTOP_SHELL_SOCKET=$shell_socket XR_DESKTOP_ISOLATED_BUS=1"

# dbus-run-session starts its daemon before it sets the child's address. Its
# activation environment must therefore be replaced explicitly as well as the
# environments of directly spawned components. Never update the user manager.
[[ -r $private/original-bus-id ]]
private_bus_id=$(gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
 --method org.freedesktop.DBus.GetId)
original_bus_id=$(<"$private/original-bus-id")
[[ -n $private_bus_id && $private_bus_id != "$original_bus_id" ]] || {
 record 'XR_DESKTOP_PRIVATE_BUS_CHECK_FAILED=1'; exit 1;
}
gtk_im_module=${XR_DESKTOP_GTK_IM_MODULE:-wayland}
qt_im_module=${XR_DESKTOP_QT_IM_MODULE-}
mkdir -p "$private/config/pulse"
printf 'autospawn = no\n' > "$private/config/pulse/client.conf"
activation_env=(
 "PATH=$prefix/bin:$PATH" "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"
 DISPLAY= XAUTHORITY= SESSION_MANAGER= DESKTOP_STARTUP_ID=
 "XDG_RUNTIME_DIR=$component_runtime" "XDG_CONFIG_HOME=$private/config"
 "XDG_STATE_HOME=$private/state" "XDG_CACHE_HOME=$private/cache"
 WAYLAND_DISPLAY=xr-wayland-0 XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=UKUI
 QT_QPA_PLATFORM=wayland GDK_BACKEND=wayland "QT_IM_MODULE=$qt_im_module" "GTK_IM_MODULE=$gtk_im_module" XMODIFIERS=
 "PIPEWIRE_RUNTIME_DIR=$component_runtime" PIPEWIRE_REMOTE=pipewire-0
 "PULSE_RUNTIME_PATH=$component_runtime/pulse" "PULSE_SERVER=unix:$component_runtime/pulse/native" PULSE_COOKIE=
 "PULSE_CLIENTCONFIG=$private/config/pulse/client.conf" XR_SYSTEM_UI_ISOLATED_BUS=1)
dbus-update-activation-environment "${activation_env[@]}"
record "XR_DESKTOP_ACTIVATION_ENV_PRIVATE=1 XR_DESKTOP_GTK_IM_MODULE=$gtk_im_module QT_QPA_PLATFORM=wayland GDK_BACKEND=wayland"

for binary in xr-shell xr-window-server xr-dock xr-system-ui; do
 [[ -x $prefix/bin/$binary ]] || { record "Missing component: $prefix/bin/$binary"; exit 1; }
done
run_shell=$(dirname -- "$(readlink -f -- "$0")")/run-shell.sh
if [[ ! -x $run_shell ]]; then run_shell=$(command -v xr-shell-simulator); fi

component_env=(env "${activation_env[@]}")
start() {
 local role=$1; shift
 "$@" > "$logs/$role.log" 2>&1 &
 local pid=$!
 pids[$role]=$pid; starts[$pid]=$(birth "$pid") || starts[$pid]=
 record "XR_DESKTOP_CHILD_START role=$role pid=$pid birth=${starts[$pid]}"
}
wait_socket() {
 local path=$1 role=$2
 for ((attempt=0;attempt<150;attempt++)); do
  alive "${pids[$role]}" || { record "XR_DESKTOP_START_FAILED role=$role"; return 1; }
  [[ ! -S $path ]] || return 0
  sleep .1
 done
 record "XR_DESKTOP_SOCKET_TIMEOUT role=$role path=$path"; return 1
}
reap() {
 local role=$1 pid=${pids[$1]} result
 local limit=$((SECONDS+5))
 while alive "$pid" && ((SECONDS < limit)); do sleep .1; done
 if alive "$pid"; then signal_owned KILL "$pid"; record "XR_DESKTOP_KILL role=$role pid=$pid"; fi
 if wait "$pid"; then result=0; else result=$?; fi
 record "XR_DESKTOP_CHILD_EXIT role=$role pid=$pid status=$result"
 unset 'pids[$role]'
 return "$result"
}
draining_recorded=false
session_draining() {
 local phase owner_pid owner_start client_pid client_start reason initial_status current
 [[ -f $private/shell-session.state && ! -L $private/shell-session.state ]] || return 1
 read -r phase owner_pid owner_start client_pid client_start reason initial_status < "$private/shell-session.state" || return 1
 [[ $phase == draining && $owner_pid =~ ^[0-9]+$ && $client_pid =~ ^[0-9]+$ \
    && $initial_status =~ ^[0-9]+$ \
    && $owner_pid == "${pids[shell-session]:-}" \
    && $owner_start == "${starts[$owner_pid]:-}" \
    && $client_pid == "${pids[shell-client]:-}" \
    && $client_start == "${starts[$client_pid]:-}" ]] || return 1
 # The wrapper can finish between the loop check and this read. Its cached
 # owned identity still applies; reject a live PID with a different birth.
 if current=$(birth "$owner_pid"); then [[ $current == "$owner_start" ]] || return 1; fi
 # A client may already be reaped, but a reused PID must not match this state.
 if current=$(birth "$client_pid"); then [[ $current == "$client_start" ]] || return 1; fi
 if ! $draining_recorded; then
  record "XR_DESKTOP_SESSION_DRAINING owner_pid=$owner_pid client_pid=$client_pid reason=$reason initial_status=$initial_status"
  draining_recorded=true
 fi
}
cleanup() {
 local result=$?
 trap - EXIT INT TERM
 set +e
 # This PID was obtained from our own simulator child's lifecycle log.
 if [[ -n ${pids[shell-client]:-} ]]; then signal_owned TERM "${pids[shell-client]}";
 elif [[ -n ${pids[shell-session]:-} ]]; then signal_owned TERM "${pids[shell-session]}"; fi
 record 'XR_DESKTOP_SHELL_EXIT_REQUESTED=1'
 for role in system-ui dock ime window-server wireplumber pulse pipewire; do
  [[ -z ${pids[$role]:-} ]] || signal_owned TERM "${pids[$role]}"
 done
 for role in system-ui dock ime window-server wireplumber pulse pipewire; do
  [[ -z ${pids[$role]:-} ]] || reap "$role"
 done
 if [[ -n ${pids[shell-session]:-} ]]; then
  local limit=$((SECONDS+20))
  while alive "${pids[shell-session]}" && ((SECONDS < limit)); do sleep .1; done
  if alive "${pids[shell-session]}"; then signal_owned TERM "${pids[shell-session]}"; fi
  reap shell-session
 fi
 record "XR_DESKTOP_EXIT=$result"
 exit "$result"
}
trap cleanup EXIT
trap 'exit 0' INT TERM

shell_args=(--headless-input --shell-binary "$prefix/bin/xr-shell" --capture-dir "$capture_dir"
 --seconds "$seconds" --shell-arg --components "${runtime_args[@]}")
if [[ $XR_DEVELOPMENT_BACKEND == native ]]; then shell_args+=(--native-development); fi
[[ -z $environment ]] || shell_args+=(--environment "$environment")
[[ -z $simulator ]] || shell_args+=(--simulator "$simulator")
if $validation; then shell_args+=(--validation); fi
if $debug_input; then shell_args+=(--shell-arg --debug-input); fi
start shell-session env "XR_SESSION_STATE_FILE=$private/shell-session.state" "$run_shell" "${shell_args[@]}"
wait_socket "$shell_socket" shell-session
while read -r marker role pid token; do
 if [[ $marker == XR_CHILD_START && $role == role=client ]]; then
  pid=${pid#pid=}; token=${token#start=}; pids[shell-client]=$pid; starts[$pid]=$token; break
 fi
done < "$logs/shell-session.log"
server_args=(--shell-socket "$shell_socket" --socket-name xr-wayland-0)
if $xwayland; then server_args+=(--xwayland); fi
start window-server "${component_env[@]}" "$prefix/bin/xr-window-server" "${server_args[@]}"
wait_socket "$component_runtime/xr-wayland-0" window-server

if $ime; then
 command -v fcitx5 >/dev/null
 mkdir -p "$private/config/fcitx5"
 if [[ -f /usr/share/fcitx5/inputmethod/pinyin.conf ]]; then
  cat > "$private/config/fcitx5/profile" <<'EOF'
[Groups/0]
Name=XR
DefaultLayout=us
DefaultIM=pinyin
[Groups/0/Items/0]
Name=keyboard-us
Layout=
[Groups/0/Items/1]
Name=pinyin
Layout=
[GroupOrder]
0=XR
EOF
 fi
 start ime "${component_env[@]}" fcitx5 --replace -D
 ime_ready=false
 for ((attempt=0;attempt<100;attempt++)); do
  alive "${pids[ime]}" || break
  if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
      --method org.freedesktop.DBus.NameHasOwner org.fcitx.Fcitx5 2>/dev/null | grep -q true; then ime_ready=true; break; fi
  sleep .1
 done
 $ime_ready || { record 'XR_DESKTOP_IME_FAILED=1'; exit 1; }
fi
if $audio; then
 for command in pipewire pipewire-pulse wireplumber pactl timeout; do command -v "$command" >/dev/null; done
 # Create the device in this private core's configuration before startup.
 # Pulse compatibility servers may reject dynamic module-null-sink loading.
 mkdir -p "$private/config/pipewire/pipewire.conf.d"
 cat > "$private/config/pipewire/pipewire.conf.d/20-xr-development-audio.conf" <<'EOF'
context.objects = [
    { factory = adapter
        args = {
            factory.name = support.null-audio-sink
            node.name = xr_desktop_null
            node.description = "XR Development Audio"
            media.class = "Audio/Sink"
            node.virtual = true
            audio.position = [ FL FR ]
        }
    }
]
EOF
 cp "$private/config/pipewire/pipewire.conf.d/20-xr-development-audio.conf" "$logs/pipewire-null-sink.conf"
 # Keep routing policy for our virtual sink, without acquiring any hardware
 # that belongs to the original desktop. WirePlumber 0.5 profiles support
 # explicit disabled features and inherit the installed policy-only profile.
 mkdir -p "$private/config/wireplumber/wireplumber.conf.d"
 cat > "$private/config/wireplumber/wireplumber.conf.d/20-xr-development.conf" <<'EOF'
wireplumber.profiles = {
    xr-development = {
        inherits = [ policy ]
        hardware.audio = disabled
        hardware.bluetooth = disabled
        hardware.video-capture = disabled
        monitor.alsa = disabled
        monitor.alsa-midi = disabled
        monitor.bluez = disabled
        monitor.bluez-midi = disabled
        monitor.v4l2 = disabled
        monitor.libcamera = disabled
        monitor.alsa.reserve-device = disabled
        monitor.bluez.seat-monitoring = disabled
        support.reserve-device = disabled
        support.logind = disabled
    }
}
EOF
 cp "$private/config/wireplumber/wireplumber.conf.d/20-xr-development.conf" "$logs/wireplumber-private.conf"
 start pipewire "${component_env[@]}" pipewire
 wait_socket "$component_runtime/pipewire-0" pipewire
 start pulse "${component_env[@]}" pipewire-pulse
 wait_socket "$component_runtime/pulse/native" pulse
 start wireplumber "${component_env[@]}" wireplumber --profile xr-development
 record 'XR_DESKTOP_WIREPLUMBER_PROFILE=xr-development XR_DESKTOP_AUDIO_HARDWARE_MONITORS=disabled'
 sink_ready=false
 deadline=$((SECONDS+15))
 while ((SECONDS < deadline)); do
  for role in pipewire pulse wireplumber; do
   alive "${pids[$role]}" || { record "XR_DESKTOP_AUDIO_FAILED role=$role"; exit 1; }
  done
  if timeout 2 "${component_env[@]}" pactl list short sinks > "$logs/audio-sinks-short.log" 2> "$logs/audio-control-error.log" \
      && awk '$2 == "xr_desktop_null" { found = 1 } END { exit !found }' "$logs/audio-sinks-short.log"; then
   sink_ready=true; break
  fi
  sleep .1
 done
 $sink_ready || { record 'XR_DESKTOP_AUDIO_SINK_MISSING=xr_desktop_null'; exit 1; }
 timeout 2 "${component_env[@]}" pactl set-default-sink xr_desktop_null > "$logs/audio-set-default.log" 2>&1
 timeout 2 "${component_env[@]}" pactl get-default-sink > "$logs/audio-default-sink.log"
 [[ $(<"$logs/audio-default-sink.log") == xr_desktop_null ]] || {
  record 'XR_DESKTOP_AUDIO_DEFAULT_SINK_MISMATCH=1'; exit 1;
 }
 timeout 2 "${component_env[@]}" pactl list sinks > "$logs/audio-sinks.log"
 record 'XR_DESKTOP_AUDIO_SINK=xr_desktop_null XR_DESKTOP_AUDIO_DEFAULT_VERIFIED=1'
fi
start dock "${component_env[@]}" "$prefix/bin/xr-dock" --socket "$shell_socket"
start system-ui "${component_env[@]}" "$prefix/bin/xr-system-ui" run --socket "$shell_socket" \
 --state-file "$logs/system-ui-state.json" --isolated-notifications
record 'XR_DESKTOP_READY=1'

while alive "${pids[shell-session]}"; do
 for role in window-server dock system-ui ime pipewire pulse wireplumber; do
  [[ -z ${pids[$role]:-} ]] || alive "${pids[$role]}" || {
   # Re-read after observing an exit to close the polling/marker boundary race.
   if [[ $role == window-server || $role == dock || $role == system-ui ]] && session_draining; then
    if reap "$role"; then
     record "XR_DESKTOP_COMPONENT_DRAINED=$role"
    else
     record "XR_DESKTOP_COMPONENT_FAILED=$role phase=draining"; exit 1
    fi
   else
    record "XR_DESKTOP_COMPONENT_FAILED=$role"; exit 1
   fi
  }
 done
 sleep .2
done
if wait "${pids[shell-session]}"; then result=0; else result=$?; fi
record "XR_DESKTOP_CHILD_EXIT role=shell-session pid=${pids[shell-session]} status=$result"
unset 'pids[shell-session]'
exit "$result"

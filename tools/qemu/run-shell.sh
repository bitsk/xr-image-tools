#!/bin/bash
# Thin development profile; runtime lifecycle remains in xr-simulator.
set -euo pipefail
usage() {
 cat <<'EOF'
Usage: run-shell.sh [--shell-binary PATH] [--simulator PATH]
       [--environment MANIFEST] [--capture-dir DIR] [--validation]
       [--seconds N] [--renderer venus|lavapipe] [--runtime-prefix DIR]
       [--remote-port PORT] [--headless-input] [--wsi-copy] [--scale 1-200]
       [--shell-arg ARG ...]

Starts Rust XR Shell with the existing head/controller sliders. On DISPLAY=:1,
defaults to remote port 4243 and WSI CPU copy; other displays use port 4242.
--headless-input selects remote devices without starting the sliders GUI.
EOF
}
shell_binary=xr-shell
simulator=
shell_args=()
simulator_args=()
remote_port=
input_mode=--sliders
while (($#)); do
 case "$1" in
  --help|-h) usage; exit 0 ;;
  --shell-binary) shell_binary=${2:?Missing Rust Shell executable}; shift 2 ;;
  --simulator) simulator=${2:?Missing simulator executable}; shift 2 ;;
  --environment|--capture-dir)
   (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
   shell_args+=("$1" "$2"); shift 2 ;;
  --validation) shell_args+=(--validation); shift ;;
  --headless-input) input_mode=--remote; shift ;;
  --shell-arg)
   (($# >= 2)) || { echo 'Missing Shell argument' >&2; exit 2; }
   shell_args+=("$2"); shift 2 ;;
  --seconds|--renderer|--runtime-prefix|--scale)
   (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
   simulator_args+=("$1" "$2"); shift 2 ;;
  --remote-port) remote_port=${2:?Missing remote port}; shift 2 ;;
  --wsi-copy) simulator_args+=(--wsi-copy); shift ;;
  *) usage >&2; exit 2 ;;
 esac
done
if [[ -z $simulator ]]; then
 sibling=$(dirname -- "$(readlink -f -- "$0")")/run-simulator.sh
 if [[ -x $sibling ]]; then simulator=$sibling; else simulator=xr-simulator; fi
fi
if [[ ${DISPLAY:-:0} == :1 ]]; then
 remote_port=${remote_port:-4243}
 simulator_args+=(--wsi-copy)
else
 remote_port=${remote_port:-4242}
fi
command=("$simulator" "$input_mode" --scale 100 --remote-port "$remote_port" "${simulator_args[@]}" --client "$shell_binary")
for arg in "${shell_args[@]}"; do command+=(--client-arg "$arg"); done
exec "${command[@]}"

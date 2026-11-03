#!/bin/bash
# qemu development entry; lifecycle is shared by the common helper.
set -euo pipefail
export XR_DEVELOPMENT_BACKEND=qemu
exec "$(dirname -- "$(readlink -f -- "$0")")/../common/run-shell.sh" "$@"

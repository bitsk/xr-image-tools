#!/bin/bash
# native development entry; lifecycle is shared by the common helper.
set -euo pipefail
export XR_DEVELOPMENT_BACKEND=native
exec "$(dirname -- "$(readlink -f -- "$0")")/../common/run-simulator.sh" "$@"

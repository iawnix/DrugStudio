#!/usr/bin/env bash
set -Eeuo pipefail
DRUGSTUDIO_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec bash "$DRUGSTUDIO_DIRECTORY/src/drugstudio/scripts/run_receptor_env.sh" "$@"

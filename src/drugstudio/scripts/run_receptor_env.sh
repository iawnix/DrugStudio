#!/usr/bin/env bash
set -Eeuo pipefail

DOCK_RECEPTOR_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
DOCK_RECEPTOR_PREFIX="${DRUGSTUDIO_RECEPTOR_PREFIX:-}"
if [[ -z "$DOCK_RECEPTOR_PREFIX" && -f "$DOCK_RECEPTOR_SOURCE/pyproject.toml" ]]; then
  DOCK_RECEPTOR_PREFIX="$DOCK_RECEPTOR_SOURCE/environments/receptor/.venv"
fi
if [[ -z "$DOCK_RECEPTOR_PREFIX" || ! -x "$DOCK_RECEPTOR_PREFIX/bin/python" ]]; then
  printf 'PDBFixer/OpenMM is not installed. Set DRUGSTUDIO_RECEPTOR_PREFIX to a receptor environment containing bin/python.\n' >&2
  exit 1
fi
if [[ $# -eq 0 ]]; then
  printf 'Usage: run_receptor_env.sh COMMAND [ARGS...]\n' >&2
  exit 2
fi

unset PYTHONHOME PYTHONPATH
export PATH="$DOCK_RECEPTOR_PREFIX/bin:$PATH"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export OPENMM_CPU_THREADS="${OPENMM_CPU_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
exec "$@"

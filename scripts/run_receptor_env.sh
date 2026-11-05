#!/usr/bin/env bash
set -Eeuo pipefail

DOCK_RECEPTOR_PACKAGE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
DOCK_RECEPTOR_PREFIX="$DOCK_RECEPTOR_PACKAGE/environments/receptor/.venv"
if [[ ! -x "$DOCK_RECEPTOR_PREFIX/bin/python" ]]; then
  printf 'PDBFixer/OpenMM is not installed. See %s/environments/receptor/README.md\n' "$DOCK_RECEPTOR_PACKAGE" >&2
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

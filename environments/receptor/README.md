# Receptor preparation environment

This isolated Python 3.11 environment provides PDBFixer and OpenMM from PyPI.
`requirements.txt` specifies the supported package versions;
`requirements.lock` records the exact installed runtime packages. It is separate
from shared RDKit/Meeko and the LLM environment. The OpenMM wheel supports CPU
execution without downloading the CUDA libraries selected by the conda package.

Installed on 2026-10-28: Python 3.11.2, PDBFixer 1.12.0, OpenMM 8.6.1 and
NumPy 2.4.6. Imports through the workflow wrapper succeeded. Available OpenMM
platforms are `CPU` and `Reference`. Optional OpenCL plugins cannot load because
`libOpenCL.so.1` is absent; CPU use does not require that library.
The installation record should be kept under the local `.runtime/` directory;
runtime logs are not part of the repository.

Install or reproduce the environment:

```bash
cd environments/receptor
mkdir -p ../../.runtime/receptor-install/{pip-cache,tmp,logs}
TMPDIR="$PWD/../../.runtime/receptor-install/tmp" python3.11 -m venv .venv
TMPDIR="$PWD/../../.runtime/receptor-install/tmp" \
PIP_CACHE_DIR="$PWD/../../.runtime/receptor-install/pip-cache" \
  .venv/bin/python -m pip install --only-binary=openmm,numpy -r requirements.lock \
  > ../../.runtime/receptor-install/logs/install-pip.log 2>&1
```

The interpreter is `.venv/bin/python` below this directory. The workflow uses
`scripts/run_receptor_env.sh` automatically for PDBFixer. It retains
the caller's working directory, clears inherited Python package paths, and
limits OpenMM CPU threads to one by default. The existing chemistry command
builder supplies run-local temporary and cache directories. Meeko and ligand
chemistry keep their existing runners.

For direct commands, first change to an owning workflow's work directory, then
call the wrapper with a command, for example:

```bash
../../scripts/run_receptor_env.sh \
  python -c 'import importlib.metadata as m; print(m.version("pdbfixer"), m.version("OpenMM"))'
```

Package installation and import checks do not validate receptor repair
chemistry or the full docking workflow. Runs that previously stopped because
the dependency was missing retain their old diagnostics. Start a new run to
avoid reusing partial preparation files.

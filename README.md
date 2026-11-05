# DrugStudio

DrugStudio is an agent workbench for structure-based drug discovery. It
combines a conversational interface with modular structure retrieval,
receptor and ligand preparation, pocket finding, docking, assessment and
human review. The LLM routes work and interprets evidence; scientific tools
perform deterministic calculations and preserve their inputs and outputs.

> DrugStudio is an active research prototype. Docking scores and predicted
> pockets are computational evidence, not proof of binding or experimental
> activity. Review the limitations in each run report before making decisions.

## Capabilities

- ReAct-style LangGraph supervisor with separate scientific stage agents.
- Experimental structure retrieval and AlphaFold Database lookup fallback.
- Optional PDBFixer/OpenMM receptor repair and RDKit ligand conformers.
- Optional xTB conformer optimization, Meeko preparation and AutoDock Vina.
- P2Rank pocket ranking, with optional fpocket comparison and human review.
- Molecular property assessment and symmetry-aware reference redocking RMSD.
- Project-scoped chat, file browser/editor, terminal and scientific run views.
- Append-only run events, artifact hashes, provenance, stage reruns and review records.

Important limits:

- AlphaFold Server submission, polling and retrieval are not implemented by the
  current adapter; see [the design document](docs/alphafold-server-workflow-design.md).
- The default assembled docking engine is Vina. Pocket prediction and docking
  scores do not establish a binding site or binding affinity.
- Automatic pKa/tautomer enumeration and general multi-microstate selection
  are not implemented. xTB optimization does not validate chemical identity.
- A successful run or a small redocking benchmark is not broad validation.

## Install

Requires Python 3.11 for the locked orchestration environment. From the
repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[openai,chemistry]'
```

RDKit, PDBFixer/OpenMM, Meeko, AutoDock Vina, xTB, P2Rank and fpocket are
separate scientific tools. Install only the tools required by your workflow
and configure their executable paths. The package does not distribute
proprietary software, model weights, or external databases. See
[receptor environment setup](environments/receptor/README.md) for the optional
PDBFixer environment.

Configure the model in the environment or in the workbench settings:

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"  # optional compatible endpoint
export DOCK_AGENT_MODEL="gpt-4.1-mini"              # optional
```

The key is not written to manifests. Never commit credentials or local model
configuration.

## Run

Create a run directory in the owning project's work area, then initialize it:

```bash
drugstudio init \
  --run-root /path/to/project/work/drugstudio/run-001 \
  --target-id target-001 \
  --ligand ligand-001='CCO'
drugstudio run --run-root /path/to/project/work/drugstudio/run-001
drugstudio status --run-root /path/to/project/work/drugstudio/run-001
```

To start the persistent web workbench:

```bash
python -m dock_agent.web.server \
  --runs-root /path/to/project/work/drugstudio/runs \
  --workspace-root /path/to/projects \
  --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`. Non-loopback binds require an access token.
The workbench can select projects and runs independently. It stores small
relationship sidecars in `.workbench/` and each run's `run-meta.json`; the
scientific manifest remains the source of truth. `GET /api/provenance` returns
the upstream artifact chain for a selected result.

## Architecture

Scientific modules communicate through the typed contracts in
`dock_agent/contracts.py` and ports in `dock_agent/ports.py`. The supervisor
uses the LLM to choose registered tools and interpret their structured
observations. Tool adapters own the chemistry and external program calls.
Run artifacts are immutable and content-hashed; reruns branch to new run
directories, retaining the original inputs and outputs.

## Development

```bash
python -m compileall -q dock_agent
node --check dock_agent/web/static/app.js
```

The focused preparation regression checks are in `tests/`. Tests requiring
PDBFixer/OpenMM or external chemistry executables need those optional tools
installed. No run data, API keys, virtual environments, or local web captures
belong in the repository.

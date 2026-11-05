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
python -m drugstudio.web.server \
  --runs-root /path/to/project/work/drugstudio/runs \
  --workspace-root /path/to/projects \
  --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`. Non-loopback binds require an access token.
The workbench can select projects and runs independently. It stores small
relationship sidecars in `.workbench/` and each run's `run-meta.json`; the
scientific manifest remains the source of truth. `GET /api/provenance` returns
the upstream artifact chain for a selected result.

### Run parameters

The tunable scientific parameters are declared once in
`src/drugstudio/parameters.py`, which the CLI, the `/api/parameters`
endpoints and the browser form all read. Values are stored per run in
`inputs/runtime.json`, the same artifact recorded as
`runtime_configuration`.

That file is the run's reproducibility record, so it wins over the argparse
defaults: `drugstudio run` reads it and uses a default only where the file
has no value and the flag was not given on the command line. An explicitly
named flag always takes precedence. The workbench passes a run's saved
values to the subprocess it starts, so resuming a run continues on the
parameters it was configured with rather than on whatever the defaults are
today.

Box centre and size are deliberately not parameters. They are a human
review decision, recorded through `overrides` by the review command and the
review panel.

### Chat tools and approval

The project assistant is given the run's current state — stage statuses and
errors, pocket candidates, docking counts and best score, recent failures
and human decisions, and any parameter that differs from its default — so it
answers from the manifest rather than searching the filesystem for what the
manifest already says.

Its read-only tools (`list_files`, `read_file`) run directly. The two that
change something, `run_command` and `write_file`, do not: the turn suspends,
the exact command or file is shown in the transcript, and nothing happens
until a person approves it. A refusal is relayed to the model with the
reason so it proposes something else instead of retrying. Both the request
and the decision go on the run's event timeline.

This is a control rather than an instruction in the prompt. A prompt asking
the model to check first is a request the model may not honour, and
`run_command` is a shell.

The server keeps no current project or current run of its own. Every request
names its own scope through `project_id` and `run_key`, so two browser tabs,
or two people, can work on different runs at the same time without changing
each other's view. `GET /api/runs?project_id=...` lists only that project's
runs.

The server answers only requests whose `Host` header matches the address it
was bound to. Behind a reverse proxy, pass the public name explicitly:

```bash
python -m drugstudio.web.server --runs-root ... --host 0.0.0.0 --port 8765 \
  --allowed-host workbench.lab.example
```

Run artifacts are served as downloads with `X-Content-Type-Options: nosniff`,
so an HTML or SVG file produced by an external tool cannot execute on the
workbench origin. The browsers' 3D viewers and the editor read them with
`fetch()` and are unaffected.

## Architecture

Scientific modules communicate through the typed contracts in
`src/drugstudio/contracts.py` and ports in `src/drugstudio/ports.py`. The supervisor
uses the LLM to choose registered tools and interpret their structured
observations. Tool adapters own the chemistry and external program calls.
Run artifacts are immutable and content-hashed; reruns branch to new run
directories, retaining the original inputs and outputs.

## Development

```bash
python -m compileall -q src/drugstudio
node --check src/drugstudio/web/static/app.js
```

The focused preparation regression checks are in `tests/`. Tests requiring
PDBFixer/OpenMM or external chemistry executables need those optional tools
installed. No run data, API keys, virtual environments, or local web captures
belong in the repository.

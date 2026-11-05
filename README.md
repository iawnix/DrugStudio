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

The editable scientific parameters and validation rules live in
`src/drugstudio/parameters.py`. The CLI and `/api/parameters` use those rules,
and the browser builds its forms from the API schema.

Saving an edit writes the run's `parameter-settings.json` sidecar, without
changing historical artifacts. Execution records an immutable
`inputs/runtime-<hash>.json` snapshot in the manifest. Legacy
`inputs/runtime.json` snapshots remain readable and retain their checksums.
A resumed run loads its latest snapshot and saved edits; explicitly supplied
CLI flags take precedence. Partial API updates retain the other saved values.

Changes affecting computed stages require a rerun branch from the earliest
affected stage. For example, seed changes require rerunning **ligand preparation**
and downstream stages, since the seed affects conformer generation as well as
docking. The UI displays the required stage and startup rejects incompatible
reuse of old outputs. Parameter saves are blocked during active web or CLI
execution, and audit events record both old and new values.

Box centre and size remain human review decisions through `overrides`. Existing
manual CLI boxes are preserved when continuing without new box flags.

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

Projects without runs return an empty state. Their files, terminal and chats
remain available; run actions are rejected until a run exists. A request naming
both a project and a run must use a run owned by that project. Requests omitting
project scope retain the legacy startup default; `project_id=.` is the
workspace-wide view.

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

The review panel supports docking-box decisions and receptor approval. The
protein viewer can overlay the current box as a wireframe. Coverage counts
residues with at least one atom inside the box and separately flags key residues
that are outside, partly covered, or missing from the loaded coordinates.
Both pocket predictions and user-specified residues are checked, including
insertion codes. This is a geometric check of observed coordinates; missing
atoms remain unassessed. Numeric coverage remains available without WebGL.

Browser regressions run with Playwright and a local copy of the same 3Dmol 2.5.5
build used by the workbench. They also exercise the no-renderer case. To run them:

```bash
python -m pip install . playwright
python -m playwright install chromium
npm install --prefix .runtime/viewer --no-save 3dmol@2.5.5
DRUGSTUDIO_REQUIRE_UI_TESTS=1 \
DRUGSTUDIO_TEST_3DMOL=.runtime/viewer/node_modules/3dmol/build/3Dmol-min.js \
  python -m unittest discover -s tests -p test_review_ui.py -v
```

```bash
python -m unittest discover -s tests -v
python -m compileall -q src/drugstudio
node --check src/drugstudio/web/static/app.js
```

The HTTP regressions use temporary workspaces and do not call models or launch
scientific jobs. CI builds and installs a wheel, checks bundled assets, and runs
the suite on Python 3.11 and 3.12. Tests requiring optional PDBFixer are explicitly
skipped when it is unavailable; run the suite in the receptor environment to
exercise those checks as well. `./run-agent.sh` also works directly from a source
checkout without installing the package.

The focused preparation regression checks are in `tests/`. Tests requiring
PDBFixer/OpenMM or external chemistry executables need those optional tools
installed. No run data, API keys, virtual environments, or local web captures
belong in the repository.

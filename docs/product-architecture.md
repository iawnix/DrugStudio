# DrugStudio product architecture

DrugStudio is an open, reproducible and extensible drug-design studio. It
integrates open scientific engines behind one project model and adds an agent
as a planning, control and interpretation layer. The product is a research
environment; it is not a chat wrapper around docking and it is not a generic
terminal or file browser.

## Product promise

Given a target, a ligand or library, and a design question, DrugStudio should
help a researcher:

1. turn the question into a reviewable design plan;
2. run the required structure, chemistry, pocket, docking and assessment
   stages on local or scheduled compute;
3. inspect the resulting structures and metrics in one evidence workspace;
4. record human decisions and preserve the complete input, parameter, tool and
   provenance trail;
5. explain what the evidence supports and what still needs to be tested.

The word “one-stop” refers to one project and evidence environment. It does
not require every algorithm to be implemented inside this repository. Engines
such as RDKit, PDBFixer, OpenMM, xTB, P2Rank, fpocket, Vina, GNINA,
DiffDock, AlphaFold and molecular-dynamics packages are integrated through
versioned adapters.

## Scientific object model

The web, CLI and Python API should address the same objects:

- **Project** — a durable target or drug-design programme.
- **Target** — sequences, structures, structure sources and preparation state.
- **Ligand / library** — molecules, microstates, conformers and versions.
- **Campaign** — a design question such as virtual screening or lead
  optimisation, including its objective and constraints.
- **Run** — one immutable execution snapshot of a campaign.
- **Stage** — a scientific step within a run, such as receptor preparation or
  docking.
- **Artifact** — an input, structure, pose, report, metric or log produced by a
  stage.
- **Decision** — a human approval, rejection, override or rationale.
- **Conversation** — the natural-language interface and decision history for a
  project or campaign.

Conversation is the interaction surface. Run and artifact records are the
scientific source of truth. A chat message must never become an implicit
parameter change or an untracked file mutation.

## Execution and authority

The execution boundary is:

```text
human → conversation agent → approved run action → run supervisor
      → stage agents → deterministic scientific tools → artifacts
      → evidence and report → conversation agent
```

The conversation agent may inspect the typed project/run context, propose a
plan, explain evidence and request a lifecycle action. Mutating actions pass
through the same approval and control methods used by the GUI. The run
supervisor and stage agents operate only inside their stage contracts. They do
not receive raw chat history and cannot bypass a human review gate. Scientific
tools remain responsible for deterministic calculations.

LangGraph or another orchestration library is an implementation detail. The
public run specification, stage contracts and artifact provenance must remain
stable if the orchestration implementation changes.

## Workbench information architecture

The application has one project shell and four kinds of surface:

- **Conversation** — the main entry point for a design question, plan,
  explanation and next action.
- **Run detail** — a compact live stage graph, controls, approvals and
  technical events for the selected run.
- **Evidence viewer** — structure, pocket, ligand, pose, property and
  provenance views for artifacts from that run.
- **Project tools** — files, terminal and environment settings for expert
  inspection and administration.

The left rail manages projects and conversations. The centre area shows the
current conversation or the selected evidence workspace. A resizable context
drawer exposes run, evidence and project data on demand. The terminal is a
collapsible expert tool. A workflow graph appears only in run detail; it is
not a second copy of the conversation and it is not a separate product.

User-facing task templates should be goal-oriented:

- structure-based design;
- virtual screening;
- ligand optimisation and SAR;
- pocket assessment;
- docking/redocking validation;
- property and ADMET assessment;
- structure or complex modelling.

Each template produces the same run and artifact objects. Expert users can
edit the generated plan or start a run from the CLI/API without changing the
underlying model.

## Open-source differentiation

DrugStudio should compete with closed suites through openness and research
quality rather than by claiming immediate parity in every proprietary
algorithm:

- adapters for multiple engines and compute backends;
- reproducible manifests, checksums, environment profiles and run snapshots;
- human-in-the-loop gates for receptor, pocket, box and result decisions;
- multi-engine comparison instead of a single unqualified score;
- a plugin SDK for new tools, models and report types;
- the same run specification through GUI, CLI and API;
- benchmark and validation packs that make scientific limits visible.

The first complete release should make one structure-based design loop robust:
target/structure retrieval, receptor preparation, ligand preparation,
candidate pockets, docking, property assessment, redocking checks, human
review and a provenance-rich report. Library-scale screening, MD/FEP,
generative design and broader ADMET models can then be added as modules that
reuse this foundation.


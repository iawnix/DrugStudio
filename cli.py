"""Initialize, inspect and execute a modular docking run."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import sys
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .contracts import DockingPolicy, LigandSpec, RunState, RunStatus, TargetSpec
from .events import EventWriter
from .storage import ArtifactStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drugstudio")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a run directory and initial manifest")
    init.add_argument("--run-root", required=True, help="owning project/run directory")
    init.add_argument("--target-id", required=True)
    init.add_argument("--uniprot-id")
    init.add_argument("--sequence")
    init.add_argument("--organism")
    init.add_argument("--pdb-id", action="append", default=[])
    init.add_argument("--chain", action="append", default=[], help="receptor chain to retain; repeat for multiple chains")
    init.add_argument("--binding-site-residue", action="append", default=[], help="residue label used for repair review")
    init.add_argument("--search-query")
    init.add_argument(
        "--ligand",
        action="append",
        default=[],
        metavar="ID=SMILES",
        help="repeat for each input ligand",
    )
    init.add_argument("--ligand-sdf", action="append", default=[], metavar="ID=PATH")
    init.add_argument("--redock-reference-sdf", metavar="LIGAND_ID=PATH",
                      help="optional single-molecule crystal reference SDF for redocking RMSD evaluation")
    init.add_argument("--redock-threshold-A", type=float, action="append", default=None,
                      help="RMSD success threshold in Å; repeat for multiple thresholds (default: 2.0)")
    init.add_argument("--redock-top-k", type=int, action="append", default=None,
                      help="score-ranked Top-k depth; repeat for multiple depths (default: 1, 3, 5)")
    init.add_argument("--prepared-receptor", help="complete, chemically prepared, protonated PDB; saved as a run input")
    init.add_argument("--engine", choices=["vina"], default="vina")
    init.add_argument("--disable-structure-prediction", action="store_true")
    init.add_argument("--require-human-box-review", action="store_true",
                      help="pause before docking until a human approves or overrides the Agent's proposed box")

    status = subparsers.add_parser("status", help="inspect a persisted manifest without model calls")
    status.add_argument("--run-root", required=True)

    benchmark = subparsers.add_parser("benchmark-evaluate", help="evaluate a frozen virtual-screening score table")
    benchmark.add_argument("--run-root", required=True, help="dedicated directory for this benchmark evaluation")
    benchmark.add_argument("--scores-csv", required=True, help="CSV with protocol,target_id,compound_id,label,score,status")
    benchmark.add_argument("--score-direction", choices=["lower", "higher"], default="lower")
    benchmark.add_argument("--ef-fraction", type=float, action="append", default=None,
                           help="top fraction for enrichment factor; repeat as needed (default 0.01,0.05,0.10)")
    benchmark.add_argument("--bootstrap-iterations", type=int, default=2000)
    benchmark.add_argument("--seed", type=int, default=20261102)

    review = subparsers.add_parser(
        "review", help="record a human review or override for a paused workflow"
    )
    review.add_argument("--run-root", required=True)
    review.add_argument("--stage", choices=["docking_box", "receptor"], required=True)
    review.add_argument("--action", choices=["approve", "select", "override", "reject"], required=True)
    review.add_argument("--pocket-rank", type=int, help="P2Rank candidate rank selected by the reviewer")
    review.add_argument("--center", nargs=3, type=float, metavar=("X", "Y", "Z"), help="manual box center in receptor coordinates")
    review.add_argument("--size", nargs=3, type=float, metavar=("X", "Y", "Z"), help="manual full box side lengths in Å")
    review.add_argument("--reason", required=True, help="scientific reason for the human decision")
    review.add_argument("--actor", default=os.environ.get("USER", "human"), help="reviewer identifier recorded in the manifest")

    run = subparsers.add_parser("run", help="execute or continue the LLM tool loop")
    run.add_argument("--run-root", required=True)
    run.add_argument("--center", nargs=3, type=float, metavar=("X", "Y", "Z"), help="manual box center; omit center and size to use P2Rank")
    run.add_argument("--size", nargs=3, type=float, metavar=("X", "Y", "Z"), help="manual full box side lengths in Å")
    run.add_argument("--pocket-rank", type=int, default=1, help="ranked P2Rank pocket used for automatic box selection")
    run.add_argument("--p2rank-executable", help="P2Rank prank executable; defaults to DOCK_AGENT_P2RANK or the workspace installation")
    run.add_argument("--p2rank-profile", choices=["auto", "default", "alphafold"], default="auto",
                     help="prediction profile; auto selects AlphaFold profile for predicted structures")
    run.add_argument("--p2rank-box-size", type=float, default=24.0, help="full cubic box side length in Å")
    run.add_argument("--approve-box", action="store_true", help="explicitly approve the selected box in the receptor coordinate frame")
    run.add_argument("--approve-receptor", action="store_true", help="approve the recorded PDBFixer repair review for this run")
    run.add_argument("--receptor-ph", type=float, default=7.4, help="pH used by PDBFixer when adding hydrogens")
    run.add_argument("--keep-receptor-residue", action="append", default=[], metavar="RESNAME", help="retain a named metal/cofactor residue")
    run.add_argument("--keep-receptor-water", action="store_true", help="retain crystallographic waters")
    run.add_argument("--add-missing-residues", action="store_true", help="add sequence gaps found by PDBFixer; review before approval")
    run.add_argument("--optimize-xtb", action="store_true")
    run.add_argument("--workers", type=int, default=1)
    run.add_argument("--allocated-cpus", type=int, default=1)
    run.add_argument("--exhaustiveness", type=int, default=8)
    run.add_argument("--seed", type=int, default=20261016)
    run.add_argument("--model", help="LangChain OpenAI chat model name; otherwise uses DOCK_AGENT_MODEL")
    run.add_argument("--prompt", default="Inspect the run, complete the remaining docking workflow stages, and summarize the evidence in Chinese.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_root = Path(args.run_root).expanduser().resolve()
        if run_root == Path(__file__).resolve().parents[1]:
            raise ValueError("use a project/run subdirectory, not the workspace root")
        if args.command not in {"init", "benchmark-evaluate"} and not (run_root / "manifest.json").is_file():
            raise ValueError("run manifest does not exist; use init first")
        store = ArtifactStore(run_root)
        if args.command == "benchmark-evaluate":
            with _run_lock(store):
                return _evaluate_benchmark(args, store)
        if args.command == "status":
            state = store.load_manifest()
            print(state.to_json())
            return 0
        with _run_lock(store):
            if args.command == "init":
                return _init_run(args, store)
            if args.command == "review":
                return _review_run(args, store)
            if args.command == "run":
                return _execute_run(args, store)
        raise AssertionError(f"unhandled command: {args.command}")
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1


def _init_run(args: argparse.Namespace, store: ArtifactStore) -> int:
    if store.path("manifest.json").exists():
        raise FileExistsError("run already exists; use a new run directory")
    ligands = [_parse_ligand(value) for value in args.ligand]
    for value in args.ligand_sdf:
        ligand_id, separator, path = value.partition("=")
        if not separator or not path:
            raise ValueError("--ligand-sdf must use ID=PATH")
        ligands.append(LigandSpec(ligand_id=ligand_id, input_path=str(Path(path).expanduser().resolve())))
    run_id = store.root.name or uuid.uuid4().hex[:12]
    state = RunState(
        run_id=run_id,
        target=TargetSpec(
            target_id=args.target_id,
            uniprot_id=args.uniprot_id,
            sequence=args.sequence,
            organism=args.organism,
            pdb_ids=[item.upper() for item in args.pdb_id],
            search_query=args.search_query,
            requested_chains=args.chain,
            binding_site_residues=args.binding_site_residue,
        ),
        ligands=ligands,
        policy=DockingPolicy(
            preferred_engine=args.engine,
            allow_structure_prediction=not args.disable_structure_prediction,
            require_human_approval_for_box=args.require_human_box_review,
        ),
    )
    snapshots = []
    for ligand in state.ligands:
        if ligand.input_path:
            source = Path(ligand.input_path)
            artifact = store.publish_bytes(relative_path=f"inputs/ligands/{ligand.ligand_id}.sdf",
                                           content=source.read_bytes(), kind="input_ligand_sdf",
                                           metadata={"ligand_id": ligand.ligand_id, "source_path": str(source)})
            state.add_artifact("inputs", artifact)
            ligand = replace(ligand, input_path=str(store.path(artifact.path)))
        snapshots.append(ligand)
    state.ligands = snapshots
    if args.redock_reference_sdf:
        ligand_id, separator, reference_path = args.redock_reference_sdf.partition("=")
        if not separator or not ligand_id or not reference_path:
            raise ValueError("--redock-reference-sdf must use LIGAND_ID=PATH")
        if ligand_id not in {ligand.ligand_id for ligand in state.ligands}:
            raise ValueError("the redocking reference ligand ID must match an input ligand")
        source = Path(reference_path).expanduser().resolve()
        content = source.read_bytes()
        if not content.strip():
            raise ValueError("redocking reference SDF is empty")
        artifact = store.publish_bytes(
            relative_path=f"inputs/redocking/{ligand_id}/reference.sdf", content=content,
            kind="redock_reference_sdf", metadata={"ligand_id": ligand_id,
                "source_path": str(source), "coordinates": "experimental crystal reference",
                "thresholds_A": args.redock_threshold_A or [2.0],
                "top_k": args.redock_top_k or [1, 3, 5]})
        state.add_artifact("inputs", artifact)
    if args.prepared_receptor:
        source = Path(args.prepared_receptor).expanduser().resolve()
        content = source.read_bytes()
        if not any(line.startswith(b"ATOM  ") for line in content.splitlines()):
            raise ValueError("--prepared-receptor must be a PDB containing protein ATOM records")
        artifact = store.publish_bytes(relative_path="inputs/structures/supplied-receptor.pdb", content=content,
                                       kind="supplied_structure", metadata={"source": "user_supplied",
                                       "source_path": str(source), "chemically_prepared": True,
                                       "preparation_evidence": "user explicitly supplied --prepared-receptor; Meeko validation still required"})
        state.add_artifact("structure", artifact)
        state.stage_results["structure"] = {"status": "succeeded", "metadata": artifact.metadata}
    manifest = store.save_manifest(state)
    EventWriter(store.root, state.run_id).emit("run_created", status="created",
                                               target_id=state.target.target_id,
                                               ligand_ids=[item.ligand_id for item in state.ligands])
    print(json.dumps({"run_id": state.run_id, "manifest": str(manifest)}, indent=2))
    return 0


def _evaluate_benchmark(args: argparse.Namespace, store: ArtifactStore) -> int:
    from .integrations.benchmark import evaluate_virtual_screening

    source = Path(args.scores_csv).expanduser().resolve()
    content = source.read_bytes()
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"protocol", "target_id", "compound_id", "label", "score", "status"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("score CSV requires columns: " + ", ".join(sorted(required)))
        rows = list(reader)
    if not rows:
        raise ValueError("score CSV contains no compound rows")
    fractions = tuple(args.ef_fraction or (0.01, 0.05, 0.10))
    metrics = evaluate_virtual_screening(
        rows, higher_is_better=args.score_direction == "higher", seed=args.seed,
        bootstrap_iterations=args.bootstrap_iterations, ef_fractions=fractions)
    source_artifact = store.publish_bytes(relative_path="inputs/benchmark/scores.csv", content=content,
        kind="benchmark_score_table", metadata={"source_path": str(source), "row_count": len(rows)})
    metrics["input"] = {"artifact_id": source_artifact.artifact_id, "path": source_artifact.path,
                        "sha256": source_artifact.sha256}
    metrics_artifact = store.publish_json(relative_path="results/benchmark/metrics.json", value=metrics,
        kind="virtual_screening_metrics", parent_ids=[source_artifact.artifact_id])
    ef_keys = [f"ef_at_{fraction:g}" for fraction in fractions]
    headers = [f"EF@{fraction:.0%}" for fraction in fractions]
    lines = ["# Virtual Screening Benchmark", "", f"Input rows: {len(rows)}",
             f"Score direction: {metrics['score_direction']}", "",
             "| Protocol | Targets | Evaluable | Failures | Failure rate | ROC-AUC (target macro) | AP (target macro) | " +
             " | ".join(headers) + " |",
             "|---|---:|---:|---:|---:|---:|---:|" + "---:|" * len(headers)]
    for item in metrics["protocols"]:
        macro = item["macro_target_metrics"]
        val = lambda key: (macro.get(key, {}).get("mean") if macro.get(key, {}).get("mean") is not None else "NA")
        ef_values = [str(val(key)) for key in ef_keys]
        lines.append(f"| {item['protocol']} | {item['target_count']} | {item['evaluable_target_count']} | "
                     f"{item['failed_count']} | {item['failure_rate']:.3f} | {val('roc_auc')} | "
                     f"{val('average_precision')} | " + " | ".join(ef_values) + " |")
    lines.extend(["", "## Limits", ""] + ["- " + item for item in metrics["limitations"]])
    report_artifact = store.publish_bytes(relative_path="results/benchmark/report.md",
        content=("\n".join(lines) + "\n").encode("utf-8"), kind="virtual_screening_report",
        parent_ids=[metrics_artifact.artifact_id])
    store.publish_json(relative_path="results/benchmark/manifest.json", kind="benchmark_manifest",
        value={"source_artifact": source_artifact.__dict__, "metrics_artifact": metrics_artifact.__dict__,
               "report_artifact": report_artifact.__dict__, "seed": args.seed,
               "bootstrap_iterations": args.bootstrap_iterations, "score_direction": args.score_direction})
    print(json.dumps({"status": "succeeded", "run_root": str(store.root),
                      "metrics": metrics_artifact.path, "report": report_artifact.path}, indent=2))
    return 0


def _execute_run(args: argparse.Namespace, store: ArtifactStore) -> int:
    from .runtime import build_controller

    state = store.load_manifest()
    if state.status == RunStatus.CANCELLED:
        raise ValueError("this run was cancelled; create a stage rerun branch to continue from preserved results")
    if state.status == RunStatus.PAUSED:
        EventWriter(store.root, state.run_id).set_control_request(None)
        state.status = RunStatus.RUNNING
        store.save_manifest(state)
    if state.status == RunStatus.SUCCEEDED:
        print(json.dumps({"run_id": state.run_id, "status": "succeeded", "message": "already complete"}))
        return 0
    if (args.center is None) != (args.size is None):
        raise ValueError("provide both --center and --size, or omit both to use P2Rank")
    if args.pocket_rank < 1:
        raise ValueError("--pocket-rank must be positive")
    settings = {key: getattr(args, key) for key in ("center", "size", "workers", "allocated_cpus",
                                                    "exhaustiveness", "seed", "optimize_xtb", "receptor_ph",
                                                    "keep_receptor_residue", "keep_receptor_water",
                                                    "add_missing_residues", "pocket_rank", "p2rank_executable",
                                                    "p2rank_profile", "p2rank_box_size")}
    controller = build_controller(store, state, settings=settings, approve_box=args.approve_box,
                                  approve_receptor=args.approve_receptor, model_name=args.model)
    result = controller.invoke(args.prompt)
    print(json.dumps({"run_id": state.run_id, "status": result["status"], "response": result["response"],
                      "manifest": str(store.path("manifest.json"))}, ensure_ascii=False, indent=2))
    return 0 if state.status == RunStatus.SUCCEEDED else 1 if state.status == RunStatus.FAILED else 2


def _review_run(args: argparse.Namespace, store: ArtifactStore) -> int:
    """Persist a human decision without invoking the LLM or scientific tools."""

    state = store.load_manifest()
    state.current_node = "human_review"
    if not args.reason.strip():
        raise ValueError("--reason must not be empty")
    if args.stage == "receptor" and args.action != "approve":
        raise ValueError("receptor review currently supports only --action approve")
    if args.stage == "docking_box" and args.action == "select" and args.pocket_rank is None:
        raise ValueError("--action select requires --pocket-rank")
    if (args.center is None) != (args.size is None):
        raise ValueError("--center and --size must be supplied together")
    if args.center is not None and args.pocket_rank is not None:
        raise ValueError("choose either --pocket-rank or a manual --center/--size override")

    decision_id = f"human-{uuid.uuid4().hex}"
    decision = {
        "decision_id": decision_id,
        "stage": args.stage,
        "action": args.action,
        "actor": args.actor,
        "reason": args.reason.strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "cli_review",
    }

    if args.stage == "receptor":
        state.approvals.append({
            "stage": "receptor", "approved": True, "source": "human",
            "decision_id": decision_id, "actor": args.actor, "reason": args.reason.strip(),
        })
        state.human_decisions.append(decision)
        state.decisions.append({"agent": "human", "action": "approve_receptor",
                                "rationale": args.reason.strip(), "decision_id": decision_id,
                                "actor": args.actor})
        if state.status == RunStatus.WAITING_APPROVAL:
            state.status = RunStatus.RUNNING
    else:
        if args.action == "reject":
            state.overrides.pop("docking_box", None)
            for approval in state.approvals:
                if approval.get("stage") == "docking_box":
                    approval["approved"] = False
            state.human_decisions.append(decision)
            state.decisions.append({"agent": "human", "action": "reject_docking_box",
                                    "rationale": args.reason.strip(), "decision_id": decision_id,
                                    "actor": args.actor})
            _invalidate_from(state, "docking", decision_id)
            state.stage_results["docking"] = {
                "status": "waiting_approval", "reason": args.reason.strip(),
                "rejected_by": decision_id,
            }
            state.status = RunStatus.WAITING_APPROVAL
        else:
            override = {
                "approved": True, "decision_id": decision_id, "actor": args.actor,
                "reason": args.reason.strip(), "source": "human",
            }
            if args.pocket_rank is not None:
                if args.pocket_rank < 1:
                    raise ValueError("--pocket-rank must be positive")
                candidates = state.stage_results.get("pocket", {}).get("metadata", {}).get("candidates", [])
                if not any(int(item.get("rank", -1)) == args.pocket_rank for item in candidates):
                    raise ValueError(f"pocket rank {args.pocket_rank} is unavailable; inspect status or candidates.json")
                override["pocket_rank"] = args.pocket_rank
            elif args.center is not None:
                if any(float(value) <= 0 for value in args.size):
                    raise ValueError("manual box sizes must be positive")
                override["center"] = [float(value) for value in args.center]
                override["size"] = [float(value) for value in args.size]
            else:
                selected_rank = state.stage_results.get("pocket", {}).get("metadata", {}).get("selected_rank")
                if selected_rank is None:
                    raise ValueError("approval needs --pocket-rank or --center/--size when no box is already selected")
                override["pocket_rank"] = int(selected_rank)
            state.overrides["docking_box"] = override
            state.human_decisions.append(decision)
            state.decisions.append({"agent": "human", "action": "override_docking_box",
                                    "rationale": args.reason.strip(), "decision_id": decision_id,
                                    "actor": args.actor, "override": override})
            state.approvals.append({
                "stage": "docking_box", "approved": True, "source": "human",
                "decision_id": decision_id, "actor": args.actor,
                "reason": args.reason.strip(), "fingerprint": "pending",
            })
            _invalidate_from(state, "docking", decision_id)
            state.status = RunStatus.RUNNING
    manifest = store.save_manifest(state)
    EventWriter(store.root, state.run_id).emit("human_decision", stage=args.stage,
                                               status=state.status.value,
                                               decision_id=decision_id, action=args.action,
                                               actor=args.actor, reason=args.reason.strip())
    print(json.dumps({"run_id": state.run_id, "status": state.status.value,
                      "decision": decision, "manifest": str(manifest)}, ensure_ascii=False, indent=2))
    return 0


def _invalidate_from(state: RunState, stage: str, decision_id: str) -> None:
    """Mark dependent results stale while retaining immutable historical artifacts."""

    order = ["structure", "receptor", "ligand", "pocket", "docking", "assessment"]
    try:
        start = order.index(stage)
    except ValueError as exc:
        raise ValueError(f"unknown invalidation stage: {stage}") from exc
    for name in order[start:]:
        previous = dict(state.stage_results.get(name, {}))
        if previous or name in {"docking", "assessment"}:
            state.stage_results[name] = {
                "status": "stale", "invalidated_by": decision_id,
                "previous_status": previous.get("status"),
            }
    if start <= order.index("docking"):
        state.tasks = []


@contextmanager
def _run_lock(store: ArtifactStore):
    with store.path(".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another process is using this run") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _parse_ligand(value: str) -> LigandSpec:
    ligand_id, separator, smiles = value.partition("=")
    if not separator or not ligand_id or not smiles:
        raise SystemExit("--ligand must use ID=SMILES")
    return LigandSpec(ligand_id=ligand_id, smiles=smiles)


if __name__ == "__main__":
    sys.exit(main())

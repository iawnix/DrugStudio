"""Assemble the runnable Vina/ReAct configuration without importing chemistry libraries."""

from __future__ import annotations

import os
from typing import Any

from .agents import (AgentContext, AssessmentAgent, DockingAgent, DockingReActController,
                     LigandAgent, PocketAgent, ReceptorAgent, StructureAgent, create_openai_chat_model)
from .contracts import RunState, RunStatus
from .events import EventWriter
from .integrations import (AlphaFoldDatabaseProvider, AlphaFoldServerRequestProvider, LocalAlphaFold3Provider,
                           AutoDockVinaEngine, AutoDockVinaLigandPreparer, AutoDockVinaReceptorPreparer,
                           ExperimentalThenPredictionProvider, LigandPreparationPipeline,
                           PredictionFallbackProvider, RCSBStructureProvider, XTBConformerPreparer,
                           PDBFixerReceptorPreparer, ReceptorPreparationPipeline, ReceptorPreparationPolicy,
                           P2RankPocketFinder)
from .integrations.assessment import VinaDruglikenessAssessor
from .integrations.rdkit_preparation import RDKitConformerPreparer
from .ports import ToolContext
from .storage import ArtifactStore


def build_controller(store: ArtifactStore, state: RunState, *, settings: dict,
                     approve_box: bool = False, approve_receptor: bool = False, model: Any = None,
                     model_name: str | None = None) -> DockingReActController:
    if state.policy.preferred_engine != "vina":
        raise ValueError("the default runtime currently supports only Vina")
    if not state.ligands:
        raise ValueError("add at least one ligand when initializing the run")
    if state.status == RunStatus.FAILED:
        raise ValueError("this run failed; preserve its diagnostics and start a new run after fixing the cause")
    if state.status == RunStatus.WAITING_INPUT:
        raise ValueError("chemical receptor preparation is missing; create a new run with --prepared-receptor")
    if any(result.get("status") == "running" for result in state.stage_results.values()):
        raise ValueError("a stage was interrupted; inspect its outputs and create a new run rather than rerunning over them")
    if any(stage not in {"inputs", "agent_response"} for stage in state.artifacts) and not state.stage_results:
        raise ValueError("legacy run has no reliable stage status; create a new run to avoid reusing unverified results")
    auto_box = settings.get("center") is None
    if auto_box != (settings.get("size") is None):
        raise ValueError("provide both --center and --size, or omit both to use P2Rank")
    engine = AutoDockVinaEngine(store,
                               center=tuple(settings["center"]) if settings.get("center") is not None else None,
                               size=tuple(settings["size"]) if settings.get("size") is not None else None,
                               workers=settings["workers"], allocated_cpus=settings["allocated_cpus"],
                               exhaustiveness=settings["exhaustiveness"], seed=settings["seed"])
    if not engine.runner.is_file():
        raise ValueError(f"Vina helper not found: {engine.runner}")
    conformer = (XTBConformerPreparer(store, seed=settings["seed"]) if settings["optimize_xtb"]
                 else RDKitConformerPreparer(store, seed=settings["seed"]))
    provider = ExperimentalThenPredictionProvider(
        experimental=RCSBStructureProvider(store),
        prediction=PredictionFallbackProvider((AlphaFoldDatabaseProvider(store), LocalAlphaFold3Provider(
                                               store, qsub=os.environ.get("AF3_QSUB", "qsub"),
                                               qstat=os.environ.get("AF3_QSTAT", "qstat"),
                                               queue=os.environ.get("AF3_PBS_QUEUE", "gpuq"),
                                               walltime=os.environ.get("AF3_PBS_WALLTIME", "12:00:00"),
                                               cpus=int(os.environ.get("AF3_PBS_CPUS", "8")),
                                               memory=os.environ.get("AF3_PBS_MEMORY", "64gb"),
                                               gpu_count=int(os.environ.get("AF3_PBS_GPU_COUNT", "1")),
                                               host=os.environ.get("AF3_PBS_HOST")),
                                               AlphaFoldServerRequestProvider(store))),
    )
    receptor_policy = ReceptorPreparationPolicy(
        chains=tuple(state.target.requested_chains),
        binding_site_residues=tuple(state.target.binding_site_residues),
        ph=float(settings.get("receptor_ph", 7.4)),
        keep_residues=tuple(settings.get("keep_receptor_residue", [])),
        keep_waters=bool(settings.get("keep_receptor_water", False)),
        add_missing_residues=bool(settings.get("add_missing_residues", False)),
    )
    receptor_preparer = ReceptorPreparationPipeline(
        repairer=PDBFixerReceptorPreparer(store, policy=receptor_policy),
        formatter=AutoDockVinaReceptorPreparer(store),
    )
    # Construct optional dependencies before modifying or snapshotting the run.
    human_box_override = state.overrides.get("docking_box", {})
    human_receptor_approved = any(
        item.get("stage") == "receptor" and item.get("approved") is True
        for item in state.approvals
    )
    context_configuration = {
        "box_approved": approve_box,
        "approve_receptor": approve_receptor or human_receptor_approved,
        "auto_box": auto_box,
        "pocket_rank": settings.get("pocket_rank", 1),
    }
    controller = DockingReActController(
        model=model if model is not None else create_openai_chat_model(model_name), run_state=state,
        context=AgentContext(store, ToolContext(state.run_id, store.path("work"),
                                                configuration=context_configuration),
                             configuration=context_configuration,
                             events=EventWriter(store.root, state.run_id)),
        structure_agent=StructureAgent(provider), receptor_agent=ReceptorAgent(receptor_preparer),
        ligand_agent=LigandAgent(LigandPreparationPipeline(store, (conformer, AutoDockVinaLigandPreparer(store)))),
        pocket_agent=PocketAgent(P2RankPocketFinder(store, executable=settings.get("p2rank_executable"),
                                                    profile=settings.get("p2rank_profile", "auto"),
                                                    box_size=settings.get("p2rank_box_size", 24.0))),
        docking_agent=DockingAgent(engine), assessment_agent=AssessmentAgent(VinaDruglikenessAssessor(store)),
    )
    saved_settings = {key: value for key, value in settings.items() if key != "pocket_rank"}
    configuration = store.publish_json(relative_path="inputs/runtime.json", value=saved_settings, kind="runtime_configuration")
    if not any(item.path == configuration.path for item in state.artifacts.get("inputs", [])):
        state.add_artifact("inputs", configuration)
    if state.status == RunStatus.WAITING_APPROVAL:
        # A boolean supplied to the CLI is never inferred from an LLM response.
        if state.stage_results.get("docking", {}).get("status") == "waiting_approval":
            if not approve_box and not auto_box and not human_box_override.get("approved"):
                raise ValueError("docking awaits box approval; use the review command or repeat with --approve-box")
            state.status = RunStatus.RUNNING
        elif state.stage_results.get("receptor", {}).get("status") == "waiting_approval":
            if not approve_receptor and not human_receptor_approved:
                raise ValueError("receptor repair awaits review; use the review command or repeat with --approve-receptor")
            state.status = RunStatus.RUNNING
        else:
            raise ValueError("run is waiting for structure/preparation input; inspect status and create a new run with the required inputs")
    return controller

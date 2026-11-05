"""RCSB PDB and AlphaFold Database structure providers.

These providers use the public HTTP endpoints directly so the domain package
does not require ``rcsbapi`` or ``requests``.  They return immutable run
artifacts and preserve the selection/download metadata in the tool result.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..contracts import ArtifactRef, TargetSpec
from ..ports import StructureProvider, ToolContext, ToolResult
from ..storage import ArtifactStore


@dataclass(frozen=True)
class RCSBStructureProvider:
    store: ArtifactStore
    timeout_seconds: int = 60
    search_url: str = "https://search.rcsb.org/rcsbsearch/v2/query"
    data_url: str = "https://data.rcsb.org/rest/v1/core/entry"
    file_url: str = "https://files.rcsb.org/download"
    max_candidates: int = 25

    def search(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        try:
            candidate_ids = self._candidate_ids(target)
            if not candidate_ids:
                return ToolResult(status="no_match", error="no RCSB candidates were found")
            selected, candidates = self._select_candidate(candidate_ids)
            pdb_id = selected["pdb_id"]
            content = _get_bytes(f"{self.file_url}/{pdb_id}.pdb", self.timeout_seconds)
            if not _looks_like_pdb(content):
                return ToolResult(status="failed", error=f"RCSB returned invalid PDB content for {pdb_id}")
            artifact = self.store.publish_bytes(
                relative_path=f"inputs/structures/rcsb-{pdb_id}.pdb",
                content=content,
                kind="experimental_structure",
                metadata={
                    "source": "rcsb_pdb",
                    "pdb_id": pdb_id,
                    "selection_policy": "lowest reported resolution, then experimental method",
                },
            )
            metadata = {
                "source": "experimental",
                "selected": selected,
                "candidates": candidates,
                "query": target.search_query or target.uniprot_id or target.target_id,
            }
            metadata_artifact = self.store.publish_json(
                relative_path=f"inputs/structures/rcsb-{pdb_id}.metadata.json",
                value=metadata,
                kind="structure_metadata",
                parent_ids=[artifact.artifact_id],
            )
            return ToolResult(
                status="succeeded",
                artifacts=[artifact, metadata_artifact],
                metadata=metadata,
            )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return ToolResult(status="failed", error=f"RCSB lookup failed: {exc}")

    def predict(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        return ToolResult(
            status="unsupported",
            error="RCSB provides experimental structures; use an AlphaFold provider for prediction",
        )

    def _candidate_ids(self, target: TargetSpec) -> List[str]:
        explicit = [_normalise_pdb_id(item) for item in target.pdb_ids]
        if any(item is None for item in explicit):
            raise ValueError("every supplied PDB ID must have four alphanumeric characters")
        explicit = [item for item in explicit if item]
        if explicit:
            return explicit[: self.max_candidates]

        query_value = target.search_query or target.uniprot_id or target.target_id
        query = {
            "query": {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": query_value},
            },
            "return_type": "entry",
            "request_options": {
                "paginate": {"start": 0, "rows": self.max_candidates},
                "results_content_type": ["experimental"],
            },
        }
        if target.uniprot_id and not target.search_query:
            query["query"] = {
                "type": "group", "logical_operator": "and", "nodes": [
                    {"type": "terminal", "service": "text", "parameters": {
                        "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                        "operator": "exact_match", "value": target.uniprot_id.upper()}},
                    {"type": "terminal", "service": "text", "parameters": {
                        "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_name",
                        "operator": "exact_match", "value": "UniProt"}},
                ],
            }
        payload = _post_json(self.search_url, query, self.timeout_seconds)
        return [
            _normalise_pdb_id(str(item.get("identifier", "")))
            for item in payload.get("result_set", [])
            if _normalise_pdb_id(str(item.get("identifier", "")))
        ][: self.max_candidates]

    def _select_candidate(self, candidate_ids: Sequence[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        candidates: List[Dict[str, Any]] = []
        for pdb_id in candidate_ids:
            try:
                entry = _get_json(f"{self.data_url}/{pdb_id}", self.timeout_seconds)
            except (OSError, ValueError, urllib.error.URLError):
                continue
            info = entry.get("rcsb_entry_info", {}) or {}
            resolution = info.get("resolution_combined")
            if isinstance(resolution, list):
                resolution = resolution[0] if resolution else None
            method = ((entry.get("exptl") or [{}])[0] or {}).get("method")
            candidate = {
                "pdb_id": pdb_id,
                "title": (entry.get("struct") or {}).get("title"),
                "method": method,
                "resolution_A": resolution,
                "deposited": (entry.get("rcsb_accession_info") or {}).get("deposit_date"),
            }
            candidates.append(candidate)
        if not candidates:
            raise ValueError("RCSB candidates had no retrievable metadata")
        candidates.sort(key=_candidate_sort_key)
        return candidates[0], candidates


@dataclass(frozen=True)
class AlphaFoldDatabaseProvider:
    """Retrieve a precomputed AlphaFold DB model for a UniProt accession.

    This is distinct from submitting a new AlphaFold Server job.  It is the
    deterministic fallback for targets already represented in AlphaFold DB.
    """

    store: ArtifactStore
    timeout_seconds: int = 60
    api_url: str = "https://alphafold.ebi.ac.uk/api/prediction"

    def search(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        return ToolResult(status="no_match", error="AlphaFold DB is a prediction source, not an experimental search")

    def predict(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        accession = _normalise_accession(target.uniprot_id)
        if accession is None:
            return ToolResult(status="unsupported", error="AlphaFold DB fallback requires target.uniprot_id")
        try:
            records = _get_json(f"{self.api_url}/{urllib.parse.quote(accession, safe='')}", self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            return ToolResult(status="no_match" if exc.code == 404 else "failed", error=f"AlphaFold DB lookup: {exc}")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return ToolResult(status="failed", error=f"AlphaFold DB lookup failed: {exc}")
        if not records:
            return ToolResult(status="no_match", error="no AlphaFold DB model for this accession")
        if not isinstance(records, list):
            return ToolResult(status="failed", error="unexpected AlphaFold DB API response")
        candidates = [item for item in records if isinstance(item, dict)
                      and item.get("uniprotAccession") == accession and item.get("pdbUrl")]
        if len(candidates) != 1:
            return ToolResult(status="failed", error="AlphaFold DB requires an unambiguous single model; inspect fragments/isoforms")
        selected = candidates[0]
        url = selected["pdbUrl"]
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "alphafold.ebi.ac.uk":
            return ToolResult(status="failed", error="unexpected AlphaFold model download URL")
        try:
            content = _get_bytes(url, self.timeout_seconds)
            if not _looks_like_pdb(content):
                raise ValueError("AlphaFold DB returned no valid PDB atom records")
            metadata = {"source": "alphafold_database", "uniprot_id": accession,
                        "model_version": selected.get("latestVersion"), "url": url,
                        "confidence_qc_required": True, "api_record": selected}
            artifact = self.store.publish_bytes(relative_path=f"inputs/structures/alphafold-{accession}.pdb",
                                                content=content, kind="predicted_structure", metadata=metadata)
            metadata_artifact = self.store.publish_json(relative_path=f"inputs/structures/alphafold-{accession}.metadata.json",
                                                       value=metadata, kind="structure_metadata", parent_ids=[artifact.artifact_id])
            return ToolResult(status="succeeded", artifacts=[artifact, metadata_artifact], metadata=metadata)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return ToolResult(status="failed", error=f"AlphaFold DB download failed: {exc}")


@dataclass(frozen=True)
class LocalAlphaFold3Provider:
    """Submit protein-only AF3 inference to PBS and resume it from run-local state."""

    store: ArtifactStore
    script: Optional[str] = None
    qsub: str = "qsub"
    qstat: str = "qstat"
    queue: str = "gpuq"
    walltime: str = "12:00:00"
    cpus: int = 8
    memory: str = "64gb"
    gpu_count: int = 1
    host: Optional[str] = None

    def __post_init__(self) -> None:
        if self.script is None:
            object.__setattr__(self, "script", str(Path(__file__).resolve().parents[1] / "pbs" / "run_local_af3.pbs"))

    def search(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        return ToolResult(status="no_match", error="local AlphaFold 3 is a prediction backend")

    def predict(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        if not target.sequence:
            return ToolResult(status="unsupported", error="local AF3 requires target.sequence")
        if not os.environ.get("AF3_ACTIVATE"):
            return ToolResult(status="unsupported", error="set AF3_ACTIVATE to the local AF3 environment activation script")
        if not os.environ.get("AF3_GPU_ID"):
            return ToolResult(status="unsupported", error="set AF3_GPU_ID to the coordinated physical GPU index")
        if os.environ.get("AF3_TERMS_ACKNOWLEDGED", "").strip().lower() not in {"1", "yes", "true"}:
            return ToolResult(status="unsupported", error="set AF3_TERMS_ACKNOWLEDGED=1 only after confirming the local AF3 model terms apply to this run")
        root = self.store.root
        work = self.store.path("work/af3")
        inp = self.store.path("inputs/structures/af3_input.json")
        output = self.store.path("results/structure_prediction/af3")
        job_path = self.store.path("work/af3/job.json")
        work.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        seq = re.sub(r"\s+", "", target.sequence).upper()
        if not seq or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", seq):
            return ToolResult(status="unsupported", error="local AF3 requires an unambiguous sequence using the 20 standard amino acids")
        payload = {"name": target.target_id, "modelSeeds": [20261102], "sequences": [{"protein": {
            "id": "A", "sequence": seq, "unpairedMsa": f">query\n{seq}", "pairedMsa": ""}}],
            "dialect": "alphafold3", "version": 2}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if job_path.is_file():
            job = json.loads(job_path.read_text(encoding="utf-8"))
            if job.get("input_sha256") != digest:
                return ToolResult(status="failed", error="existing AF3 job belongs to a different sequence; create a new run")
        else:
            self.store.publish_json(relative_path="inputs/structures/af3_input.json", value=payload,
                                    kind="alphafold3_input", metadata={"target_id": target.target_id, "mode": "apo_protein"})
            terms = self.store.publish_json(relative_path="inputs/structures/af3_terms_acknowledgement.json",
                value={"acknowledged": True, "source": "AF3_TERMS_ACKNOWLEDGED environment setting",
                       "recorded_at_epoch": time.time(), "run_id": context.run_id},
                kind="alphafold3_terms_acknowledgement")
            env = {"AF3_INPUT_JSON": str(inp), "AF3_OUTPUT_DIR": str(output),
                   "AF3_RUN_DIR": str(root), "AF3_WORK_DIR": str(work),
                   "AF3_ACTIVATE": os.environ["AF3_ACTIVATE"], "AF3_GPU_ID": os.environ["AF3_GPU_ID"]}
            for key in ("AF3_FLASH_ATTENTION_IMPLEMENTATION", "AF3_NUM_DIFFUSION_SAMPLES", "AF3_NUM_RECYCLES",
                        "XLA_CLIENT_MEM_FRACTION"):
                if os.environ.get(key):
                    env[key] = os.environ[key]
            job = {"backend": "local_alphafold3_pbs", "phase": "prepared", "input_sha256": digest,
                   "input_path": str(inp), "output_dir": str(output), "created_at": time.time(),
                   "terms_acknowledgement_artifact_id": terms.artifact_id, "environment": env}
            job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
        phase = job.get("phase")
        if phase == "prepared":
            command = [self.qsub, "-q", self.queue, "-l", f"select=1:ncpus={self.cpus}:mem={self.memory}:ngpus={self.gpu_count}",
                       "-l", f"walltime={self.walltime}", "-v", ",".join(f"{k}={v}" for k, v in job["environment"].items())]
            command.extend(["-o", str(work / "pbs.out"), "-e", str(work / "pbs.err")])
            if self.host:
                command.extend(["-l", f"host={self.host}"])
            command.append(str(Path(self.script).resolve()))
            try:
                submitted = subprocess.run(command, cwd=work, text=True, capture_output=True, timeout=60, check=True)
                match = re.search(r"^\s*([0-9]+(?:\.[A-Za-z0-9_.-]+)?)\s*$", submitted.stdout, re.MULTILINE)
                if not match:
                    raise ValueError(f"qsub returned no job id: {submitted.stdout.strip()}")
                job.update(phase="submitted", job_id=match.group(1), submitted_at=time.time())
                job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
                return ToolResult(status="waiting_execution", metadata={"backend": job["backend"], "phase": "submitted", "job_id": job["job_id"]})
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                return ToolResult(status="failed", error=f"AF3 PBS submission failed: {exc}")
        if phase == "submitted":
            try:
                status = subprocess.run([self.qstat, "-f", job["job_id"]], cwd=work, text=True,
                                         capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                return ToolResult(status="waiting_execution", metadata={"phase": "status_unavailable", "job_id": job["job_id"], "detail": str(exc)})
            if status.returncode == 0:
                state_match = re.search(r"job_state\s*=\s*(\w)", status.stdout)
                job_state = state_match.group(1) if state_match else "?"
                if job_state in {"C", "F"}:
                    exit_match = re.search(r"exit_status\s*=\s*(-?\d+)", status.stdout)
                    if exit_match and int(exit_match.group(1)) != 0:
                        job.update(phase="failed", error=f"PBS job exited with status {exit_match.group(1)}")
                        job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
                        return ToolResult(status="failed", error=job["error"])
                else:
                    return ToolResult(status="waiting_execution", metadata={"phase": {"Q": "queued", "H": "queued", "W": "queued", "R": "running", "E": "running"}.get(job_state, "unknown"), "job_id": job["job_id"]})
            try:
                job_history = subprocess.run([self.qstat, "-xf", job["job_id"]], cwd=work, text=True,
                                             capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                return ToolResult(status="waiting_execution", metadata={"phase": "status_unavailable", "job_id": job["job_id"], "detail": str(exc)})
            if job_history.returncode == 0:
                exit_match = re.search(r"exit_status\s*=\s*(-?\d+)", job_history.stdout)
                if exit_match and int(exit_match.group(1)) != 0:
                    job.update(phase="failed", error=f"PBS job exited with status {exit_match.group(1)}")
                    job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
                    return ToolResult(status="failed", error=job["error"])
                if not exit_match:
                    return ToolResult(status="waiting_execution", metadata={"phase": "status_unavailable", "job_id": job["job_id"]})
            models = sorted(output.rglob("*_model.cif"), key=lambda path: (len(path.relative_to(output).parts), str(path)))
            if not models:
                job.update(phase="failed", error=status.stderr.strip() or "PBS job disappeared without AF3 model output")
                job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
                return ToolResult(status="failed", error=job["error"])
            model = models[0]
            pdb_path = output / "model.pdb"
            if not pdb_path.is_file():
                return ToolResult(status="failed", error="AF3 model exists but run-local model.pdb conversion output is missing")
            pdb_bytes = pdb_path.read_bytes()
            if not _looks_like_pdb(pdb_bytes):
                return ToolResult(status="failed", error="AF3 PDB conversion did not produce protein ATOM records")
            cif_artifact = self.store.publish_bytes(relative_path="results/structure_prediction/af3/model.cif", content=model.read_bytes(),
                kind="alphafold3_model_mmcif", metadata={"source": "local_alphafold3", "job_id": job["job_id"]})
            pdb_artifact = self.store.publish_bytes(relative_path="inputs/structures/alphafold3.pdb", content=pdb_bytes,
                kind="predicted_structure", parent_ids=[cif_artifact.artifact_id], metadata={"source": "local_alphafold3", "job_id": job["job_id"], "confidence_qc_required": True})
            job.update(phase="succeeded", completed_at=time.time(), model_path=str(model))
            job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
            evidence = [cif_artifact, pdb_artifact]
            summaries = sorted(output.rglob("*_summary_confidences.json"), key=lambda path: (len(path.relative_to(output).parts), str(path)))
            rankings = sorted(output.rglob("*_ranking_scores.csv"), key=lambda path: (len(path.relative_to(output).parts), str(path)))
            for source, relative, kind in ((summaries[0] if summaries else None, "results/structure_prediction/af3/summary_confidences.json", "alphafold3_confidence_summary"),
                                           (rankings[0] if rankings else None, "results/structure_prediction/af3/ranking_scores.csv", "alphafold3_ranking_scores")):
                if source is not None:
                    evidence.append(self.store.publish_bytes(relative_path=relative, content=source.read_bytes(), kind=kind,
                                      parent_ids=[cif_artifact.artifact_id], metadata={"source": "local_alphafold3", "job_id": job["job_id"]}))
            return ToolResult(status="succeeded", artifacts=evidence, metadata={"source": "local_alphafold3", "job_id": job["job_id"], "mode": "apo_protein", "confidence_qc_required": True, "confidence_summary_artifact": next((item.artifact_id for item in evidence if item.kind == "alphafold3_confidence_summary"), None)})
        if phase == "succeeded":
            artifact_path = self.store.path("inputs/structures/alphafold3.pdb")
            if artifact_path.is_file():
                artifact = self.store.publish_bytes(relative_path="inputs/structures/alphafold3.pdb", content=artifact_path.read_bytes(), kind="predicted_structure", metadata={"source": "local_alphafold3", "job_id": job.get("job_id")})
                return ToolResult(status="succeeded", artifacts=[artifact], metadata={"source": "local_alphafold3", "job_id": job.get("job_id")})
        return ToolResult(status="failed", error=f"local AF3 job is in terminal phase {phase}")


@dataclass(frozen=True)
class ExperimentalThenPredictionProvider:
    """Compose an experimental provider with a prediction fallback."""

    experimental: StructureProvider
    prediction: StructureProvider

    def search(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        return self.experimental.search(target, context)

    def predict(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        return self.prediction.predict(target, context)


@dataclass(frozen=True)
class AlphaFoldServerRequestProvider:
    """Prepare a validated Server request without submitting it."""

    store: ArtifactStore
    builder: Optional[str] = None
    timeout_seconds: int = 60
    seed: Optional[int] = None

    def __post_init__(self) -> None:
        if self.builder is None:
            object.__setattr__(
                self,
                "builder",
                str(Path(__file__).resolve().parents[2] / ".agents/skills/alphafold-server/scripts/build_server_json.py"),
            )

    def search(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        return ToolResult(status="no_match", error="AlphaFold Server is a prediction service")

    def predict(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        if not target.sequence:
            return ToolResult(status="unsupported", error="AlphaFold Server request requires target.sequence")
        fasta = context.workdir / f"{target.target_id}.fasta"
        request_json = context.workdir / f"{target.target_id}-alphafold-server.json"
        fasta.write_text(f">{target.target_id}\n{target.sequence.strip().upper()}\n", encoding="ascii")
        builder_path = Path(self.builder).resolve()
        command = [
            sys.executable,
            str(builder_path),
            "--name",
            target.target_id,
            "--protein",
            str(fasta),
            "--output",
            str(request_json),
        ]
        if self.seed is not None:
            command.extend(["--seed", str(self.seed)])
        try:
            completed = subprocess.run(
                command,
                cwd=context.workdir,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=True,
            )
            request = json.loads(request_json.read_text(encoding="utf-8"))
            artifact = self.store.publish_bytes(
                relative_path=f"inputs/structures/{target.target_id}-alphafold-server.json",
                content=request_json.read_bytes(),
                kind="alphafold_server_request",
                metadata={
                    "source": "alphafold_server",
                    "submission_status": "awaiting_user_authorization",
                    "target_id": target.target_id,
                },
            )
            return ToolResult(
                status="waiting_approval",
                artifacts=[artifact],
                metadata={"request": request, "builder_output": completed.stdout, "submission_status": "awaiting_user_authorization"},
            )
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            return ToolResult(status="failed", error=f"AlphaFold Server request preparation failed: {exc}")


@dataclass(frozen=True)
class PredictionFallbackProvider:
    """Try an existing prediction database before preparing a Server request."""

    providers: Tuple[StructureProvider, ...]

    def search(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        return ToolResult(status="no_match", error="prediction fallback providers are used only for prediction")

    def predict(self, target: TargetSpec, context: ToolContext) -> ToolResult:
        errors: List[str] = []
        for provider in self.providers:
            result = provider.predict(target, context)
            if result.status in {"succeeded", "waiting_approval", "waiting_execution"}:
                return result
            if result.status not in {"no_match", "unsupported"}:
                return result
            if result.error:
                errors.append(result.error)
        return ToolResult(status="failed", error="; ".join(errors) or "all prediction providers failed")


def _candidate_sort_key(candidate: Dict[str, Any]) -> Tuple[float, int, str]:
    resolution = candidate.get("resolution_A")
    try:
        resolution_value = float(resolution)
    except (TypeError, ValueError):
        resolution_value = float("inf")
    method = str(candidate.get("method") or "").upper()
    method_rank = 0 if "X-RAY" in method else 1 if "ELECTRON" in method else 2
    return resolution_value, method_rank, str(candidate.get("pdb_id"))


def _normalise_pdb_id(value: str) -> Optional[str]:
    value = value.strip().upper()
    if len(value) != 4 or not value.isalnum():
        return None
    return value


def _normalise_accession(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    accession = value.strip().upper()
    if not accession or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in accession):
        return None
    return accession


def _get_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DrugStudio/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _get_json(url: str, timeout: int) -> Any:
    return json.loads(_get_bytes(url, timeout).decode("utf-8"))


def _post_json(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "DrugStudio/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        return json.loads(body.decode("utf-8")) if body else {}


def _looks_like_pdb(content: bytes) -> bool:
    text = content.decode("utf-8", errors="replace")
    return any(line.startswith("ATOM  ") and len(line) >= 54 for line in text.splitlines())


__all__ = [
    "AlphaFoldDatabaseProvider",
    "AlphaFoldServerRequestProvider",
    "ExperimentalThenPredictionProvider",
    "PredictionFallbackProvider",
    "RCSBStructureProvider",
]

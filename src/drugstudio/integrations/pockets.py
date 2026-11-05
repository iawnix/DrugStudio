"""P2Rank adapter for ranked pocket candidates and Vina search boxes."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..contracts import ArtifactRef
from ..ports import ToolContext, ToolResult
from ..storage import ArtifactStore
from .tool_paths import external_executable


class P2RankPocketFinder:
    def __init__(self, store: ArtifactStore, executable: str | Path | None = None,
                 profile: str = "auto", box_size: float = 24.0, timeout_seconds: int = 900,
                 java_home: str | Path | None = None,
                 fpocket_activate: str | Path | None = None):
        self.store = store
        self.executable = (Path(executable).expanduser().resolve() if executable else
                           external_executable(
                               "DOCK_AGENT_P2RANK", ("prank",),
                               ("p2rank/2.5.1/prank", "p2rank/bin/prank")))
        self.profile = profile
        self.box_size = float(box_size)
        self.timeout_seconds = timeout_seconds
        configured_fpocket = fpocket_activate or os.environ.get("DOCK_AGENT_FPOCKET_ACTIVATE")
        self.fpocket_activate = Path(configured_fpocket).expanduser() if configured_fpocket else None
        configured_java = java_home or os.environ.get("DOCK_AGENT_JAVA_HOME") or os.environ.get("JAVA_HOME")
        self.java_home = Path(configured_java).expanduser() if configured_java else None
        if not math.isfinite(self.box_size) or self.box_size <= 0:
            raise ValueError("P2Rank-derived box size must be positive")

    def predict(self, receptor: ArtifactRef, *, is_predicted: bool, context: ToolContext) -> ToolResult:
        source = self.store.path(receptor.path)
        if not source.is_file():
            return ToolResult(status="failed", error=f"prepared receptor file does not exist: {source}")
        executable = self.executable.resolve()
        profile = self.profile
        if profile == "auto":
            profile = "alphafold" if is_predicted else "default"
        if not executable.is_file():
            return self._fpocket_fallback(receptor, source, context,
                                           f"P2Rank executable not found: {executable}", profile)

        workdir = context.workdir
        output = workdir / "p2rank_output"
        output.mkdir(parents=True, exist_ok=True)
        input_pdb = workdir / "receptor.pdb"
        shutil.copyfile(source, input_pdb)
        command = [str(executable), "predict", "-f", str(input_pdb), "-o", str(output),
                   "-visualizations", "0", "-threads", "1"]
        if profile != "default":
            command.extend(["-c", profile])
        log_path = workdir / "p2rank.log"
        try:
            temporary = workdir / "tmp"
            temporary.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment["TMPDIR"] = str(temporary.resolve())
            if self.java_home is not None:
                java_home = self.java_home.resolve()
                if not (java_home / "bin/java").is_file():
                    raise FileNotFoundError(f"Java executable not found under JAVA_HOME: {java_home / 'bin/java'}")
                environment["JAVA_HOME"] = str(java_home)
                environment["PATH"] = str(java_home / "bin") + os.pathsep + environment.get("PATH", "")
            completed = subprocess.run(command, cwd=workdir, text=True, capture_output=True,
                                       env=environment, timeout=self.timeout_seconds, check=False)
            log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
            if completed.returncode:
                raise RuntimeError(f"P2Rank exited with status {completed.returncode}")
            predictions = output / "receptor.pdb_predictions.csv"
            if not predictions.is_file():
                raise FileNotFoundError("P2Rank did not create receptor.pdb_predictions.csv")
            candidates = self._parse_predictions(predictions)
            if not candidates:
                raise ValueError("P2Rank returned no pocket candidates")
            p2rank_candidates = candidates
            fpocket_result = self._run_fpocket(source, workdir)
            fpocket_candidates = fpocket_result.get("candidates", [])
            candidates = _merge_pocket_candidates(p2rank_candidates, fpocket_candidates, self.box_size)
            reference_residues = {_canonical_residue(value) for value in context.configuration.get("binding_site_residues", [])}
            for candidate in candidates:
                candidate["box"] = {"center": candidate["center"],
                                    "size": [self.box_size] * 3,
                                    "size_source": "configured fixed cubic extent; Angstrom"}
                overlap = sorted({value for value in candidate["residue_ids"]
                                  if _canonical_residue(value) in reference_residues})
                candidate["reference_site_overlap"] = overlap
                candidate["reference_site_overlap_count"] = len(overlap)
                candidate["evidence_class"] = ("reference_site_overlap" if overlap else
                                                "computational_only")
                candidate["human_review_recommended"] = True
            report = {
                "predictor": "P2Rank", "profile": profile,
                "executable": str(executable), "box_size_A": self.box_size,
                "predictors": ["P2Rank"] + (["fpocket"] if fpocket_candidates else []),
                "predictor_count": 1 + int(bool(fpocket_candidates)),
                "fpocket": {"status": fpocket_result["status"], "version": fpocket_result.get("version"),
                            "error": fpocket_result.get("error"), "candidate_count": len(fpocket_candidates),
                            "candidates": fpocket_candidates},
                "java_home": str(self.java_home.resolve()) if self.java_home else None,
                "coordinate_frame": "input receptor PDB (original coordinate frame)",
                "structure_quality": context.configuration.get("structure_quality"),
                "site_support": ("reference_site_residues_provided" if reference_residues else
                                 "no_reference_site_or_known_ligand_support"),
                "consensus_available": bool(fpocket_candidates),
                "review_recommended": True,
                "candidates": candidates,
                "interpretation": "Pocket agreement is spatial agreement between independent computational predictors, not experimental validation or proof of ligand binding. Raw predictor-specific scores are kept separate.",
            }
            raw_artifact = self.store.publish_bytes(
                relative_path="results/pocket/p2rank_predictions.csv", content=predictions.read_bytes(),
                kind="pocket_predictions_csv", parent_ids=[receptor.artifact_id],
                metadata={"predictor": "P2Rank", "profile": profile})
            report_artifact = self.store.publish_json(
                relative_path="results/pocket/candidates.json", value=report,
                kind="pocket_candidates", parent_ids=[receptor.artifact_id, raw_artifact.artifact_id])
            artifacts = [raw_artifact, report_artifact]
            fpocket_log = fpocket_result.get("log_path")
            if fpocket_log and Path(fpocket_log).is_file():
                artifacts.append(self.store.publish_bytes(
                    relative_path="results/pocket/fpocket.log", content=Path(fpocket_log).read_bytes(),
                    kind="fpocket_log", parent_ids=[receptor.artifact_id]))
            return ToolResult(status="succeeded", artifacts=artifacts,
                              metadata=report)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            fallback = self._fpocket_fallback(receptor, source, context, str(exc), profile)
            log_artifact = None
            if log_path.is_file():
                log_artifact = self.store.publish_bytes(
                    relative_path="results/pocket/p2rank.log", content=log_path.read_bytes(),
                    kind="pocket_prediction_log", parent_ids=[receptor.artifact_id])
            if log_artifact:
                fallback.artifacts.append(log_artifact)
            return fallback

    def _fpocket_fallback(self, receptor: ArtifactRef, source: Path, context: ToolContext,
                          p2rank_error: str, profile: str) -> ToolResult:
        result = self._run_fpocket(source, context.workdir)
        pockets = result.get("candidates", [])
        artifacts = self._publish_fpocket_log(receptor, result)
        if not pockets:
            status = "waiting_input" if result.get("status") == "unavailable" else "failed"
            return ToolResult(status=status, artifacts=artifacts,
                              error=f"P2Rank failed ({p2rank_error}); fpocket {result.get('status')}: {result.get('error')}")
        candidates = _merge_pocket_candidates([], pockets, self.box_size)
        references = {_canonical_residue(value) for value in context.configuration.get("binding_site_residues", [])}
        for candidate in candidates:
            overlap = sorted({value for value in candidate.get("residue_ids", [])
                              if _canonical_residue(value) in references})
            candidate.update(reference_site_overlap=overlap, reference_site_overlap_count=len(overlap),
                             evidence_class="reference_site_overlap" if overlap else "computational_only",
                             human_review_recommended=True)
        report = {
            "predictors": ["fpocket"], "predictor_count": 1, "consensus_available": False,
            "predictor_status": {"P2Rank": {"status": "failed", "error": p2rank_error, "profile": profile},
                                 "fpocket": {"status": result["status"], "version": result.get("version")}},
            "p2rank": {"candidate_count": 0},
            "fpocket": {"version": result.get("version"), "candidate_count": len(pockets),
                        "candidates": pockets},
            "box_size_A": self.box_size,
            "coordinate_frame": "input receptor PDB (original coordinate frame)",
            "structure_quality": context.configuration.get("structure_quality"),
            "site_support": "reference_site_residues_provided" if references else "no_reference_site_or_known_ligand_support",
            "review_recommended": True, "candidates": candidates,
            "interpretation": "P2Rank failed; these are fpocket-only computational candidates without consensus or experimental validation.",
        }
        artifacts.append(self.store.publish_json(relative_path="results/pocket/candidates.json", value=report,
                                                  kind="pocket_candidates", parent_ids=[receptor.artifact_id]))
        return ToolResult(status="succeeded", artifacts=artifacts, metadata=report)

    def _publish_fpocket_log(self, receptor: ArtifactRef, result: dict[str, Any]) -> list[ArtifactRef]:
        path = result.get("log_path")
        if not path or not Path(path).is_file():
            return []
        return [self.store.publish_bytes(relative_path="results/pocket/fpocket.log",
                                         content=Path(path).read_bytes(), kind="fpocket_log",
                                         parent_ids=[receptor.artifact_id])]

    def _run_fpocket(self, receptor_path: Path, workdir: Path) -> dict[str, Any]:
        if self.fpocket_activate is None or not self.fpocket_activate.is_file():
            return {"status": "unavailable", "error": "fpocket activation script not configured"}
        fpocket_root = workdir / "fpocket"
        fingerprint = hashlib.sha256(receptor_path.read_bytes()).hexdigest()[:16]
        attempt_dir = fpocket_root / fingerprint
        attempt_dir.mkdir(parents=True, exist_ok=True)
        local_input = attempt_dir / "receptor.pdb"
        if not local_input.exists():
            local_input.write_bytes(receptor_path.read_bytes())
        output_dir = attempt_dir / "receptor_out"
        log_path = attempt_dir / "fpocket.log"
        if not output_dir.is_dir():
            command = "source " + shlex.quote(str(self.fpocket_activate.resolve())) + " && fpocket -f " + shlex.quote(str(local_input))
            env = os.environ.copy()
            env["TMPDIR"] = str((attempt_dir / "tmp").resolve())
            Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
            try:
                completed = subprocess.run(["bash", "-lc", command], cwd=attempt_dir, text=True,
                                           capture_output=True, env=env, timeout=self.timeout_seconds, check=False)
                log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
                if completed.returncode:
                    return {"status": "failed", "error": f"fpocket exited with status {completed.returncode}",
                            "log_path": str(log_path)}
            except (OSError, subprocess.SubprocessError) as exc:
                return {"status": "failed", "error": f"fpocket execution failed: {exc}",
                        "log_path": str(log_path)}
        pocket_dir = output_dir / "pockets"
        pocket_files = sorted(pocket_dir.glob("pocket*_atm.pdb")) if pocket_dir.is_dir() else []
        info_files = sorted(output_dir.glob("*info*"))
        info = _parse_fpocket_info(info_files[0]) if info_files else {}
        parsed = []
        for pocket_file in pocket_files:
            match = re.search(r"pocket(\d+)_atm", pocket_file.name)
            if not match:
                continue
            rank = int(match.group(1))
            vertices = pocket_dir / f"pocket{rank}_vert.pqr"
            center = _pdb_centroid(vertices if vertices.is_file() else pocket_file)
            if center is None:
                continue
            parsed.append({"source": "fpocket", "source_rank": rank, "center": center,
                           "score": info.get(rank, {}).get("score"),
                           "druggability_score": info.get(rank, {}).get("druggability_score"),
                           "box": {"center": center, "size": [self.box_size] * 3,
                                   "size_source": "configured fixed cubic extent; Angstrom"}})
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        version_match = re.search(r"fpocket\s+([0-9]+(?:\.[0-9]+)*)", log_text, re.IGNORECASE)
        return {"status": "succeeded" if parsed else "no_candidates",
                "version": version_match.group(1) if version_match else None,
                "candidates": parsed, "log_path": str(log_path),
                "error": None if parsed else "fpocket produced no parseable pockets"}

    def _parse_predictions(self, path: Path) -> list[dict[str, Any]]:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, skipinitialspace=True)
            rows = []
            for row in reader:
                normalized = {str(key).strip(): str(value or "").strip()
                              for key, value in row.items() if key is not None}
                try:
                    rank = int(normalized["rank"])
                    center = [float(normalized[f"center_{axis}"]) for axis in "xyz"]
                    score = float(normalized["score"])
                    probability = float(normalized["probability"])
                except (KeyError, TypeError, ValueError):
                    continue
                rows.append({"rank": rank, "name": normalized.get("name", f"pocket{rank}"),
                             "score": score, "probability": probability,
                             "center": center,
                             "residue_ids": normalized.get("residue_ids", "").split(),
                             "surface_points": int(float(normalized.get("sas_points", "0") or 0))})
        return sorted(rows, key=lambda candidate: candidate["rank"])


def _canonical_residue(value: Any) -> str:
    match = re.search(r"([A-Za-z0-9])[^A-Za-z0-9]*(-?\d+[A-Za-z]?)", str(value).strip())
    return f"{match.group(1).upper()}:{match.group(2).upper()}" if match else str(value).strip().upper()


def _pdb_centroid(path: Path) -> list[float] | None:
    coordinates = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
            continue
        try:
            point = [float(line[start:start + 8]) for start in (30, 38, 46)]
        except ValueError:
            fields = line.split()
            try:
                point = [float(value) for value in fields[6:9]]
            except (ValueError, IndexError):
                continue
        if all(math.isfinite(value) for value in point):
            coordinates.append(point)
    if not coordinates:
        return None
    return [sum(point[i] for point in coordinates) / len(coordinates) for i in range(3)]


def _parse_fpocket_info(path: Path) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    active_rank = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        pocket_match = re.search(r"Pocket\s+(\d+)\s*:\s*score\s+([-+0-9.eE]+)", line, re.IGNORECASE)
        if pocket_match:
            active_rank = int(pocket_match.group(1))
            result.setdefault(active_rank, {})["score"] = float(pocket_match.group(2))
            continue
        if active_rank is not None:
            drug_match = re.search(r"Druggability Score\s*:\s*([-+0-9.eE]+)", line, re.IGNORECASE)
            if drug_match:
                result[active_rank]["druggability_score"] = float(drug_match.group(1))
    return result


def _merge_pocket_candidates(p2rank: list[dict[str, Any]], fpocket: list[dict[str, Any]],
                             box_size: float, agreement_distance_A: float = 8.0) -> list[dict[str, Any]]:
    merged = [{**item, "p2rank_rank": int(item["rank"]), "p2rank_score": item["score"],
               "p2rank_probability": item["probability"], "fpocket_rank": None,
               "fpocket_score": None, "fpocket_druggability_score": None,
               "sources": ["P2Rank"]} for item in p2rank]
    matches = []
    for index, candidate in enumerate(merged):
        for fp_candidate in fpocket:
            distance = math.dist(candidate["center"], fp_candidate["center"])
            if distance <= agreement_distance_A:
                matches.append((distance, index, fp_candidate))
    matched_p2, matched_fp = set(), set()
    for distance, index, fp_candidate in sorted(matches, key=lambda item: item[0]):
        fp_rank = fp_candidate["source_rank"]
        if index in matched_p2 or fp_rank in matched_fp:
            continue
        merged[index].update(fpocket_rank=fp_rank, fpocket_score=fp_candidate.get("score"),
                             fpocket_druggability_score=fp_candidate.get("druggability_score"),
                             predictor_center_distance_A=round(distance, 3),
                             sources=["P2Rank", "fpocket"])
        matched_p2.add(index)
        matched_fp.add(fp_rank)
    for item in fpocket:
        if item["source_rank"] in matched_fp:
            continue
        merged.append({"name": f"fpocket{item['source_rank']}", "rank": 0, "score": item.get("druggability_score"),
                       "probability": None, "center": item["center"], "residue_ids": [],
                       "surface_points": 0, "box": item["box"], "p2rank_rank": None,
                       "p2rank_score": None, "p2rank_probability": None,
                       "fpocket_rank": item["source_rank"], "fpocket_score": item.get("score"),
                       "fpocket_druggability_score": item.get("druggability_score"), "sources": ["fpocket"]})
    merged.sort(key=lambda item: (-len(item["sources"]),
                                  -(item.get("p2rank_probability") if item.get("p2rank_probability") is not None else -1),
                                  -(item.get("fpocket_druggability_score") if item.get("fpocket_druggability_score") is not None else -1),
                                  item.get("p2rank_rank") or 10**6, item.get("fpocket_rank") or 10**6))
    for rank, item in enumerate(merged, start=1):
        item["rank"] = rank
        item["name"] = item.get("name") or f"pocket{rank}"
        item["consensus_support_count"] = len(item["sources"])
        item["consensus_distance_threshold_A"] = agreement_distance_A
        item["box"] = item.get("box") or {"center": item["center"], "size": [box_size] * 3,
                                            "size_source": "configured fixed cubic extent; Angstrom"}
    return merged

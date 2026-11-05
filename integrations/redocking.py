"""Reference-pose evaluation for redocking benchmarks."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS_A = (2.0,)
DEFAULT_TOP_K = (1, 3, 5)
MAX_SYMMETRY_MAPPINGS = 50000


def evaluate_redocking(request: dict[str, Any]) -> dict[str, Any]:
    """Compare docked poses to a crystal reference in a verified receptor frame.

    The request maps one reference SDF and its crystal receptor to a prepared
    docking receptor, then lists task result/pose SDF paths. Heavy atom graph
    automorphisms handle symmetric atom labels without fitting the ligand.
    """
    from rdkit import Chem, rdBase
    reference_spec = request.get("reference")
    if not reference_spec:
        return {"status": "not_applicable", "reason": "no crystal reference ligand was supplied"}
    reference_path = Path(reference_spec["path"])
    reference = _load_single_sdf(reference_path, Chem, "reference ligand")
    _assign_3d_stereo(reference)
    reference = Chem.RemoveHs(reference)
    reference_key = _molecule_key(reference, Chem)
    reference_receptor = Path(request["reference_receptor"]["path"])
    prepared_receptor = Path(request["prepared_receptor"]["path"])
    receptor_frame = _verify_receptor_frame(
        reference_receptor, prepared_receptor,
        tolerance_A=float(request.get("receptor_frame_tolerance_A", 0.05)),
        minimum_coverage=float(request.get("minimum_receptor_atom_coverage", 0.80)),
    )

    thresholds = tuple(sorted({float(value) for value in request.get("thresholds_A", DEFAULT_THRESHOLDS_A)}))
    top_k = tuple(sorted({int(value) for value in request.get("top_k", DEFAULT_TOP_K)}))
    if not thresholds or any(not math.isfinite(v) or v <= 0 for v in thresholds):
        raise ValueError("RMSD thresholds must be positive finite distances in Å")
    if not top_k or any(v < 1 for v in top_k):
        raise ValueError("top-k values must be positive integers")

    tasks = []
    requested_ligand_id = reference_spec.get("ligand_id")
    for task_spec in request.get("tasks", []):
        row = {key: task_spec.get(key) for key in ("task_id", "ligand_id", "state_id", "seed", "status")}
        row["poses"] = []
        if requested_ligand_id and row["ligand_id"] != requested_ligand_id:
            row.update(redocking_status="not_applicable", reason="task is not the ligand associated with the reference")
            tasks.append(row)
            continue
        if row.get("status") != "complete":
            row.update(redocking_status="failed", reason="docking task did not complete")
            tasks.append(row)
            continue

        result_path = Path(task_spec["result_path"])
        pose_path = Path(task_spec["poses_path"])
        result = json.loads(result_path.read_text(encoding="utf-8"))
        pose_records = {int(item["mode"]): item for item in result.get("poses", []) if "mode" in item}
        molecules = [mol for mol in Chem.SDMolSupplier(str(pose_path), removeHs=False) if mol is not None]
        if len(molecules) != len(pose_records):
            row.update(redocking_status="failed", reason="pose SDF and docking result records do not match")
            tasks.append(row)
            continue
        pose_errors = []
        for index, molecule in enumerate(molecules, start=1):
            mode = int(molecule.GetProp("mode")) if molecule.HasProp("mode") else index
            record = pose_records.get(mode)
            if record is None:
                pose_errors.append(f"pose mode {mode} has no matching docking result")
                continue
            try:
                pose = Chem.RemoveHs(Chem.Mol(molecule))
                _assign_3d_stereo(pose)
                if _molecule_key(pose, Chem) != reference_key:
                    raise ValueError("pose molecular graph, charge or stereochemistry differs from the reference")
                rmsd, mapping_count = _symmetry_corrected_rmsd(reference, pose, Chem)
                qc_pass = record.get("qc_pass") is True
                score = float(record["affinity_kcal_mol"])
                if not math.isfinite(score):
                    raise ValueError("non-finite docking score")
                row["poses"].append({"mode": mode, "affinity_kcal_mol": score, "qc_pass": qc_pass,
                                     "symmetry_corrected_heavy_rmsd_A": rmsd,
                                     "symmetry_mapping_count": mapping_count,
                                     "success_thresholds_A": {f"{threshold:g}": rmsd <= threshold
                                                              for threshold in thresholds}})
            except Exception as exc:
                pose_errors.append(f"pose mode {mode}: {type(exc).__name__}: {exc}")

        if pose_errors:
            row.update(redocking_status="failed", reason="; ".join(pose_errors[:10]))
            tasks.append(row)
            continue
        passing = sorted((pose for pose in row["poses"] if pose["qc_pass"]),
                         key=lambda pose: (pose["affinity_kcal_mol"], pose["mode"]))
        if not passing:
            row.update(redocking_status="failed", reason="no QC-passing docked pose")
            tasks.append(row)
            continue
        row["redocking_status"] = "succeeded"
        row["ranked_qc_passing_pose_count"] = len(passing)
        row["top_scoring_pose"] = passing[0]
        row["best_rmsd_pose"] = min(passing, key=lambda pose: (pose["symmetry_corrected_heavy_rmsd_A"],
                                                               pose["affinity_kcal_mol"], pose["mode"]))
        row["top_k"] = {}
        for k in top_k:
            ranked = passing[:k]
            row["top_k"][str(k)] = {"pose_count": len(ranked), **{
                f"success_at_{threshold:g}A": any(p["symmetry_corrected_heavy_rmsd_A"] <= threshold for p in ranked)
                for threshold in thresholds}}
        tasks.append(row)

    applicable = [task for task in tasks if task.get("redocking_status") != "not_applicable"]
    evaluated = [task for task in applicable if task.get("redocking_status") == "succeeded"]
    aggregates = {str(k): {f"success_at_{threshold:g}A": {
        "successes": sum(task["top_k"].get(str(k), {}).get(f"success_at_{threshold:g}A", False) for task in evaluated),
        "denominator_tasks": len(applicable),
        "evaluated_tasks": len(evaluated),
        "failed_tasks": len(applicable) - len(evaluated),
        "rate": (sum(task["top_k"].get(str(k), {}).get(f"success_at_{threshold:g}A", False) for task in evaluated)
                 / len(applicable)) if applicable else None,
    } for threshold in thresholds} for k in top_k}
    failed = [task for task in applicable if task.get("redocking_status") == "failed"]
    return {
        "status": "failed" if failed else ("succeeded" if evaluated else "not_applicable"),
        "method": "minimum fixed-frame heavy-atom RMSD over stereochemistry-preserving molecular graph mappings",
        "reference_ligand": {"ligand_id": requested_ligand_id,
                             "artifact_id": reference_spec.get("artifact_id"),
                             "path": reference_spec.get("artifact_path", reference_path.name),
                             "sha256": reference_spec.get("sha256"),
                             "heavy_atom_count": reference.GetNumAtoms(), "canonical_isomeric_smiles": reference_key},
        "coordinate_frame": {"verified": True,
                             "reference_receptor_artifact_id": request["reference_receptor"].get("artifact_id"),
                             "prepared_receptor_artifact_id": request["prepared_receptor"].get("artifact_id"),
                             **receptor_frame},
        "ligand_alignment_performed": False,
        "thresholds_A": list(thresholds), "top_k_values": list(top_k),
        "task_count": len(applicable), "evaluated_task_count": len(evaluated), "failed_task_count": len(failed),
        "aggregates": aggregates, "tasks": tasks, "rdkit_version": rdBase.rdkitVersion,
        "limitations": ["A passed redocking result validates pose recovery only for this reference complex.",
                        "Docking score is not experimental affinity; top-k rates depend on the specified pose ranking.",
                        "A missing crystal reference makes redocking not applicable, not a zero RMSD."],
    }


def _load_single_sdf(path: Path, Chem: Any, label: str) -> Any:
    molecules = [mol for mol in Chem.SDMolSupplier(str(path), removeHs=False) if mol is not None]
    if len(molecules) != 1:
        raise ValueError(f"{label} SDF must contain exactly one valid molecule; found {len(molecules)}")
    molecule = molecules[0]
    if molecule.GetNumConformers() != 1:
        raise ValueError(f"{label} must contain exactly one 3D conformer")
    if len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError(f"{label} must be one connected molecular graph")
    _finite_coordinates(molecule)
    return molecule


def _assign_3d_stereo(molecule: Any) -> None:
    if molecule.GetNumConformers() != 1:
        raise ValueError("each compared molecule must contain exactly one conformer")
    from rdkit.Chem import AssignStereochemistryFrom3D
    AssignStereochemistryFrom3D(molecule, confId=0, replaceExistingTags=True)


def _finite_coordinates(molecule: Any) -> None:
    conformer = molecule.GetConformer()
    for index in range(molecule.GetNumAtoms()):
        point = conformer.GetAtomPosition(index)
        if not all(math.isfinite(value) for value in (point.x, point.y, point.z)):
            raise ValueError(f"atom {index} has non-finite coordinates")


def _molecule_key(molecule: Any, Chem: Any) -> str:
    _finite_coordinates(molecule)
    if len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError("compared molecule is disconnected")
    Chem.SanitizeMol(molecule)
    return Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)


def _symmetry_corrected_rmsd(reference: Any, pose: Any, Chem: Any) -> tuple[float, int]:
    if reference.GetNumAtoms() != pose.GetNumAtoms():
        raise ValueError("reference and pose heavy atom counts differ")
    mappings = pose.GetSubstructMatches(reference, uniquify=False, useChirality=True,
                                        maxMatches=MAX_SYMMETRY_MAPPINGS + 1)
    if not mappings:
        raise ValueError("reference and pose have no stereochemistry-preserving graph mapping")
    if len(mappings) > MAX_SYMMETRY_MAPPINGS:
        raise ValueError(f"molecular symmetry exceeds the mapping limit ({MAX_SYMMETRY_MAPPINGS})")
    ref_conf, pose_conf = reference.GetConformer(), pose.GetConformer()
    best_squared = math.inf
    for mapping in mappings:
        total = 0.0
        for ref_index, pose_index in enumerate(mapping):
            a, b = ref_conf.GetAtomPosition(ref_index), pose_conf.GetAtomPosition(pose_index)
            total += (a.x-b.x)**2 + (a.y-b.y)**2 + (a.z-b.z)**2
        best_squared = min(best_squared, total / reference.GetNumAtoms())
    return math.sqrt(best_squared), len(mappings)


def _pdb_heavy_atoms(path: Path) -> dict[tuple[str, str, str, str, str], tuple[float, float, float]]:
    atoms = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("ATOM  ") or len(line) < 54:
            continue
        if line[16:17] not in {" ", "A"}:
            continue
        element = (line[76:78].strip() if len(line) >= 78 else "") or line[12:16].strip()[0:1]
        if element.upper() in {"H", "D"}:
            continue
        key = (line[21:22], line[22:26].strip(), line[26:27].strip(),
               line[12:16].strip(), element.upper())
        xyz = tuple(float(line[start:start+8]) for start in (30, 38, 46))
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError(f"non-finite receptor coordinate at {key}")
        atoms.setdefault(key, xyz)
    if not atoms:
        raise ValueError(f"no protein heavy atoms found in receptor: {path}")
    return atoms


def _verify_receptor_frame(reference_path: Path, prepared_path: Path, *, tolerance_A: float,
                           minimum_coverage: float) -> dict[str, Any]:
    if not math.isfinite(tolerance_A) or tolerance_A <= 0:
        raise ValueError("receptor coordinate tolerance must be positive")
    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum receptor atom coverage must be in (0, 1]")
    reference, prepared = _pdb_heavy_atoms(reference_path), _pdb_heavy_atoms(prepared_path)
    prepared_chains = {key[0] for key in prepared}
    reference_selected = {key: value for key, value in reference.items() if key[0] in prepared_chains}
    common = reference_selected.keys() & prepared.keys()
    if not common:
        raise ValueError("crystal and prepared receptors have no matching heavy-atom identities")
    displacements = [math.sqrt(sum((a-b)**2 for a, b in zip(reference[key], prepared[key])))
                     for key in common]
    coverage = len(common) / len(reference_selected)
    maximum = max(displacements)
    rms = math.sqrt(sum(value*value for value in displacements) / len(displacements))
    if coverage < minimum_coverage:
        raise ValueError(f"receptor coordinate identity coverage {coverage:.3f} is below {minimum_coverage:.3f}")
    if maximum > tolerance_A:
        raise ValueError(f"receptor heavy atoms moved by up to {maximum:.4f} Å; fixed-frame RMSD is invalid")
    return {"reference_receptor_heavy_atoms": len(reference),
            "reference_receptor_heavy_atoms_on_prepared_chains": len(reference_selected),
            "prepared_receptor_heavy_atoms": len(prepared), "prepared_receptor_chains": sorted(prepared_chains),
            "matched_heavy_atoms": len(common), "reference_atom_coverage": coverage,
            "rms_displacement_A": rms, "maximum_displacement_A": maximum,
            "maximum_allowed_displacement_A": tolerance_A}


__all__ = ["evaluate_redocking"]

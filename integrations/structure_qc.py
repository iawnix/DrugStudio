"""Dependency-free sanity and confidence checks for PDB protein coordinates."""

from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any


_AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}


def assess_pdb(path: Path, *, predicted: bool, expected_sequence: str | None = None) -> dict[str, Any]:
    atoms = 0
    atom_keys: set[tuple[str, str, str, str, str]] = set()
    chains: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    invalid_coordinates = 0
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if not line.startswith("ATOM  ") or len(line) < 66:
            continue
        altloc = line[16:17].strip()
        if altloc not in {"", "A"}:
            continue
        atoms += 1
        chain = line[21:22].strip()
        residue = (line[22:26].strip(), line[26:27].strip())
        atom_name = line[12:16].strip()
        resname = line[17:20].strip().upper()
        key = (chain, residue[0], residue[1], resname, atom_name)
        if key in atom_keys:
            continue
        atom_keys.add(key)
        try:
            xyz = tuple(float(line[start:start + 8]) for start in (30, 38, 46))
            bfactor = float(line[60:66])
            if not all(math.isfinite(value) for value in xyz + (bfactor,)):
                invalid_coordinates += 1
                continue
        except ValueError:
            invalid_coordinates += 1
            continue
        entry = chains.setdefault(chain, {}).setdefault(residue, {"name": resname, "ca": None})
        if atom_name == "CA":
            entry["ca"] = {"xyz": xyz, "bfactor": bfactor}

    chain_reports = []
    all_ca_bfactors = []
    max_ca_gap = 0.0
    for chain_id, residues in sorted(chains.items()):
        ordered = sorted(residues.items(), key=lambda item: (int(item[0][0]) if item[0][0].lstrip("-").isdigit() else 0,
                                                              item[0][1]))
        sequence = "".join(_AA3_TO_1.get(record["name"], "X") for _, record in ordered)
        ca_atoms = [record["ca"] for _, record in ordered if record["ca"] is not None]
        factors = [float(atom["bfactor"]) for atom in ca_atoms]
        all_ca_bfactors.extend(factors)
        gaps = []
        for left, right in zip(ca_atoms, ca_atoms[1:]):
            distance = math.dist(left["xyz"], right["xyz"])
            if distance > 4.5:
                gaps.append(distance)
        if gaps:
            max_ca_gap = max(max_ca_gap, max(gaps))
        report = {"chain_id": chain_id, "residue_count": len(ordered), "ca_count": len(ca_atoms),
                  "sequence": sequence, "ca_bfactor_median": statistics.median(factors) if factors else None,
                  "ca_bfactor_below_50_fraction": (sum(value < 50 for value in factors) / len(factors)) if factors else None,
                  "ca_gaps_over_4_5_A": len(gaps)}
        if expected_sequence:
            expected = "".join(expected_sequence.split()).upper()
            report["expected_sequence_length"] = len(expected)
            report["exact_sequence_match"] = sequence == expected
            report["expected_sequence_is_subsequence"] = expected in sequence
        chain_reports.append(report)

    median_b = statistics.median(all_ca_bfactors) if all_ca_bfactors else None
    low_fraction = sum(value < 50 for value in all_ca_bfactors) / len(all_ca_bfactors) if all_ca_bfactors else None
    exact_match = any(item.get("exact_sequence_match") for item in chain_reports)
    full_sequence = any(item.get("expected_sequence_is_subsequence") for item in chain_reports)
    hard_failures = []
    review_flags = []
    if atoms == 0 or not chains:
        hard_failures.append("no protein ATOM records were found")
    if invalid_coordinates:
        hard_failures.append(f"{invalid_coordinates} atom records have invalid coordinates or B factors")
    if not all_ca_bfactors:
        review_flags.append("no CA atoms were available for confidence and continuity checks")
    if max_ca_gap > 4.5:
        review_flags.append(f"backbone CA gap exceeds 4.5 A (maximum {max_ca_gap:.2f} A)")
    if expected_sequence and not full_sequence:
        review_flags.append("target sequence was not found intact in any single chain")
    if predicted and median_b is not None and median_b < 70:
        review_flags.append("median CA B-factor/confidence is below 70")
    if predicted and low_fraction is not None and low_fraction > 0.10:
        review_flags.append("more than 10% of CA B-factor/confidence values are below 50")
    return {
        "schema_version": 1,
        "input_file": path.name,
        "structure_source_class": "predicted" if predicted else "experimental_or_user_supplied",
        "atom_count": atoms,
        "chain_count": len(chains),
        "chains": chain_reports,
        "ca_bfactor_median": median_b,
        "ca_bfactor_below_50_fraction": low_fraction,
        "maximum_consecutive_ca_distance_A": max_ca_gap,
        "hard_failures": hard_failures,
        "review_flags": review_flags,
        "gate": "fail" if hard_failures else "review" if review_flags else "pass",
        "confidence_interpretation": "B-factor fields are treated as confidence only for predicted structures; values are not experimental validation.",
        "limitations": ["This is a structural sanity screen, not a fold-accuracy benchmark or binding-site validation.",
                        "No atom-clash, oligomeric-state, membrane-context, ligandability or biological-assembly assessment is performed."],
    }

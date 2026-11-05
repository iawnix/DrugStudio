"""Descriptor worker executed in the managed RDKit environment."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, FilterCatalog, QED, rdMolDescriptors

from rdkit_bridge import load_one
from redocking import evaluate_redocking


def describe(path: Path) -> dict:
    molecule = Chem.RemoveHs(load_one(path))
    values = {
        "mw": Descriptors.MolWt(molecule),
        "logp": Descriptors.MolLogP(molecule),
        "tpsa": Descriptors.TPSA(molecule),
        "hbd": Descriptors.NumHDonors(molecule),
        "hba": Descriptors.NumHAcceptors(molecule),
        "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds(molecule, strict=True),
        "qed": QED.qed(molecule),
        "fraction_csp3": rdMolDescriptors.CalcFractionCSP3(molecule),
        "formal_charge": Chem.GetFormalCharge(molecule),
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("non-finite molecular descriptor")
    violations = [name for name, limit in (("mw", 500), ("logp", 5), ("hbd", 5), ("hba", 10))
                  if values[name] > limit]
    alerts = {}
    for name in ("PAINS", "BRENK"):
        params = FilterCatalog.FilterCatalogParams()
        params.AddCatalog(getattr(FilterCatalog.FilterCatalogParams.FilterCatalogs, name))
        catalog = FilterCatalog.FilterCatalog(params)
        alerts[name.lower()] = sorted({entry.GetDescription() for entry in catalog.GetMatches(molecule)})
    return {
        **values,
        "isomeric_smiles": Chem.MolToSmiles(molecule, isomericSmiles=True),
        "lipinski_violations": violations,
        "lipinski_all_four_pass": not violations,
        "veber_pass": values["rotatable_bonds"] <= 10 and values["tpsa"] <= 140,
        "unassigned_stereocenters": sum(label == "?" for _, label in Chem.FindMolChiralCenters(molecule, includeUnassigned=True)),
        "alerts": alerts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--redocking-inputs")
    args = parser.parse_args()
    records = []
    for item in json.loads(Path(args.inputs).read_text(encoding="utf-8")):
        record = {"ligand_id": item["ligand_id"], "artifact_id": item["artifact_id"]}
        try:
            record.update(status="succeeded", properties=describe(Path(item["path"])))
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        records.append(record)
    result = {"rdkit_version": rdBase.rdkitVersion, "ligands": records}
    if args.redocking_inputs:
        try:
            result["redocking"] = evaluate_redocking(
                json.loads(Path(args.redocking_inputs).read_text(encoding="utf-8")))
        except Exception as exc:
            result["redocking"] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

"""Small RDKit bridge used inside the managed chemistry environment."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional

from rdkit import Chem
from rdkit.Chem import AllChem


def load_one(path: Path) -> Chem.Mol:
    supplier = Chem.SDMolSupplier(str(path), removeHs=False)
    molecules = list(supplier)
    if len(molecules) != 1 or molecules[0] is None:
        raise ValueError(f"expected exactly one valid molecule in {path}")
    molecule = molecules[0]
    if len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError("disconnected ligand is not supported")
    return molecule


def from_smiles(smiles: str, seed: int) -> Chem.Mol:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("invalid SMILES")
    if len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError("select one connected ligand microstate before preparation")
    molecule = Chem.AddHs(molecule)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    if AllChem.EmbedMolecule(molecule, params) != 0:
        raise ValueError("RDKit ETKDG embedding failed")
    return molecule


def ensure_3d(molecule: Chem.Mol, seed: int) -> Chem.Mol:
    if molecule.GetNumConformers() == 1 and molecule.GetConformer().Is3D():
        return Chem.AddHs(molecule, addCoords=True)
    base = Chem.RemoveHs(molecule)
    return from_smiles(Chem.MolToSmiles(base, isomericSmiles=True), seed)


def write_xyz(molecule: Chem.Mol, path: Path, comment: str) -> None:
    block = Chem.MolToXYZBlock(molecule, confId=0).splitlines()
    block[1] = comment
    path.write_text("\n".join(block) + "\n", encoding="ascii")


def parse_xyz(path: Path) -> tuple[list[str], list[tuple[float, float, float]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError(f"invalid XYZ file: {path}")
    count = int(lines[0].strip())
    if count <= 0:
        raise ValueError("XYZ atom count must be positive")
    coordinate_lines = lines[2 : 2 + count]
    if len(coordinate_lines) != count:
        raise ValueError(f"truncated XYZ file: {path}")
    elements = []
    coordinates = []
    for line in coordinate_lines:
        fields = line.split()
        if len(fields) < 4:
            raise ValueError(f"invalid XYZ coordinate line: {line}")
        elements.append(fields[0])
        coordinates.append((float(fields[1]), float(fields[2]), float(fields[3])))
    if any(not math.isfinite(value) for xyz in coordinates for value in xyz):
        raise ValueError("non-finite XYZ coordinates")
    return elements, coordinates


def xyz_to_sdf(reference_path: Path, xyz_path: Path, output_path: Path) -> None:
    molecule = load_one(reference_path)
    reference = Chem.Mol(molecule)
    elements, coordinates = parse_xyz(xyz_path)
    expected = [atom.GetSymbol() for atom in molecule.GetAtoms()]
    if elements != expected:
        raise ValueError("xTB changed atom count or atom ordering")
    molecule.RemoveAllConformers()
    conformer = Chem.Conformer(molecule.GetNumAtoms())
    conformer.Set3D(True)
    for index, coordinate in enumerate(coordinates):
        conformer.SetAtomPosition(index, coordinate)
    molecule.AddConformer(conformer, assignId=True)
    validate_geometry(molecule)
    before = geometry_identity(reference)
    after = geometry_identity(molecule)
    if before != after:
        raise ValueError("xTB optimized geometry changed inferred stereochemistry")
    writer = Chem.SDWriter(str(output_path))
    writer.write(molecule)
    writer.close()


def geometry_identity(molecule: Chem.Mol) -> str:
    inferred = Chem.Mol(molecule)
    Chem.RemoveStereochemistry(inferred)
    Chem.AssignStereochemistryFrom3D(inferred, replaceExistingTags=True)
    if not Chem.RemoveHs(inferred).HasSubstructMatch(Chem.RemoveHs(molecule), useChirality=True):
        raise ValueError("stereochemistry labels contradict the 3D coordinates")
    return Chem.MolToSmiles(Chem.RemoveHs(inferred), isomericSmiles=True)


def validate_geometry(molecule: Chem.Mol) -> None:
    """Conservative distance screen, not a QM proof of unchanged bonding."""
    conf = molecule.GetConformer()
    coordinates = [tuple(conf.GetAtomPosition(i)) for i in range(molecule.GetNumAtoms())]
    if any(not math.isfinite(value) for xyz in coordinates for value in xyz):
        raise ValueError("non-finite ligand coordinates")
    table = Chem.GetPeriodicTable()
    for i, atom in enumerate(molecule.GetAtoms()):
        for j in range(i):
            other = molecule.GetAtomWithIdx(j)
            distance = math.dist(coordinates[i], coordinates[j])
            radius = table.GetRcovalent(atom.GetAtomicNum()) + table.GetRcovalent(other.GetAtomicNum())
            if molecule.GetBondBetweenAtoms(i, j) is not None:
                if not 0.65 * radius <= distance <= 1.35 * radius:
                    raise ValueError(f"implausible bonded distance for atoms {j}, {i}: {distance:.3f} Å")
            elif distance < radius:
                raise ValueError(f"nonbonded atoms {j}, {i} are too close: {distance:.3f} Å; possible connectivity change")
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() != 1:
            continue
        neighbors = [neighbor.GetIdx() for neighbor in atom.GetNeighbors() if neighbor.GetAtomicNum() > 1]
        if len(neighbors) != 1:
            raise ValueError("each hydrogen must be attached to one heavy atom")
        heavy = [other.GetIdx() for other in molecule.GetAtoms() if other.GetAtomicNum() > 1]
        nearest = min(heavy, key=lambda index: math.dist(coordinates[atom.GetIdx()], coordinates[index]))
        if nearest != neighbors[0]:
            raise ValueError("hydrogen is closer to another heavy atom; possible proton transfer")


def build_initial(args: argparse.Namespace) -> None:
    if bool(args.sdf) == bool(args.smiles):
        raise ValueError("provide exactly one of --sdf or --smiles")
    molecule = load_one(Path(args.sdf)) if args.sdf else from_smiles(args.smiles, args.seed)
    molecule = ensure_3d(molecule, args.seed)
    if not molecule.GetNumConformers() or not molecule.GetConformer().Is3D():
        raise ValueError("failed to produce a 3D conformer")
    if any(atom.GetNumRadicalElectrons() for atom in molecule.GetAtoms()):
        raise ValueError("radical microstates require a separately configured spin workflow")
    if any(atom.GetIsotope() for atom in molecule.GetAtoms()):
        raise ValueError("isotopically labeled ligands are not supported by this XYZ preparation path")
    validate_geometry(molecule)
    geometry_identity(molecule)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    Chem.MolToMolFile(molecule, str(output))
    write_xyz(molecule, output.with_suffix(".xyz"), "DrugStudio initial conformer")
    metadata = {
        "formal_charge": Chem.GetFormalCharge(molecule),
        "isomeric_smiles": Chem.MolToSmiles(Chem.RemoveHs(molecule), isomericSmiles=True),
        "atom_count": molecule.GetNumAtoms(),
        "heavy_atom_count": molecule.GetNumHeavyAtoms(),
        "explicit_hydrogens": all(atom.GetNumImplicitHs() == 0 and atom.GetNumExplicitHs() == 0 for atom in molecule.GetAtoms()),
        "total_nuclear_charge": sum(atom.GetAtomicNum() for atom in molecule.GetAtoms()),
        "geometry_qc": "finite coordinates, covalent-distance screen, H attachment and 3D stereo consistency",
    }
    Path(args.metadata).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def convert_optimized(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    xyz_to_sdf(Path(args.reference), Path(args.xyz), output)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    initial = commands.add_parser("initial")
    initial.add_argument("--sdf")
    initial.add_argument("--smiles")
    initial.add_argument("--seed", type=int, default=20261016)
    initial.add_argument("--output", required=True)
    initial.add_argument("--metadata", required=True)
    optimized = commands.add_parser("optimized")
    optimized.add_argument("--reference", required=True)
    optimized.add_argument("--xyz", required=True)
    optimized.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "initial":
        build_initial(args)
    else:
        convert_optimized(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

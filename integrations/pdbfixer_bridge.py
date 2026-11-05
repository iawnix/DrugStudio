"""Isolated PDBFixer worker with explicit filtering and repair inventories."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _residue_key(line: str) -> tuple[str, str, str, str]:
    return line[21:22], line[22:26].strip(), line[26:27].strip(), line[17:20].strip()


def _filtered_pdb(source: Path, destination: Path, policy: dict[str, Any]) -> dict[str, Any]:
    from pdbfixer.pdbfixer import substitutions

    keep = {str(v).strip().upper() for v in policy.get('keep_residues', [])}
    keep_water = bool(policy.get('keep_waters', False))
    remove = bool(policy.get('remove_heterogens', True))
    waters = {'HOH', 'WAT', 'DOD'}
    lines = source.read_text().splitlines()
    polymer_names: dict[str, set[str]] = {}
    modified = set()
    atom_names: dict[tuple, set[str]] = {}
    for line in lines:
        if line.startswith('SEQRES'):
            polymer_names.setdefault(line[11:12], set()).update(line[19:70].split())
        if line.startswith('MODRES'):
            modified.add((line[16:17], line[18:22].strip(), line[22:23].strip(), line[12:15].strip()))
        if line.startswith(('ATOM  ', 'HETATM')):
            atom_names.setdefault(_residue_key(line), set()).add(line[12:16].strip())
    kept_serials, kept_keys, removed, retained, protected = set(), set(), {}, {}, {}
    output = []
    for line in lines:
        if line.startswith(('ATOM  ', 'HETATM')):
            key = _residue_key(line)
            chain, number, icode, name = key
            polymer = key in modified or (name in substitutions and
                (name in polymer_names.get(chain, set()) or {'N', 'CA', 'C'} <= atom_names[key]))
            retain = line.startswith('ATOM  ') or not remove or polymer or name in keep or (keep_water and name in waters)
            if not retain:
                removed.setdefault(key, []).append(line[12:16].strip())
                continue
            kept_serials.add(line[6:11].strip())
            kept_keys.add(key)
            if line.startswith('HETATM'):
                (protected if polymer else retained).setdefault(key, []).append(line[12:16].strip())
        output.append(line)
    clean = []
    for line in output:
        if line.startswith('CONECT'):
            ids = [line[i:i+5].strip() for i in range(6, len(line), 5) if line[i:i+5].strip()]
            if not ids or ids[0] not in kept_serials:
                continue
            ids = [ids[0]] + [v for v in ids[1:] if v in kept_serials]
            if len(ids) < 2:
                continue
            line = 'CONECT' + ''.join(f'{v:>5}' for v in ids)
        elif line.startswith(('ANISOU', 'SIGATM', 'SIGUIJ')) and line[6:11].strip() not in kept_serials:
            continue
        elif line.startswith('LINK  '):
            second = (line[51:52], line[52:56].strip(), line[56:57].strip(), line[47:50].strip())
            if _residue_key(line) not in kept_keys or second not in kept_keys:
                continue
        elif line.startswith('MASTER'):
            continue  # Original record counts no longer describe the filtered file.
        clean.append(line)
    destination.write_text('\n'.join(clean) + '\n')
    def inventory(items):
        return [{'chain': k[0], 'residue_id': k[1], 'insertion_code': k[2], 'name': k[3], 'atoms': v}
                for k, v in sorted(items.items())]
    return {'removed_heterogen_residues': sorted({k[3] for k in removed}),
            'retained_heterogen_residues': sorted({k[3] for k in retained}),
            'removed_heterogens': inventory(removed), 'retained_heterogens': inventory(retained),
            'protected_polymer_heterogens': inventory(protected)}


def _label(residue) -> str:
    return f'{residue.chain.id}:{residue.id}{residue.insertionCode.strip()}'


def _atoms(topology) -> set[tuple]:
    return {(_label(a.residue), a.residue.name, a.name, a.element.symbol if a.element else '')
            for a in topology.atoms()}


def _gaps(fixer, binding_site: list[str]) -> list[dict]:
    from openmm import unit
    positions = fixer.positions.value_in_unit(unit.angstrom)
    pocket = [positions[a.index] for a in fixer.topology.atoms()
              if _label(a.residue) in binding_site and a.element and a.element.symbol != 'H']
    chains = list(fixer.topology.chains())
    gaps = []
    for (chain_index, index), names in fixer.missingResidues.items():
        residues = list(chains[chain_index].residues())
        flanks = ([residues[index-1]] if index else []) + ([residues[index]] if index < len(residues) else [])
        xyz = [positions[a.index] for r in flanks for a in r.atoms() if a.element and a.element.symbol != 'H']
        distance = min((math.dist(a, b) for a in xyz for b in pocket), default=None)
        terminal = index == 0 or index == len(residues)
        # Internal gaps always need review. Distances refer to observed flanks,
        # never to the absent residues, whose location is unknown.
        review = not terminal or not pocket or distance is None or distance <= 6.0
        gaps.append({'chain': chains[chain_index].id, 'insertion_index': index, 'residue_names': list(names),
                     'count': len(names), 'terminal': terminal, 'flanking_residues': [_label(r) for r in flanks],
                     'minimum_flank_to_site_distance_A': distance, 'requires_review': review,
                     'distance_limit': 'Observed flanks only; missing-residue coordinates are unknown.'})
    return gaps


def repair(args: argparse.Namespace) -> None:
    try:
        from pdbfixer import PDBFixer
        from openmm.app import PDBFile
    except ImportError as exc:
        raise RuntimeError('PDBFixer/OpenMM is not installed in the selected chemistry environment') from exc
    policy = json.loads(Path(args.policy).read_text())
    source, output = Path(args.input).resolve(), Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    filtered = output.with_name('input.filtered.pdb')
    filter_metadata = _filtered_pdb(source, filtered, policy)
    fixer = PDBFixer(filename=str(filtered))
    requested = list(policy.get('chains', []))
    available = [c.id for c in fixer.topology.chains()]
    if requested:
        if set(requested) - set(available):
            raise ValueError(f'requested chains absent: {set(requested) - set(available)}')
        fixer.removeChains([i for i, c in enumerate(available) if c not in requested])
    before = _atoms(fixer.topology)
    binding_site = list(policy.get('binding_site_residues', []))
    if set(binding_site) - {_label(r) for r in fixer.topology.residues()}:
        raise ValueError('binding-site labels are absent from selected receptor chains')
    fixer.findMissingResidues()
    gaps = _gaps(fixer, binding_site)
    fixer.findNonstandardResidues()
    nonstandard = [{'residue': _label(r), 'original': r.name, 'replacement': name}
                   for r, name in fixer.nonstandardResidues]
    replace = bool(policy.get('replace_nonstandard_residues', True))
    if replace:
        fixer.replaceNonstandardResidues()
    add_residues = bool(policy.get('add_missing_residues', False))
    if not add_residues:
        fixer.missingResidues = {}
    elif any(g['count'] > 5 or g['requires_review'] for g in gaps):
        raise ValueError('automatic gap building requires external modeling/review for internal, unassessed, near-site or >5-residue gaps')
    fixer.findMissingAtoms()
    missing_atoms = [{'residue': _label(r), 'atoms': [a.name for a in atoms]}
                     for r, atoms in fixer.missingAtoms.items()]
    missing_terminals = [{'residue': _label(r), 'atoms': list(names)}
                         for r, names in fixer.missingTerminals.items()]
    if bool(policy.get('add_missing_atoms', True)):
        fixer.addMissingAtoms(seed=20261029)
    # Heterogens were already filtered. A second removeHeterogens would delete
    # intentionally protected polymer residues when replacement is disabled.
    ph = float(policy.get('ph', 7.4))
    if not 0.0 < ph < 14.0:
        raise ValueError('receptor pH must be between 0 and 14')
    fixer.addMissingHydrogens(pH=ph)
    PDBFile.writeFile(fixer.topology, fixer.positions, str(output), keepIds=True)
    after = _atoms(fixer.topology)
    metadata = {'backend': 'pdbfixer', 'input': str(source), 'output': str(output),
                'chains_requested': requested, 'chains_available_before_selection': available,
                'chains_selected': [c.id for c in fixer.topology.chains()], 'ph': ph,
                'missing_residues': gaps, 'missing_residue_count': sum(g['count'] for g in gaps),
                'nonstandard_residues': nonstandard, 'nonstandard_replacement_enabled': replace,
                'missing_atoms_before_repair': missing_atoms, 'missing_terminals_before_repair': missing_terminals,
                'added_atoms': sorted(after-before), 'removed_or_replaced_atoms': sorted(before-after),
                'added_missing_atoms': bool(policy.get('add_missing_atoms', True)), 'added_missing_residues': add_residues,
                'binding_site_residues': binding_site,
                'binding_site_requires_review': any(g['requires_review'] for g in gaps),
                'nonstandard_requires_review': bool(nonstandard),
                'retained_heterogen_requires_review': bool(filter_metadata['retained_heterogens']),
                'chemically_prepared': True, 'protonation_status': 'hydrogens_added_at_requested_pH',
                'protonation_limit': 'PDBFixer does not establish experimental pKa or unique tautomer populations',
                **filter_metadata}
    Path(args.metadata).write_text(json.dumps(metadata, indent=2, sort_keys=True)+'\n')


def main() -> int:
    parser = argparse.ArgumentParser()
    for key in ('input', 'output', 'metadata', 'policy'):
        parser.add_argument('--'+key, required=True)
    repair(parser.parse_args())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

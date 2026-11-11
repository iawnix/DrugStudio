"""Read-only result projections shared by the workbench and model context.

Artifact identity and preparation checksums determine coordinate compatibility;
file order and receptor display names never establish that relationship.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from statistics import median


@lru_cache(maxsize=256)
def _json_file(path: str, stamp: int, size: int) -> dict:
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read_record(root: Path, relative: str) -> dict:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or '.workbench' in path.parts:
        return {}
    try:
        stat = path.stat()
        if stat.st_size > 16 * 1024 * 1024:
            return {}
        return _json_file(str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def status(value):
    return 'succeeded' if value in {'complete', 'completed', 'succeeded'} else value or 'unknown'


def presentation(root: Path, manifest: dict, report: dict | None = None) -> dict:
    artifacts = [a for rows in manifest.get('artifacts', {}).values() for a in rows]
    by_id = {a.get('artifact_id'): a for a in artifacts}
    report = report or {}
    stage_attempt = manifest.get('stage_results', {}).get('docking', {}).get('metadata', {}).get('attempt')
    attempt = stage_attempt or report.get('docking_attempt') or 1
    invalidated = manifest.get('stage_results', {}).get('assessment', {}).get('status') == 'stale' or manifest.get('stage_results', {}).get('docking', {}).get('status') == 'stale'
    current_report = not invalidated and (not report.get('docking_attempt') or str(report['docking_attempt']) == str(attempt))
    reported = {t.get('task_id'): t for t in report.get('tasks', [])} if current_report else {}
    tasks = manifest.get('tasks') or list(reported.values())
    rows = []
    for task in tasks:
        tid = task.get('task_id')
        outputs = [a for a in artifacts if a.get('kind') == 'docking_output'
                   and str(a.get('metadata', {}).get('docking_attempt', 1)) == str(attempt)
                   and str(a.get('metadata', {}).get('relative_output', '')).startswith(str(tid) + '/')
                   and (not task.get('output_artifact_ids') or a.get('artifact_id') in task['output_artifact_ids'])]
        record_art = next((a for a in outputs if a['path'].endswith('/result.json')), None)
        record = read_record(root, record_art['path']) if record_art else {}
        assessment = reported.get(tid, {})
        data = {**task, **assessment, **record}
        inputs = [by_id[i] for i in task.get('input_artifact_ids', []) if i in by_id]
        receptor_input = next((a for a in inputs if a.get('kind') == 'prepared_receptor_pdbqt'), None)
        receptor = None
        if receptor_input:
            meta = read_record(root, receptor_input.get('metadata', {}).get('metadata_relpath', ''))
            if meta.get('pdbqt', {}).get('sha256') == receptor_input.get('sha256'):
                digest = meta.get('prepared_pdb', {}).get('sha256')
                receptor = next((a for a in artifacts if a.get('kind') == 'prepared_receptor_pdb'
                                 and digest and a.get('sha256') == digest), None)
        pose_art = next((a for a in outputs if a['path'].endswith('/poses.sdf')), None)
        poses = [{**p, 'record_index': i} for i, p in enumerate(record.get('poses', []))]
        valid = [p for p in poses if p.get('qc_pass') is True and number(p.get('affinity_kcal_mol')) is not None]
        best = min(valid, key=lambda p: p['affinity_kcal_mol']) if valid else None
        # An explicit QC rejection must never become the "best" result.
        fallback = data.get('best_pose') or {}
        score = best['affinity_kcal_mol'] if best else (None if poses or fallback.get('qc_pass') is False else
                number(fallback.get('affinity_kcal_mol', data.get('score_kcal_mol', data.get('score')))))
        metadata = task.get('metadata', {})
        protocol = {k: data.get(k, metadata.get(k)) for k in
                    ('center', 'size', 'seed', 'exhaustiveness', 'num_modes', 'min_rmsd', 'energy_range')}
        engine = data.get('engine') or task.get('engine') or 'unknown'
        environment = data.get('environment') or {}
        engine_version = data.get('engine_version') or environment.get('reported_version') or environment.get('package_version')
        group = json.dumps([attempt, engine, engine_version, receptor_input and receptor_input['artifact_id'],
                            task.get('receptor_id'), protocol], sort_keys=True)
        rows.append({'task_id': tid, 'status': status(data.get('status')), 'engine': engine, 'engine_version': engine_version,
                     'ligand_id': data.get('ligand_id'), 'state_id': data.get('state_id') or data.get('ligand_state_id'),
                     'receptor_id': data.get('receptor_id'), 'attempt': attempt, 'protocol': protocol,
                     'score': score, 'best_pose': best, 'poses': poses,
                     'pose_count': len(poses) if record else data.get('pose_count'),
                     'qc_pass_count': sum(p.get('qc_pass') is True for p in poses) if record else data.get('qc_pass_count'),
                     'receptor_artifact': receptor, 'pose_artifact': pose_art, 'record_artifact': record_art,
                     'viewable': bool(receptor and pose_art and poses), 'group': group,
                     'error': data.get('error')})
    groups = []
    for key in dict.fromkeys(r['group'] for r in rows):
        members = [r for r in rows if r['group'] == key]
        scores = [r['score'] for r in members if r['score'] is not None and r['status'] == 'succeeded']
        groups.append({'key': key, 'engine': members[0]['engine'], 'attempt': attempt,
                       'tasks': len(members), 'best_kcal_mol': min(scores) if scores else None,
                       'median_kcal_mol': median(scores) if scores else None})
    summary = {'total': len(rows), 'succeeded': sum(r['status'] == 'succeeded' for r in rows),
               'ligands': len({r['ligand_id'] for r in rows if r['ligand_id']}),
               'poses': sum(r['pose_count'] or 0 for r in rows), 'groups': groups,
               'best_kcal_mol': groups[0]['best_kcal_mol'] if len(groups) == 1 else None,
               'median_kcal_mol': groups[0]['median_kcal_mol'] if len(groups) == 1 else None}
    return {'tasks': rows, 'summary': summary, 'attempt': attempt, 'stale': invalidated}

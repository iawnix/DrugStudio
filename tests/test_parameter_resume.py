"""Exercise parameter edits through immutable artifacts, CLI startup and reruns."""
import contextlib
import fcntl
import io
import json
import unittest
from unittest.mock import patch

import test_parameters as fixtures
from drugstudio import cli, parameters
from drugstudio.contracts import RunStatus
from drugstudio.storage import ArtifactStore


class ParameterResume(unittest.TestCase):
    setUp = fixtures.ResumeUsesTheRunsOwnParameters.setUp
    tearDown = fixtures.ResumeUsesTheRunsOwnParameters.tearDown

    def setup_run(self, completed=False, legacy=False, **values):
        store = ArtifactStore(self.run_root)
        state = store.load_manifest()
        from drugstudio.contracts import LigandSpec
        state.ligands = [LigandSpec(ligand_id='lig-1', smiles='C')]
        settings = {**parameters.defaults(), 'center': None, 'size': None, 'pocket_rank': 1, **values}
        if completed:
            state.stage_results = {stage: {'status': 'succeeded'} for stage in parameters.STAGE_ORDER}
            state.status = RunStatus.SUCCEEDED
        if legacy:
            artifact = store.publish_json(relative_path=parameters.RUNTIME_RELATIVE,
                                          value=settings, kind='runtime_configuration')
            state.add_artifact('inputs', artifact)
            store.save_manifest(state)
        else:
            parameters.record_runtime(store, state, settings)
        return store

    def execute(self, run_root=None, *flags):
        # Execute the real CLI -> runtime -> snapshot path. Only the external
        # docking engine availability and LLM controller are stubbed.
        class PausingController:
            def __init__(self, **kwargs):
                self.state = kwargs['run_state']
                self.store = kwargs['context'].store
            def invoke(self, prompt):
                self.state.status = RunStatus.PAUSED
                self.store.save_manifest(self.state)
                return {'status': 'paused', 'response': 'checkpoint'}
        with patch('drugstudio.runtime.AutoDockVinaEngine') as engine, \
             patch('drugstudio.runtime.DockingReActController', PausingController), \
             patch('drugstudio.runtime.create_openai_chat_model', return_value=object()), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            result = cli.main(['run', '--run-root', str(run_root or self.run_root), *flags])
            self.assertEqual(result, 2, error.getvalue())
            return engine.call_args.kwargs

    def test_new_run_saved_parameters_start_and_resume(self):
        store = ArtifactStore(self.run_root)
        state = store.load_manifest()
        from drugstudio.contracts import LigandSpec
        state.ligands = [LigandSpec(ligand_id='lig-1', smiles='C')]
        store.save_manifest(state)
        self.app.save_run_parameters({'values': {'exhaustiveness': 32}}, self.run_root)
        for _ in range(2):
            self.assertEqual(self.execute()['exhaustiveness'], 32)
            store.load_manifest()  # Every artifact hash still matches.
        self.assertFalse((self.run_root / parameters.RUNTIME_RELATIVE).exists())
        self.assertEqual(len(store.load_manifest().artifacts['inputs']), 1)

    def test_edit_legacy_run_then_branch_preserves_original_artifacts(self):
        store = self.setup_run(completed=True, legacy=True)
        before = (self.run_root / parameters.RUNTIME_RELATIVE).read_bytes()
        result = self.app.save_run_parameters({'values': {'exhaustiveness': 32}}, self.run_root)
        self.assertEqual(result['rerun_required'], 'docking')
        store.load_manifest()
        self.assertEqual((self.run_root / parameters.RUNTIME_RELATIVE).read_bytes(), before)
        with self.assertRaisesRegex(RuntimeError, 'docking'):
            self.app.start_workflow({}, self.run_root)
        branch = self.app.rerun_from('docking', self.run_root)
        root = self.app.resolve_run(branch['run_key'])
        self.assertEqual(self.execute(root)['exhaustiveness'], 32)
        self.assertEqual(parameters.runtime_settings(root)['exhaustiveness'], 32)
        self.assertEqual(parameters.runtime_settings(self.run_root)['exhaustiveness'], 8)
        store.load_manifest()
        self.assertEqual(len(ArtifactStore(root).load_manifest().artifacts['inputs']), 2)

    def test_seed_changes_require_ligand_rerun(self):
        self.setup_run(completed=True)
        result = self.app.save_run_parameters({'values': {'seed': 33}}, self.run_root)
        self.assertEqual(result['affects_from'], 'ligand')
        wrong = self.app.rerun_from('docking', self.run_root)
        with self.assertRaisesRegex(RuntimeError, 'ligand'):
            self.app.start_workflow({}, self.app.resolve_run(wrong['run_key']))
        right = self.app.rerun_from('ligand', self.run_root)
        self.assertEqual(self.execute(self.app.resolve_run(right['run_key']))['seed'], 33)

    def test_cli_explicit_defaults_win_and_manual_box_is_preserved(self):
        self.setup_run(legacy=True, exhaustiveness=64, center=[1, 2, 3], size=[20, 22, 24])
        self.app.save_run_parameters({'values': {'exhaustiveness': 32}}, self.run_root)
        settings = self.execute(None, '--exhaustiveness=8')
        self.assertEqual(settings['exhaustiveness'], 8)
        self.assertEqual(settings['center'], (1, 2, 3))
        self.assertEqual(settings['size'], (20, 22, 24))
        self.assertEqual(self.execute()['exhaustiveness'], 8)
        ArtifactStore(self.run_root).load_manifest()

    def test_partial_save_keeps_other_values_and_records_before_after(self):
        self.setup_run(exhaustiveness=64)
        result = self.app.save_run_parameters({'values': {'receptor_ph': 6.5}}, self.run_root)
        self.assertEqual(result['values']['exhaustiveness'], 64)
        self.assertEqual(result['affects_from'], 'receptor')
        events = self.app.events_for(self.run_root).read_since(0)
        update = next(item for item in events if item['type'] == 'parameters_updated')
        self.assertEqual(update['previous'], {'receptor_ph': 7.4})
        self.assertEqual(update['values'], {'receptor_ph': 6.5})

    def test_save_rejected_when_external_cli_holds_run_lock(self):
        with (self.run_root / '.run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'another process'):
                self.app.save_run_parameters({'values': {'seed': 2}}, self.run_root)
        self.assertFalse((self.run_root / parameters.SETTINGS_RELATIVE).exists())

    def test_invalid_numbers_never_write_settings(self):
        for key, value in [('receptor_ph', 'nan'), ('p2rank_box_size', float('inf')),
                           ('exhaustiveness', 1.5), ('seed', True), ('workers', 'Infinity'),
                           ('keep_receptor_water', 'typo')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.app.save_run_parameters({'values': {key: value}}, self.run_root)
        self.assertFalse((self.run_root / parameters.SETTINGS_RELATIVE).exists())

    def test_loading_invalid_saved_values_applies_range_checks(self):
        (self.run_root / parameters.RUNTIME_RELATIVE).write_text(json.dumps(
            {'receptor_ph': 99, 'seed': -1, 'exhaustiveness': 64, 'p2rank_box_size': 'nan'}))
        loaded = parameters.load(self.run_root)
        self.assertEqual(loaded['exhaustiveness'], 64)
        for key in ('receptor_ph', 'seed', 'p2rank_box_size'):
            self.assertEqual(loaded[key], parameters.defaults()[key])

    def test_receptor_rerun_invalidates_approval_of_old_chemistry(self):
        store = self.setup_run(completed=True)
        state = store.load_manifest()
        state.approvals = [{'stage': 'receptor', 'approved': True},
                           {'stage': 'docking_box', 'approved': True}]
        state.overrides = {'docking_box': {'approved': True, 'pocket_rank': 1}}
        store.save_manifest(state)
        self.app.save_run_parameters({'values': {'receptor_ph': 6.0}}, self.run_root)
        branch = self.app.rerun_from('receptor', self.run_root)
        child = ArtifactStore(self.app.resolve_run(branch['run_key'])).load_manifest()
        self.assertTrue(all(not row['approved'] for row in child.approvals))
        self.assertNotIn('docking_box', child.overrides)
        self.assertTrue(all(row['approved'] for row in store.load_manifest().approvals))

"""Model connection persistence and private-file access boundaries."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drugstudio.web.server import WebApplication


class ModelConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            'OPENAI_API_KEY': 'environment-key',
            'OPENAI_BASE_URL': 'https://env.example/v1',
            'DOCK_AGENT_MODEL': 'environment-model',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = self.make_app()
        self.settings = {'base_url': 'https://saved.example/v1/',
                         'model': 'saved-model', 'api_key': 'test-private-key'}

    def make_app(self):
        app = WebApplication(None, self.root, self.root, self.root)
        self.addCleanup(app.close)
        return app

    def test_save_restart_and_blank_key_preserve(self):
        public = self.app.update_model_config(self.settings)
        self.assertTrue(public['api_key_configured'])
        self.assertNotIn('api_key', public)
        self.assertNotIn(self.settings['api_key'], json.dumps(public))
        saved = self.app.model_config_path
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        restarted = self.make_app()
        self.assertEqual(restarted.model_config(), public)
        self.assertEqual(restarted._llm_config['api_key'], self.settings['api_key'])
        restarted.update_model_config(dict(self.settings, api_key='', model='next-model'))
        again = self.make_app()
        self.assertEqual(again._llm_config['api_key'], self.settings['api_key'])
        self.assertEqual(again.model_config()['model'], 'next-model')
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_clear_key_survives_restart_and_overrides_environment(self):
        self.app.update_model_config(self.settings)
        self.app.update_model_config(dict(self.settings, api_key='', clear_api_key=True))
        restarted = self.make_app()
        self.assertFalse(restarted.model_config()['api_key_configured'])
        self.assertEqual(restarted._llm_config['api_key'], '')

    def test_blank_key_saves_environment_default(self):
        self.app.update_model_config(dict(self.settings, api_key=''))
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(self.make_app()._llm_config['api_key'], 'environment-key')

    def test_chat_and_workflow_models_are_saved_independently(self):
        public = self.app.update_model_config({
            'base_url': 'https://saved.example/v1',
            'model': 'workflow-model',
            'chat_model': 'chat-model',
            'workflow_model': 'workflow-model',
            'api_key': 'test-private-key',
        })
        self.assertEqual(public['chat_model'], 'chat-model')
        self.assertEqual(public['workflow_model'], 'workflow-model')
        restarted = self.make_app()
        self.assertEqual(restarted.model_config()['chat_model'], 'chat-model')
        self.assertEqual(restarted.model_config()['workflow_model'], 'workflow-model')

    def test_legacy_single_model_setting_migrates_to_both_roles(self):
        self.app.model_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.app.model_config_path.write_text(json.dumps({
            'base_url': 'https://legacy.example/v1',
            'model': 'legacy-model',
            'api_key': 'legacy-key',
        }), encoding='utf-8')
        restarted = self.make_app()
        self.assertEqual(restarted.model_config()['chat_model'], 'legacy-model')
        self.assertEqual(restarted.model_config()['workflow_model'], 'legacy-model')

    def test_failed_write_does_not_replace_live_or_saved_config(self):
        self.app.update_model_config(self.settings)
        before = self.app.model_config_path.read_bytes()
        with patch('drugstudio.workbench.os.replace', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(ValueError, 'cannot save model connection'):
                self.app.update_model_config(dict(self.settings, api_key='replacement'))
        self.assertEqual(self.app.model_config_path.read_bytes(), before)
        self.assertEqual(self.app._llm_config['api_key'], self.settings['api_key'])
        self.assertFalse(list(self.app.workbench_root.glob('.model-connection*')))

    def test_private_settings_cannot_be_browsed_or_downloaded(self):
        self.app.update_model_config(self.settings)
        relative = self.app.model_config_path.relative_to(self.root).as_posix()
        self.assertNotIn('.workbench', [e['name'] for e in self.app.list_project_files()['entries']])
        for operation in (
            lambda: self.app.read_project_file(relative),
            lambda: self.app.write_project_file(relative, '{}'),
            lambda: self.app.list_project_files('.workbench'),
            lambda: self.app.file_path(relative),
        ):
            with self.assertRaisesRegex(ValueError, 'private server files'):
                operation()
        (self.root / 'linked-settings.json').symlink_to(self.app.model_config_path)
        with self.assertRaisesRegex(ValueError, 'private server files'):
            self.app.read_project_file('linked-settings.json')

    def test_corrupt_saved_config_fails_without_exposing_contents(self):
        self.app.model_config_path.write_text('{"api_key": "do-not-print"')
        with self.assertRaisesRegex(ValueError, '^cannot load saved model connection settings$'):
            self.make_app()

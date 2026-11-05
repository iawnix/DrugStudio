"""Real browser checks for editing, persistence and cross-run request races."""
import json
from urllib.parse import urlparse

import test_review_ui as harness
from drugstudio import parameters


class StageParametersPanel(harness.BrowserHarness):
    def setUp(self):
        super().setUp()
        self.open_workflow()
        self.open_stage('docking')

    def open_workflow(self):
        if not self.page.locator('#conversation-workflow').get_attribute('open') == '':
            self.page.click('#conversation-workflow summary')

    def open_stage(self, stage):
        self.page.click(f'[data-workflow-stage="{stage}"] [data-stage-action="params"]')
        self.page.wait_for_function("document.querySelector('#stage-parameters-fields').textContent !== '参数尚未载入。'")

    def save(self, status=200):
        with self.page.expect_response(lambda r: urlparse(r.url).path == '/api/parameters'
                                        and r.request.method == 'POST') as pending:
            self.page.click('#save-parameters')
        self.assertEqual(pending.value.status, status)
        self.page.wait_for_function("!document.querySelector('#save-parameters').disabled")

    def saved(self, root=None):
        return json.loads(((root or self.run_root) / parameters.SETTINGS_RELATIVE).read_text())

    def test_each_stage_shows_only_its_own_parameters(self):
        self.assertTrue(self.page.is_visible('#param-exhaustiveness'))
        self.assertEqual(self.page.locator('#param-receptor_ph').count(), 0)
        self.open_stage('receptor')
        self.assertTrue(self.page.is_visible('#param-receptor_ph'))
        self.assertEqual(self.page.locator('#param-exhaustiveness').count(), 0)

    def test_a_stage_without_parameters_says_so(self):
        self.open_stage('structure')
        self.assertIn('没有可调参数', self.page.inner_text('#stage-parameters-fields'))

    def test_editing_warns_which_stage_must_be_rerun(self):
        self.page.fill('#param-exhaustiveness', '32')
        self.assertIn('分子对接', self.page.inner_text('#stage-parameters-note'))
        self.open_stage('ligand')
        self.page.fill('#param-seed', '4242')
        self.assertIn('小分子准备', self.page.inner_text('#stage-parameters-note'))

    def test_saving_writes_editable_settings_not_runtime_artifact(self):
        self.page.fill('#param-exhaustiveness', '32')
        self.save()
        self.assertEqual(self.saved()['exhaustiveness'], 32)
        self.assertFalse((self.run_root / parameters.RUNTIME_RELATIVE).exists())

    def test_a_checkbox_saves_as_a_boolean(self):
        self.open_stage('ligand')
        self.page.check('#param-optimize_xtb')
        self.save()
        self.assertIs(self.saved()['optimize_xtb'], True)

    def test_a_residue_list_saves_as_a_list(self):
        self.open_stage('receptor')
        self.page.fill('#param-keep_receptor_residue', 'ZN\nMG')
        self.save()
        self.assertEqual(self.saved()['keep_receptor_residue'], ['ZN', 'MG'])

    def test_out_of_range_values_are_refused_with_a_reason(self):
        self.open_stage('receptor')
        self.page.fill('#param-receptor_ph', '99')
        self.save(400)
        self.assertIn('pH', self.page.inner_text('#toast'))
        self.assertFalse((self.run_root / parameters.SETTINGS_RELATIVE).exists())
        # This negative test expects the browser's HTTP 400 console message.
        self.errors[:] = [e for e in self.errors if '400 (Bad Request)' not in e]

    def test_saved_values_come_back_on_reload(self):
        self.page.fill('#param-exhaustiveness', '64')
        self.save()
        self.page.reload(wait_until='domcontentloaded')
        self.open_workflow()
        self.open_stage('docking')
        self.page.wait_for_function("document.querySelector('#param-exhaustiveness')?.value === '64'")

    def test_a_real_click_on_a_stage_node_selects_it(self):
        self.open_stage('structure')
        self.page.click('[data-workflow-stage="docking"]')
        self.assertEqual(self.page.inner_text('#workflow-node-title'), '分子对接')
        self.assertTrue(self.page.is_visible('#param-exhaustiveness'))

    def test_dragging_the_canvas_still_pans_and_does_not_select(self):
        self.open_stage('structure')
        title = self.page.inner_text('#workflow-node-title')
        node = self.page.locator('[data-workflow-stage="docking"]')
        node.hover()  # Wait for stable, visible coordinates before raw mouse events.
        start = node.bounding_box()
        before = self.page.eval_on_selector('#workflow-world', 'el => el.style.transform')
        self.page.mouse.move(start['x'] + start['width'] / 2, start['y'] + start['height'] / 2)
        self.page.mouse.down()
        self.page.mouse.move(start['x'] + 160, start['y'] + 40, steps=8)
        self.page.mouse.up()
        after = self.page.eval_on_selector('#workflow-world', 'el => el.style.transform')
        self.assertNotEqual(before, after)
        self.assertEqual(self.page.inner_text('#workflow-node-title'), title)

    def set_stage_status(self, stage, status):
        path = self.run_root / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.setdefault("stage_results", {})[stage] = {"status": status}
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def refresh(self, times=1):
        for _ in range(times):
            self.page.evaluate("document.querySelector('#refresh-button').click()")
            self.page.wait_for_timeout(350)

    def test_the_graph_follows_the_run_until_the_user_picks_a_node(self):
        self.set_stage_status("docking", "running")
        self.refresh()
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "分子对接")

    def test_a_manual_pick_survives_incoming_events(self):
        self.set_stage_status("docking", "running")
        self.refresh()
        self.page.click('[data-workflow-stage="receptor"]')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "受体准备")
        self.refresh(times=4)
        self.assertEqual(self.page.inner_text("#workflow-node-title"), "受体准备")

    def test_updates_patch_the_graph_instead_of_rebuilding_it(self):
        self.page.evaluate("document.querySelector('[data-workflow-stage=\"docking\"]').dataset.probe = 'kept'")
        self.page.focus('[data-workflow-stage="docking"]')
        for _ in range(3):
            self.page.evaluate("document.querySelector('#refresh-button').click()")
            self.page.wait_for_timeout(250)
        self.assertEqual(self.page.get_attribute('[data-workflow-stage="docking"]', "data-probe"), "kept")
        self.assertEqual(self.page.evaluate("document.activeElement.dataset.workflowStage"), "docking")

    def test_nothing_selected_says_so_rather_than_claiming_no_parameters(self):
        # Reload without selecting a node; the parameter panel must distinguish
        # this from a stage that simply has no tunable parameters.
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_timeout(900)
        self.open_workflow()
        self.assertIn("选择一个阶段", self.page.inner_text("#stage-parameters-fields"))

    def test_reset_restores_the_defaults_for_that_stage(self):
        self.page.fill('#param-exhaustiveness', '64')
        self.page.click('#reset-parameters')
        self.assertEqual(self.page.input_value('#param-exhaustiveness'), '8')

    def second_run(self):
        root = self.run_root.parent / 'r2'
        harness.build_run(root)
        self.app.save_run_parameters({'values': {'exhaustiveness': 64}}, root)
        self.page.click('#refresh-button')
        self.page.wait_for_function("document.querySelector('#run-selector option[value=\"proj/runs/r2\"]')")
        return root

    def hold_next_parameter_response(self, method):
        # Delay only delivery to the UI. The real HTTP server still handles
        # the request, including writes, so persistence remains observable.
        self.page.evaluate('''method => {
          const original = window.fetch;
          window.__held = false;
          window.fetch = async (...args) => {
            const hit = String(args[0]).includes('/api/parameters') &&
              (args[1]?.method || 'GET') === method;
            if (hit) window.fetch = original;
            const response = await original(...args);
            if (!hit) return response;
            return new Promise(resolve => {
              window.__release = () => resolve(response);
              window.__held = true;
            });
          };
        }''', method)

    def select_second_run(self):
        self.page.select_option('#run-selector', 'proj/runs/r2')
        self.page.wait_for_function("document.querySelector('#param-exhaustiveness')?.value === '64'")

    def release_response(self):
        self.page.evaluate('''async () => {
          window.__release();
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }''')

    def test_late_get_cannot_replace_another_runs_parameters(self):
        self.second_run()
        self.hold_next_parameter_response('GET')
        self.page.click('#refresh-button')
        self.page.wait_for_function('window.__held')
        self.select_second_run()
        self.release_response()
        self.assertEqual(self.page.input_value('#param-exhaustiveness'), '64')
        self.page.fill('#param-exhaustiveness', '96')
        self.save()
        self.assertFalse((self.run_root / parameters.SETTINGS_RELATIVE).exists())
        self.assertEqual(self.saved(self.run_root.parent / 'r2')['exhaustiveness'], 96)

    def test_late_save_response_cannot_replace_another_runs_parameters(self):
        root = self.second_run()
        self.hold_next_parameter_response('POST')
        self.page.fill('#param-exhaustiveness', '32')
        self.page.click('#save-parameters')
        self.page.wait_for_function('window.__held')
        self.select_second_run()
        self.release_response()
        self.assertEqual(self.page.input_value('#param-exhaustiveness'), '64')
        self.assertEqual(self.saved()['exhaustiveness'], 32)
        self.assertEqual(self.saved(root)['exhaustiveness'], 64)

    def test_empty_project_has_no_parameter_requests_or_save_action(self):
        requests = []
        self.page.on('request', lambda r: requests.append(r.url) if '/api/parameters' in r.url else None)
        with self.page.expect_response(lambda r: urlparse(r.url).path == '/api/state'):
            self.page.select_option('#project-selector', 'empty')
        self.page.wait_for_function("document.querySelector('#stage-parameters-fields').textContent.includes('暂无运行')")
        self.assertTrue(self.page.locator('#stage-parameters-actions').is_hidden())
        self.assertEqual(requests, [])

    def test_refresh_retains_an_unsaved_residue_list(self):
        self.open_stage('receptor')
        self.page.fill('#param-keep_receptor_residue', 'ZN\nMG')
        with self.page.expect_response(lambda r: urlparse(r.url).path == '/api/parameters'):
            self.page.click('#refresh-button')
        self.page.wait_for_function("document.querySelector('#param-keep_receptor_residue')?.value === 'ZN\\nMG'")
        self.save()
        self.assertEqual(self.saved()['keep_receptor_residue'], ['ZN', 'MG'])

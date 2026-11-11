"""Presentation preferences must not reset research state or unsaved work."""
import test_review_ui as harness


class PresentationPreferences(harness.BrowserHarness):
    def switch_language(self):
        self.page.click('#language-toggle')

    def test_late_module_response_preserves_unsaved_fields(self):
        # Deliver the startup GET after the user starts editing. A fast local
        # response otherwise hides this race between defaults and form drafts.
        self.page.add_init_script('''(() => {
          const original = window.fetch;
          window.fetch = async (...args) => {
            const response = await original(...args);
            if (!String(args[0]).includes('/api/module-config')) return response;
            return new Promise(resolve => {
              window.__releaseModuleConfig = () => resolve(response);
              window.__moduleConfigHeld = true;
            });
          };
        })();''')
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_function('window.__moduleConfigHeld')
        self.page.click('[data-view="settings"]')
        field = self.page.locator('[data-module-key="vina_binary"]')
        checkbox = self.page.locator('[data-module-key="af3_terms_acknowledged"]')
        field.fill('/tmp/unsaved-vina')
        checkbox.check()
        self.page.evaluate('''async () => {
          window.__releaseModuleConfig();
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }''')
        self.assertEqual(field.input_value(), '/tmp/unsaved-vina')
        self.assertTrue(checkbox.is_checked())
        self.switch_language()
        self.assertEqual(field.input_value(), '/tmp/unsaved-vina')
        self.assertTrue(checkbox.is_checked())

    def test_theme_persistence_and_system_changes(self):
        self.page.select_option('#theme-selector', 'system')
        self.page.emulate_media(color_scheme='dark')
        self.page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
        self.page.emulate_media(color_scheme='light')
        self.page.wait_for_function("document.documentElement.dataset.theme === 'light'")
        self.page.select_option('#theme-selector', 'dark')
        self.page.emulate_media(color_scheme='light')
        self.assertEqual(self.page.get_attribute('html', 'data-theme'), 'dark')
        self.switch_language()
        self.page.reload(wait_until='domcontentloaded')
        self.assertEqual(self.page.get_attribute('html', 'lang'), 'en')
        self.assertEqual(self.page.get_attribute('html', 'data-theme'), 'dark')
        self.assertEqual(self.page.input_value('#theme-selector'), 'dark')
        self.assertEqual(self.page.inner_text('.brand-sub'), 'Drug design workstation')

    def test_locale_updates_nodes_and_preserves_pending_forms(self):
        self.page.fill('#chat-input', '不要更改我的研究问题')
        self.page.click('#conversation-workflow summary')
        self.page.click('[data-workflow-stage="docking"] [data-stage-action="params"]')
        self.page.fill('#param-exhaustiveness', '23')
        run = self.page.input_value('#run-selector')
        self.switch_language()
        self.assertEqual(self.page.inner_text('#workflow-node-title'), 'Docking')
        self.assertEqual(self.page.inner_text('[data-workflow-stage="receptor"] strong'), 'Receptor preparation')
        self.assertEqual(self.page.input_value('#param-exhaustiveness'), '23')
        self.assertEqual(self.page.input_value('#chat-input'), '不要更改我的研究问题')
        self.assertEqual(self.page.input_value('#run-selector'), run)
        self.switch_language()
        self.assertEqual(self.page.inner_text('#workflow-node-title'), '分子对接')
        self.assertEqual(self.page.inner_text('.brand-sub'), '药物设计工作站')
        self.assertEqual(self.page.input_value('#param-exhaustiveness'), '23')

    def test_module_and_model_drafts_survive_locale_switch(self):
        config_before = self.app.model_config()
        self.page.click('[data-view="settings"]')
        field = self.page.locator('[data-module-key="vina_binary"]')
        field.fill('/tmp/unsaved-vina')
        self.page.locator('[data-module-key="af3_terms_acknowledged"]').check()
        self.switch_language()
        self.assertEqual(field.input_value(), '/tmp/unsaved-vina')
        self.assertTrue(self.page.locator('[data-module-key="af3_terms_acknowledged"]').is_checked())
        self.assertEqual(self.page.inner_text('#view-settings h1'), 'Module settings')
        self.assertIn('Vina executable', self.page.inner_text('#module-settings-groups'))
        self.assertTrue(self.page.is_visible('#view-project'))
        self.page.click('#view-settings [data-close-drawer]')
        self.page.click('#open-model-settings')
        self.page.fill('#model-base-url', 'https://draft.example/v1')
        self.page.fill('#model-api-key', 'unsaved-test-value')
        # A modal intentionally blocks the topbar. Close it without saving,
        # change locale, then reopen and verify both draft values remain.
        self.page.click('[data-close-model-settings]')
        self.switch_language()
        self.page.click('#open-model-settings')
        self.assertEqual(self.page.input_value('#model-base-url'), 'https://draft.example/v1')
        self.assertEqual(self.page.input_value('#model-api-key'), 'unsaved-test-value')
        self.assertEqual(self.app.model_config(), config_before)

    def test_language_does_not_translate_conversation_content(self):
        chat = self.app.list_chats('.')['chats'][0]
        messages = [{'role': 'user', 'content': '结构获取：保留我的原文'},
                    {'role': 'assistant', 'content': '这是科学记录，不是界面标签。'}]
        self.app.save_chat('.', chat['chat_id'], messages)
        self.page.reload(wait_until='domcontentloaded')
        self.page.locator('.chat-content').first.wait_for()
        before = self.page.locator('.chat-content').all_text_contents()
        self.switch_language()
        self.assertEqual(self.page.locator('.chat-content').all_text_contents(), before)
        self.switch_language()
        self.assertEqual(self.page.locator('.chat-content').all_text_contents(), before)

    def test_mobile_drawers_and_light_surfaces_fit(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.select_option('#theme-selector', 'light')
        self.switch_language()
        self.page.click('[data-view="results"]')
        box = self.page.locator('#view-results').bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 390)
        self.assertLessEqual(box['y'] + box['height'], 844)
        self.page.click('#view-results [data-close-drawer]')
        self.assertTrue(self.page.is_visible('#chat-input'))
        self.assertEqual(self.page.eval_on_selector('.chat-composer-shell', 'el => getComputedStyle(el).backgroundColor'), 'rgb(255, 255, 255)')

    def test_theme_contrast_in_model_dialog_and_selected_file(self):
        # Regression: dark mode inherited a white URL field and white file
        # selection from the old light-only component rules.
        filename = 'theme-preview.py'
        (self.run_root.parent.parent / filename).write_text('print("preview")\n')
        self.page.click('[data-view="files"]')
        # Select through the real file-open path. Injecting a CSS class on a
        # directory loses selection when the asynchronous listing refreshes.
        entry = self.page.locator(f'.file-entry[data-file-path="{filename}"]')
        entry.click()
        selected = self.page.locator(f'.file-entry.selected[data-file-path="{filename}"]')
        selected.wait_for()
        self.assertEqual(self.page.inner_text('#editor-file-label'), filename)
        self.page.click('#view-files [data-close-drawer]')
        for theme in ('light', 'dark'):
            self.page.select_option('#theme-selector', theme)
            self.page.click('#open-model-settings')
            self.assert_readable('#model-base-url', theme)
            self.page.click('[data-close-model-settings]')
            self.page.click('[data-view="files"]')
            selected.hover()
            self.assert_readable('.file-entry.selected', theme)
            self.page.click('#view-files [data-close-drawer]')
            self.page.locator('.chat-list-row').first.hover()
            self.assert_readable('.chat-list-item.active', theme)
            self.page.click('[data-view="results"]')
            for i in range(4):
                self.assert_readable(f'.metric:nth-child({i + 1}) .metric-icon', theme, minimum=3)
            self.page.click('#view-results [data-close-drawer]')

    def assert_readable(self, selector, theme, minimum=4.5):
        result = self.page.locator(selector).first.evaluate('''el => {
          const style = getComputedStyle(el);
          const ctx = document.createElement('canvas').getContext('2d');
          const rgb = color => {
            ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1);
            return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3);
          };
          const luminance = c => c.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
            .reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
          const fg = luminance(rgb(style.color)), bg = luminance(rgb(style.backgroundColor));
          return {contrast: (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05), bg};
        }''')
        self.assertGreaterEqual(result['contrast'], minimum, f'{theme}: {selector}: {result}')
        if theme == 'dark':
            self.assertLess(result['bg'], .15, f'Unexpected light surface: {selector}')
        else:
            self.assertGreater(result['bg'], .7, f'Unexpected dark surface: {selector}')

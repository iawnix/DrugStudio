"""Real interaction checks for local icons, async controls and interruptible motion."""
from test_review_ui import BrowserHarness


class WorkbenchControls(BrowserHarness):
    def test_icons_work_offline_and_refresh_preserves_existing_svg(self):
        self.page.wait_for_selector('#new-chat svg[data-icon="square-pen"]')
        self.page.evaluate('window.savedIcon = document.querySelector("#new-chat svg")')
        self.assertEqual(self.page.locator('i[data-icon-pending], i[data-lucide]').count(),0)
        self.page.click('#refresh-button')
        self.page.wait_for_function('!document.querySelector("#refresh-button").hasAttribute("aria-busy")')
        self.assertTrue(self.page.evaluate('window.savedIcon === document.querySelector("#new-chat svg")'))
        self.assertEqual(self.page.get_attribute('#new-chat svg','aria-hidden'),'true')
        self.page.click('[data-view="files"]')
        self.page.wait_for_selector('#file-tree svg')
        self.assertEqual(self.page.locator('i[data-icon-pending], i[data-lucide]').count(),0)

    def test_busy_button_keeps_geometry_and_does_not_send_duplicate_requests(self):
        self.page.click('[data-view="files"]')
        self.page.wait_for_selector('#file-tree .file-entry')
        pending=[]
        self.context.route('**/api/project/files?*',lambda route: pending.append(route))
        before = self.page.locator('#refresh-files').bounding_box()
        self.page.click('#refresh-files')
        self.page.wait_for_function('document.querySelector("#refresh-files").getAttribute("aria-busy") === "true"')
        self.page.evaluate('document.querySelector("#refresh-files").click()')
        self.assertEqual(len(pending),1)
        self.assertAlmostEqual(self.page.locator('#refresh-files').bounding_box()['width'],before['width'],delta=.5)
        pending[0].continue_()
        self.page.wait_for_function('!document.querySelector("#refresh-files").hasAttribute("aria-busy")')
        self.assertIsNone(self.page.get_attribute('#refresh-files','aria-disabled'))

    def test_rapid_drawer_switches_preserve_chat_and_escape_restores_focus(self):
        self.page.fill('#chat-input','保留我的草稿')
        self.page.click('[data-view="files"]')
        self.page.click('[data-view="results"]')
        self.assertTrue(self.page.evaluate('document.querySelector("#view-files").inert'))
        self.assertFalse(self.page.evaluate('document.querySelector("#view-results").inert'))
        self.page.locator('#view-results [data-close-drawer]').focus()
        self.page.keyboard.press('Escape')
        self.page.locator('#view-results').wait_for(state='hidden')
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.view'),'results')
        self.assertEqual(self.page.input_value('#chat-input'),'保留我的草稿')
        self.assertEqual(self.page.locator('.view.active:not(#view-project)').count(),0)
        self.assertEqual(self.page.get_attribute('[data-view="project"]','aria-pressed'),'true')

    def test_reduced_motion_has_no_ongoing_panel_or_button_animations(self):
        self.page.emulate_media(reduced_motion='reduce')
        self.page.click('[data-view="files"]')
        self.assertEqual(self.page.locator('#view-files').evaluate('node => node.getAnimations().length'),0)
        self.page.click('#view-files [data-close-drawer]')
        self.assertFalse(self.page.is_visible('#view-files'))
        self.page.click('#open-model-settings')
        self.assertEqual(self.page.locator('#model-settings-dialog').evaluate('node => node.getAnimations().length'),0)
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.is_visible('#model-settings-dialog'))

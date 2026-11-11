"""File opening and resizing must keep the conversation and editor mounted."""
import test_review_ui as harness


class WorkspaceInteraction(harness.BrowserHarness):
    def test_file_opens_in_place_and_remains_selected_after_refresh(self):
        root = self.run_root.parent.parent
        (root / 'note.txt').write_text('First file\n', encoding='utf-8')
        (root / 'second.txt').write_text('Second file\n', encoding='utf-8')
        self.page.fill('#chat-input', '保持草稿')
        self.page.click('[data-view="files"]')
        for filename, expected in [('note.txt', 'First file\n'), ('second.txt', 'Second file\n')]:
            self.page.click(f'[data-file-path="{filename}"]')
            self.page.wait_for_function('(text) => document.querySelector("#file-editor").value === text', arg=expected)
            self.assertTrue(self.page.is_visible('#view-files'))
            self.assertTrue(self.page.is_visible('#view-project'))
            self.assertEqual(self.page.input_value('#chat-input'), '保持草稿')
        self.page.click('#refresh-files')
        self.page.locator('[data-file-path="second.txt"].selected').wait_for()
        self.page.click('#view-files [data-close-drawer]')
        self.page.click('[data-view="files"]')
        self.page.locator('[data-file-path="second.txt"].selected').wait_for()
        self.assertEqual(self.page.input_value('#file-editor'), 'Second file\n')

    def test_sidebar_and_drawer_drag_persist_without_hiding_chat(self):
        self.page.fill('#chat-input', '未发送的内容')
        for side, target, delta in [('left', '.layout > .sidebar', 65),
                                    ('right', '.tool-sidebar', -35)]:
            before = self.page.locator(target).bounding_box()['width']
            handle = self.page.locator(f'[data-sidebar-resizer="{side}"]').bounding_box()
            x, y = handle['x'] + handle['width'] / 2, handle['y'] + 120
            self.page.mouse.move(x, y)
            self.page.mouse.down()
            self.page.mouse.move(x + delta, y, steps=8)
            self.page.mouse.up()
            self.assertGreater(self.page.locator(target).bounding_box()['width'], before + 20)
        self.page.click('[data-view="files"]')
        before = self.page.locator('#view-files').bounding_box()['width']
        handle = self.page.locator('[data-sidebar-resizer="drawer"]').bounding_box()
        x, y = handle['x'] + 3, handle['y'] + 100
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x - 90, y, steps=8)
        self.page.mouse.up()
        width = self.page.locator('#view-files').bounding_box()['width']
        self.assertGreater(width, before + 70)
        self.assertTrue(self.page.is_visible('#view-project'))
        self.assertEqual(self.page.input_value('#chat-input'), '未发送的内容')
        self.page.reload(wait_until='domcontentloaded')
        self.page.click('[data-view="files"]')
        self.assertAlmostEqual(self.page.locator('#view-files').bounding_box()['width'], width, delta=2)
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertFalse(self.page.locator('[data-sidebar-resizer="drawer"]').is_visible())
        box = self.page.locator('#view-files').bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 390)

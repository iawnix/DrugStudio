"""Actual local renderer assets, streamed HTTP replies and conversation isolation."""
import threading
import test_review_ui as harness


class ChatRendering(harness.BrowserHarness):
    def load_messages(self, messages):
        self.page.locator('.chat-list-item.active').wait_for()
        self.chat_id = self.page.get_attribute('.chat-list-item.active', 'data-chat-id')
        self.project_id = next(project for project in ('.', 'proj')
                               if any(c['chat_id'] == self.chat_id for c in self.app.list_chats(project)['chats']))
        self.app.save_chat(self.project_id, self.chat_id, messages)
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.workspaceReady === 'true'")
        self.page.locator('.chat-message').first.wait_for()

    def reply(self, source):
        self.load_messages([{'role': 'assistant', 'content': source}])

    def test_markdown_tables_formulas_and_highlight(self):
        self.reply(r'''## 分子评价

**注意**：行内公式 $K_d = e^{\Delta G/(RT)}$ 与 \(x^2 + y^2\)，化学式 $\ce{CO2 + H2O <=> H2CO3}$。

$$
\mathrm{RMSD}=\sqrt{\frac{1}{N}\sum_{i=1}^{N}\|x_i-y_i\|^2}
$$

\[
\begin{aligned} a &= b+c \\ d &= e-f \end{aligned}
\]

| 配体 | 分数 | 备注 |
| --- | ---: | --- |
| benzene | -5.2 | **待验证** |

1. 准备结构
   - 保留金属
2. 检查结果

> 预测不等于实验。

```python
for atom in atoms:
    print(atom.name)
```

[数据库](https://www.rcsb.org)
''')
        self.assertEqual(self.page.inner_text('.chat-content h2'), '分子评价')
        self.assertEqual(self.page.locator('.chat-content table tbody tr').count(), 1)
        self.assertGreaterEqual(self.page.locator('.chat-content .katex').count(), 4)
        self.assertEqual(self.page.locator('.chat-content .katex-error').count(), 0)
        self.assertGreater(self.page.locator('.chat-content math').count(), 0)
        self.assertGreater(self.page.locator('.chat-code .hljs-keyword').count(), 0)
        self.assertIn('    print(atom.name)', self.page.inner_text('.chat-code code'))
        self.assertEqual(self.page.get_attribute('.chat-content a', 'rel'), 'noopener noreferrer')
        self.assertEqual(self.page.locator('.chat-content ol ul li').count(), 1)

    def test_untrusted_markup_and_code_stay_inert(self):
        self.reply(r'''<img src=x onerror="window.chatXSS=1"><script>window.chatXSS=1</script>

[bad](javascript:alert(1))

$\href{javascript:alert(1)}{click}$

`$not_math$` and \$5 and $10, ordinary prices.

```html
<script>window.chatXSS=1</script>
$$ not a formula $$
```

$\frac{1}{$
''')
        self.assertIsNone(self.page.evaluate('window.chatXSS'))
        self.assertEqual(self.page.locator('.chat-content script,.chat-content [onerror],.chat-content a[href^="javascript:"]').count(), 0)
        self.assertEqual(self.page.locator('.chat-code .katex').count(), 0)
        self.assertIn('$$ not a formula $$', self.page.inner_text('.chat-code code'))
        self.assertIn('$5 and $10', self.page.inner_text('.chat-content'))
        self.assertGreater(self.page.locator('.katex-error').count(), 0)

    def test_wide_content_stays_inside_mobile_chat_and_both_themes(self):
        header = '| ' + ' | '.join(f'Column {i}' for i in range(12)) + ' |'
        separator = '| ' + ' | '.join('---' for _ in range(12)) + ' |'
        row = '| ' + ' | '.join('molecular result' for _ in range(12)) + ' |'
        self.reply(header + '\n' + separator + '\n' + row + '\n\n```python\n' + 'x = ' + '1 + ' * 150 + '0\n```\n\n$$\\sum_{i=1}^{n} x_i^2$$')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        for theme in ('dark', 'light'):
            self.page.select_option('#theme-selector', theme)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 391)
            for selector in ('.chat-table-scroll', '.chat-code pre'):
                self.assertTrue(self.page.eval_on_selector(selector, 'el => el.scrollWidth > el.clientWidth'))
                box = self.page.locator(selector).bounding_box()
                self.assertGreaterEqual(box['x'], 0)
                self.assertLessEqual(box['x'] + box['width'], 391)
            self.assertNotEqual(self.page.eval_on_selector('.chat-code', 'el => getComputedStyle(el).backgroundColor'), 'rgba(0, 0, 0, 0)')

    def test_copy_preserves_original_code_and_markdown(self):
        source = '## Result\n\n```python\nprint("α")\n    # indentation\n```\n\n$E=mc^2$'
        self.reply(source)
        self.page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async text => {window.copiedText = text;}}})")
        self.page.click('[data-copy-code]')
        self.assertEqual(self.page.evaluate('window.copiedText'), 'print("α")\n    # indentation\n')
        self.page.click('[data-copy-message]')
        self.assertEqual(self.page.evaluate('window.copiedText'), source)
        self.page.click('#language-toggle')
        self.assertEqual(self.page.inner_text('[data-copy-code]'), 'Copy code')
        self.assertEqual(self.page.inner_text('.chat-content h2'), 'Result')

    def slow_reply(self, *, fail=False):
        release = threading.Event()
        self.addCleanup(release.set)
        def chat(payload, *, project_root=None, run_root=None, emit=None):
            emit({'type': 'response_start'})
            emit({'type': 'delta', 'text': '**正在生成**\n\n$E='})
            if not release.wait(15):
                raise RuntimeError('test stream was not released')
            if fail:
                raise RuntimeError('fixture disconnected')
            emit({'type': 'delta', 'text': 'mc^2$'})
            return {'reply': '**正在生成**\n\n$E=mc^2$', 'actions': []}
        self.app.chat = chat
        return release

    def send(self):
        self.page.fill('#chat-input', '解释结果')
        self.page.click('#send-chat')
        self.page.locator('.chat-message.assistant').filter(has_text='正在生成').wait_for()

    def test_streaming_keeps_earlier_nodes_and_reading_position(self):
        self.load_messages([{'role': 'assistant', 'content': f'## Record {i}\n\n' + 'Scientific evidence. ' * 40} for i in range(14)])
        release = self.slow_reply()
        self.page.evaluate('window.firstMessage = document.querySelector(".chat-message")')
        self.send()
        self.page.eval_on_selector('#chat-messages', 'el => {el.scrollTop = 0;}')
        release.set()
        self.page.wait_for_function('!document.querySelector("#send-chat").disabled')
        self.page.locator('.chat-message.assistant').last.locator('.katex').wait_for()
        self.assertTrue(self.page.evaluate('window.firstMessage === document.querySelector(".chat-message")'))
        self.assertLess(self.page.eval_on_selector('#chat-messages', 'el => el.scrollTop'), 20)
        self.page.click('#chat-jump-latest')
        self.assertTrue(self.page.eval_on_selector('#chat-messages', 'el => el.scrollHeight-el.scrollTop-el.clientHeight < 80'))

    def test_switching_conversation_cannot_receive_another_stream(self):
        self.reply('Original conversation')
        release = self.slow_reply()
        self.send()
        self.page.click('#new-chat')
        self.page.locator('.chat-welcome').wait_for()
        other_id = self.page.get_attribute('.chat-list-item.active', 'data-chat-id')
        self.assertNotEqual(other_id, self.chat_id)
        release.set()
        self.page.wait_for_function('!document.querySelector("#send-chat").disabled')
        self.assertEqual(self.page.locator('.chat-message').count(), 0)
        self.page.click(f'[data-chat-id="{self.chat_id}"]')
        self.page.locator('.chat-content .katex').wait_for()
        self.page.reload(wait_until='domcontentloaded')
        self.page.locator('.chat-content .katex').wait_for()

    def test_interrupted_stream_preserves_partial_text(self):
        self.reply('Start')
        release = self.slow_reply(fail=True)
        self.send(); release.set()
        self.page.locator('.chat-message').filter(has_text='回复中断').wait_for()
        self.assertIn('正在生成', self.page.locator('.chat-message.assistant').last.inner_text())
        self.page.reload(wait_until='domcontentloaded')
        self.page.locator('.chat-message').filter(has_text='回复中断').wait_for()

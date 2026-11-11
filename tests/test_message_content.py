"""Content blocks and legacy assistant records must not leak transport metadata."""
import json
import tempfile
import unittest
from pathlib import Path

from drugstudio.web.server import WebApplication


class MessageContent(unittest.TestCase):
    def test_content_blocks_and_legacy_repr(self):
        blocks = [{'type': 'reasoning', 'text': 'private metadata'},
                  {'type': 'text', 'text': '你好！\n\n', 'annotations': [], 'id': 'msg_1', 'phase': 'final_answer'},
                  {'type': 'output_text', 'text': '项目 `run-002` 已完成。'},
                  {'type': 'image', 'url': 'not transcript text'}]
        expected = '你好！\n\n项目 `run-002` 已完成。'
        for value in (blocks, repr(blocks), json.dumps(blocks)):
            with self.subTest(value=value):
                self.assertEqual(WebApplication._message_text(value), expected)
        self.assertEqual(WebApplication._message_text([{'type': 'text', 'text': '[1, 2]'}]), '[1, 2]')

    def test_ordinary_text_is_not_parsed_as_content_blocks(self):
        for value in ('[1, 2]', '{"content": "scientific JSON"}', '["a", "b"]',
                      '[broken', '[{"type":"text", "text":__import__("os")}]',
                      '[{"type":"experiment", "text":"scientific data"}]'):
            with self.subTest(value=value):
                self.assertEqual(WebApplication._message_text(value), value)
        quoted = "[{'type': 'text', 'text': 'example posted by user'}]"
        self.assertEqual(WebApplication._message_text(quoted, recover_legacy=False), quoted)

    def test_legacy_store_is_read_without_rewriting_user_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = WebApplication(None, root, root, root)
            self.addCleanup(app.close)
            chat = app.create_chat('.')
            content = "[{'type': 'text', 'text': '你好', 'annotations': [], 'phase': 'final_answer'}]"
            store = {'.': [{**chat, 'messages': [{'role': 'user', 'content': content},
                                                {'role': 'assistant', 'content': content}]}]}
            app._write_chat_store(store)
            original = app.chat_store.read_bytes()
            messages = app.list_chats('.')['chats'][0]['messages']
            self.assertEqual(messages[0]['content'], content)
            self.assertEqual(messages[1]['content'], '你好')
            self.assertEqual(app.chat_store.read_bytes(), original)
            saved = app.save_chat('.', chat['chat_id'], messages)
            self.assertEqual(saved['messages'], messages)

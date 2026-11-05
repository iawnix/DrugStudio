"""Regressions for the incremental event-log reader.

The log is append-only and is written by the workflow subprocess while the
web server follows it, so these cover the reader's cursor behaviour as much
as its results: a full re-parse would pass every correctness check here and
still be the thing that made following a long run expensive.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from drugstudio.events import EventWriter


class EventLogReading(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.writer = EventWriter(self.root, "run-1")
        self.log = self.root / "logs" / "events.jsonl"

    def tearDown(self):
        self._directory.cleanup()

    def ids(self, events):
        return [event["event_id"] for event in events]

    def append_raw(self, text):
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def test_events_round_trip_with_monotonic_ids(self):
        for index in range(10):
            self.writer.emit("stage_started", stage=f"s{index}", status="running")
        self.assertEqual(self.ids(self.writer.read_since(0)), list(range(1, 11)))
        self.assertEqual(self.ids(self.writer.read_since(7)), [8, 9, 10])
        self.assertEqual(self.ids(self.writer.read_since(0, limit=3)), [8, 9, 10])

    def test_append_from_another_process_is_picked_up(self):
        self.writer.emit("run_started")
        self.append_raw(json.dumps({"event_id": 2, "type": "stage_completed", "run_id": "run-1"}) + "\n")
        self.assertEqual(self.ids(self.writer.read_since(1)), [2])
        # Our own next id must clear the externally written one, or two
        # writers would hand out the same event_id.
        self.assertEqual(self.writer.emit("stage_started")["event_id"], 3)

    def test_partial_line_is_held_back_until_complete(self):
        self.writer.emit("run_started")
        payload = json.dumps({"event_id": 2, "type": "tool_started", "run_id": "run-1"})
        self.append_raw(payload[:20])
        self.assertEqual(self.writer.read_since(1), [],
                         "a line torn mid-write must not be parsed yet")
        self.append_raw(payload[20:] + "\n")
        self.assertEqual(self.ids(self.writer.read_since(1)), [2],
                         "the event must appear once its newline lands")

    def test_truncated_log_resets_the_cursor(self):
        for _ in range(5):
            self.writer.emit("stage_started")
        self.log.write_text(json.dumps({"event_id": 1, "type": "run_started"}) + "\n",
                            encoding="utf-8")
        self.assertEqual(self.ids(self.writer.read_since(0)), [1])

    def test_reading_past_the_tail_window_still_returns_everything(self):
        total = EventWriter.TAIL_WINDOW + 200
        for _ in range(total):
            self.writer.emit("stage_progress", stage="docking", status="running")
        deep = self.writer.read_since(0, limit=total * 2)
        self.assertEqual(len(deep), total)
        self.assertEqual(deep[0]["event_id"], 1)
        # The default limit keeps trimming to the most recent slice.
        self.assertEqual(len(self.writer.read_since(0)), 500)


class EventLogUsageTotals(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.writer = EventWriter(Path(self._directory.name), "run-1")

    def tearDown(self):
        self._directory.cleanup()

    def test_totals_match_an_independent_full_scan(self):
        for _ in range(50):
            self.writer.emit("model_finished", status="completed", model="gpt-x",
                             usage={"input_tokens": 10, "output_tokens": 5})
        for _ in range(5):
            self.writer.emit("model_finished", status="completed", model="gpt-y", usage={})
        self.writer.emit("model_finished", status="failed", model="gpt-x",
                         usage={"input_tokens": 999, "output_tokens": 999})

        usage = self.writer.usage_by_model()
        self.assertEqual(usage["gpt-x"], {"calls": 50, "input_tokens": 500,
                                          "output_tokens": 250, "reported_calls": 50})
        # A call that reported no usage still counts, but contributes no
        # tokens, which is what stops cost estimates from understating spend.
        self.assertEqual(usage["gpt-y"], {"calls": 5, "input_tokens": 0,
                                          "output_tokens": 0, "reported_calls": 0})

        scan = {}
        for line in (self.writer.path).read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("type") == "model_finished" and item.get("status") == "completed":
                row = scan.setdefault(item["model"], {"calls": 0, "input_tokens": 0})
                row["calls"] += 1
                row["input_tokens"] += (item.get("usage") or {}).get("input_tokens", 0)
        for model, row in scan.items():
            self.assertEqual(usage[model]["calls"], row["calls"])
            self.assertEqual(usage[model]["input_tokens"], row["input_tokens"])


class EventLogStaysCheapAsItGrows(unittest.TestCase):
    """Guard the property that made this reader worth rewriting.

    Thresholds are deliberately an order of magnitude above what the
    incremental reader needs, so this fails on a return to full re-parsing
    rather than on a slow machine.
    """

    EVENTS = 4000

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.writer = EventWriter(Path(self._directory.name), "run-1")
        for _ in range(self.EVENTS):
            self.writer.emit("model_finished", status="completed", model="m",
                             usage={"input_tokens": 10, "output_tokens": 5})

    def tearDown(self):
        self._directory.cleanup()

    def elapsed(self, call, repeats=200):
        start = time.perf_counter()
        for _ in range(repeats):
            call()
        return time.perf_counter() - start

    def test_following_the_tail_does_not_rescan_the_log(self):
        cursor = self.writer.read_since(0, limit=self.EVENTS * 2)[-1]["event_id"]
        # 200 polls is roughly 100 seconds of one SSE client at 0.5s.
        self.assertLess(self.elapsed(lambda: self.writer.read_since(cursor)), 0.5)

    def test_usage_totals_do_not_rescan_the_log(self):
        self.assertLess(self.elapsed(self.writer.usage_by_model), 0.5)

    def test_emitting_does_not_rescan_the_log(self):
        # This one used to be O(events) per call, so O(events squared) overall.
        self.assertLess(self.elapsed(lambda: self.writer.emit("stage_progress")), 1.0)


if __name__ == "__main__":
    unittest.main()

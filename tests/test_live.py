import copy
import json
import os
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from stepagent.core.adapters import IncrementalParser, parse_codex_records
from stepagent.core.graph_builder import build_execution_graph
from stepagent.source import JsonlTail, SessionCatalog
from stepagent.worker import Watcher


def record(kind, **payload):
    return dict(timestamp="2026-09-08T12:00:00Z", type=kind, payload=payload)


def line(value):
    return (json.dumps(value, ensure_ascii=False) + "\n").encode()


def scenario():
    return [
        record("session_meta", id="test-session", cwd="/test"),
        record("response_item", type="message", role="user", content=[dict(type="input_text", text="<environment_context>x</environment_context>")]),
        record("event_msg", type="task_started", turn_id="t1"),
        record("event_msg", type="user_message", message="Napraw błąd"),
        record("response_item", type="message", role="user", content=[dict(type="input_text", text="Napraw błąd")]),
        record("response_item", type="function_call", name="exec_command", call_id="call_a", arguments='{"cmd":"pytest"}'),
        record("response_item", type="function_call_output", call_id="call_a", output="Process exited with code 1\nOutput:\nassertion mismatch"),
        record("response_item", type="custom_tool_call", name="apply_patch", call_id="call_b", input="*** Begin Patch\n*** Update File: app.py\n@@\n-a\n+b\n*** End Patch"),
        record("response_item", type="custom_tool_call_output", call_id="call_b", output="Success. Updated app.py"),
        record("event_msg", type="task_complete", turn_id="t1"),
    ]


class TailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rollout.jsonl"
        self.path.touch()
        self.tail = JsonlTail(self.path)

    def test_partial_utf8_and_no_replay(self):
        data = line(record("event_msg", type="user_message", message="Zażółć 世界"))
        split = data.index("ż".encode()) + 1
        self.path.write_bytes(data[:split])
        self.assertEqual(self.tail.poll(), ([], False))
        with self.path.open("ab") as stream:
            stream.write(data[split:-1])
        self.assertEqual(self.tail.poll(), ([], False))
        with self.path.open("ab") as stream:
            stream.write(b"\n")
        records, reset = self.tail.poll()
        self.assertFalse(reset)
        self.assertEqual(records[0]["payload"]["message"], "Zażółć 世界")
        self.assertEqual(self.tail.poll(), ([], False))

    def test_truncate_rotate_and_missing_file(self):
        self.path.write_bytes(line(record("session_meta", id="old")) * 3)
        self.tail.poll()
        self.path.write_bytes(line(record("session_meta", id="new")))
        records, reset = self.tail.poll()
        self.assertTrue(reset)
        self.assertEqual(records[0]["payload"]["id"], "new")
        moved = self.path.with_suffix(".old")
        self.path.rename(moved)
        self.assertEqual(self.tail.poll()[0], [])
        self.assertIn("Oczekiwanie", self.tail.error)
        self.path.write_bytes(line(record("session_meta", id="rotated")))
        records, reset = self.tail.poll()
        self.assertTrue(reset)
        self.assertEqual(records[0]["payload"]["id"], "rotated")

    def test_corrupt_and_long_invalid_line_recovery(self):
        self.tail.chunk_size = 120
        self.path.write_bytes(b"broken\n[]\n" + b"x" * 700 + b"\n" + line({"type": "new_event"}))
        records = []
        while True:
            records.extend(self.tail.poll()[0])
            if self.tail.offset == self.tail.size:
                break
        self.assertEqual(records, [{"type": "new_event"}])
        self.assertEqual(self.tail.bad_lines, 3)


class ParserTests(unittest.TestCase):
    def test_distinct_output_item_id_still_correlates_by_call_id(self):
        doc = parse_codex_records([
            record("response_item", type="custom_tool_call", id="ctc_unique", name="functions.exec", call_id="shared", input="text(1)"),
            record("response_item", type="custom_tool_call_output", id="ctco_different", call_id="shared", output="1"),
        ])
        self.assertEqual(len(doc.interactions), 1)
        self.assertEqual(doc.interactions[0].result["output"], "1")
        self.assertEqual(doc.interactions[0].lifecycle.value, "completed")
        self.assertEqual(doc.interactions[0].metadata["correlation"]["basis"], "call_id")

    def test_identical_consecutive_user_inputs_are_not_mirror_copies(self):
        doc = parse_codex_records([record("event_msg", type="user_message", message="Powtórz") for _ in range(2)])
        self.assertEqual(len(doc.interactions), 2)
        self.assertEqual(doc.interactions[-1].conversation_turn_number, 2)

    def test_live_call_becomes_result_without_duplicate_and_context_is_not_turn(self):
        parser = IncrementalParser()
        rows = scenario()
        for row in rows[:6]:
            parser.feed(row)
        doc = parser.snapshot()
        call = next(i for i in doc.interactions if i.call_id == "call_a")
        stable_id = call.interaction_id
        self.assertEqual(call.lifecycle.value, "started")
        self.assertEqual(max(i.conversation_turn_number for i in doc.interactions), 1)
        for row in rows[6:]:
            parser.feed(row)
        doc = parser.snapshot()
        calls = [i for i in doc.interactions if i.call_id == "call_a"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].interaction_id, stable_id)
        self.assertEqual(calls[0].status, "error")
        self.assertEqual(calls[0].result["exit_code"], 1)
        patches = [i for i in doc.interactions if i.call_id == "call_b"]
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].lifecycle.value, "completed")
        self.assertEqual(len(patches[0].raw_record_ids), 2)
        self.assertIn("*** Begin Patch", patches[0].detail)
        self.assertEqual(len(doc.raw_records), len(rows))
        self.assertEqual(len([i for i in doc.interactions if i.metadata.get("conversation_message_classification") == "conversation_start"]), 1)

    def test_future_types_preserved_and_source_never_mutated(self):
        rows = scenario() + [record("future_rollout", type="future_event", content={"anything": [1, 2]})]
        before = copy.deepcopy(rows)
        doc = parse_codex_records(rows)
        self.assertEqual(rows, before)
        self.assertIn("future_rollout:", doc.metadata["unknown_types"])
        self.assertEqual(doc.interactions[-1].kind.value, "event_unknown")
        self.assertEqual(doc.raw_records[-1].record, rows[-1])

    def test_repeated_prompts_in_different_turns_and_interleaved_calls(self):
        parser = IncrementalParser()
        for n in range(2):
            parser.feed(record("event_msg", type="task_started", turn_id=str(n)))
            parser.feed(record("event_msg", type="user_message", message="Powtórz"))
        for cid in ["one", "two"]:
            parser.feed(record("response_item", type="function_call", name="exec_command", call_id=cid, arguments=cid))
        for cid in ["two", "one"]:
            parser.feed(record("response_item", type="function_call_output", call_id=cid, output=cid))
        doc = parser.snapshot()
        self.assertEqual(max(i.conversation_turn_number for i in doc.interactions), 2)
        for cid in ["one", "two"]:
            matches = [i for i in doc.interactions if i.call_id == cid]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].result["output"], cid)


class WorkerTests(unittest.TestCase):
    def test_live_append_snapshot_isolation_switch_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "rollout.jsonl"
            initial = b"".join(map(line, scenario()[:6]))
            path.write_bytes(initial)
            watcher = Watcher(root, .05)
            watcher.thread.start()
            watcher.select(path)
            try:
                first = self.wait_for(watcher, lambda u: len(u.get("doc").raw_records) == 6 if u.get("doc") else False)
                call = next(i for i in first["doc"].interactions if i.call_id == "call_a")
                before = time.monotonic()
                with path.open("ab") as stream:
                    stream.write(line(scenario()[6]))
                second = self.wait_for(watcher, lambda u: len(u.get("doc").raw_records) == 7 if u.get("doc") else False)
                self.assertLess(time.monotonic() - before, 2)
                self.assertEqual(call.lifecycle.value, "started", "published snapshots must not change")
                self.assertEqual(next(i for i in second["doc"].interactions if i.call_id == "call_a").lifecycle.value, "failed")
                self.assertEqual(path.read_bytes(), initial + line(scenario()[6]))
                other = root / "other.jsonl"
                other.write_bytes(line(record("session_meta", id="other")))
                watcher.select(other)
                changed = self.wait_for(watcher, lambda u: u.get("path") == other and u.get("doc") is not None)
                self.assertEqual(changed["doc"].session_id, "other")
            finally:
                watcher.stop.set()
                watcher.thread.join(3)

    @staticmethod
    def wait_for(watcher, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            update = watcher.updates.get(timeout=5)
            if predicate(update):
                return update
        raise AssertionError("No expected live update")

    def test_catalog_updates_titles_and_orders_by_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            root.mkdir()
            path = root / "a.jsonl"
            path.write_bytes(line(record("session_meta", id="abc", cwd="/work")))
            catalog = SessionCatalog(root)
            self.assertEqual(catalog.scan()[0].session_id, "abc")
            (root.parent / "session_index.jsonl").write_text(json.dumps(dict(id="abc", thread_name="Nazwa rozmowy")) + "\n")
            self.assertEqual(catalog.scan()[0].title, "Nazwa rozmowy")


class FakeScreen:
    def __init__(self, h, w):
        self.h, self.w = h, w
        self.lines = {}

    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        self.lines = {}

    def addstr(self, y, x, text, attr=0):
        assert 0 <= y < self.h and 0 <= x < self.w
        self.lines[y, x] = text

    def refresh(self):
        pass


class UITests(unittest.TestCase):
    def app(self):
        from stepagent.ui import TerminalApp
        app = TerminalApp(Path("/unused"))
        app.doc = parse_codex_records(scenario())
        app.graph = build_execution_graph(app.doc)
        app.version = 1
        app.path = Path("test.jsonl")
        app.screen = "timeline"
        app.rebuild()
        return app

    def test_responsive_layout_and_navigation(self):
        import curses
        app = self.app()
        for h, w in [(45, 160), (24, 80), (12, 42), (8, 20)]:
            app.win = FakeScreen(h, w)
            app.draw()
            app.key("d")
            app.draw()
            app.key("d")
            app.key("m")
            app.draw()
            app.key("m")
        app.win = FakeScreen(30, 120)
        app.key("g")
        self.assertFalse(app.follow)
        self.assertEqual(app.cursor, 0)
        app.key(curses.KEY_DOWN)
        self.assertEqual(app.cursor, 1)
        app.key("G")
        self.assertTrue(app.follow)
        self.assertEqual(app.cursor, len(app.items) - 1)
        app.key("/")
        for char in "pytest":
            app.key(char)
        app.key("\n")
        self.assertEqual(len(app.items), 1)
        app.key("\x1b")
        self.assertGreater(len(app.items), 1)

    def test_terminal_controls_are_neutralized(self):
        from stepagent.ui import clean, clip
        self.assertNotIn("\x1b", clean("\x1b[31mtext\x00"))
        self.assertEqual(clip("世界abc", 5), "世界a")


if __name__ == "__main__":
    unittest.main()

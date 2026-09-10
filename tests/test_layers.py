import curses
import json
import tempfile
import time
import unittest
from pathlib import Path

from stepagent.core.adapters import parse_codex_records
from stepagent.core.domain import Interaction, NodeKind
from stepagent.core.graph_builder import build_execution_graph
from stepagent.detail_document import DetailDocument, SECTIONS, field_rows
from stepagent.details import cells
from stepagent.diff import diff_rows
from stepagent.paging import PagedRows, content_spans, json_spans, wrapped_rows
from stepagent.source import JsonlTail
from stepagent.ui import TerminalApp
from test_live import FakeScreen, record, scenario


def plain(rows, width=100):
    return "\n".join("".join(span.text for span in row) for row in wrapped_rows(rows, width))


def app_fixture(rows=None):
    app = TerminalApp(Path("/unused"), initial=Path("example.jsonl"))
    app.doc = parse_codex_records(rows or scenario())
    app.graph = build_execution_graph(app.doc)
    app.version = 1
    app.win = FakeScreen(40, 150)
    app.rebuild()
    return app


class LayersTests(unittest.TestCase):
    def test_all_35_kinds_have_sections_and_all_layers_render(self):
        self.assertEqual(set(SECTIONS), {kind.value for kind in NodeKind})
        for kind in NodeKind:
            with self.subTest(kind=kind):
                app = app_fixture()
                item = Interaction(interaction_id="fixture", index=99, kind=kind, detail="Fixture content", conversation_turn_number=1)
                app.doc.interactions.append(item)
                document = DetailDocument(item.interaction_id, app.doc, None, app.path)
                for layer in range(5):
                    self.assertTrue(plain(document.rows(layer)))

    def test_fields_keep_all_versions_null_empty_and_json_pointer_escaping(self):
        rows = [record("event_msg", type="user_message", message="Start"),
                record("response_item", type="function_call", call_id="a", name="exec_command", arguments='{"cmd":"pwd"}',
                       extra={"a/b~c": [False, 0, None, "", [], {}]}, state="before"),
                record("response_item", type="function_call_output", call_id="a", output="ok", state="after")]
        app = app_fixture(rows)
        document = app.detail_context()
        text = plain(document.rows(1), 200)
        self.assertIn('"before"', text)
        self.assertIn('"after"', text)
        for value in ["/payload/extra/a~1b~0c/0", "[boolean]", "false", "[null]", "null", '""', "[]", "{}"]:
            self.assertIn(value, text)
        raw = plain(document.rows(3), 200)
        self.assertIn('"before"', raw)
        self.assertIn('"after"', raw)
        self.assertIn("NORMALIZACJA", text)

    def test_streaming_json_preserves_exact_values_and_controls(self):
        value = {"x/~": [False, 0, None, "", [], {}, 'a\n\t\x1b\u202e\u200d"世界' + "ż" * 5000]}
        encoded = "".join(span.text for span in json_spans(value))
        self.assertEqual(json.loads(encoded), value)
        self.assertNotIn("\x1b", encoded)
        self.assertNotIn("\u202e", encoded)
        self.assertNotIn("\u200d", encoded)

    def test_numbered_diff_uses_only_recorded_hunk_numbers(self):
        rows = list(diff_rows("@@ -10,2 +20,2 @@\n keep\n-old\n+new\n"))
        text = plain(rows)
        self.assertIn("numery linii ze źródła", text)
        self.assertIn("10 20 │  keep", text)
        self.assertIn("11    │- old", text)
        self.assertIn("   21 │+ new", text)
        spans = [span for row in rows for span in row]
        self.assertEqual(next(span.color for span in spans if span.text == "old"), 7)
        self.assertEqual(next(span.color for span in spans if span.text == "new"), 3)

    def test_questions_include_ids_options_and_recorded_answers(self):
        app = app_fixture([record("event_msg", type="user_message", message="Start"),
            record("event_msg", type="request_user_input", questions=[dict(id="target", title="Dokąd?",
                options=[dict(label="Local", description="Pełny opis opcji", recommended=False)])], answers={"target": "Local"})])
        text = plain(app.detail_context().rows())
        for expected in ("id: target", "Dokąd?", "Pełny opis opcji", "false", "ANSWERS", "Local"):
            self.assertIn(expected, text)

    def test_no_200k_cutoff_and_first_page_is_lazy(self):
        value = "界" * 220_000 + "TAIL_MARKER"
        started = time.monotonic()
        page = PagedRows([content_spans(value)], 79)
        page.ensure(30, budget_ms=50)
        self.assertLess(time.monotonic() - started, 1)
        self.assertLessEqual(len(page.rows), 30)
        self.assertFalse(page.complete)
        while not page.complete:
            page.ensure(len(page.rows) + 1000, budget_ms=100)
        recovered = "".join(span.text for row in page.rows for span in row)
        self.assertEqual(recovered, value)
        self.assertTrue(all(sum(cells(span.text) for span in row) <= 79 for row in page.rows))

    def test_large_valid_jsonl_over_16_mib_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.jsonl"
            payload = "x" * (17 * 1024 * 1024) + "LAST_BYTE"
            path.write_text(json.dumps({"type": "future", "payload": payload}) + "\n")
            tail = JsonlTail(path)
            records = []
            while tail.offset < path.stat().st_size:
                records.extend(tail.poll()[0])
            self.assertEqual(tail.bad_lines, 0)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["payload"], payload)

    def test_layers_and_steps_remember_independent_scroll(self):
        app = app_fixture()
        app.key("4")
        app.detail_scroll = 7
        app.key("2")
        self.assertEqual(app.detail_scroll, 0)
        app.detail_scroll = 11
        app.key("4")
        self.assertEqual(app.detail_scroll, 7)
        original = app.detail_target()
        app.key(curses.KEY_LEFT)
        app.key("k")
        app.detail_context()
        self.assertNotEqual(app.detail_target(), original)
        app.detail_scroll = 3
        app.key("j")
        app.detail_context()
        self.assertEqual(app.detail_target(), original)
        self.assertEqual(app.detail_scroll, 7)
        app.key("2")
        self.assertEqual(app.detail_scroll, 11)

    def test_live_snapshot_pinned_until_refresh_and_selection_not_lost(self):
        app = app_fixture(scenario()[:6])
        app.win = FakeScreen(24, 80)
        app.key("4")
        old = app.detail_context()
        target = app.detail_target()
        app.detail_scroll = 3
        new = parse_codex_records(scenario())
        app.watcher.updates.put(dict(path=app.path, doc=new, graph=build_execution_graph(new), version=2))
        app.receive()
        self.assertEqual(app.detail_target(), target)
        self.assertIs(app.detail_context(), old)
        self.assertNotIn('"output":', plain(old.rows(3)))
        self.assertEqual(app.detail_scroll, 3)
        app.draw()
        self.assertTrue(any("NOWE DANE" in line for line in app.win.lines.values()))
        app.key("u")
        self.assertIsNot(app.detail_context(), old)
        self.assertIn('"output":', plain(app.detail_context().rows(3)))
        self.assertEqual(app.detail_scroll, 3)
        app.key("G")
        self.assertTrue(app.follow)
        self.assertEqual(app.cursor, len(app.items) - 1)

    def test_graph_only_relations_and_back_restore_exact_context(self):
        app = app_fixture()
        app.key("3")
        document = app.detail_context()
        n = next(n for n, edge in enumerate(document.edges)
                 if document.nodes[document.other(edge)].interaction_index is None)
        app.relation_cursor = n
        app.detail_scroll = 5
        old_count = len(app.items)
        old_doc = app.detail_document
        app.key("\n")
        self.assertTrue(app.detail_context().graph_only)
        self.assertEqual(len(app.items), old_count)
        self.assertEqual(app.layer, 0)
        self.assertIn("Węzeł strukturalny", plain(app.detail_context().rows()))
        app.key("b")
        self.assertIs(app.detail_context(), old_doc)
        self.assertEqual(app.layer, 2)
        self.assertEqual(app.detail_scroll, 5)
        self.assertEqual(app.relation_cursor, n)

    def test_relation_metadata_confidence_direction_and_chronology(self):
        app = app_fixture()
        for item in app.items:
            document = DetailDocument(item.interaction_id, app.doc, app.graph, app.path)
            for n, edge in enumerate(document.edges):
                text = plain(document.rows(2, n), 200)
                self.assertIn("Pewność:", text)
                self.assertIn("detected_by", text)
                self.assertIn(edge.source, text)
                self.assertIn(edge.target, text)
                if edge.kind.value == "next":
                    self.assertIn("nie dowód przyczynowości", text)

    def test_all_layers_resize_and_session_reset(self):
        app = app_fixture()
        for layer in ("1", "2", "3", "4", "a"):
            app.key(layer)
            for h, w in ((45, 160), (24, 80), (12, 42)):
                app.win = FakeScreen(h, w)
                app.draw()
        app.select_session(Path("other.jsonl"))
        self.assertIsNone(app.detail_document)
        self.assertFalse(app.pages)
        self.assertFalse(app.back_stack)
        self.assertEqual(app.layer, 0)


if __name__ == "__main__":
    unittest.main()

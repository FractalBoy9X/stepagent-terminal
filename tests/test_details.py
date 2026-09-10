import curses
import unittest
from pathlib import Path
from unittest.mock import patch

from stepagent.core.adapters import IncrementalParser, parse_codex_records
from stepagent.core.domain import Interaction, NodeKind, LifecycleState
from stepagent.core.graph_builder import build_execution_graph
from stepagent.detail_document import DetailDocument
from stepagent.details import PROFILES, Span, cells, detail_rows, section, wrap_rows
from stepagent.paging import wrapped_rows
from stepagent.ui import PULSE, TerminalApp
from test_live import FakeScreen, record, scenario


def text(rows):
    return "\n".join("".join(span.text for span in row) for row in rows)


class VisibilityTests(unittest.TestCase):
    def test_every_post_prompt_kind_visible_and_initialization_stays_hidden(self):
        app = TerminalApp(Path("/unused"))
        app.screen = "timeline"
        app.doc = parse_codex_records(scenario()[:5])
        for kind in NodeKind:
            app.doc.interactions.append(Interaction(interaction_id=f"kind:{kind.value}", index=len(app.doc.interactions),
                kind=kind, conversation_turn_number=1))
        app.rebuild()
        expected = [i.interaction_id for i in app.doc.interactions if i.conversation_turn_number > 0]
        self.assertEqual([i.interaction_id for i in app.items], expected)
        self.assertEqual({i.kind for i in app.items}, set(NodeKind))
        for key in ["p", "g", "G", "\x1b"]:
            app.key(key)
            self.assertEqual([i.interaction_id for i in app.items], expected)
        app.query = "no match"
        app.rebuild()
        self.assertFalse(app.items)
        app.key("\x1b")
        self.assertEqual([i.interaction_id for i in app.items], expected)

    def test_live_protocol_records_added_to_timeline_and_matrix(self):
        parser = IncrementalParser()
        app = TerminalApp(Path("/unused"))
        parser.feed(record("session_meta", id="live"))
        app.doc = parser.snapshot()
        app.rebuild()
        self.assertFalse(app.items)
        parser.feed(record("event_msg", type="user_message", message="Start"))
        for row in [record("turn_context", model="test"), record("token_usage_record", usage=dict(input_tokens=7)),
                    record("event_msg", type="task_complete"), record("event_msg", type="future_event")]:
            parser.feed(row)
            app.doc = parser.snapshot()
            app.rebuild()
            self.assertEqual(len(app.items), len(app.doc.interactions) - 1)
        app.win = FakeScreen(35, 140)
        app.screen = "matrix"
        app.draw()
        self.assertTrue(any("PROTOKÓŁ" in line for line in app.win.lines.values()))

    def test_initial_control_does_not_hide_first_user_prompt(self):
        doc = parse_codex_records([record("event_msg", type="request_permissions", reason="setup"),
                                  record("event_msg", type="user_message", message="Actual task")])
        self.assertEqual(doc.interactions[0].conversation_turn_number, 0)
        self.assertEqual(doc.interactions[1].conversation_turn_number, 1)


class DetailTests(unittest.TestCase):
    def render(self, rows, kind=None, raw=False):
        doc = parse_codex_records([record("event_msg", type="user_message", message="Start")] + rows)
        item = next(i for i in doc.interactions if i.kind.value == kind) if kind else doc.interactions[-1]
        return detail_rows(item, doc, build_execution_graph(doc), Path("sample.jsonl"), raw)

    def test_all_domain_kinds_have_deliberate_presentations(self):
        self.assertEqual(set(PROFILES), {kind.value for kind in NodeKind})
        doc = parse_codex_records(scenario())
        for kind in NodeKind:
            with self.subTest(kind=kind):
                item = Interaction(interaction_id="fixture", index=99, kind=kind, detail="Example", conversation_turn_number=1)
                rows = detail_rows(item, doc, None, Path("fixture"))
                self.assertIn(PROFILES[kind.value].title, text(rows))

    def test_command_separates_arguments_exit_status_stdout_and_stderr(self):
        rows = self.render([
            record("response_item", type="function_call", call_id="cmd", name="exec_command", arguments='{"cmd":"pytest -q", "cwd":"/work"}'),
            record("response_item", type="function_call_output", call_id="cmd", output="test result", stderr="Traceback: problem", exit_code=2),
        ], "command")
        content = text(rows)
        for label in ["POLECENIE", "pytest -q", "cwd: /work", "Kod wyjścia: 2", "STDERR", "Traceback: problem", "RELACJE"]:
            self.assertIn(label, content)
        self.assertTrue(any(s.text == "2" and s.color == 7 for row in rows for s in row))
        self.assertTrue(any("Traceback" in s.text and s.color == 7 for row in rows for s in row))

    def test_diff_additions_deletions_and_hunks_have_distinct_colors(self):
        rows = self.render([record("response_item", type="custom_tool_call", name="apply_patch", call_id="patch",
            input="*** Begin Patch\n*** Update File: a.py\n@@\n-old\n+new\n*** End Patch")], "file_change")
        spans = {s.text: s.color for row in rows for s in row}
        self.assertEqual(spans["-old"], 7)
        self.assertEqual(spans["+new"], 3)
        self.assertEqual(spans["@@"], 2)

    def test_plan_questions_tokens_and_messages_have_semantic_layouts(self):
        plan = self.render([record("event_msg", type="plan_update", plan=[dict(step="Inspect", status="completed"), dict(step="Fix", status="in_progress")])], "plan")
        self.assertIn("1. [completed] Inspect", text(plan))
        self.assertIn("2. [in_progress] Fix", text(plan))
        questions = self.render([record("event_msg", type="request_user_input", questions=[dict(question="Which target?", options=[dict(label="Local", description="Use this machine")])])], "user_input_request")
        self.assertIn("Pytanie 1: Which target?", text(questions))
        self.assertIn("• Opcja: Local", text(questions))
        tokens = self.render([record("token_usage_record", thread_token_usage=dict(input_tokens=12345), turn_token_usage=dict(output_tokens=8))], "usage")
        self.assertIn("── SESJA \ninput_tokens: 12,345", text(tokens))
        self.assertIn("── TURA \noutput_tokens: 8", text(tokens))
        message = self.render([record("event_msg", type="agent_message", message="## Result\nRun `pytest` now")], "message")
        # The first message in the document is the user prompt.
        self.assertIn("WIADOMOŚĆ UŻYTKOWNIKA", text(message))

    def test_json_syntax_color_survives_wrapping_and_actual_draw(self):
        doc = parse_codex_records(scenario())
        app = TerminalApp(Path("/unused"))
        app.doc, app.graph = doc, build_execution_graph(doc)
        app.version = 1
        app.rebuild()
        app.cursor = next(n for n, i in enumerate(app.items) if i.call_id == "call_a")
        app.raw = True
        rows = app.detail_lines(34)
        self.assertTrue(all(sum(cells(s.text) for s in row) <= 33 for row in rows))
        colors = {s.color for row in rows for s in row if s.text.strip()}
        self.assertTrue({2, 3, 5} <= colors)
        app.win = FakeScreen(55, 100)
        app.colors = True
        with patch("stepagent.ui.curses.color_pair", side_effect=lambda n: n * (curses.A_COLOR & -curses.A_COLOR)):
            with patch.object(app.win, "addstr", wraps=app.win.addstr) as draw:
                app.draw_detail(1, 53, 2, 90)
                attrs = {call.args[3] & curses.A_COLOR for call in draw.call_args_list}
        self.assertGreaterEqual(len(attrs), 3)

    def test_wide_unicode_and_control_codes_are_preserved_safely(self):
        rows = wrap_rows([[Span("世界Zażółć\x1b[31m", 3, True)]], 5)
        self.assertTrue(all(sum(cells(s.text) for s in row) <= 5 for row in rows))
        self.assertEqual("".join(s.text for row in rows for s in row), "世界Zażółć [31m")
        self.assertTrue(all(s.color == 3 and s.bold for row in rows for s in row))


class SectionHeaderTests(unittest.TestCase):
    def test_section_rule_reaches_the_panel_edge_at_any_width(self):
        for width in (30, 64, 120):
            line = "".join(span.text for span in next(iter(wrapped_rows([section("POLECENIE")], width))))
            self.assertTrue(line.startswith("── POLECENIE "))
            self.assertTrue(line.endswith("─"))
            self.assertEqual(cells(line), width)

    def test_section_colour_no_longer_repeats_the_type_colour(self):
        doc = parse_codex_records([record("event_msg", type="user_message", message="Start"),
            record("response_item", type="function_call", call_id="c", name="exec_command", arguments='{"cmd":"pwd"}'),
            record("response_item", type="function_call_output", call_id="c", output="out", stderr="boom")])
        item = next(i for i in doc.interactions if i.kind.value == "command")
        rows = DetailDocument(item.interaction_id, doc, build_execution_graph(doc), Path("s.jsonl")).rows(0)
        spans = [span for row in rows for span in row]
        # The command profile is green (3); its section headers are not.
        self.assertEqual(PROFILES["command"].color, 3)
        self.assertEqual(next(s.color for s in spans if s.text.startswith("── POLECENIE")), 2)
        # Result and error keep the colours that carry status.
        self.assertEqual(next(s.color for s in spans if s.text.startswith("── WYNIK")), 3)
        self.assertEqual(next(s.color for s in spans if s.text.strip() == "· STDERR"), 7)

    def test_result_streams_become_indented_sub_sections(self):
        doc = parse_codex_records([record("event_msg", type="user_message", message="Start"),
            record("response_item", type="function_call", call_id="c", name="exec_command", arguments='{"cmd":"pwd"}'),
            record("response_item", type="function_call_output", call_id="c", output="alfa\nbeta")])
        item = next(i for i in doc.interactions if i.kind.value == "command")
        rows = DetailDocument(item.interaction_id, doc, build_execution_graph(doc), Path("s.jsonl")).rows(0)
        rendered = "\n".join("".join(s.text for s in row) for row in wrapped_rows(rows, 90))
        self.assertIn("  · OUTPUT", rendered)
        self.assertIn("    alfa\n    beta", rendered)


class FocusPulseTests(unittest.TestCase):
    def pane(self, focus, width=130):
        app = TerminalApp(Path("/unused"))
        app.doc = parse_codex_records(scenario())
        app.graph = build_execution_graph(app.doc)
        app.version, app.path, app.screen = 1, Path("t.jsonl"), "timeline"
        app.rebuild()
        app.focus = focus
        app.win = FakeScreen(24, width)
        app.draw()
        return app, int(width * .54)

    def test_only_the_active_pane_carries_the_pulse(self):
        app, split = self.pane("timeline")
        left = {text for (y, x), text in app.win.lines.items() if x == 0 and 6 <= y < 21}
        self.assertTrue(left and left <= set(PULSE))
        self.assertEqual({text for (y, x), text in app.win.lines.items() if x == split - 1 and 6 <= y < 21}, {"│"})
        app, split = self.pane("detail")
        self.assertFalse({text for (y, x), text in app.win.lines.items() if x == 0 and 6 <= y < 21})
        marks = {text for (y, x), text in app.win.lines.items() if x == split - 1 and 6 <= y < 21}
        self.assertTrue(marks and marks <= set(PULSE))

    def test_pulse_advances_with_time_and_never_thins_below_the_separator(self):
        app = TerminalApp(Path("/unused"))
        with patch("stepagent.ui.time.monotonic", side_effect=[n / 4 for n in range(len(PULSE))]):
            frames = [app.pulse() for _ in range(len(PULSE))]
        self.assertEqual(len(set(frames)), len(set(PULSE)))
        self.assertNotIn("│", PULSE)

    def test_inactive_timeline_keeps_its_selection_without_the_reverse_bar(self):
        for focus, expected in (("timeline", True), ("detail", False)):
            app = TerminalApp(Path("/unused"))
            app.doc = parse_codex_records(scenario())
            app.graph = build_execution_graph(app.doc)
            app.version, app.path, app.screen = 1, Path("t.jsonl"), "timeline"
            app.rebuild()
            app.focus = focus
            app.win = FakeScreen(24, 130)
            with patch.object(app.win, "addstr", wraps=app.win.addstr) as draw:
                app.draw()
            reversed_rows = [call for call in draw.call_args_list
                             if call.args[1] < 60 and call.args[3] & curses.A_REVERSE]
            self.assertEqual(bool(reversed_rows), expected, focus)

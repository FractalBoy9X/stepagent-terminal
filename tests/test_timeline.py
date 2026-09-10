"""Timeline semantics, narrow layouts and selection contrast regressions."""
import curses
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from stepagent.core.adapters import parse_codex_records
from stepagent.core.domain import Interaction, InteractionFamily, LifecycleState
from stepagent.details import cells
from stepagent.timeline import labels, state_label
from stepagent.ui import SELECTION_PAIR, TerminalApp, ellipsize
from test_live import FakeScreen, record, scenario


class TimelineTests(unittest.TestCase):
    def app(self, width=80):
        app = TerminalApp(Path("/unused"))
        app.doc = parse_codex_records(scenario())
        app.rebuild()
        app.follow = False
        app.cursor = next(n for n, item in enumerate(app.items) if item.call_id == "call_a")
        app.win = FakeScreen(24, width)
        return app

    def test_command_content_is_only_in_details_but_still_searchable(self):
        app = self.app()
        app.draw_timeline(6, 21, 80)
        rendered = " ".join(app.win.lines.values())
        self.assertIn("WYKONANIE / PROCES", rendered)
        self.assertIn("Uruchomienie polecenia", rendered)
        self.assertIn("! BŁĄD", rendered)
        self.assertNotIn("pytest", rendered)
        self.assertNotIn("exec_command", rendered)
        for query in ("pytest", "uruchomienie", "wykonanie", "proces", "function_call", "błąd"):
            app.query = query
            app.rebuild()
            self.assertTrue(any(i.call_id == "call_a" for i in app.items), query)

    def test_recorded_message_channel_and_unknown_subtype(self):
        doc = parse_codex_records([
            record("event_msg", type="user_message", message="Prompt"),
            record("response_item", type="message", role="assistant", channel="commentary",
                   content=[dict(type="output_text", text="Working")]),
            record("response_item", type="message", role="assistant", channel="final",
                   content=[dict(type="output_text", text="Done")]),
        ])
        variants = [labels(i)[2] for i in doc.interactions]
        self.assertIn("Asystent · aktualizacja", variants)
        self.assertIn("Asystent · odpowiedź końcowa", variants)
        unknown = Interaction("unknown", 0, subkind="future_protocol_event", action="unknown_event")
        self.assertEqual(labels(unknown)[1:3], ("NIEZNANE", "future_protocol_event"))

    def test_waiting_is_not_blocked_and_completion_is_not_success(self):
        item = Interaction("state", 0)
        self.assertEqual(state_label(replace(item, lifecycle=LifecycleState.WAITING, status="blocked"))[0], "◌ OCZEKIWANIE")
        self.assertEqual(state_label(replace(item, lifecycle=LifecycleState.COMPLETED, status="success"))[0], "✓ ZAKOŃCZONY")
        self.assertEqual(state_label(replace(item, lifecycle=LifecycleState.COMPLETED, status="error"))[0], "! BŁĄD")
        states = {state_label(replace(item, lifecycle=state))[0] for state in LifecycleState}
        self.assertEqual(len(states), len(LifecycleState))

    def test_inferred_effect_has_visible_qualifier(self):
        item = Interaction("inferred", 0, action="file_read", action_confidence="inferred")
        self.assertEqual(labels(item)[2], "≈ Odczyt pliku · wnioskowane")

    def test_selection_covers_both_rows_and_ignores_category_color(self):
        app = self.app()
        app.items = [app.items[app.cursor]]
        app.cursor = 0
        app.colors = app.selection_color = True
        for family in InteractionFamily:
            app.items[0] = replace(app.items[0], family=family)
            with patch("stepagent.ui.curses.color_pair", side_effect=lambda n: n * (curses.A_COLOR & -curses.A_COLOR)), patch.object(app.win, "addstr") as draw:
                app.draw_timeline(6, 21, 80)
            for y in (7, 8):
                row = [call.args for call in draw.call_args_list if call.args[0] == y]
                self.assertTrue(row)
                self.assertTrue(all(args[3] & curses.A_COLOR == SELECTION_PAIR * (curses.A_COLOR & -curses.A_COLOR) for args in row))
                self.assertTrue(any(x + cells(text) == 79 for _, x, text, _ in row))
                self.assertFalse(any(args[3] & curses.A_REVERSE for args in row))
                self.assertFalse(any(args[3] & curses.A_BOLD for args in row))

    def test_focus_and_monochrome_selection(self):
        app = self.app()
        app.items = [app.items[app.cursor]]
        app.cursor = 0
        for active in (True, False):
            with patch.object(app.win, "addstr", wraps=app.win.addstr) as draw:
                app.draw_timeline(6, 21, 80, active)
            for y in (7, 8):
                row = [c.args for c in draw.call_args_list if c.args[0] == y]
                self.assertEqual(any(args[3] & curses.A_REVERSE for args in row), active)
            self.assertEqual(app.win.lines[7, 2], "▶" if active else "│")
            self.assertEqual(app.win.lines[8, 2], "│")
            self.assertIn("↑↓ wybór" if active else "fokus: szczegóły", app.win.lines[5, 2])

    def test_narrow_panes_keep_identity_type_state_and_subtype(self):
        app = self.app()
        item = app.items[app.cursor]
        for width in (42, 58, 61, 80, 120):
            for lifecycle in LifecycleState:
                app.items = [replace(item, lifecycle=lifecycle, status="unknown")]
                app.cursor = 0
                app.win = FakeScreen(24, width)
                with patch.object(app.win, "addstr", wraps=app.win.addstr) as draw:
                    app.draw_timeline(6, 21, width)
                for call in draw.call_args_list:
                    y, x, text, _ = call.args
                    self.assertLessEqual(x + cells(text), width - 1)
                text = " ".join(app.win.lines.values())
                self.assertIn(f"T01/#{item.index + 1}", text)
                self.assertIn("PROCES", text)
                self.assertIn(state_label(app.items[0])[0], text)
                self.assertIn("Uruchomienie polecenia", text)
        self.assertEqual(ellipsize("世界abc", 5), "世界…")

    def test_search_focus_and_exit_hints_remain_visible_with_long_query(self):
        for h, w in ((12, 42), (24, 80), (40, 150)):
            app = self.app(w)
            app.screen = "timeline"
            app.win = FakeScreen(h, w)
            app.key("/")
            app.query = "世界" * 100 + "KONIEC"
            with patch.object(app.win, "addstr", wraps=app.win.addstr) as draw:
                app.draw()
            self.assertTrue(app.win.lines[4, 2].startswith("WYSZUKIWANIE: …"))
            self.assertTrue(app.win.lines[4, 2].endswith("KONIEC▏"))
            self.assertLessEqual(cells(app.win.lines[4, 2]), w - 4)
            self.assertEqual(app.win.lines[h - 2, 2], "Esc: wyjdź i usuń filtr")
            self.assertEqual(app.win.lines[h - 1, 2], "Enter: nawigacja z filtrem")
            reversed_rows = {c.args[0] for c in draw.call_args_list if c.args[3] & curses.A_REVERSE}
            self.assertEqual(reversed_rows, {4})
            self.assertNotIn("↑↓ wybór", app.win.lines[5, 2])

    def test_search_enter_keeps_filter_escape_clears_and_returns_to_steps(self):
        app = self.app()
        app.screen, app.focus, app.detail_full = "timeline", "detail", True
        app.follow = True
        app.key("/")
        self.assertFalse(app.follow)
        self.assertEqual(app.focus, "timeline")
        self.assertFalse(app.detail_full)
        for char in "pytest":
            app.key(char)
        app.key(curses.KEY_ENTER)
        self.assertFalse(app.editing)
        self.assertEqual(app.query, "pytest")
        self.assertEqual(len(app.items), 1)
        app.draw()
        self.assertEqual(app.win.lines[4, 2], "FILTR AKTYWNY: pytest")
        app.key("/")
        app.key("\x1b")
        self.assertFalse(app.editing)
        self.assertEqual(app.query, "")
        self.assertGreater(len(app.items), 1)
        cursor = app.cursor
        app.key(curses.KEY_DOWN)
        self.assertEqual(app.cursor, cursor + 1)

    def test_session_search_has_the_same_editing_and_filter_states(self):
        app = self.app(42)
        app.screen = "sessions"
        app.query = "unrelated step filter"
        app.key("/")
        app.key("x")
        app.draw()
        self.assertEqual(app.win.lines[5, 2], "WYSZUKIWANIE: x▏")
        self.assertEqual(app.win.lines[22, 2], "Esc: wyjdź i usuń filtr")
        app.key("\n")
        app.draw()
        self.assertEqual(app.win.lines[5, 2], "FILTR AKTYWNY: x")
        app.key("\x1b")
        self.assertEqual(app.session_query, "")
        self.assertEqual(app.query, "unrelated step filter")


if __name__ == "__main__":
    unittest.main()

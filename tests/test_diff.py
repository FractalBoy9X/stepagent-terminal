import unittest
from pathlib import Path

from stepagent.core.adapters import parse_codex_records
from stepagent.core.graph_builder import build_execution_graph
from stepagent.detail_document import DetailDocument
from stepagent.details import Span, cells
from stepagent.diff import diff_rows, parse_patch
from stepagent.paging import wrapped_rows
from test_live import record


ENVELOPE = "*** Begin Patch\n{}\n*** End Patch\n"


def plain(rows, width=100):
    return "\n".join("".join(span.text for span in row) for row in wrapped_rows(rows, width))


def detail(patch, **payload):
    rows = [record("event_msg", type="user_message", message="Start"),
            record("response_item", type="custom_tool_call", name="apply_patch",
                   call_id="patch", input=patch, **payload)]
    doc = parse_codex_records(rows)
    item = next(item for item in doc.interactions if item.kind.value == "file_change")
    return DetailDocument(item.interaction_id, doc, build_execution_graph(doc), Path("sample.jsonl"))


class NumberingTests(unittest.TestCase):
    def test_added_file_is_numbered_absolutely_from_line_one(self):
        patch = parse_patch(ENVELOPE.format("*** Add File: nowy.py\n+alfa\n+beta"))
        added = patch.files[0]
        self.assertEqual((added.operation, added.path), ("add", "nowy.py"))
        self.assertEqual((added.added, added.removed), (2, 0))
        self.assertTrue(added.hunks[0].absolute)
        self.assertEqual([line.new for line in added.hunks[0].lines], [1, 2])
        self.assertEqual([line.old for line in added.hunks[0].lines], [None, None])

    def test_bare_hunks_count_continuously_and_say_so(self):
        patch = parse_patch(ENVELOPE.format("*** Update File: a.py\n@@\n-jeden\n+JEDEN\n@@\n-dwa\n+DWA"))
        first, second = patch.files[0].hunks
        self.assertFalse(first.absolute)
        self.assertEqual([(line.old, line.new) for line in first.lines], [(1, None), (None, 1)])
        # The source records no file position, so the second hunk continues
        # the count instead of restarting at 1 and implying a line number.
        self.assertEqual([(line.old, line.new) for line in second.lines], [(2, None), (None, 2)])
        self.assertIn("numeracja kolejna w patchu", plain(diff_rows(ENVELOPE.format(
            "*** Update File: a.py\n@@\n-jeden\n+JEDEN"))))

    def test_recorded_hunk_positions_are_used_and_labelled(self):
        hunk = parse_patch("--- a/a.py\n+++ b/a.py\n@@ -10,2 +20,2 @@\n keep\n-old\n+new\n").files[0].hunks[0]
        self.assertTrue(hunk.absolute)
        self.assertEqual([(line.old, line.new) for line in hunk.lines], [(10, 20), (11, None), (None, 21)])

    def test_context_lines_advance_both_sides(self):
        lines = parse_patch(ENVELOPE.format("*** Update File: a.py\n@@\n ctx\n-old\n+new\n ctx2")).files[0].hunks[0].lines
        self.assertEqual([(line.old, line.new) for line in lines], [(1, 1), (2, None), (None, 2), (3, 3)])


class FormatTests(unittest.TestCase):
    def test_removed_dashes_stay_a_removal_inside_the_envelope(self):
        lines = parse_patch(ENVELOPE.format("*** Update File: a.md\n@@\n---- stara\n+++ nowa")).files[0].hunks[0].lines
        self.assertEqual([(line.kind, line.text) for line in lines], [("-", "--- stara"), ("+", "++ nowa")])
        spans = {span.text: span.color for row in diff_rows(ENVELOPE.format(
            "*** Update File: a.md\n@@\n---- stara\n+++ nowa")) for span in row}
        self.assertEqual(spans["--- stara"], 7)
        self.assertEqual(spans["++ nowa"], 3)

    def test_operations_paths_and_counts_reach_the_header(self):
        text = plain(diff_rows(ENVELOPE.format(
            "*** Delete File: stary.py\n*** Add File: nowy.py\n+alfa")))
        self.assertIn("USUNIĘTY PLIK · stary.py", text)
        self.assertIn("NOWY PLIK · nowy.py", text)
        self.assertIn("+1", text)

    def test_move_target_is_shown_next_to_the_path(self):
        self.assertIn("a.py → b.py", plain(diff_rows(ENVELOPE.format(
            "*** Update File: a.py\n*** Move to: b.py\n@@\n-x\n+y"))))

    def test_unparseable_text_is_shown_verbatim(self):
        text = plain(diff_rows("zwykły tekst\nbez patcha"))
        self.assertIn("Nie rozpoznano formatu patcha", text)
        self.assertIn("zwykły tekst", text)
        self.assertIn("bez patcha", text)

    def test_no_patch_line_is_dropped(self):
        body = "*** Update File: a.py\n@@ kotwica\n ctx\n-old\n+new\n\\ No newline at end of file"
        rendered = plain(diff_rows(ENVELOPE.format(body)), width=200)
        for line in ["ctx", "old", "new", "kotwica", "No newline at end of file"]:
            self.assertIn(line, rendered)


class WrappingTests(unittest.TestCase):
    def test_wrapped_line_keeps_its_marker_and_column(self):
        patch = ENVELOPE.format("*** Update File: a.py\n@@\n-" + "x" * 120 + "\n+krótka")
        rows = [row for row in wrapped_rows(diff_rows(patch), 40) if any("x" in span.text for span in row)]
        self.assertGreater(len(rows), 1)
        for row in rows:
            self.assertEqual("".join(span.text for span in row)[6:8], "│-")
            self.assertLessEqual(sum(cells(span.text) for span in row), 40)
        # Only the first visual row carries the number; the rest are padded.
        self.assertEqual([("".join(span.text for span in row)[:6]).strip() for row in rows][1:], [""] * (len(rows) - 1))

    def test_narrow_terminal_drops_the_prefix_instead_of_squeezing_content(self):
        row = [Span("  1   2 ", 6, hang="        "), Span("│", 6, hang="│"), Span("+", 3, True, hang="+"),
               Span(" ", 0, hang=" "), Span("treść która i tak się nie zmieści", 3)]
        wrapped = list(wrapped_rows([row], 12))
        self.assertTrue(all(sum(cells(span.text) for span in line) <= 12 for line in wrapped))
        self.assertIn("treść", "".join(span.text for line in wrapped for span in line))

    def test_rows_without_a_prefix_wrap_exactly_as_before(self):
        rows = [[Span("a" * 30, 3)], [Span("b\nc", 0)]]
        self.assertEqual([[span.text for span in row] for row in wrapped_rows(rows, 10)],
                         [["a" * 10], ["a" * 10], ["a" * 10], ["b"], ["c"]])


class SectionTests(unittest.TestCase):
    def test_single_file_patch_does_not_repeat_a_file_index(self):
        text = plain(detail(ENVELOPE.format("*** Update File: a.py\n@@\n-old\n+new")).rows(0))
        self.assertNotIn("INDEKS PLIKÓW", text)
        self.assertIn("ZMIANA · a.py", text)

    def test_recorded_change_paths_are_listed_next_to_the_patch(self):
        document = detail(ENVELOPE.format("*** Add File: nowy.md\n+tekst"))
        document.item.metadata["changes"] = {"/abs/nowy.md": {"type": "add"}}
        text = plain(document.rows(0))
        self.assertIn("INDEKS PLIKÓW", text)
        self.assertIn("ADD · /abs/nowy.md", text)

    def test_full_file_content_is_not_painted_as_a_diff(self):
        rows = [record("event_msg", type="user_message", message="Start"),
                record("event_msg", type="patch_apply_end", call_id="applied", success=True,
                       changes={"a.md": {"type": "add", "content": "- punkt listy\n- drugi punkt"}})]
        doc = parse_codex_records(rows)
        item = next(item for item in doc.interactions if item.kind.value == "file_change")
        document = DetailDocument(item.interaction_id, doc, build_execution_graph(doc), Path("sample.jsonl"))
        rows = list(document.rows(0))
        text = plain(rows)
        self.assertIn("Pełna treść pliku po zmianie, nie diff.", text)
        # A Markdown bullet must not be coloured as a removal (7).
        self.assertFalse(any(span.color == 7 and "punkt listy" in span.text for row in rows for span in row))


if __name__ == "__main__":
    unittest.main()

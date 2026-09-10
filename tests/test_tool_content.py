import copy
import json
import unittest

from stepagent.paging import wrapped_rows
from stepagent.tool_content import literal_calls, result_rows
from test_layers import app_fixture, plain
from test_live import FakeScreen, record


def wrapper_fixture():
    command = "python3 - <<'PY'\nfrom pathlib import Path\nprint('Visible: 228 of 228')\nPY"
    arguments = dict(cmd=command, workdir="/workspace", yield_time_ms=1000, max_output_tokens=1000)
    script = "text(await tools.exec_command(" + json.dumps(arguments) + "));"
    inner = dict(chunk_id="demo", wall_time_seconds=1.1, exit_code=0,
                 output="Visible: 228 of 228\nAll steps rendered successfully\n", extra=False)
    output = json.dumps([dict(type="input_text", text="Script completed\nWall time: 1.1 seconds\nOutput:\n"),
                         dict(type="input_text", text=json.dumps(inner))])
    app = app_fixture([record("event_msg", type="user_message", message="Start"),
        record("response_item", type="custom_tool_call", name="exec", call_id="wrapper", input=script),
        record("response_item", type="custom_tool_call_output", call_id="wrapper", output=output)])
    return app, command, script, output


class ToolContentTests(unittest.TestCase):
    def test_screenshot_shape_is_readable_without_losing_originals(self):
        app, command, script, output = wrapper_fixture()
        document = app.detail_context()
        self.assertEqual(document.item.kind.value, "command")
        self.assertFalse(document.item.metadata.get("protocol_wrapper"))
        original = copy.deepcopy(document.item.to_dict())
        rows = list(wrapped_rows(document.rows(), 150))
        rendered = "\n".join("".join(s.text for s in row) for row in rows)
        main, original_script = rendered.split("SKRYPT WRAPPERA — PEŁNY ORYGINAŁ", 1)
        for expected in ("WRAPPER · exec", "POLECENIE", command, "PARAMETRY WYKONANIA",
                         "exit_code: 0", "Visible: 228 of 228\nAll steps rendered successfully", "extra: false"):
            self.assertIn(expected, main)
        self.assertNotIn(r"\n", main)
        self.assertNotIn(r'\"', main)
        self.assertIn("text(await tools.exec_command", original_script)
        self.assertEqual(document.item.to_dict(), original)
        raw = plain(document.rows(3), 10000)
        self.assertIn(json.dumps(script, ensure_ascii=False), raw)
        self.assertIn(json.dumps(output, ensure_ascii=False), raw)
        command_spans = [s for row in rows for s in row if "Path" in s.text]
        self.assertTrue(any(s.color == 1 for s in command_spans))

    def test_literal_extraction_never_executes_or_guesses_expressions(self):
        script = """
        // tools.exec_command({cmd: 'IGNORE_COMMENT'})
        const example = "tools.exec_command({cmd: 'IGNORE_STRING'})";
        const template = `tools.exec_command({cmd: 'IGNORE_TEMPLATE'})`;
        tools.exec_command({cmd: 'pwd', env: {}, tty: false});
        tools.exec_command({cmd: dynamicValue});
        tools.exec_command({cmd: 'part' + variable});
        tools.exec_command({cmd: 'ls', workdir: '/tmp',});
        tools.exec_command({cmd: 'first', cmd: 'duplicate'});
        """
        self.assertEqual(list(literal_calls(script)), [
            ("exec_command", dict(cmd="pwd", env={}, tty=False)),
            ("exec_command", dict(cmd="ls", workdir="/tmp"))])

    def test_nested_blocks_empty_values_errors_and_plain_backslashes(self):
        value = dict(content=[dict(type="text", text=json.dumps(dict(stdout="one\ntwo\n", stderr="problem\n", exit_code=2))),
                              dict(type="image", data="base64-demo", mimeType="image/png")],
                     isError=True, empty={}, nothing=None, zero=0)
        text = plain(result_rows(value), 200)
        for expected in ("one\ntwo", "problem", "exit_code: 2", "base64-demo", "image/png", "nothing: null", "zero: 0", "{}"):
            self.assertIn(expected, text)
        self.assertEqual(plain(result_rows(r'C:\new\test')), r'C:\new\test')
        self.assertEqual(plain(result_rows('{broken JSON')), '{broken JSON')
        spans = [s for row in wrapped_rows(result_rows(value), 100) for s in row]
        self.assertTrue(any(s.text == "problem" and s.color == 7 for s in spans))

    def test_direct_command_output_also_uses_nested_response_view(self):
        app = app_fixture([record("event_msg", type="user_message", message="Start"),
            record("response_item", type="function_call", name="exec_command", call_id="direct", arguments='{"cmd":"pwd"}'),
            record("response_item", type="function_call_output", call_id="direct", output=json.dumps([
                dict(type="input_text", text=json.dumps(dict(output="lineA\nlineB", exit_code=0)))]))])
        text = plain(app.detail_context().rows())
        # Nested response bodies are indented under their sub-section.
        self.assertIn("    lineA\n    lineB", text)
        self.assertNotIn(r"\n", text)

    def test_d_and_r_are_visible_and_stay_on_selected_step(self):
        app, *_ = wrapper_fixture()
        target = app.detail_target()
        for h, w in ((45, 160), (24, 80), (12, 42)):
            app.win = FakeScreen(h, w)
            app.draw()
            footer = app.win.lines[h - 2, 2]
            self.assertIn("d powiększ", footer)
            self.assertIn("r RAW", footer)
        app.win = FakeScreen(30, 100)
        app.key("2")
        app.key("d")
        self.assertTrue(app.detail_full)
        self.assertEqual(app.layer, 0)
        self.assertEqual(app.detail_target(), target)
        app.draw()
        self.assertIn("d zmniejsz", app.win.lines[28, 2])
        app.key("r")
        self.assertEqual(app.layer, 3)
        self.assertTrue(app.detail_full)
        self.assertEqual(app.detail_target(), target)
        app.key("r")
        self.assertEqual(app.layer, 0)
        app.key("d")
        self.assertFalse(app.detail_full)
        self.assertEqual(app.detail_target(), target)


if __name__ == "__main__":
    unittest.main()

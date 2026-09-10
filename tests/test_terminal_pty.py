"""Exercise actual curses in a pseudoterminal, including appends and resize."""
import json
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "posix", "PTY integration requires macOS or Linux")
class TerminalIntegrationTest(unittest.TestCase):
    def test_keyboard_live_append_resize_and_clean_exit(self):
        import fcntl
        import pty
        import termios
        with tempfile.TemporaryDirectory(prefix="stepagent-pty-") as directory:
            path = Path(directory) / "rollout.jsonl"
            with path.open("w") as stream:
                for row in [dict(type="session_meta", payload=dict(id="pty-session")),
                            dict(type="event_msg", payload=dict(type="user_message", message="LIVE_PTY_INITIAL"))]:
                    stream.write(json.dumps(row) + "\n")
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 150, 0, 0))
            process = subprocess.Popen([sys.executable, "-m", "stepagent", "--file", str(path), "--sessions-dir", directory, "--interval", "0.05"],
                cwd=Path(__file__).resolve().parents[1], stdin=slave, stdout=slave, stderr=slave,
                env={**os.environ, "TERM": "xterm-256color"}, close_fds=True)
            os.close(slave)
            try:
                def until(marker):
                    output = b""
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        ready, _, _ = select.select([master], [], [], .1)
                        if ready:
                            try:
                                output += os.read(master, 65536)
                            except OSError:
                                break
                            if marker.encode() in output:
                                return output
                        if process.poll() is not None:
                            break
                    self.fail(f"Missing terminal marker {marker!r}; output tail: {output[-1200:]!r}")

                until("LIVE_PTY_INITIAL")
                started = time.monotonic()
                with path.open("a") as stream:
                    stream.write(json.dumps(dict(type="response_item", payload=dict(type="function_call", name="exec_command", call_id="pty-call", arguments='{"cmd":"echo LIVE_APPEND_OK"}'))) + "\n")
                until("LIVE_APPEND_OK")
                latency = time.monotonic() - started
                self.assertLess(latency, 2)
                os.write(master, b"m")
                until("AKTYWNOŚĆ")
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
                process.send_signal(signal.SIGWINCH)
                os.write(master, b"md")
                until("SZCZEGÓŁY")
                os.write(master, b"2")
                until("JSON Pointer")
                os.write(master, b"4")
                until("kompletne wartości JSON")
                with path.open("a") as stream:
                    stream.write(json.dumps(dict(type="response_item", payload=dict(type="function_call_output", call_id="pty-call", output="LAYER_RESULT_OK", exit_code=0))) + "\n")
                until("NOWE DANE SESJI")
                # Refresh while reading, then view the typed result in a taller
                # terminal. Frozen RAW must not jump when the output arrives.
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 100, 0, 0))
                process.send_signal(signal.SIGWINCH)
                os.write(master, b"u1")
                until("LAYER_RESULT_OK")
                os.write(master, b"3")
                until("Pewność:")
                os.write(master, b"\r")
                until("WĘZEŁ GRAFU")
                os.write(master, b"b")
                until("Pewność:")
                # Same nested wrapper shape as the user's screenshot, not just
                # a flat command/output fixture. Exercise the advertised r/d.
                os.write(master, b"G1")
                with path.open("a") as stream:
                    script = 'text(await tools.exec_command({"cmd":"echo first\\necho second","workdir":"/tmp"}));'
                    nested = json.dumps([dict(type="input_text", text=json.dumps(dict(exit_code=0, output="WRAPPED_START\nWRAPPED_END")))])
                    stream.write(json.dumps(dict(type="response_item", payload=dict(type="custom_tool_call", name="exec", call_id="nested", input=script))) + "\n")
                    stream.write(json.dumps(dict(type="response_item", payload=dict(type="custom_tool_call_output", call_id="nested", output=nested))) + "\n")
                os.write(master, b"G")
                until("WRAPPED_END")
                os.write(master, b"r")
                until("kompletne wartości JSON")
                os.write(master, b"r")
                until("WRAPPED_END")
                os.write(master, b"d")
                until("TURA / KROK")
                os.write(master, b"d")
                until("WRAPPED_END")
                os.write(master, b"s")
                until("WYBIERZ SESJĘ")
                os.write(master, b"q")
                # Drain the PTY while curses restores the screen; its output buffer
                # can otherwise block process exit when no terminal emulator reads it.
                until("\x1b[?1049l")
                self.assertEqual(process.wait(timeout=5), 0)
                print(f"\nPTY live append visible after {latency * 1000:.0f} ms; 150×40 → 80×24; clean exit.")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                os.close(master)

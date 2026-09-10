"""Generate a synthetic growing rollout in a temporary directory (never real sessions)."""
import argparse
import json
import os
import shlex
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=.8)
    parser.add_argument("--turns", type=int, default=3)
    args = parser.parse_args()
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8", errors="backslashreplace")
    directory = Path(tempfile.mkdtemp(prefix="stepagent-demo-"))
    path = directory / "demo.jsonl"
    command = [sys.executable, "-m", "stepagent", "--file", str(path)]
    if os.name == "nt":
        # PowerShell requires the call operator for a quoted executable path.
        quoted = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in command)
    else:
        quoted = shlex.join(command)
    print(f"W drugim terminalu (Windows: PowerShell):\n\n{quoted}\n", flush=True)
    with path.open("w", encoding="utf-8") as stream:
        def emit(kind, payload):
            record = dict(timestamp=datetime.now(timezone.utc).isoformat(), type=kind, payload=payload)
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            time.sleep(max(0, args.delay))

        emit("session_meta", dict(id="stepagent-demo", cwd=str(directory / "project"), cli_version="demo"))
        for turn in range(1, args.turns + 1):
            tid = f"demo-turn-{turn}"
            emit("event_msg", dict(type="task_started", turn_id=tid))
            emit("event_msg", dict(type="user_message", message=f"Sprawdź testy i popraw plik — tura {turn}."))
            emit("turn_context", dict(turn_id=tid, model="demo-model"))
            emit("response_item", dict(type="reasoning", summary=[dict(type="summary_text", text="Sprawdzę wynik testu, a następnie poprawię wskazany plik.")]))
            for attempt, code in [(1, 1), (2, 0)]:
                call = f"call-test-{turn}-{attempt}"
                emit("response_item", dict(type="function_call", name="functions.exec_command", call_id=call, arguments=json.dumps(dict(cmd="python3 -m unittest"))))
                emit("response_item", dict(type="function_call_output", call_id=call, output=f"Process exited with code {code}\nOutput:\n{'FAILED: expected 4, got 3' if code else 'Ran 8 tests — OK'}"))
                if code:
                    patch_id = f"call-patch-{turn}"
                    emit("response_item", dict(type="custom_tool_call", name="apply_patch", call_id=patch_id, input="*** Begin Patch\n*** Update File: app.py\n@@\n-return 3\n+return 4\n*** End Patch"))
                    emit("response_item", dict(type="custom_tool_call_output", call_id=patch_id, output="Success. Updated app.py"))
            emit("response_item", dict(type="message", role="assistant", content=[dict(type="output_text", text="Poprawka gotowa. Wszystkie 8 testów przechodzi.")]))
            emit("event_msg", dict(type="task_complete", turn_id=tid))
    print(f"Demo zakończone. Plik do ponownego odtworzenia: {path}")


if __name__ == "__main__":
    main()

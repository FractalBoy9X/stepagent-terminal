# StepAgent Terminal

Live terminal viewer for local Codex sessions. Inspect prompts, agent activity,
tool calls and results, file diffs, relationships, and final responses.

Runs on macOS, Linux, and Windows with Python 3.10+.

## Install

Clone the repository and run these commands from its directory.

### macOS / Linux

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/stepagent-terminal
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\stepagent-terminal.exe
```

Windows installs `windows-curses` automatically. Use a Unicode-capable terminal.
Virtual-environment activation is optional. Checkout launchers are also available:
`sh run.sh --latest`, `python run.py --latest`, and `.\run.ps1 --latest`.

## Session logs

No machine-specific log path is built in. The session directory is selected in
this order:

1. `--sessions-dir DIRECTORY`
2. `CODEX_SESSIONS_DIR`
3. `CODEX_HOME/sessions`
4. `.codex/sessions` under the current user's home directory

Paths support `~`, environment variables, spaces, Unicode, and relative paths.
The reader scans nested `*.jsonl` files and never modifies them.

```sh
stepagent-terminal --latest
stepagent-terminal --list
stepagent-terminal --session ID_OR_FRAGMENT
stepagent-terminal --file "path/to/rollout.jsonl"
stepagent-terminal --snapshot --file "path/to/rollout.jsonl"
stepagent-terminal --sessions-dir "path/to/sessions"
```

`--latest` selects a session once. `--snapshot` emits one JSON snapshot without
an interactive terminal. The default polling interval is 0.25 seconds; change it
with `--interval` (minimum 0.05 seconds).

Example configuration:

```sh
export CODEX_SESSIONS_DIR="$HOME/my-session-logs"
```

```powershell
$env:CODEX_SESSIONS_DIR = Join-Path $HOME 'my-session-logs'
```

## Controls

A comfortable terminal size is 120×35; the minimum is 42×12.

| Key | Action |
| --- | --- |
| `s` | select a session |
| `↑` / `↓`, `j` / `k` | select a step or scroll details |
| `←` / `→` | focus timeline or details |
| `d`, `Enter` | open full-screen details |
| `1` / `2` / `3` / `4` | content / fields / relationships / raw JSON |
| `a`, `r` | all layers / toggle raw view |
| `u` | refresh pinned details |
| `Space`, `f` | follow new steps / browse history |
| `g` / `G` | first / newest step |
| `[` / `]`, `t` | previous / next turn; filter to selected turn |
| `/` | search; `Enter` keeps the filter |
| `Tab`, `m` | timeline / activity matrix |
| `Esc` | go back or clear the filter |
| `?` | help |
| `q`, `Ctrl+C` | quit |

See the [full English guide](docs/usage.md) or the [Polish guide](docs/usage.pl.md).

## Demo

Generate a synthetic log in the system temporary directory:

```sh
stepagent-demo --delay 1 --turns 3
```

Run the command printed by the demo in a second terminal. The demo never reads
or changes real sessions.

## Privacy and limits

The application is local and read-only. It sends no network requests, never
executes commands found in logs, and requires no API key. Logs may contain
private prompts and tool arguments; review them before sharing screens or files.

This observes a saved JSONL file rather than a model token stream. Incomplete
lines wait for a newline, malformed records are skipped with a count, and file
truncation or replacement rebuilds the state. Large logs use more memory.

## Verify

```sh
python -m unittest discover -s tests -v
python -m build
```

GitHub Actions tests and builds the package on Windows, macOS, and Linux with
Python 3.10 and 3.14.

## License

MIT — see [LICENSE](LICENSE).

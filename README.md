# StepAgent Terminal

Watch local Codex sessions as a live terminal timeline: prompts, agent activity,
tool calls and results, file diffs, and final responses. Browse the original
records and their relationships without leaving your terminal.

Runs on **macOS, Linux, and Windows** with **Python 3.10+** and a Unicode terminal.
The interface is currently in Polish. [Polska instrukcja obsługi](docs/usage.pl.md).

- Live session picker with conversation names, search, and automatic refresh.
- Turn-based timeline and activity matrix; responsive split and full-screen views.
- Four detail layers: readable content, all fields, relationships, and raw JSON.
- Incremental JSONL reader, background parsing, and pinned detail snapshots.
- Local, read-only operation: no API key, server, telemetry, or network requests.

## Install from this repository

Download or clone this repository and open a terminal in its directory.
The commands below install this local checkout; a PyPI release is not required.

### macOS / Linux

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/stepagent-terminal
```

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\stepagent-terminal.exe
```

If your installation provides `python` instead of `py`, use `python -m venv .venv`.
Installing the package automatically installs `windows-curses` on Windows.
Virtual environment activation is optional; these commands do not require changing
the PowerShell execution policy. With an activated environment, use
`stepagent-terminal` or `python -m stepagent` from any directory.

For development, replace `pip install .` with `pip install -e .`.
Checkout launchers `sh run.sh`, `.\run.ps1`, and `python run.py` are also available;
they preserve the caller's working directory for relative file arguments.
The shell launchers prefer the checkout's `.venv` if it exists.

On macOS/Linux, Python must include `curses`; minimal Python builds may omit it.
On Windows, use a CPython installation compatible with the available
[windows-curses wheels](https://pypi.org/project/windows-curses/) (x86 or x64;
native ARM64 Python wheels are currently unavailable). The
[Python curses guide](https://docs.python.org/3/howto/curses.html) explains the
platform backend. A real Unicode-capable terminal is required for the UI;
`--list`, `--snapshot`, and `--help` also work without one.

## Find your sessions

The application has no machine-specific session path. Directory selection follows
this order, evaluated every time you start it:

| Priority | Configuration | Meaning |
| --- | --- | --- |
| 1 | `--sessions-dir DIRECTORY` | Use exactly this session directory |
| 2 | `CODEX_SESSIONS_DIR` | Environment override for the session directory |
| 3 | `CODEX_HOME` | Read the `sessions` child of your configured Codex home |
| 4 | Current user's home | Use `.codex/sessions` under `Path.home()` |

Empty environment values are ignored. Paths support `~`, environment variables
in the host OS syntax, spaces, Unicode, and relative paths. Relative paths are
resolved from the directory where you invoked the command. Quote paths containing
spaces. Session discovery scans nested `*.jsonl` files; conversation titles are
read, when available, from `session_index.jsonl` next to the session directory.

```sh
stepagent-terminal --sessions-dir "path/to/sessions"
stepagent-terminal --file "path/to/rollout.jsonl"
stepagent-terminal --list
stepagent-terminal --session UNIQUE_ID_OR_FRAGMENT
stepagent-terminal --latest
stepagent-terminal --latest --interval 0.1
stepagent-terminal --snapshot --file "path/to/rollout.jsonl"
stepagent-terminal --version
```

`--file` opens a specific log independently of session discovery. `--latest`
selects once and keeps watching that session. The minimum poll interval is 0.05 s;
the default is 0.25 s. `--snapshot` emits UTF-8 JSON; `--list` emits UTF-8 text.
No sessions yet? Use a directory containing your local Codex rollout logs, or try
the synthetic demo below. The app does not fetch cloud-only conversations.

For a persistent override, configure either environment variable in your shell
profile or operating system settings. For the current shell:

```sh
# macOS / Linux
export CODEX_SESSIONS_DIR="$HOME/my-session-logs"
```

```powershell
# Windows PowerShell
$env:CODEX_SESSIONS_DIR = Join-Path $HOME 'my-session-logs'
```

## Controls

Use a terminal of at least 42 × 12 cells; 120 × 35 is more comfortable. At 110
columns the timeline and details appear side by side.

| Key | Action |
| --- | --- |
| `s` | Select or switch session |
| `↑` / `↓`, `k` / `j` | Select step / scroll focused details |
| `←` / `→` | Move focus between timeline and details |
| `d`, `Enter` | Expand / close details |
| `1` / `2` / `3` / `4` | Content / fields / relationships / RAW |
| `a`, `r` | All layers / toggle RAW |
| `n` / `N`, `Enter`, `b` | Select relationship, open target, go back |
| `u` | Refresh pinned details when new session data arrives |
| `Space`, `f` | Toggle following new steps |
| `g` / `G` | First step / newest step and resume following |
| `PgUp` / `PgDn` | Scroll a page |
| `[` / `]`, `t` | Previous / next turn; filter to selected turn |
| `/` | Search; Enter keeps filter, Esc clears it |
| `Tab`, `m` | Toggle activity matrix |
| `Esc` | Back / clear filters |
| `?` | Help |
| `q`, `Ctrl+C` | Quit and restore terminal |

## Synthetic demo

After installation, run in one terminal:

```sh
stepagent-demo --delay 1 --turns 3
```

Or, with the virtual environment's Python, run `python -m stepagent.demo`.
It prints a command to paste into a second terminal, then writes synthetic turns
to a fresh directory chosen by the operating system's temporary-file API. The
demo log remains there for replay; it never reads or modifies your real sessions.
When running directly from a checkout, open the second terminal in the checkout
too. `python demo.py` is a convenience wrapper.

## Behavior and limits

The app watches what Codex writes to a local rollout file; it is not a model token
stream. Buffering upstream affects freshness. `LIVE` means the view is following
new steps; recent file activity does not prove that an agent process is running.
Call/result correlation uses recorded IDs; relationships distinguish explicit
links from inference. Record order alone does not prove causality.

Incomplete JSONL lines wait for a newline, including split UTF-8 characters.
Malformed lines are counted and skipped. Truncation or file replacement rebuilds
the state; an absent file is retried. In-place edits that preserve size and file
identity are not detected: reselect the session to reload. Large logs consume
memory for parsed records, graph data, and visited detail pages.

Log content is treated as data: embedded commands are never executed. Logs may
contain sensitive conversation and tool content, so use synthetic examples in
public bug reports. Local logs, credentials, virtual environments, and build
outputs are excluded by `.gitignore`.

## Tests and packaging

From an installed checkout or development environment:

```sh
python -m unittest discover -s tests -v
python -m pip install build
python -m build
```

GitHub Actions is configured for macOS, Ubuntu, and Windows with Python 3.10 and
3.14. It runs the suite, builds a wheel and source archive, and checks installation
and a Unicode snapshot from outside the checkout. Real PTY integration tests run
on macOS/Linux; Windows runs parser, reader, CLI, worker, and mocked UI tests.
Interactive Windows rendering still needs a real terminal smoke test.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and release checks.

## License

MIT, copyright 2026 FractalBoy9X. See [LICENSE](LICENSE). The semantic parser and
graph model are derived from the MIT-licensed StepAgent core.

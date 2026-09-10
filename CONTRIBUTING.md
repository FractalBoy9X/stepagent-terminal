# Contributing

Use Python 3.10+ in a virtual environment and install the checkout with
`python -m pip install -e .`. Run `python -m unittest discover -s tests -v`.
Tests create synthetic sessions in temporary directories and must never depend on
a contributor's Codex home, API keys, account, or personal conversation history.

Keep operating-system-specific behavior behind platform checks. Use `pathlib`,
`tempfile`, and `sys.executable` for filesystem and interpreter selection. Do not
change the caller's working directory in launchers. Unix PTY tests are explicitly
skipped on Windows; avoid assuming ncurses bit masks for Windows UI assertions.

The interface and detailed user guide are currently Polish. Keep visible key
bindings and both README/usage documentation consistent when changing behavior.

Before a release:

1. Run the full GitHub Actions OS/Python matrix.
2. Test keyboard input, Unicode, live append, resize, and clean exit in a real
   Windows terminal as well as macOS/Linux.
3. Build with `python -m build`, inspect archive contents, and install the wheel
   into a fresh environment outside the source tree.
4. Verify `stepagent-terminal --help`, `--version`, and a synthetic `--snapshot`.
5. Review the exact Git diff for personal paths, session data, credentials, and
   generated files. Preserve the existing MIT copyright notice.

GitHub Actions validates builds but does not publish packages or create releases.

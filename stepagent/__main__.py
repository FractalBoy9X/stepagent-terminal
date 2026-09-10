from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .core.adapters import IncrementalParser
from .source import JsonlTail, SessionCatalog, default_sessions_dir, expand_path
from . import __version__


def positive_interval(value):
    interval = float(value)
    if not math.isfinite(interval) or interval < .05:
        raise argparse.ArgumentTypeError("Interwał musi być skończoną liczbą >= 0.05 s")
    return interval


def main(argv=None):
    # Redirected Windows streams may otherwise use a legacy ANSI code page.
    # Emit portable UTF-8 JSON/text, including Polish and CJK session content.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    cli = argparse.ArgumentParser(description="StepAgent Terminal — obserwuj lokalną sesję Codexa na żywo.")
    cli.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    cli.add_argument("--sessions-dir", type=Path, default=default_sessions_dir(), help="katalog sesji (domyślnie CODEX_SESSIONS_DIR lub CODEX_HOME/sessions)")
    choose = cli.add_mutually_exclusive_group()
    choose.add_argument("--session", help="ID sesji lub jego jednoznaczny fragment")
    choose.add_argument("--file", type=Path, help="konkretny plik JSONL")
    choose.add_argument("--latest", action="store_true", help="wybierz ostatnio zapisaną sesję")
    cli.add_argument("--interval", type=positive_interval, default=.25, help="interwał odczytu w sekundach (domyślnie 0.25)")
    cli.add_argument("--list", action="store_true", help="wypisz dostępne sesje bez interfejsu")
    cli.add_argument("--snapshot", action="store_true", help="jednorazowa lista znormalizowanych kroków (JSON)")
    args = cli.parse_args(argv)
    args.sessions_dir = expand_path(args.sessions_dir).resolve()
    catalog = SessionCatalog(args.sessions_dir)
    sessions = catalog.scan() if args.list or args.session or (args.snapshot and args.latest) else []
    if args.list:
        from datetime import datetime
        for session in sessions:
            import unicodedata
            def safe(value):
                return " ".join("".join(c if not unicodedata.category(c).startswith("C") else " " for c in value).split())
            print(f"{safe(session.session_id)}\t{datetime.fromtimestamp(session.mtime).astimezone().isoformat(timespec='seconds')}\t{safe(session.title)}\t{safe(session.cwd)}")
        if catalog.error:
            print(catalog.error, file=sys.stderr)
        return 0 if sessions else 1
    path = expand_path(args.file).resolve() if args.file else None
    if args.session:
        matches = [s for s in sessions if args.session in s.session_id]
        if len(matches) != 1:
            cli.error(f"Dopasowano {len(matches)} sesji; użyj --list i podaj jednoznaczne ID.")
        path = matches[0].path
    if args.snapshot:
        if args.latest and sessions:
            path = sessions[0].path
        if path is None:
            cli.error("--snapshot wymaga --file, --session lub --latest z dostępną sesją")
        tail = JsonlTail(path)
        parser = IncrementalParser(path.name)
        while True:
            records, reset = tail.poll()
            if tail.error:
                cli.error(tail.error)
            if reset:
                parser = IncrementalParser(path.name)
            for record in records:
                parser.feed(record)
            if tail.offset >= tail.size:
                break
        doc = parser.snapshot()
        print(json.dumps(dict(session_id=doc.session_id, model=doc.agent.get("model"),
            raw_records=len(doc.raw_records), bad_lines=tail.bad_lines, incomplete_line=bool(tail.buffer),
            interactions=[dict(step=i.index + 1, turn=i.conversation_turn_number, action=i.action,
                kind=i.kind.value, state=i.lifecycle.value, call_id=i.call_id,
                source_records=len(i.raw_record_ids), summary=i.detail[:180]) for i in doc.interactions]), ensure_ascii=False, indent=2))
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        cli.error("Uruchom interfejs w interaktywnym terminalu; do potoku użyj --list lub --snapshot.")
    try:
        import curses
        from .ui import TerminalApp
    except ImportError:
        cli.error("Brak curses. macOS/Linux: Python z curses. Windows: py -m pip install windows-curses.")
    try:
        curses.wrapper(TerminalApp(args.sessions_dir, args.interval, path, args.latest).run)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print(f"Nie można uruchomić terminalu ({exc}). Użyj terminalu z obsługą curses (np. Windows Terminal lub terminal macOS/Linux); na Unix sprawdź TERM.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

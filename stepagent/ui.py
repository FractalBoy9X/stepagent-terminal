"""Responsive curses UI (windows-curses on Windows); no server or network."""
from __future__ import annotations

import curses
import queue
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

from .details import Span, cells, detail_rows, wrap_rows
from .detail_document import DetailDocument, LAYERS
from .paging import PagedRows
from .worker import Watcher
from .timeline import FAMILIES, labels, search_text, state_label


# The loop already redraws every 50 ms (screen.timeout), so the pulse costs
# nothing extra. Thickness, not colour, so it reads as motion and never blinks.
# Even the thinnest frame stays heavier than the inactive "│" separator.
PULSE = "▎▍▌█▌▍"

SELECTION_PAIR = 8
HELP = [
    "STEPAGENT / TERMINAL — skróty",
    "",
    "s              wybór / zmiana sesji",
    "/              wyszukiwanie w sesjach lub krokach; Enter zatwierdza",
    "W szukajce: Esc usuwa filtr i wraca do nawigacji; Enter zachowuje filtr.",
    "Esc            usuń filtr / wróć / zamknij pomoc",
    "↑ ↓ lub k j    wybierz krok; w szczegółach przewijaj treść",
    "PgUp PgDn      przewiń o stronę",
    "← →            przenieś fokus: kroki / szczegóły",
    "Enter lub d    pełnoekranowe szczegóły kroku",
    "1 2 3 4        treść / wszystkie pola / relacje / źródła RAW",
    "a / r          wszystkie warstwy / przełącz RAW",
    "n / N, Enter   następna/poprzednia relacja, otwórz jej cel",
    "b / u          wróć z relacji / wczytaj nowe dane bez resetu pozycji",
    "Spacja lub f   zatrzymaj / wznów podążanie za nowymi krokami",
    "g / G          pierwszy / najnowszy krok (G włącza śledzenie)",
    "[ / ]          poprzednia / następna tura",
    "t              tylko wybrana tura / wszystkie tury",
    "Tab lub m      oś kroków / macierz aktywności",
    "← → w macierzy wybierz turę; Enter otwiera jej kroki",
    "?              pomoc",
    "q / Ctrl+C     wyjście",
    "",
    "Pulsujący pasek przy krawędzi wskazuje panel, który przyjmie ↑↓.",
    "▶ i tło obu wierszy = aktywny wybór; │ = wybór w tle.",
    "Oś: rodzina / typ, odmiana i stan. Polecenia są w szczegółach.",
    "≈ = akcja wnioskowana; zakończony nie oznacza sukcesu.",
    "Kolory: rozmowa • analiza/plan • narzędzia • pliki • protokół.",
    "Wszystkie kroki po pierwszym prompcie są widoczne, także protokół.",
    "Inicjalizacja przed pierwszym promptem jest zawsze pomijana.",
    "→ oznacza kolejność zapisu, nie dowód zależności przyczynowej.",
    "Relacje w szczegółach podają podstawę oraz jawne / wnioskowane.",
    "Wywołanie i wynik ze wspólnym call_id stanowią jeden krok.",
    "NOWY ZAPIS = plik zmieniony w ostatnich 120 s; nie wykrycie procesu.",
    "WIDOK LIVE = odczyt co 250 ms (domyślnie). Codex może buforować zapis.",
    "Pauza zatrzymuje podążanie widoku; odczyt nadal działa.",
]


def clean(value) -> str:
    return "".join(c if c in "\n\t" or not unicodedata.category(c).startswith("C") else " " for c in str(value)).expandtabs(4)


def clip(value, width: int) -> str:
    """Clip by terminal cells, including wide CJK characters and emoji."""
    result = []
    used = 0
    for char in clean(value).replace("\n", " "):
        cells = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + cells > max(0, width):
            break
        result.append(char)
        used += cells
    return "".join(result)


def age(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def ellipsize(value, width):
    value = clean(value).replace("\n", " ")
    if cells(value) <= width:
        return value
    return clip(value, max(0, width - 1)) + ("…" if width > 0 else "")


class TerminalApp:
    def __init__(self, root: Path, interval: float = .25, initial: Path | None = None, latest: bool = False):
        self.watcher = Watcher(root, interval)
        self.path = initial
        self.latest = latest
        self.sessions = []
        self.catalog_error = ""
        self.session_cursor = 0
        self.screen = "timeline" if initial else "sessions"
        self.doc = self.graph = None
        self.info = {}
        self.version = None
        self.items = []
        self.cursor = 0
        self.follow = True
        self.turn_filter = None
        self.query = ""
        self.session_query = ""
        self.editing = False
        self.focus = "timeline"
        self.detail_scroll = 0
        self.detail_full = False
        self.raw = False
        self.help = False
        self.matrix_turn = None
        self.matrix_row = 0
        self.detail_cache = (None, [])
        self.notice = ""
        self.colors = False
        self.selection_color = False
        self.layer = 0
        self.relation_cursor = 0
        self.related_target = None
        self.detail_document = None
        self.document_version = None
        self.pages = {}
        self.scrolls = {}
        self.back_stack = []

    def pulse(self):
        return PULSE[int(time.monotonic() * 4) % len(PULSE)]  # ~1.5 s cycle

    def attr(self, color=0, bold=False):
        return (curses.color_pair(color) if self.colors else 0) | (curses.A_BOLD if bold else 0)

    def put(self, y, x, value, width=None, color=0, bold=False, selected=False):
        h, w = self.win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        width = max(0, min(width if width is not None else w - x, w - x - 1))
        value = clip(value, width)
        if selected:
            # A fixed foreground AND background, independent of family colors
            # and terminal defaults. Monochrome still has the same geometry.
            # Some terminals turn bold black into bright gray, reducing contrast.
            attr = self.attr(SELECTION_PAIR) if self.selection_color else self.attr(0, bold) | curses.A_REVERSE
        else:
            attr = self.attr(color, bold)
        try:
            self.win.addstr(y, x, value, attr)
            if selected:
                cells = sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in value)
                self.win.addstr(y, x + cells, " " * max(0, width - cells), attr)
        except curses.error:
            pass  # Resize and bottom-right writes are normal terminal races.

    def filtered_sessions(self):
        query = self.session_query.casefold()
        return [s for s in self.sessions if query in f"{s.title} {s.cwd} {s.session_id}".casefold()]

    def rebuild(self):
        old = self.items[self.cursor].interaction_id if self.items and self.cursor < len(self.items) else None
        query = self.query.casefold()
        self.items = [i for i in self.doc.interactions if
            i.conversation_turn_number > 0 and
            (self.turn_filter is None or i.conversation_turn_number == self.turn_filter) and
            (not query or query in f"{i.detail} {i.action} {i.label} {i.call_id} {search_text(i)}".casefold())] if self.doc else []
        if self.follow:
            self.cursor = max(0, len(self.items) - 1)
        else:
            self.cursor = next((n for n, item in enumerate(self.items) if item.interaction_id == old), min(self.cursor, max(0, len(self.items) - 1)))
        self.detail_cache = (None, [])

    def select_session(self, path):
        self.path = path
        self.doc = self.graph = None
        self.version = None
        self.items = []
        self.info = {}
        self.cursor = self.detail_scroll = 0
        self.follow = True
        self.query = ""
        self.turn_filter = None
        self.detail_full = False
        self.screen = "timeline"
        self.reset_details()
        self.watcher.select(path)

    def reset_details(self):
        self.related_target = self.detail_document = None
        self.document_version = None
        self.pages = {}
        self.scrolls = {}
        self.back_stack = []
        self.layer = self.relation_cursor = self.detail_scroll = 0
        self.raw = False

    def detail_target(self):
        return self.related_target or (self.items[self.cursor].interaction_id if self.items else None)

    def save_scroll(self):
        if self.detail_document:
            self.scrolls[self.detail_document.target, self.layer] = self.detail_scroll

    def detail_context(self, refresh=False):
        target = self.detail_target()
        if target is None or self.doc is None:
            return None
        changed = self.detail_document is None or self.detail_document.target != target
        live = self.follow and self.focus != "detail" and self.document_version != self.version
        if changed or refresh or live:
            if changed:
                self.save_scroll()
                self.detail_scroll = self.scrolls.get((target, self.layer), 0)
                self.relation_cursor = 0
            self.detail_document = DetailDocument(target, self.doc, self.graph, self.path)
            self.document_version = self.version
            self.pages = {}
        return self.detail_document

    def set_layer(self, layer):
        self.detail_context()
        self.save_scroll()
        self.layer = layer
        self.raw = layer == 3
        self.detail_scroll = self.scrolls.get((self.detail_target(), layer), 0)
        self.focus = "detail"
        self.follow = False

    def open_relation(self):
        document = self.detail_context()
        if not document or not document.edges:
            return
        edge = document.edges[self.relation_cursor % len(document.edges)]
        target = document.other(edge)
        if target not in document.nodes:
            self.notice = "Cel relacji nie istnieje w tej migawce grafu. Dane relacji pozostają dostępne."
            return
        self.save_scroll()
        self.back_stack.append((document, self.document_version, self.layer, self.detail_scroll,
                                self.relation_cursor, self.related_target, self.detail_full, self.pages,
                                self.items[self.cursor].interaction_id if self.items else None,
                                self.query, self.turn_filter))
        self.back_stack = self.back_stack[-32:]
        self.related_target = target
        # Follow against the same snapshot; LIVE must not change the evidence
        # midway through reading a chain of relations.
        self.detail_document = DetailDocument(target, document.doc, document.graph, document.path)
        self.pages = {}
        self.layer = self.detail_scroll = self.relation_cursor = 0
        self.raw = False
        self.focus = "detail"
        self.follow = False

    def back_relation(self):
        if not self.back_stack:
            return False
        self.save_scroll()
        (self.detail_document, self.document_version, self.layer, self.detail_scroll,
         self.relation_cursor, self.related_target, self.detail_full, self.pages,
         cursor_id, self.query, self.turn_filter) = self.back_stack.pop()
        self.follow = False
        self.rebuild()
        self.cursor = next((n for n, item in enumerate(self.items) if item.interaction_id == cursor_id), self.cursor)
        self.raw = self.layer == 3
        self.focus = "detail"
        return True

    def receive(self):
        try:
            while True:
                selected_path = None
                filtered = self.filtered_sessions()
                if filtered and self.session_cursor < len(filtered):
                    selected_path = filtered[self.session_cursor].path
                self.sessions, self.catalog_error = self.watcher.catalog_updates.get_nowait()
                filtered = self.filtered_sessions()
                self.session_cursor = next((n for n, s in enumerate(filtered) if s.path == selected_path), 0)
                if self.latest and self.sessions and self.path is None:
                    self.latest = False
                    self.select_session(self.sessions[0].path)
        except queue.Empty:
            pass
        try:
            while True:
                value = self.watcher.updates.get_nowait()
                if value.get("path") != self.path:
                    continue
                if "doc" not in value:
                    self.notice = value["error"]
                    continue
                self.info = value
                if value["version"] != self.version or value.get("reset"):
                    if value.get("reset"):
                        self.reset_details()
                    self.doc, self.graph, self.version = value["doc"], value["graph"], value["version"]
                    self.rebuild()
                self.notice = value.get("error", "")
        except queue.Empty:
            pass

    def draw_sessions(self, h, w):
        self.put(2, 2, "WYBIERZ SESJĘ CODEXA", color=2, bold=True)
        self.put(3, 2, f"{self.watcher.catalog.root}  ·  {len(self.sessions)} sesji", color=6)
        self.draw_search(5, w - 4, self.session_query, "/ Szukaj sesji")
        sessions = self.filtered_sessions()
        self.session_cursor = min(self.session_cursor, max(0, len(sessions) - 1))
        count = max(1, (h - 10) // 3)
        start = max(0, self.session_cursor - count + 1)
        for n, session in enumerate(sessions[start:start + count], start):
            y = 7 + (n - start) * 3
            selected = n == self.session_cursor and not self.editing
            marker = "● NOWY ZAPIS" if session.recent else "○ ZAPISANA"
            self.put(y, 2, f"{marker:13} {age(session.mtime):>4}  {session.title}", w - 4, color=3 if session.recent else 0, selected=selected)
            self.put(y + 1, 4, f"{session.cwd or 'Brak cwd'}  ·  {session.session_id}", w - 6, color=6)
        if not sessions:
            self.put(8, 2, "Brak pasujących sesji. Lista odświeża się co 3 s.", color=4)
            self.put(10, 2, self.catalog_error or "Uruchom rozmowę w Codexie lub podaj --sessions-dir.", color=6)
        controls = "Esc: wyjdź i usuń filtr" if self.editing else "↑↓ wybierz   Enter obserwuj   / szukaj   Esc wróć   q wyjdź"
        self.put(h - 2, 2, controls, w - 4, color=2, bold=self.editing)

    def draw_search(self, y, width, query, idle):
        if self.editing:
            prefix = "WYSZUKIWANIE: "
            room = max(0, width - cells(prefix) - 1)
            # Keep the insertion point visible even with a long Unicode query.
            tail = clip(clean(query)[::-1], max(0, room - 1))[::-1]
            visible = "…" + tail if cells(clean(query)) > room else clean(query)
            self.put(y, 2, prefix + visible + "▏", width, selected=True)
        elif query:
            self.put(y, 2, ellipsize("FILTR AKTYWNY: " + query, width), width, color=4, bold=True)
        else:
            self.put(y, 2, idle, width, color=4)

    def heading(self, h, w):
        state = "LIVE" if self.follow else "HISTORIA"
        self.put(0, 0, f" STEPAGENT / TERMINAL   ● {state} ", w - 1, color=3, bold=True)
        if self.screen == "sessions":
            return
        if self.path:
            title = next((s.title for s in self.sessions if s.path == self.path), self.path.stem)
            self.put(1, 2, title, w - 4, bold=True)
        if self.doc:
            total = sum(i.conversation_turn_number > 0 for i in self.doc.interactions)
            initialization = len(self.doc.interactions) - total
            turns = max((i.conversation_turn_number for i in self.doc.interactions), default=0)
            status = self.activity_status()
            tokens = self.graph.metrics.get("total_tokens") if self.graph else None
            usage = f" · {tokens:,} tok." if isinstance(tokens, int) else ""
            self.put(2, 2, f"{status}  ·  {turns} tur  ·  {len(self.items)}/{total} kroków  ·  {self.doc.agent.get('model', '?')}{usage}", w - 4, color=2)
            self.put(3, 2, f"Zapis {age(self.info.get('mtime', 0))} temu  ·  {self.info.get('offset', 0):,}/{self.info.get('size', 0):,} B  ·  {len(self.doc.raw_records)} rekordów  ·  pominięto {initialization} inicjalizacyjnych", w - 4, color=6)
        tabs = "[OŚ KROKÓW]   MACIERZ" if self.screen == "timeline" else "OŚ KROKÓW   [MACIERZ]"
        turn = " · Tura " + str(self.turn_filter) if self.turn_filter is not None else ""
        self.draw_search(4, w - 4, self.query, tabs + "   / szukaj" + turn)

    def activity_status(self):
        if not self.doc or not self.doc.raw_records:
            return "OCZEKIWANIE NA ZAPIS"
        for raw in reversed(self.doc.raw_records):
            payload = raw.record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if kind in {"task_complete", "turn_complete", "turn_completed", "task_completed"}:
                return "TURA ZAKOŃCZONA"
            if kind in {"turn_aborted", "shutdown_complete"}:
                return "TURA PRZERWANA"
            if kind in {"task_started", "turn_started", "user_message"}:
                return "TURA OTWARTA W LOGU"
        return "OBSERWACJA ZAPISU"

    def draw_timeline(self, top, bottom, width, active=True):
        available = width - 3
        active = active and not self.editing
        focus = "KROKI · fokus: wyszukiwanie" if self.editing else "KROKI · ↑↓ wybór" if active else "KROKI · fokus: szczegóły"
        self.put(top - 1, 2, focus, available,
                 color=4 if active else 0, bold=active)
        self.put(top, 2, ellipsize("TURA / KROK  TYP / ODMIANA", available - 6), available - 6, color=0, bold=True)
        self.put(top, width - 5, "STAN", 4, bold=True)
        rows = max(1, (bottom - top - 1) // 2)
        start = max(0, self.cursor - rows + 1)
        for n, item in enumerate(self.items[start:start + rows], start):
            y = top + 1 + (n - start) * 2
            family, kind, variant, color = labels(item)
            status, status_color = state_label(item)
            try:
                stamp = datetime.fromisoformat(item.timestamp.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
            except ValueError:
                stamp = ""
            chosen = n == self.cursor
            selected = chosen and active
            marker = "▶" if selected else "│" if chosen else " "
            for offset in (0, 1):
                self.put(y + offset, 2, marker if offset == 0 else "│" if chosen else " ",
                         available, bold=chosen, selected=selected)

            # Reserve the state first so narrow panes never cut it off.
            state_x = width - 1 - cells(status)
            identity = f"T{item.conversation_turn_number:02}/#{item.index + 1}"
            identity_width = max(cells(identity), 10 if available >= 50 else 7)
            type_x = 4 + identity_width + 1
            type_width = max(0, state_x - type_x - 1)
            self.put(y, 4, identity, identity_width, bold=chosen, selected=selected)
            title = family if kind == family else family + " / " + kind
            # Keep the type ahead of the family when both do not fit.
            if cells(title) > type_width:
                title = kind
            title = ellipsize(title, type_width)
            self.put(y, type_x, title, type_width, bold=chosen, selected=selected)
            if not selected and title.startswith(family):
                self.put(y, type_x, family, min(cells(family), type_width), color=color, bold=chosen)
            self.put(y, state_x, status, cells(status), color=status_color, selected=selected)

            # Drop the clock before duration, and duration before the subtype.
            second_width = available - 2
            duration = f"{item.duration_ms / 1000:.1f}s" if item.duration_ms is not None else ""
            suffix = ""
            if duration and cells(variant + " · " + duration) <= second_width:
                suffix = " · " + duration
            if stamp and cells(variant + suffix + " · " + stamp) <= second_width:
                suffix += " · " + stamp
            self.put(y + 1, 4, ellipsize(variant, second_width - cells(suffix)) + suffix,
                     second_width, selected=selected)
        if not self.items:
            self.put(top + 3, 2, "Oczekiwanie na kroki…" if not self.doc else "Brak kroków dla tego filtra.", width - 4, color=6)
            self.put(top + 5, 2, "/: filtr  Esc: usuń filtr · oczekiwanie na prompt", width - 4, color=6)

    def detail_lines(self, width):
        if not self.items:
            return [[Span("Wybierz krok, aby zobaczyć jego treść i relacje.", 6)]]
        item = self.items[self.cursor]
        key = (self.version, item.interaction_id, self.raw, width)
        if self.detail_cache[0] != key:
            rows = detail_rows(item, self.doc, self.graph, self.path, self.raw)
            self.detail_cache = (key, wrap_rows(rows, max(2, width - 1)))
        return self.detail_cache[1]

    def draw_detail(self, top, bottom, x, width):
        document = self.detail_context()
        if not document:
            self.put(top, x, "Wybierz krok, aby zobaczyć szczegóły.", width, color=6)
            return
        # Four fixed identity lines in normal windows; compact header retains
        # room for content at the minimum supported terminal size.
        header = document.header()
        header_count = 4 if bottom - top >= 12 else 1
        for row, spans in enumerate(header[:header_count]):
            self.draw_spans(top + row, x, width, spans)
        labels = ("1 Treść", "2 Pola", "3 Rel.", "4 RAW", "a Σ") if width < 60 else ("1 Treść", "2 Pola", "3 Relacje", "4 RAW", "a Wszystko")
        self.draw_spans(top + header_count, x, width,
            [Span(("[" + label + "]" if n == self.layer else label) + " ", 3 if n == self.layer else 2, n == self.layer)
             for n, label in enumerate(labels)])
        body = top + header_count + 1
        available = max(0, bottom - body - 2)
        key = (self.layer, self.relation_cursor if self.layer == 2 else 0, width)
        if key not in self.pages:
            # Resizing creates a fresh wrapper, but never resets logical scroll.
            if len(self.pages) >= 10:
                self.pages.pop(next(iter(self.pages)))
            self.pages[key] = PagedRows(document.rows(self.layer, self.relation_cursor), max(2, width - 1))
        page = self.pages[key]
        lines = page.ensure(self.detail_scroll + available + 1)
        if page.complete:
            self.detail_scroll = min(self.detail_scroll, max(0, len(lines) - available))
        for n, line in enumerate(lines[self.detail_scroll:self.detail_scroll + available]):
            self.draw_spans(body + n, x, width, line)
        pending = " · NOWE DANE SESJI: u" if self.document_version != self.version else ""
        label = "Wszystko" if self.layer == 4 else LAYERS[self.layer]
        total = str(len(lines)) if page.complete else f"{len(lines)}+"
        footer = f"{label} {self.detail_scroll + 1}/{total}{pending}"
        if self.back_stack:
            footer += " · b wróć"
        if bottom - 2 >= body:
            self.put(bottom - 2, x, footer, width, color=4 if pending else 6)
        controls = "d zmniejsz" if self.detail_full else "d powiększ"
        controls += " · r treść" if self.layer == 3 else " · r RAW"
        self.put(bottom - 1, x, controls + " · ↑↓ przewiń", width, color=2)

    def draw_spans(self, y, x, width, line):
        column = 0
        for span in line:
            self.put(y, x + column, span.text, max(0, width - column), color=span.color, bold=span.bold)
            column += cells(span.text)

    def draw_matrix(self, top, bottom, w):
        if not self.doc:
            return
        turns = sorted({i.conversation_turn_number for i in self.items})
        if not turns:
            self.put(top + 2, 2, "Brak kroków dla tego filtra.", color=6)
            return
        if self.matrix_turn not in turns:
            self.matrix_turn = turns[-1]
        capacity = max(1, (w - 20) // 7)
        selected = turns.index(self.matrix_turn)
        start = max(0, selected - capacity + 1)
        visible = turns[start:start + capacity]
        counts = Counter((i.family.value, i.conversation_turn_number) for i in self.items)
        self.put(top, 2, "AKTYWNOŚĆ", color=6)
        for col, turn in enumerate(visible):
            self.put(top, 19 + col * 7, f"T{turn:02}", 6, color=2, selected=turn == self.matrix_turn and not self.editing)
        available_rows = max(1, bottom - top - 5)
        self.matrix_row = min(self.matrix_row, max(0, len(FAMILIES) - available_rows))
        family_rows = list(FAMILIES.items())[self.matrix_row:self.matrix_row + available_rows]
        for row, (family, (label, color)) in enumerate(family_rows):
            y = top + row + 2
            if y >= bottom - 3:
                break
            self.put(y, 2, label, 16, color=color)
            for col, turn in enumerate(visible):
                count = counts[family, turn]
                self.put(y, 19 + col * 7, f"{count:>4}" if count else "   ·", 6, color=color if count else 6, selected=turn == self.matrix_turn and not self.editing)
        prompt = next((i.detail for i in self.doc.interactions if i.conversation_turn_number == self.matrix_turn and i.metadata.get("conversation_message_classification") == "conversation_start"), "Inicjalizacja")
        self.put(bottom - 2, 2, prompt, w - 4)
        self.put(bottom - 1, 2, "←→ tura · ↑↓ kategorie · Enter kroki · Tab oś", color=2)

    def draw(self):
        self.win.erase()
        h, w = self.win.getmaxyx()
        if h < 12 or w < 42:
            self.put(0, 0, "StepAgent: powiększ terminal do 42×12.", color=4)
            self.put(2, 0, "Odczyt trwa. q: wyjście.")
        elif self.help:
            for y, line in enumerate(HELP[:h - 2]):
                self.put(y, 2, line, w - 4, color=2 if y == 0 else 0)
            self.put(h - 1, 2, "? lub Esc — zamknij pomoc", color=2)
        else:
            self.heading(h, w)
            if self.screen == "sessions":
                self.draw_sessions(h, w)
            else:
                top, bottom = 6, h - 3
                if self.screen == "matrix":
                    self.draw_matrix(top, bottom, w)
                elif self.detail_full or (w < 110 and self.focus == "detail"):
                    self.draw_detail(top, bottom, 2, w - 4)
                elif w >= 110:
                    split = int(w * .54)
                    detail_active = self.focus == "detail" and not self.editing
                    self.draw_timeline(top, bottom, split - 1, not detail_active)
                    mark = self.pulse()
                    for y in range(top, bottom):
                        self.put(y, split - 1, mark if detail_active else "│", 1,
                                 color=4 if detail_active else 6, bold=detail_active)
                        if not detail_active and not self.editing:
                            self.put(y, 0, mark, 1, color=4, bold=True)
                    self.draw_detail(top, bottom, split + 1, w - split - 3)
                else:
                    self.draw_timeline(top, bottom, w)
                warning = self.notice
                if self.info.get("bad_lines"):
                    warning += f"  Pominięte błędne linie: {self.info['bad_lines']}"
                if self.info.get("partial"):
                    warning += "  Oczekiwanie na koniec linii"
                self.put(h - 3, 2, warning or "→ kolejność zapisu · call_id łączy wywołanie z wynikiem", w - 4, color=4 if warning else 6)
                controls = "d zmniejsz" if self.detail_full else "d powiększ"
                controls += "  r RAW  ? pomoc  q wyjdź"
                if w >= 80:
                    controls += "  s sesje  ↑↓ wybierz  f live"
                if w >= 115:
                    controls += "  → szczegóły  Tab macierz  / filtr"
                if self.editing:
                    controls = "Esc: wyjdź i usuń filtr"
                self.put(h - 2, 2, controls, w - 4, color=2, bold=self.editing)
            query = self.session_query if self.screen == "sessions" else self.query
            if self.editing:
                self.put(h - 1, 2, "Enter: nawigacja z filtrem", w - 4, color=2)
            elif query:
                self.put(h - 1, 2, "Esc: wróć / usuń filtr · /: edytuj", w - 4, color=4)
        self.win.refresh()

    def key(self, key):
        if self.editing:
            field = "session_query" if self.screen == "sessions" else "query"
            value = getattr(self, field)
            if key in ("\n", "\r", curses.KEY_ENTER, "\x1b"):
                self.editing = False
                if key == "\x1b":
                    setattr(self, field, "")
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                setattr(self, field, value[:-1])
            elif isinstance(key, str) and key.isprintable():
                setattr(self, field, value + key)
            self.session_cursor = 0
            self.rebuild()
            return True
        if key in ("q", "\x03"):
            return False
        if key == "?":
            self.help = not self.help
            return True
        if self.help:
            if key == "\x1b":
                self.help = False
            return True
        if key == "/":
            self.editing = True
            if self.screen != "sessions":
                self.save_scroll()
                self.follow = False
                self.related_target = None
                self.back_stack = []
                self.focus = "timeline"
                self.detail_full = False
            return True
        if key == "s":
            self.screen = "sessions"
            return True
        if self.screen == "sessions":
            sessions = self.filtered_sessions()
            if key in (curses.KEY_DOWN, "j"):
                self.session_cursor = min(len(sessions) - 1, self.session_cursor + 1)
            elif key in (curses.KEY_UP, "k"):
                self.session_cursor = max(0, self.session_cursor - 1)
            elif key in ("\n", "\r", curses.KEY_ENTER) and sessions:
                self.select_session(sessions[self.session_cursor].path)
            elif key == "\x1b":
                if self.session_query:
                    self.session_query = ""
                elif self.path:
                    self.screen = "timeline"
            return True
        if key in ("\t", "m"):
            self.screen = "matrix" if self.screen != "matrix" else "timeline"
            return True
        if key == "\x1b":
            if self.back_relation():
                return True
            self.related_target = None
            self.query = ""
            self.turn_filter = None
            self.detail_full = False
            self.focus = "timeline"
            self.screen = "timeline"
            self.rebuild()
            return True
        if self.screen == "matrix":
            if key in (curses.KEY_UP, "k"):
                self.matrix_row = max(0, self.matrix_row - 1)
            elif key in (curses.KEY_DOWN, "j"):
                self.matrix_row += 1
            turns = sorted({i.conversation_turn_number for i in self.items})
            if turns:
                n = turns.index(self.matrix_turn) if self.matrix_turn in turns else len(turns) - 1
                if key == curses.KEY_LEFT:
                    self.matrix_turn = turns[max(0, n - 1)]
                elif key == curses.KEY_RIGHT:
                    self.matrix_turn = turns[min(len(turns) - 1, n + 1)]
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    self.turn_filter = turns[n]
                    self.screen = "timeline"
                    self.follow = False
                    self.rebuild()
                    self.cursor = 0
            return True
        if key in ("1", "2", "3", "4", "a", "r"):
            self.set_layer(4 if key == "a" and self.layer != 4 else 0 if key == "a" else
                           (0 if self.layer == 3 else 3) if key == "r" else int(key) - 1)
            return True
        if key == "u":
            self.detail_context(refresh=True)
            return True
        if key == "b":
            self.back_relation()
            return True
        if self.focus == "detail" and self.layer == 2:
            document = self.detail_context()
            if key in ("n", "N") and document and document.edges:
                self.relation_cursor = (self.relation_cursor + (1 if key == "n" else -1)) % len(document.edges)
                self.detail_scroll = 0
                return True
            if key in ("\n", "\r", curses.KEY_ENTER):
                self.open_relation()
                return True
        if key in (" ", "f", "G"):
            self.follow = True if key == "G" else not self.follow
            if self.follow:
                self.related_target = None
                self.back_stack = []
                self.focus = "timeline"
                self.detail_context(refresh=True)
                self.turn_filter = None
                self.rebuild()
                self.detail_scroll = 0
        elif key == "g":
            self.save_scroll()
            self.related_target = None
            self.follow = False
            self.cursor = 0
        elif key in ("d", "\n", "\r", curses.KEY_ENTER):
            self.detail_full = not self.detail_full
            self.focus = "detail" if self.detail_full else "timeline"
            if self.focus == "detail":
                if key == "d":
                    self.set_layer(0)
                self.detail_context()
                self.follow = False
        elif key == curses.KEY_RIGHT:
            self.detail_context()
            self.focus = "detail"
            self.follow = False
        elif key == curses.KEY_LEFT:
            self.save_scroll()
            self.related_target = None
            self.focus = "timeline"
            self.detail_full = False
        elif key == "t" and self.items:
            self.turn_filter = None if self.turn_filter is not None else self.items[self.cursor].conversation_turn_number
            self.follow = False
            self.rebuild()
        elif key in ("[", "]") and self.items:
            current = self.items[self.cursor].conversation_turn_number
            choices = [n for n, i in enumerate(self.items) if i.conversation_turn_number > current] if key == "]" else [n for n, i in enumerate(self.items) if i.conversation_turn_number < current]
            if choices:
                self.save_scroll()
                self.related_target = None
                self.cursor = choices[0] if key == "]" else choices[-1]
                self.follow = False
        elif key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_NPAGE, curses.KEY_PPAGE, "j", "k"):
            delta = -1 if key in (curses.KEY_UP, "k", curses.KEY_PPAGE) else 1
            if key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                delta *= max(1, (self.win.getmaxyx()[0] - 10) // 2)
            if self.focus == "detail":
                self.follow = False
                self.detail_scroll = max(0, self.detail_scroll + delta)
            else:
                self.save_scroll()
                self.related_target = None
                self.follow = False
                self.cursor = max(0, min(len(self.items) - 1, self.cursor + delta))
        return True

    def run(self, screen):
        self.win = screen
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            for n, color in enumerate([curses.COLOR_WHITE, curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_MAGENTA, curses.COLOR_BLUE, curses.COLOR_RED], 1):
                curses.init_pair(n, color, background)
            self.colors = True
            if curses.COLOR_PAIRS > SELECTION_PAIR:
                try:
                    curses.init_pair(SELECTION_PAIR, curses.COLOR_BLACK, curses.COLOR_WHITE)
                    self.selection_color = True
                except curses.error:
                    pass
        screen.keypad(True)
        screen.timeout(50)
        self.watcher.thread.start()
        if self.path:
            self.watcher.select(self.path)
        try:
            while True:
                self.receive()
                self.draw()
                try:
                    key = screen.get_wch()
                except curses.error:
                    continue
                if not self.key(key):
                    break
        finally:
            self.watcher.stop.set()
            self.watcher.thread.join(timeout=2)

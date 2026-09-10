"""Typed detail presenters and styled terminal text; independent of curses."""
from __future__ import annotations

import json
import re
import shlex
import unicodedata
from dataclasses import dataclass
from itertools import chain


@dataclass(frozen=True)
class Span:
    text: str
    color: int = 0
    bold: bool = False
    # Leading spans that set `hang` form a row prefix (a diff gutter and its
    # +/- marker); wrapping repeats it, so a wrapped line keeps its marker.
    hang: str | None = None
    # `fill` stretches an empty span with a repeated character to the panel
    # edge; only wrapping knows the width, so it expands the text there.
    fill: str | None = None


@dataclass(frozen=True)
class Profile:
    title: str
    section: str
    color: int
    mode: str
    fields: tuple[str, ...] = ()


# Every domain kind has an explicit presentation contract, including uncommon
# protocol, control, media and future/unknown records. Actions refine it below.
PROFILES = {
    "session": Profile("SESJA", "METRYKA SESJI", 2, "json", ("cwd", "model", "cli_version")),
    "agent": Profile("AGENT", "KONFIGURACJA AGENTA", 5, "json", ("agent_id", "model", "role")),
    "turn": Profile("TURA", "KONTEKST TURY", 2, "json", ("turn_id", "status")),
    "message": Profile("WIADOMOŚĆ", "TREŚĆ WIADOMOŚCI", 2, "markdown", ("role", "channel")),
    "reasoning": Profile("ANALIZA", "ZAPISANE PODSUMOWANIE ANALIZY", 5, "markdown"),
    "plan": Profile("PLAN", "KROKI PLANU", 5, "plan", ("explanation",)),
    "plan_step": Profile("KROK PLANU", "ZADANIE", 5, "plan", ("step", "status")),
    "command": Profile("KOMENDA", "POLECENIE", 3, "command", ("cwd", "workdir", "shell", "session_id", "process_id", "timeout_ms")),
    "terminal_io": Profile("WEJŚCIE TERMINALA", "PRZESŁANE ZNAKI", 3, "code", ("session_id", "process_id", "chars", "stdin")),
    "tool_call": Profile("NARZĘDZIE", "ARGUMENTY NARZĘDZIA", 3, "json", ("name", "tool")),
    "tool_result": Profile("WYNIK NARZĘDZIA", "ZWRÓCONE DANE", 3, "json", ("name",)),
    "tool_search": Profile("WYSZUKIWANIE NARZĘDZI", "ZAPYTANIE O NARZĘDZIA", 2, "json", ("query", "limit")),
    "mcp_server": Profile("SERWER MCP", "KONFIGURACJA / STAN SERWERA", 2, "json", ("server", "server_name", "status", "error")),
    "mcp_call": Profile("WYWOŁANIE MCP", "ARGUMENTY MCP", 3, "json", ("server", "server_name", "tool", "name", "uri")),
    "web_action": Profile("WEB", "OPERACJA SIECIOWA", 2, "json", ("query", "url", "ref_id", "action")),
    "file_change": Profile("ZMIANA PLIKÓW", "PATCH / ZMIANY", 4, "diff", ("path", "file_path", "operation")),
    "artifact": Profile("ARTEFAKT", "ZASÓB", 4, "json", ("path", "uri", "mime_type")),
    "image_generation": Profile("GENEROWANIE OBRAZU", "OPIS I PARAMETRY OBRAZU", 5, "json", ("prompt", "size", "path", "output_file")),
    "image_view": Profile("PODGLĄD OBRAZU", "ŹRÓDŁO OBRAZU", 5, "json", ("path", "image_url", "detail")),
    "media_stream": Profile("STRUMIEŃ MULTIMEDIALNY", "ZDARZENIE STRUMIENIA", 5, "json", ("session_id", "format", "voice", "status")),
    "approval": Profile("ZATWIERDZENIE", "OPERACJA WYMAGAJĄCA ZGODY", 4, "json", ("request_id", "reason", "command", "decision")),
    "permission_request": Profile("UPRAWNIENIA", "ZAKRES UPRAWNIEŃ", 4, "json", ("reason", "permissions", "sandbox_permissions", "justification")),
    "user_input_request": Profile("PYTANIE DO UŻYTKOWNIKA", "PYTANIA I ODPOWIEDZI", 4, "questions", ("request_id",)),
    "review": Profile("PRZEGLĄD", "ZAKRES I WYNIK PRZEGLĄDU", 5, "markdown", ("target", "review_mode", "status")),
    "compaction": Profile("KOMPAKCJA", "PODSUMOWANIE KONTEKSTU", 5, "markdown", ("reason", "tokens_before", "tokens_after")),
    "world_state": Profile("STAN ŚWIATA", "MIGAWKA ŚRODOWISKA", 2, "json", ("full", "cwd", "files")),
    "hook": Profile("HOOK", "SKRYPT HOOKA", 3, "code", ("event", "hook_id", "command", "exit_code")),
    "safety": Profile("OCENA BEZPIECZEŃSTWA", "OCENA / UZASADNIENIE", 4, "json", ("assessment", "reason", "risk_level", "decision")),
    "model_event": Profile("MODEL", "ZMIANA / WERYFIKACJA MODELU", 5, "json", ("from_model", "to_model", "model", "reason")),
    "environment": Profile("KONTEKST", "USTAWIENIA ŚRODOWISKA", 2, "json", ("cwd", "model", "approval_policy", "sandbox_policy")),
    "usage": Profile("TOKENY", "ZUŻYCIE TOKENÓW", 2, "usage"),
    "error": Profile("BŁĄD", "KOMUNIKAT BŁĘDU", 7, "code", ("code", "error", "message", "retryable")),
    "warning": Profile("OSTRZEŻENIE", "KOMUNIKAT OSTRZEŻENIA", 4, "markdown", ("code", "message")),
    "lifecycle": Profile("CYKL ŻYCIA", "ZDARZENIE PROTOKOŁU", 2, "json", ("type", "turn_id", "status", "reason")),
    "event_unknown": Profile("NIEZNANY TYP", "DANE NIEROZPOZNANEGO ZDARZENIA", 4, "json", ("type",)),
}

JSON_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"\s*(?=:)|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b')
CODE_TOKEN = re.compile(r'#[^\n]*|"(?:\\.|[^"\\])*"|\x27[^\x27]*\x27|\b(?:await|async|const|let|return|if|else|for|import|from|def|class|True|False|None)\b|\$[\w{}]+|(?<!\w)--?[\w-]+')


def section(label, color=2):
    """One header style for every section; the rule reaches the panel edge."""
    return [Span("── " + label + " ", color, True), Span("", 6, fill="─")]


def subsection(label, color=2):
    return [Span("  · " + label, color, True)]


def indented(rows, depth=4):
    """Indent whole rows, wrapped continuations included.

    Materialized rows stay re-iterable; generator rows stay lazy, so a
    multi-megabyte body is still only walked on demand.
    """
    pad = " " * depth
    for row in rows:
        lead = Span(pad, 0, hang=pad)
        yield [lead, *row] if isinstance(row, (list, tuple)) else chain([lead], row)


def cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def safe(text):
    return "".join(c if c in "\n\t" or not unicodedata.category(c).startswith("C") else " " for c in str(text)).expandtabs(4)


def syntax(text, mode="json", base=0):
    text = safe(text)
    pattern = JSON_TOKEN if mode == "json" else CODE_TOKEN
    spans, offset = [], 0
    for token in pattern.finditer(text):
        spans.append(Span(text[offset:token.start()], base))
        value = token.group()
        color = 6 if value.startswith("#") else 3 if value.startswith(('"', "'")) else 5
        if mode == "json" and value.startswith('"') and text[token.end():].lstrip().startswith(":"):
            color = 2
        spans.append(Span(value, color))
        offset = token.end()
    spans.append(Span(text[offset:], base))
    return spans


def wrap_rows(rows, width, limit=None):
    """Wrap styled spans by display cells, retaining newlines, color and content."""
    output, consumed = [], 0
    width = max(2, width)
    for row in rows:
        current, used = [], 0
        for span in row:
            fragment = ""
            for char in safe(span.text):
                if limit is not None and consumed >= limit:
                    if fragment:
                        current.append(Span(fragment, span.color, span.bold))
                    output.append(current)
                    output.append([Span("[Jawnie ustawiony limit podglądu.]", 4)])
                    return output
                consumed += 1
                size = cells(char)
                if char == "\n" or used + size > width:
                    if fragment:
                        current.append(Span(fragment, span.color, span.bold))
                        fragment = ""
                    output.append(current)
                    current, used = [], 0
                    if char == "\n":
                        continue
                fragment += char
                used += size
            if fragment:
                current.append(Span(fragment, span.color, span.bold))
        output.append(current)
    return output


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def source_payloads(item, doc):
    records = []
    for index in item.metadata.get("source_indices", []):
        if isinstance(index, int) and 0 <= index < len(doc.raw_records):
            records.append(doc.raw_records[index].record)
    return records


class Presenter:
    def __init__(self, item, doc):
        self.item = item
        self.records = source_payloads(item, doc)
        self.data = {}
        for record in self.records:
            payload = record.get("payload", record)
            if isinstance(payload, dict):
                self.data.update(payload)
                if isinstance(payload.get("item"), dict):
                    self.data.update(payload["item"])
        arguments = decoded(self.data.get("arguments", self.data.get("input")))
        if isinstance(arguments, dict):
            self.data.update(arguments)
        # Prefer source arguments: normalized metadata may contain an output's
        # empty argument object after lifecycle correlation.
        self.arguments = arguments if arguments is not None else item.metadata.get("arguments") or decoded(item.detail)
        self.rows = []

    def title(self, text, color=2):
        self.rows.extend([[], section(text, color)])

    def subtitle(self, text, color=2):
        self.rows.append(subsection(text, color))

    def field(self, label, value, color=0):
        if value is not None and value != "":
            if isinstance(value, (dict, list)):
                self.rows.append([Span(label + ":", 2)])
                self.content(value, "json")
            else:
                self.rows.append([Span(label + ": ", 2), Span(safe(value), color)])

    def content(self, value, mode="markdown", color=0):
        if mode == "json":
            value = decoded(value)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
            mode = "json"
        for line in safe(value).split("\n"):
            if mode == "diff":
                tint = 2 if line.startswith(("@@", "***", "diff ", "---", "+++")) else 3 if line.startswith("+") else 7 if line.startswith("-") else color
                self.rows.append([Span(line, tint, line.startswith(("@@", "***")))])
            elif mode in {"json", "code", "command"}:
                self.rows.append(syntax(line, "json" if mode == "json" else "code", color))
            elif line.startswith("#"):
                self.rows.append([Span(line, 2, True)])
            else:
                parts = re.split(r"(`[^`]+`|\*\*[^*]+\*\*)", line)
                self.rows.append([Span(p, 3 if p.startswith("`") else color, p.startswith("**")) for p in parts])

    def result_content(self, value, color=0):
        parsed = decoded(value)
        start = len(self.rows)
        self.content(parsed, "json" if isinstance(parsed, (dict, list)) else "code", color)
        self.rows[start:] = list(indented(self.rows[start:]))

    def render(self):
        item = self.item
        profile = PROFILES.get(item.kind.value, PROFILES["event_unknown"])
        if item.family.value == "multi_agent":
            profile = Profile("WSPÓŁPRACA AGENTÓW", "OPERACJA / WIADOMOŚĆ AGENTA", 5, "json", ("agent_id", "target", "receiver_agent_id", "message", "task"))
        elif item.action == "tool_script" or item.metadata.get("protocol_wrapper"):
            profile = Profile("SKRYPT NARZĘDZI", "KOD WYWOŁUJĄCY NARZĘDZIA", 3, "code", ("script_tools",))
        elif item.kind.value == "command" and item.action in {"process_input", "process_wait", "process_kill"}:
            profile = Profile("STEROWANIE PROCESEM", "WEJŚCIE / OCZEKIWANIE", 3, "json", ("session_id", "process_id", "chars", "yield_time_ms"))
        self.rows.append([Span(f"KROK #{item.index + 1} / TURA {item.conversation_turn_number}", profile.color, True)])
        self.rows.append([Span(profile.title + " · ", profile.color, True), Span(item.action or item.subkind, 6)])
        status_color = 7 if item.status == "error" or item.lifecycle.value == "failed" else 3 if item.lifecycle.value == "completed" else 4
        self.field("Stan", f"{item.lifecycle.value} · {item.status}", status_color)
        if item.duration_ms is not None:
            self.field("Czas", f"{item.duration_ms / 1000:.3f} s", 5)
        self.field("Narzędzie", item.metadata.get("tool_name"), 3)
        for key in profile.fields:
            self.field(key, self.data.get(key, item.metadata.get(key)))
        section_label = profile.section
        if item.kind.value == "message" and item.family.value != "multi_agent":
            section_label = {"user": "WIADOMOŚĆ UŻYTKOWNIKA", "assistant": "WIADOMOŚĆ CODEXA", "developer": "INSTRUKCJA DEWELOPERA", "system": "INSTRUKCJA SYSTEMOWA"}.get(item.role, section_label)
        self.title(section_label, 7 if item.kind.value in {"error", "warning"} else 2)
        mode = profile.mode
        value = self.arguments if mode in {"json", "command", "questions", "plan"} else item.detail
        if mode == "json" and item.kind.value in {"session", "agent", "turn", "world_state", "safety", "model_event", "environment", "lifecycle", "event_unknown", "mcp_server"}:
            value = self.data or value
        if item.kind.value == "terminal_io":
            value = self.data.get("chars", self.data.get("stdin", value))
        if mode == "command":
            command = self.data.get("cmd", self.data.get("command"))
            if isinstance(command, list):
                command = shlex.join(str(part) for part in command)
            if command is not None:
                self.content(command, "code")
            else:
                self.content(item.detail, "code")
            if isinstance(self.arguments, dict):
                remaining = {k: v for k, v in self.arguments.items() if k not in {"cmd", "command"}}
                if remaining:
                    self.title("PARAMETRY WYKONANIA", 2)
                    self.content(remaining, "json")
        elif mode == "plan":
            steps = self.data.get("plan", self.data.get("steps", item.metadata.get("steps")))
            if isinstance(steps, list):
                for n, step in enumerate(steps, 1):
                    if isinstance(step, dict):
                        status = step.get("status", "pending")
                        color = 3 if status == "completed" else 4 if status == "in_progress" else 6
                        self.rows.append([Span(f"{n}. [{status}] ", color, True), Span(str(step.get("step", step.get("title", ""))))])
                        extra = {k: v for k, v in step.items() if k not in {"step", "title", "status"}}
                        if extra:
                            self.content(extra, "json")
                    else:
                        self.content(step)
            else:
                self.content(value or self.data, "json")
        elif mode == "questions":
            questions = self.data.get("questions")
            if isinstance(questions, list):
                for n, question in enumerate(questions, 1):
                    if not isinstance(question, dict):
                        self.content(question)
                        continue
                    self.field(f"Pytanie {n}", question.get("question", question.get("title", "")), 4)
                    for option in question.get("options", []) or []:
                        self.field("  • Opcja", option.get("label", "") if isinstance(option, dict) else option, 3)
                        if isinstance(option, dict):
                            self.content(option.get("description", ""))
            else:
                self.content(value or self.data, "json")
        elif mode == "usage":
            found = False
            for scope, label in (("thread_token_usage", "SESJA"), ("turn_token_usage", "TURA"), ("total_token_usage", "ŁĄCZNIE"), ("last_token_usage", "OSTATNIE WYWOŁANIE"), ("usage", "WYWOŁANIE")):
                usage = self.data.get(scope)
                if usage is None and isinstance(self.data.get("info"), dict):
                    usage = self.data["info"].get(scope)
                if usage is not None:
                    found = True
                    self.title(label, 2)
                    if isinstance(usage, dict):
                        for key, count in usage.items():
                            self.field(key, f"{count:,}" if isinstance(count, int) else count, 5)
                    else:
                        self.content(usage, "json")
            if not found:
                self.content(self.data, "json")
        elif mode == "diff":
            patch = self.data.get("input", self.data.get("patch", self.data.get("diff")))
            if isinstance(patch, str) and patch.strip():
                self.content(patch, "diff")
            else:
                changes = self.data.get("changes")
                if isinstance(changes, dict):
                    for path, change in changes.items():
                        self.field("Plik", path, 4)
                        if isinstance(change, dict):
                            for key, text in change.items():
                                self.field("Pole", key, 2)
                                if key in {"diff", "patch", "unified_diff"}:
                                    self.content(text, "diff")
                                elif key == "content":
                                    # Pełna treść pliku po zmianie, nie diff:
                                    # kolorowanie +/- oznaczałoby tu usunięcia,
                                    # których w zapisie nie ma.
                                    self.rows.append([Span("Pełna treść pliku po zmianie, nie diff.", 6)])
                                    self.content(text, "code")
                                else:
                                    self.content(text, "json")
                        else:
                            self.content(change, "diff")
                elif "\n" in (item.detail or ""):
                    self.content(item.detail, "diff")
                else:
                    self.rows.append([Span("Patch nie jest zapisany w tym kroku; poniżej znormalizowany skrót.", 4)])
                    self.content(item.detail, "code")
        else:
            if not value:
                value = "Brak zapisanego tekstu analizy." if item.kind.value == "reasoning" else self.data if mode == "json" else "(bez treści)"
            self.content(value, mode, 7 if item.kind.value == "error" else 0)
        if item.result:
            self.title("WYNIK / ODPOWIEDŹ", 3 if status_color != 7 else 7)
            for key, value in item.result.items():
                if key == "exit_code":
                    self.field("Kod wyjścia", value, 3 if value == 0 else 7)
                elif key in {"output", "stdout", "stderr", "aggregated_output", "formatted_output", "error"}:
                    self.subtitle(key.upper(), 7 if key in {"stderr", "error"} else 2)
                    self.result_content(value, 7 if key in {"stderr", "error"} else 0)
                else:
                    self.field(key, value)
        return self.rows


def detail_rows(item, doc, graph, path, raw=False):
    presenter = Presenter(item, doc)
    if raw:
        presenter.title("SUROWE REKORDY — " + item.interaction_id, 5)
        for index, record in enumerate(presenter.records, 1):
            presenter.title(f"REKORD {index} / {len(presenter.records)}", 5)
            presenter.content(record, "json")
        return presenter.rows
    presenter.render()
    presenter.title("RELACJE (→ kolejność; pozostałe wg danych)")
    nodes = {n.node_id: n for n in graph.nodes} if graph else {}
    edges = [e for e in graph.edges if item.interaction_id in {e.source, e.target}] if graph else []
    for edge in edges:
        other = edge.target if edge.source == item.interaction_id else edge.source
        node = nodes.get(other)
        label = f"#{node.interaction_index + 1}" if node and node.interaction_index is not None else node.label if node else other
        direction = "→" if edge.source == item.interaction_id else "←"
        basis = "wnioskowana" if edge.metadata.get("inferred") else "jawna"
        color = 4 if edge.metadata.get("inferred") else 3
        if edge.kind.value == "next":
            basis, color = "kolejność zapisu", 6
        presenter.rows.append([Span(f"{direction} {edge.kind.value} ", 2, True), Span(label, 0), Span(f" [{basis}; {edge.metadata.get('detected_by', '?')}]", color)])
    if not edges:
        presenter.content("Brak relacji w źródle.")
    presenter.title("POCHODZENIE I KLASYFIKACJA", 2)
    presenter.field("Typ", f"{item.kind.value} / {item.subkind}")
    presenter.field("Klasyfikacja", f"{item.action_confidence} / {item.action_source}", 5)
    presenter.field("call_id", item.call_id)
    presenter.field("Korelacja", item.metadata.get("correlation", {}).get("basis"))
    presenter.field("Źródło", str(path))
    presenter.field("Rekordy", ", ".join(item.raw_record_ids))
    presenter.content("r — źródłowe rekordy JSON")
    return presenter.rows

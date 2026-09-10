"""Four detail layers over an immutable snapshot; source versions never merge."""
from __future__ import annotations

from dataclasses import fields

from .core.domain import Interaction, InteractionFamily
from .details import PROFILES, Presenter, Span, decoded, indented, section, subsection
from .diff import diff_rows, file_summary, parse_patch
from .paging import content_spans, json_spans
from .tool_content import result_rows, wrapper_rows, wrapper_script


LAYERS = ("Treść", "Wszystkie pola", "Relacje", "Źródła RAW")

# Deliberate layouts, not a single JSON template for unrelated interactions.
# Keys not recognized here are still available, version by version, in layer 2.
SECTIONS = {
    "session": (("TOŻSAMOŚĆ I KATALOG", "session_id id cwd"), ("URUCHOMIENIE", "model model_provider originator cli_version"), ("PEŁNE INSTRUKCJE", "instructions base_instructions developer_instructions")),
    "agent": (("AGENT I ROLA", "agent_id id role model primary"), ("PEŁNE ZADANIE", "task prompt message"), ("POCHODZENIE", "source_interaction parent_agent_id")),
    "turn": (("PROMPT OTWIERAJĄCY", "prompt message"), ("ZAKRES KROKÓW", "interaction_indices interaction_ids steps"), ("TURA ROZMOWY / PROTOKÓŁ", "conversation_turn_number turn_id source_turn_ids status started_at completed_at")),
    "message": (("NADAWCA I KANAŁ", "role channel recipient"), ("PEŁNA WIADOMOŚĆ", "message text content"), ("ZAŁĄCZNIKI I BLOKI NIETEKSTOWE", "images local_images attachments audio")),
    "reasoning": (("ZAPISANE PODSUMOWANIE ANALIZY", "summary text content"), ("FRAGMENTY ZE ŹRÓDŁA", "delta fragments summary_index content_index")),
    "plan": (("WYJAŚNIENIE", "explanation"), ("ZADANIA", "plan steps")),
    "plan_step": (("PEŁNE ZADANIE", "step title"), ("STATUS I POZYCJA", "status step_index index plan_id")),
    "command": (("PROCES", "session_id process_id"), ("PARAMETRY", "cwd workdir shell env timeout_ms")),
    "terminal_io": (("PROCES DOCELOWY", "session_id process_id"), ("DOKŁADNE WEJŚCIE (ESCAPED)", "chars stdin"), ("PARAMETRY TRANSMISJI", "encoding yield_time_ms")),
    "tool_call": (("NARZĘDZIE I KORELACJA", "name tool call_id"), ("ARGUMENTY", "arguments input")),
    "tool_result": (("POWIĄZANIE Z WYWOŁANIEM", "call_id item_id"), ("PEŁNY WYNIK", "output content result"), ("BŁĄD I STATUS", "isError error status")),
    "tool_search": (("ZAPYTANIE", "query limit"), ("NARZĘDZIA I PEŁNE SCHEMATY", "tools additional_tools matches results")),
    "mcp_server": (("SERWER I STAN", "server server_name status error"), ("ZASOBY I SZABLONY", "resources resource_templates templates tools")),
    "mcp_call": (("SERWER → NARZĘDZIE / URI", "server server_name tool name uri"), ("ARGUMENTY", "arguments input"), ("ODPOWIEDŹ I TRANSPORT", "content isError status error")),
    "web_action": (("OPERACJA WEB", "action"), ("ZAPYTANIE / URL / REFERENCJA", "query url ref_id search_query open find"), ("WSZYSTKIE WYNIKI", "results title content sources response_length")),
    "file_change": (("PLIKI I OPERACJE", "path file_path operation changes"), ("PATCH", "input patch diff")),
    "artifact": (("TOŻSAMOŚĆ ZASOBU", "path uri mime_type"), ("ŹRÓDŁO ROZPOZNANIA", "detected_by inferred source_interaction")),
    "image_generation": (("PEŁNY PROMPT", "prompt"), ("PARAMETRY GENEROWANIA", "size format background quality"), ("ZWRÓCONE OBRAZY", "images path output_file image_url content")),
    "image_view": (("ŹRÓDŁO OBRAZU", "path image_url url"), ("PARAMETRY ODCZYTU", "detail"), ("ZAPISANE METADANE / BLOKI", "width height mime_type content")),
    "media_stream": (("STRUMIEŃ I FORMAT", "session_id format voice"), ("ZDARZENIA I TRANSKRYPCJA", "events sequence transcript text delta"), ("STEROWANIE I PEŁNY PAYLOAD", "sdp voices audio content")),
    "approval": (("OPERACJA WYMAGAJĄCA ZGODY", "command cwd changes patch"), ("UZASADNIENIE", "reason justification"), ("DECYZJA Z LOGU — TYLKO ODCZYT", "request_id decision approved")),
    "permission_request": (("POWÓD I ADRESAT", "reason justification target request_id"), ("ŻĄDANE UPRAWNIENIA", "permissions requested_permissions sandbox_permissions"), ("ZAPISANA ODPOWIEDŹ", "granted granted_permissions decision")),
    "user_input_request": (("PYTANIA", "questions"), ("ZAPISANE ODPOWIEDZI", "answers response request_id")),
    "review": (("TRYB I ZAKRES PRZEGLĄDU", "type target review_mode status"), ("WSZYSTKIE USTALENIA", "findings"), ("PEŁNY RAPORT", "review report message text content")),
    "compaction": (("POWÓD I RODZAJ", "reason type tokens_before tokens_after"), ("PEŁNE PODSUMOWANIE", "summary message text"), ("HISTORIA ZASTĘPCZA", "replacement_history")),
    "world_state": (("ZAKRES MIGAWKI", "full cwd"), ("ZAPISANY STAN", "files state snapshot")),
    "hook": (("WYZWALACZ", "hook_id event name type"), ("SKRYPT LUB PROMPT", "command script prompt"), ("ODPOWIEDŹ", "stdout stderr exit_code")),
    "safety": (("RODZAJ OCENY", "type assessment"), ("DECYZJA ZE ŹRÓDŁA", "decision risk_level status"), ("UZASADNIENIE I DOWODY", "reason evidence input")),
    "model_event": (("RODZAJ ZDARZENIA", "type"), ("MODEL ŹRÓDŁOWY → DOCELOWY", "from_model to_model model"), ("POWÓD / WERYFIKACJA", "reason status verification")),
    "environment": (("KATALOG I MODEL", "cwd model effort"), ("ZATWIERDZENIA I SANDBOX", "approval_policy sandbox_policy"), ("USTAWIENIA WĄTKU", "name title goal queue")),
    "usage": (("ZAKRESY TOKENÓW", "thread_token_usage turn_token_usage total_token_usage last_token_usage usage"), ("LIMITY ZE ŹRÓDŁA", "rate_limits limits model_context_window")),
    "error": (("KOMUNIKAT I KOD", "message error code exit_code"), ("PEŁNY TRACEBACK", "traceback stderr"), ("PONOWIENIE ZE ŹRÓDŁA", "retryable retry_after attempt")),
    "warning": (("KATEGORIA I POCHODZENIE", "type code category"), ("PEŁNY KOMUNIKAT", "message text"), ("WSKAZÓWKI ZE ŹRÓDŁA", "hint suggestion details")),
    "lifecycle": (("ZDARZENIE PROTOKOŁU", "type"), ("OBIEKT DOCELOWY", "turn_id item_id session_id agent_id"), ("STAN I CZAS", "status reason timestamp started_at completed_at")),
    "event_unknown": (("ORYGINALNY TYP — SEMANTYKA NIEZNANA", "type"),),
}


def mapping(obj):
    # Unlike asdict(), this does not copy multi-megabyte payloads on the UI thread.
    return {field.name: getattr(obj, field.name) for field in fields(obj)}


def title(label, color=2):
    """A plain bold label: list entries and layer banners, not sections."""
    return [Span(label, color, True)]


def block_rows(blocks):
    for n, block in enumerate(blocks, 1):
        if not isinstance(block, dict):
            yield content_spans(block)
            continue
        yield subsection(f"BLOK {n} · {block.get('type', 'typ niepodany')}", 5)
        for key in ("text", "summary_text", "transcript"):
            if key in block:
                yield content_spans(block[key])
        rest = {key: value for key, value in block.items() if key not in {"text", "summary_text", "transcript"}}
        if rest:
            yield json_spans(rest)


def field_rows(value, path=""):
    """JSON Pointer + JSON type, including every empty/false/null value."""
    kind = ("null" if value is None else "boolean" if isinstance(value, bool) else
            "object" if isinstance(value, dict) else "array" if isinstance(value, list) else
            "string" if isinstance(value, str) else "number")
    yield [Span(path or '"" (korzeń)', 2, True), Span(f"  [{kind}]", 5)]
    if isinstance(value, (dict, list)) and value:
        yield [Span(f"{len(value)} pól" if isinstance(value, dict) else f"{len(value)} elementów", 6)]
        children = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in children:
            pointer = str(key).replace("~", "~0").replace("/", "~1")
            yield from field_rows(child, path + "/" + pointer)
    else:
        yield json_spans(value)


class LayerPresenter(Presenter):
    """Same 35 type contracts, with deferred, bounded-size styled content."""
    def field(self, label, value, color=0):
        if label in {"Stan", "Czas", "Narzędzie"}:
            return  # These belong to the fixed header.
        if value is not None and value != "":
            self.rows.append([Span(label + ": ", 2)])
            self.content(value, "json" if isinstance(value, (dict, list)) else "markdown", color)

    def content(self, value, mode="markdown", color=0):
        if mode == "diff" and isinstance(value, str):
            self.rows.extend(diff_rows(value))
        else:
            self.rows.append(content_spans(value, mode, color))

    def result_content(self, value, color=0):
        # Command/file/usage presenters share this hook with the generic tool
        # view. Nested output blocks must not fall back to pretty-printed JSON.
        self.rows.extend(indented(result_rows(value, color=color)))

    def semantic_rows(self):
        item = self.item
        kind = item.kind.value
        data = dict(item.metadata)
        data.update(self.data)
        for key in ("call_id", "role"):
            if key not in data and getattr(item, key):
                data[key] = getattr(item, key)
        script = wrapper_script(item, self.data)
        if script is not None:
            yield from wrapper_rows(item, script)
            return
        if kind in {"plan", "user_input_request"}:
            if "explanation" in data:
                yield section("WYJAŚNIENIE ZMIANY")
                yield content_spans(data["explanation"])
            entries = data.get("plan", data.get("steps")) if kind == "plan" else data.get("questions")
            yield section("ZADANIA I STATUSY" if kind == "plan" else "PYTANIA I OPCJE")
            if isinstance(entries, list):
                for n, entry in enumerate(entries, 1):
                    if not isinstance(entry, dict):
                        yield json_spans(entry)
                        continue
                    if kind == "plan":
                        status = entry.get("status", "nie zapisano")
                        yield title(f"{n}. [{status}]", 3 if status == "completed" else 4 if status in {"in_progress", "inProgress"} else 6)
                        for key in ("step", "title", "text"):
                            if key in entry:
                                yield content_spans(entry[key])
                        extra = {key: value for key, value in entry.items() if key not in {"status", "step", "title", "text"}}
                        if extra:
                            yield json_spans(extra)
                    else:
                        yield subsection(f"PYTANIE {n} · id: {entry.get('id', 'nie zapisano')}", 4)
                        for key in ("question", "title", "header"):
                            if key in entry:
                                yield content_spans(entry[key])
                        options = entry.get("options")
                        if isinstance(options, list):
                            for number, option in enumerate(options, 1):
                                yield subsection(f"OPCJA {number}", 3)
                                yield content_spans(option)
                        elif "options" in entry:
                            yield json_spans(options)
                        extra = {key: value for key, value in entry.items() if key not in {"question", "title", "header", "id", "options"}}
                        if extra:
                            yield json_spans(extra)
            else:
                yield json_spans(entries if entries is not None else self.data)
            for key in ("answers", "response", "request_id"):
                if key in data:
                    yield section(key.upper())
                    yield json_spans(data[key])
            if item.result:
                yield section("ZAPISANY WYNIK / ODPOWIEDŹ", 3)
                yield json_spans(item.result)
            return
        specialized = kind in {"command", "file_change", "plan", "user_input_request", "usage"} or item.action == "tool_script" or item.metadata.get("protocol_wrapper")
        if specialized:
            if kind == "file_change":
                patch = data.get("input", data.get("patch", data.get("diff", "")))
                parsed = parse_patch(patch) if isinstance(patch, str) else None
                changes = data.get("changes") if isinstance(data.get("changes"), dict) else None
                # One file repeats the header the patch itself already shows.
                if changes or not parsed or len(parsed.files) > 1:
                    yield section("INDEKS PLIKÓW")
                    for patch_file in parsed.files if parsed else ():
                        yield file_summary(patch_file)
                    if changes and parsed:
                        yield [Span("Ścieżki zapisane przy zastosowaniu patcha:", 6)]
                    for path, change in (changes or {}).items():
                        operation = change.get("type", change.get("operation")) if isinstance(change, dict) else None
                        yield ([Span(str(operation).upper() if operation else "ZMIANA", 4, True), Span(" · ", 6), Span(str(path), 1)]
                               + ([Span(" → ", 6), Span(str(change["move_path"]), 1)] if isinstance(change, dict) and change.get("move_path") else []))
                    if not parsed and not changes:
                        yield [Span("Brak indeksu plików w zapisanych danych tego kroku.", 6)]
            yield from self.render()[2:]
            # Typed secondary sections supplement, rather than replace, the
            # dedicated plan/question/token/process/diff presenters.
            extras = {"usage": ("rate_limits", "limits", "model_context_window"),
                      "user_input_request": ("answers", "response"),
                      "command": ("chars", "stdin")}.get(kind, ())
            for key in extras:
                if key in data:
                    yield section(key.upper())
                    yield json_spans(data[key])
            return
        if item.family.value == "multi_agent":
            sections = (("OPERACJA AGENTA: " + item.action, "agent_id target receiver_agent_id id status"),
                        ("PEŁNE ZADANIE / WIADOMOŚĆ", "message task prompt"), ("PARAMETRY", "arguments input"))
        else:
            sections = SECTIONS[kind]
        if item.action == "tool_result":
            yield [Span("Wynik bez skorelowanego wywołania w tym kroku (action=tool_result).", 4)]
        if kind == "tool_search" and item.subkind == "additional_tools":
            yield title("REJESTRACJA NARZĘDZI — nie wyszukiwanie", 4)
        seen = set()
        for label, keys in sections:
            available = [key for key in keys.split() if key in data]
            if not available:
                continue
            yield section(label, 7 if kind in {"error", "warning"} else 2)
            for key in available:
                seen.add(key)
                yield [Span(key, 2)]
                value = data[key]
                escaped = kind == "terminal_io" and key in {"chars", "stdin"}
                if key in {"content", "summary"} and isinstance(value, list):
                    yield from block_rows(value)
                elif escaped or not isinstance(value, str):
                    yield json_spans(value)
                else:
                    yield content_spans(value, "code" if kind in {"hook", "error"} else "markdown", 7 if kind == "error" else 0)
                if escaped:
                    yield [Span("Czytelny podgląd:", 6)]
                    yield content_spans(value, "code")
        if not seen:
            yield section(PROFILES[kind].section, 7 if kind in {"error", "warning"} else 2)
            if kind == "reasoning":
                yield [Span("Brak zapisanego tekstu analizy. Nie odtwarzamy ukrytego rozumowania.", 6)]
            elif kind == "event_unknown":
                yield from field_rows(self.data)
            elif item.detail:
                yield content_spans(item.detail, PROFILES[kind].mode)
        remaining = {key: value for key, value in self.data.items() if key not in seen}
        if remaining:
            yield section("POZOSTAŁE DANE TEJ WERSJI", 6)
            yield json_spans(remaining)
        if item.result:
            yield section("PEŁNY WYNIK / ODPOWIEDŹ", 3)
            yield from result_rows(item.result)


class DetailDocument:
    def __init__(self, target, doc, graph, path):
        self.doc, self.graph, self.path = doc, graph, path
        self.target = target
        self.nodes = {node.node_id: node for node in graph.nodes} if graph else {}
        self.item = next((item for item in doc.interactions if item.interaction_id == target), None)
        self.node = self.nodes.get(target)
        self.graph_only = self.item is None
        if self.item is None and self.node is not None:
            node = mapping(self.node)
            allowed = {field.name for field in fields(Interaction)}
            self.item = Interaction(**{k: v for k, v in node.items() if k in allowed and k != "family"},
                interaction_id=target, index=-1, family=self.node.family or InteractionFamily.CONTEXT_STATE)
        self.edges = [edge for edge in graph.edges if target in {edge.source, edge.target}] if graph else []
        indices = self.item.metadata.get("source_indices", []) if self.item else []
        ids = set(self.item.raw_record_ids) if self.item else set()
        self.records = [doc.raw_records[n] for n in dict.fromkeys(indices)
                        if isinstance(n, int) and 0 <= n < len(doc.raw_records)]
        if not self.records and ids:
            self.records = [record for record in doc.raw_records if record.raw_id in ids]

    def header(self):
        item = self.item
        if item is None:
            return [title("Brak celu w bieżącym grafie.", 4)]
        profile = PROFILES[item.kind.value]
        tool_name = str(item.metadata.get("tool_name") or "")
        presentation = profile.title + (" / WRAPPER " + tool_name if tool_name.rsplit(".", 1)[-1] == "exec" else "")
        identity = "WĘZEŁ GRAFU" if self.graph_only else f"KROK #{item.index + 1} / T{item.conversation_turn_number}"
        status = f"{item.lifecycle.value} / {item.status}"
        duration = f" · {item.duration_ms / 1000:.3f} s" if item.duration_ms is not None else ""
        exit_code = item.result.get("exit_code")
        if exit_code is not None:
            duration += f" · exit {exit_code}"
        return [title(f"SZCZEGÓŁY · {identity} · {presentation}", profile.color),
                [Span(f"{item.family.value} / {item.kind.value}", profile.color)],
                [Span(f"podtyp: {item.subkind or '—'} · akcja: {item.action or '—'}", 2)],
                [Span(status + duration, 7 if item.lifecycle.value == "failed" else 3 if item.lifecycle.value == "completed" else 4)]]

    def other(self, edge):
        return edge.target if edge.source == self.target else edge.source

    def relation_label(self, edge, n):
        other = self.other(edge)
        node = self.nodes.get(other)
        name = (f"#{node.interaction_index + 1} " if node and node.interaction_index is not None else "") + (node.label if node else other)
        direction = "→" if edge.source == self.target else "←"
        return f"{n + 1}. {direction} {edge.kind.value} · {name}"

    def relation_rows(self, selected=0, all_edges=False):
        if not self.edges:
            yield [Span("Brak relacji w grafie.", 6)]
            return
        selected = max(0, min(selected, len(self.edges) - 1))
        yield [Span("n/N: następna/poprzednia · Enter: otwórz cel · b: wróć", 6)]
        for n in (range(len(self.edges)) if all_edges else [selected]):
            edge = self.edges[n]
            yield title(self.relation_label(edge, n))
            basis = "KOLEJNOŚĆ — nie dowód przyczynowości" if edge.kind.value == "next" else "WNIOSKOWANA" if edge.metadata.get("inferred") else "JAWNA (wg grafu)"
            yield [Span(basis, 4 if edge.metadata.get("inferred") else 6 if edge.kind.value == "next" else 3, True)]
            yield [Span(f"Pewność: {edge.confidence} · podstawa: {edge.metadata.get('detected_by', 'nie podano')}", 5)]
            # Includes source/target identifiers, raw provenance and every unknown metadata field.
            yield json_spans(mapping(edge))
            yield []
        if not all_edges:
            yield section(f"WSZYSTKIE RELACJE ({len(self.edges)})")
            for n, edge in enumerate(self.edges):
                yield [Span(("▶ " if n == selected else "  ") + self.relation_label(edge, n), 3 if n == selected else 2, n == selected)]

    def content_rows(self):
        if not self.item:
            yield [Span("Cel nie jest dostępny.", 4)]
            return
        if self.graph_only:
            yield [Span("Węzeł strukturalny — nie jest dodatkowym krokiem osi.", 4)]
        presenter = LayerPresenter(self.item, self.doc)
        if self.graph_only:
            presenter.data.update(self.item.metadata)
            kind = self.item.kind.value
            if kind == "session":
                presenter.data.update(self.doc.agent)
                presenter.data["session_id"] = self.doc.session_id
            elif kind == "turn":
                presenter.data["prompt"] = self.item.detail
                presenter.data["conversation_turn_number"] = self.item.conversation_turn_number
                presenter.data["interaction_indices"] = [item.index + 1 for item in self.doc.interactions
                    if item.conversation_turn_id == self.item.conversation_turn_id]
            elif kind == "plan_step":
                step = decoded(self.item.detail)
                if isinstance(step, dict):
                    presenter.data.update(step)
            elif kind == "artifact":
                presenter.data["uri" if self.item.detail.startswith("http") else "path"] = self.item.detail
            elif kind == "agent":
                presenter.data["agent_id"] = self.target
                presenter.data["task"] = self.item.detail
            elif kind == "error":
                error = decoded(self.item.detail)
                if isinstance(error, dict):
                    presenter.data.update(error)
        # The compact normalized description is only a fallback. Source-backed
        # text, including attachments and multiple begin/delta/end versions, is
        # accessible in the following explicit source sections.
        yield from presenter.semantic_rows()
        if self.records:
            yield section("FAZY / WERSJE ŹRÓDŁOWE", 5)
            for raw in self.records:
                payload = raw.record.get("payload", raw.record)
                event = payload.get("type", raw.rollout_type) if isinstance(payload, dict) else raw.rollout_type
                yield [Span(f"#{raw.index + 1} · {raw.timestamp} · {event}", 5)]
            yield [Span("Pełne wartości każdej fazy: 2 Pola / 4 RAW.", 6)]
        yield []
        yield [Span("2: wszystkie pola i wersje · 3: relacje · 4: pełne rekordy RAW", 6)]

    def rows(self, layer=0, relation=0):
        if layer == 4:
            for number, label in enumerate(LAYERS):
                yield title(f"━━ {number + 1} / {label.upper()} ━━", 4)
                if number == 2:
                    yield from self.relation_rows(relation, all_edges=True)
                else:
                    yield from self.rows(number, relation)
                yield []
        elif layer == 0:
            yield from self.content_rows()
        elif layer == 1:
            yield [Span("Każda wersja osobno. Ścieżki: JSON Pointer; brak pola ≠ null.", 6)]
            for raw in self.records:
                yield section(f"REKORD #{raw.index + 1} · {raw.raw_id}", 5)
                yield from field_rows(raw.record)
                yield []
            yield section("NORMALIZACJA — osobno od oryginalnych wartości", 4)
            if self.item:
                yield from field_rows(mapping(self.node if self.graph_only else self.item))
        elif layer == 2:
            yield from self.relation_rows(relation)
        else:
            yield section("ŹRÓDŁA — kompletne wartości JSON (formatowane)", 5)
            yield content_spans(str(self.path), color=6)
            yield [Span("Kolejność źródłowa; bez scalania i bez skracania rekordów.", 6)]
            if not self.records:
                yield [Span("Brak bezpośredniego rekordu RAW. Sprawdź pola i relacje węzła.", 4)]
            for raw in self.records:
                yield section(f"REKORD #{raw.index + 1} · {raw.raw_id} · {raw.timestamp}", 5)
                yield json_spans(raw.record)
                yield []

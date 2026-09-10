"""Semantic labels for the timeline; never summarize command contents."""
from .details import PROFILES


FAMILIES = {
    "communication": ("ROZMOWA", 2), "cognition": ("ANALIZA", 5),
    "planning": ("PLAN", 5), "execution": ("WYKONANIE", 3),
    "filesystem": ("PLIKI", 4), "tooling": ("NARZĘDZIA", 3),
    "mcp": ("MCP", 3), "web": ("WEB", 2), "media": ("MEDIA", 5),
    "multi_agent": ("AGENCI", 5), "human_control": ("PYTANIE", 4),
    "safety_security": ("OCHRONA", 4), "context_state": ("KONTEKST", 6),
    "lifecycle_observability": ("PROTOKÓŁ", 6),
}

KINDS = {
    "command": "PROCES", "terminal_io": "PROCES", "tool_call": "WYWOŁANIE",
    "tool_search": "ODKRYWANIE", "web_action": "OPERACJA",
    "file_change": "ZMIANA", "user_input_request": "DANE UŻYTKOWNIKA",
    "lifecycle": "CYKL ŻYCIA", "event_unknown": "NIEZNANE",
}

ACTIONS = {
    "message": "Wiadomość", "user_message": "Użytkownik · prompt",
    "agent_message": "Asystent · wiadomość", "system_message": "System · wiadomość",
    "reasoning": "Zapis analizy", "plan_update": "Aktualizacja planu",
    "plan_step": "Zadanie planu", "shell_command": "Uruchomienie polecenia",
    "process_input": "Przekazanie wejścia", "process_wait": "Oczekiwanie na proces",
    "process_kill": "Zatrzymanie procesu", "tool_script": "Skrypt narzędziowy",
    "tool_invocation": "Wywołanie narzędzia", "tool_result": "Odbiór wyniku",
    "tool_discovery": "Wyszukiwanie narzędzi", "mcp_server": "Obsługa serwera",
    "mcp_discovery": "Odkrywanie zasobów", "mcp_call": "Wywołanie MCP",
    "web_search": "Wyszukiwanie", "web_open": "Otwarcie strony",
    "web_find": "Szukanie na stronie", "file_change": "Zmiana plików",
    "file_patch": "Zastosowanie poprawki", "turn_diff": "Zmiany w turze",
    "file_created": "Utworzenie pliku", "file_moved": "Przeniesienie pliku",
    "file_deleted": "Usunięcie pliku", "file_copied": "Kopiowanie pliku",
    "file_read": "Odczyt pliku", "environment_probe": "Sprawdzenie środowiska",
    "image_generation": "Generowanie obrazu", "image_view": "Podgląd obrazu",
    "media_session": "Sesja multimedialna", "agent_spawn": "Uruchomienie agenta",
    "agent_wait": "Oczekiwanie na agenta", "agent_resume": "Wznowienie agenta",
    "agent_close": "Zamknięcie agenta", "agent_list": "Lista agentów",
    "agent_action": "Operacja agenta", "agent_activity": "Aktywność agenta",
    "approval_request": "Prośba o zatwierdzenie", "permission_request": "Prośba o uprawnienia",
    "input_request": "Pytanie do użytkownika", "review_mode": "Przegląd",
    "context_compaction": "Kompakcja kontekstu", "context_rollback": "Cofnięcie kontekstu",
    "world_snapshot": "Migawka środowiska", "context_update": "Aktualizacja kontekstu",
    "hook_run": "Uruchomienie hooka", "safety_assessment": "Ocena bezpieczeństwa",
    "model_routing": "Wybór modelu", "token_usage": "Zużycie tokenów",
    "error": "Zdarzenie błędu", "warning": "Ostrzeżenie",
    "extension_action": "Operacja rozszerzenia",
}

EVENTS = {
    "task_started": "Rozpoczęcie tury", "turn_started": "Rozpoczęcie tury",
    "task_complete": "Zakończenie tury", "turn_complete": "Zakończenie tury",
    "task_completed": "Zakończenie tury", "turn_completed": "Zakończenie tury",
    "turn_aborted": "Przerwanie tury", "shutdown_complete": "Zamknięcie sesji",
    "item_started": "Rozpoczęcie elementu", "item_completed": "Zakończenie elementu",
    "raw_response_completed": "Zakończenie odpowiedzi",
}


def labels(item):
    family, color = FAMILIES.get(item.family.value, ("INNE", 6))
    kind = KINDS.get(item.kind.value)
    if kind is None:
        profile = PROFILES.get(item.kind.value)
        kind = profile.title if profile else item.kind.value
    if item.family.value == "multi_agent" and item.kind.value == "tool_call":
        kind = "WSPÓŁPRACA"
    if item.kind.value == "lifecycle" and item.subkind in EVENTS:
        kind = "SESJA" if item.subkind == "shutdown_complete" else "TURA" if item.subkind.startswith(("turn_", "task_")) else "ZDARZENIE"
    if item.kind.value == "event_unknown":
        color = 4
    # The parser can infer command effects. Keep that distinction visible;
    # this layer neither inspects commands nor presents an inference as fact.
    variant = ACTIONS.get(item.action) or item.subkind or item.action or "Brak podtypu"
    if item.kind.value == "lifecycle":
        variant = EVENTS.get(item.subkind, item.subkind or "Zdarzenie cyklu życia")
    if item.kind.value == "message":
        if item.family.value == "multi_agent":
            variant = "Wiadomość między agentami"
        elif item.role == "user":
            variant = "Użytkownik · prompt"
        elif item.role in {"assistant", "agent"}:
            channel = item.metadata.get("channel")
            variant = "Asystent · " + {"final": "odpowiedź końcowa", "commentary": "aktualizacja", "analysis": "analiza"}.get(channel, "wiadomość")
        elif item.role:
            variant = {"system": "System", "developer": "Deweloper", "tool": "Narzędzie"}.get(item.role, item.role) + " · wiadomość"
    if item.action_confidence == "inferred":
        variant = "≈ " + variant + " · wnioskowane"
    return family, kind, variant, color


def state_label(item):
    """Completion describes lifecycle, not proof of a successful outcome."""
    state = item.lifecycle.value
    if item.status in {"failed", "failure", "error"} or state == "failed":
        return "! BŁĄD", 7
    if state == "cancelled" or item.status in {"cancelled", "canceled"}:
        return "× ANULOWANY", 4
    if state == "aborted" or item.status == "aborted":
        return "× PRZERWANY", 4
    # The parser also calls waiting 'blocked' in its coarse status field.
    if state == "waiting":
        return "◌ OCZEKIWANIE", 4
    if state == "blocked" or item.status == "blocked":
        return "! BLOKADA", 4
    return {
        "pending": ("◌ OCZEKUJĄCY", 4), "started": ("◌ W TOKU", 2),
        "streaming": ("◌ STRUMIEŃ", 2), "completed": ("✓ ZAKOŃCZONY", 0),
    }.get(state, ("? NIEZNANY", 6))


def search_text(item):
    family, kind, variant, _ = labels(item)
    state, _ = state_label(item)
    return f"{family} {kind} {variant} {state} {item.family.value} {item.kind.value} {item.subkind}"

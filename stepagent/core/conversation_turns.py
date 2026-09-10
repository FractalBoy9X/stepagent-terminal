from __future__ import annotations

import re
from collections.abc import Iterable

from .domain import (
    ConversationTurnKind,
    Interaction,
    InteractionFamily,
    NodeKind,
    RawRecord,
)

_CONTEXT_ENVELOPE = re.compile(
    r"^\s*<(environment_context|user_instructions)>.*</\1>\s*$",
    re.DOTALL,
)
_CONTROL_KINDS = {
    NodeKind.APPROVAL,
    NodeKind.PERMISSION_REQUEST,
    NodeKind.USER_INPUT_REQUEST,
}
_CONTROL_RESPONSE_MARKERS = (
    "approval_response",
    "permission_response",
    "user_input_response",
    "elicitation_response",
)


def _source(interaction: Interaction, records: dict[str, RawRecord]) -> list[RawRecord]:
    return [records[raw_id] for raw_id in interaction.raw_record_ids if raw_id in records]


def _is_context_only(detail: str) -> bool:
    return bool(_CONTEXT_ENVELOPE.fullmatch(detail or ""))


def _has_control_reference(interaction: Interaction, records: list[RawRecord]) -> bool:
    if interaction.call_id:
        return True
    for raw in records:
        payload = raw.record.get("payload") if isinstance(raw.record.get("payload"), dict) else raw.record
        if not isinstance(payload, dict):
            continue
        item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
        if any(item.get(key) or payload.get(key) for key in ("call_id", "request_id", "approval_id")):
            return True
    return False


def _is_direct_user_message(interaction: Interaction, records: dict[str, RawRecord]) -> bool:
    if interaction.kind != NodeKind.MESSAGE or interaction.role != "user" or _is_context_only(interaction.detail):
        return False
    source_records = _source(interaction, records)
    if _has_control_reference(interaction, source_records):
        return False
    if not source_records:
        raw_types = set(interaction.metadata.get("raw_types") or [])
        return bool({"response_item:message", "event_msg:user_message"} & raw_types)
    for raw in source_records:
        payload = raw.record.get("payload") if isinstance(raw.record.get("payload"), dict) else raw.record
        if not isinstance(payload, dict):
            continue
        nested = payload.get("item") if isinstance(payload.get("item"), dict) else None
        nested_type = str((nested or {}).get("type") or "").replace("-", "_").lower()
        if raw.rollout_type == "response_item" and raw.response_item_type in {"message", "user_message"}:
            source_role = str((nested or payload).get("role") or payload.get("role") or "")
            if source_role == "user":
                return True
        if raw.rollout_type == "event_msg" and raw.event_type == "user_message":
            return True
        if nested_type == "user_message":
            return True
    return False


def assign_conversation_turns(
    interactions: Iterable[Interaction],
    raw_records: Iterable[RawRecord] = (),
) -> None:
    """Assign presentation turns without changing source protocol turn fields."""
    ordered = sorted(interactions, key=lambda item: item.index)
    records = {record.raw_id: record for record in raw_records}
    conversation_number = 0
    conversation_id = ConversationTurnKind.INITIALIZATION.value
    conversation_kind = ConversationTurnKind.INITIALIZATION
    pending_controls: list[str] = []

    for interaction in ordered:
        subkind = interaction.subkind.lower()
        if any(marker in subkind for marker in _CONTROL_RESPONSE_MARKERS):
            pending_controls.clear()

        is_user_message = interaction.kind == NodeKind.MESSAGE and interaction.role == "user"
        resumed_execution = interaction.family in {
            InteractionFamily.EXECUTION,
            InteractionFamily.FILESYSTEM,
            InteractionFamily.TOOLING,
            InteractionFamily.MCP,
            InteractionFamily.WEB,
        }
        if pending_controls and resumed_execution and not is_user_message:
            pending_controls.clear()

        classification = ""
        if _is_direct_user_message(interaction, records):
            if pending_controls and conversation_number > 0:
                pending_controls.pop()
                classification = "control_response"
            else:
                pending_controls.clear()
                conversation_number += 1
                conversation_id = f"conversation-turn-{conversation_number}"
                conversation_kind = ConversationTurnKind.USER_MESSAGE
                classification = "conversation_start"

        interaction.conversation_turn_id = conversation_id
        interaction.conversation_turn_number = conversation_number
        interaction.conversation_turn_kind = conversation_kind
        if classification:
            interaction.metadata["conversation_message_classification"] = classification

        if interaction.family == InteractionFamily.HUMAN_CONTROL and interaction.kind in _CONTROL_KINDS:
            pending_controls.append(interaction.interaction_id)

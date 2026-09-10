from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO

from .domain import (
    ConversationTurnKind,
    JSONDict,
    SCHEMA_VERSION,
    Interaction,
    InteractionFamily,
    LifecycleState,
    NodeKind,
    RawRecord,
    SessionDocument,
)
from .conversation_turns import assign_conversation_turns

_MAX_BUFFER = 50_000_000


def _iso_to_epoch_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        return int(numeric * 1000.0 if abs(numeric) < 100_000_000_000 else numeric)
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _camel_to_snake(value: str) -> str:
    value = value.replace("-", "_").replace("/", "_")
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()


def iter_json_objects(stream: TextIO) -> Iterator[JSONDict]:
    """Read JSONL, NDJSON, or concatenated objects without loading a huge buffer."""
    decoder = json.JSONDecoder()
    buffer = ""
    for line in stream:
        buffer += line
        cursor = 0
        while True:
            while cursor < len(buffer) and buffer[cursor].isspace():
                cursor += 1
            if cursor >= len(buffer):
                buffer = ""
                break
            try:
                value, end = decoder.raw_decode(buffer, cursor)
            except json.JSONDecodeError:
                buffer = buffer[cursor:]
                break
            if isinstance(value, dict):
                yield value
            cursor = end
        if len(buffer) > _MAX_BUFFER:
            raise ValueError("JSON input buffer exceeded 50 MB; the source is likely malformed.")


def _compact(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "..."


def _text_from_content(content: Any) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content.strip(), ["text"]
    if not isinstance(content, list):
        return "", []
    text: list[str] = []
    types: list[str] = []
    for item in content:
        if isinstance(item, str):
            text.append(item)
            types.append("text")
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "content")
        types.append(item_type)
        for key in ("text", "input_text", "output_text", "message", "transcript"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                text.append(value.strip())
                break
    return "\n".join(text).strip(), types


def _reasoning_text(payload: JSONDict) -> str:
    for key in ("summary_text", "text", "raw_content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    summary = payload.get("summary")
    if isinstance(summary, str):
        return summary.strip()
    if isinstance(summary, list):
        return "\n".join(
            str(item.get("text", "")).strip()
            for item in summary
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        )
    return ""


def _parse_jsonish(value: Any) -> tuple[str, JSONDict]:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False), value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return value, parsed if isinstance(parsed, dict) else {"value": parsed}
        except (json.JSONDecodeError, TypeError):
            return value, {"raw": value}
    return json.dumps(value, ensure_ascii=False, default=str), {"value": value}


# How sure the parser is about the semantic reading, mirroring the confidence
# metadata already carried by graph edges.
CONFIDENCE_EXACT = "exact"       # the source names the action itself (item type, dedicated event)
CONFIDENCE_DERIVED = "derived"   # structural, one step removed (the tool's name)
CONFIDENCE_INFERRED = "inferred" # read out of the content (a patch header in the arguments)
CONFIDENCE_UNKNOWN = "unknown"   # no rule matched; the record is kept, unclassified

_CONFIDENCE_RANK = {CONFIDENCE_UNKNOWN: 0, CONFIDENCE_INFERRED: 1, CONFIDENCE_DERIVED: 2, CONFIDENCE_EXACT: 3}


@dataclass(frozen=True, slots=True)
class InteractionSpec:
    family: InteractionFamily
    kind: NodeKind
    lifecycle: LifecycleState = LifecycleState.COMPLETED
    subkind: str = ""
    action: str = ""
    confidence: str = CONFIDENCE_EXACT
    source: str = ""
    script_tools: tuple[str, ...] = ()


def _spec(
    family: InteractionFamily,
    kind: NodeKind,
    lifecycle: LifecycleState,
    subkind: str,
    action: str = "",
    confidence: str = CONFIDENCE_EXACT,
    source: str = "",
    script_tools: tuple[str, ...] = (),
) -> InteractionSpec:
    return InteractionSpec(
        family, kind, lifecycle, subkind,
        action or _ACTION_BY_KIND.get(kind, ""), confidence, source, script_tools,
    )


# What the agent did, independent of the envelope Codex used to say it. This is
# the layer the interface reads; `subkind` stays the protocol name underneath.
_ACTION_BY_KIND: dict[NodeKind, str] = {
    NodeKind.MESSAGE: "message",
    NodeKind.REASONING: "reasoning",
    NodeKind.PLAN: "plan_update",
    NodeKind.PLAN_STEP: "plan_step",
    NodeKind.COMMAND: "shell_command",
    NodeKind.TERMINAL_IO: "process_input",
    NodeKind.TOOL_CALL: "tool_invocation",
    NodeKind.TOOL_RESULT: "tool_result",
    NodeKind.TOOL_SEARCH: "tool_discovery",
    NodeKind.MCP_SERVER: "mcp_server",
    NodeKind.MCP_CALL: "mcp_call",
    NodeKind.WEB_ACTION: "web_search",
    NodeKind.FILE_CHANGE: "file_change",
    NodeKind.IMAGE_GENERATION: "image_generation",
    NodeKind.IMAGE_VIEW: "image_view",
    NodeKind.MEDIA_STREAM: "media_session",
    NodeKind.APPROVAL: "approval_request",
    NodeKind.PERMISSION_REQUEST: "permission_request",
    NodeKind.USER_INPUT_REQUEST: "input_request",
    NodeKind.REVIEW: "review_mode",
    NodeKind.COMPACTION: "context_compaction",
    NodeKind.WORLD_STATE: "world_snapshot",
    NodeKind.HOOK: "hook_run",
    NodeKind.SAFETY: "safety_assessment",
    NodeKind.MODEL_EVENT: "model_routing",
    NodeKind.ENVIRONMENT: "context_update",
    NodeKind.USAGE: "token_usage",
    NodeKind.ERROR: "error",
    NodeKind.WARNING: "warning",
    NodeKind.LIFECYCLE: "lifecycle",
    NodeKind.EVENT_UNKNOWN: "unknown_event",
}

# The name of the tool says what happened; the envelope (`function_call`,
# `custom_tool_call`) only says how it was transported. Without this table a
# test run, a patch and a plan update all read as "tool call".
TOOL_ROUTING: dict[str, tuple[InteractionFamily, NodeKind, str]] = {
    "shell": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "shell_command"),
    "shell_command": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "shell_command"),
    "exec_command": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "shell_command"),
    "bash": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "shell_command"),
    "local_shell": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "shell_command"),
    "write_stdin": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "process_input"),
    "wait": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "process_wait"),
    "kill": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "process_kill"),
    # The unified-exec wrapper is a script that calls other tools; the real
    # actions arrive as separate items between its call and its output.
    "exec": (InteractionFamily.TOOLING, NodeKind.TOOL_CALL, "tool_script"),
    "apply_patch": (InteractionFamily.FILESYSTEM, NodeKind.FILE_CHANGE, "file_patch"),
    "update_plan": (InteractionFamily.PLANNING, NodeKind.PLAN, "plan_update"),
    "view_image": (InteractionFamily.MEDIA, NodeKind.IMAGE_VIEW, "image_view"),
    "web_search": (InteractionFamily.WEB, NodeKind.WEB_ACTION, "web_search"),
    "web__run": (InteractionFamily.WEB, NodeKind.WEB_ACTION, "web_search"),
    "request_user_input": (InteractionFamily.HUMAN_CONTROL, NodeKind.USER_INPUT_REQUEST, "input_request"),
    "list_mcp_resources": (InteractionFamily.MCP, NodeKind.MCP_SERVER, "mcp_discovery"),
    "list_mcp_resource_templates": (InteractionFamily.MCP, NodeKind.MCP_SERVER, "mcp_discovery"),
    "read_mcp_resource": (InteractionFamily.MCP, NodeKind.MCP_CALL, "mcp_call"),
    "call_mcp_tool": (InteractionFamily.MCP, NodeKind.MCP_CALL, "mcp_call"),
    "spawn_agent": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_spawn"),
    "wait_agent": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_wait"),
    "resume_agent": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_resume"),
    "close_agent": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_close"),
    "list_agents": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_list"),
    "send_agent_message": (InteractionFamily.MULTI_AGENT, NodeKind.MESSAGE, "agent_message"),
}

# Codex's own semantic layer: the thread item names the action outright, so it
# outranks every other source.
ITEM_ROUTING: dict[str, tuple[InteractionFamily, NodeKind, str]] = {
    "agent_message": (InteractionFamily.COMMUNICATION, NodeKind.MESSAGE, "agent_message"),
    "user_message": (InteractionFamily.COMMUNICATION, NodeKind.MESSAGE, "user_message"),
    "hook_prompt": (InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.HOOK, "hook_run"),
    "reasoning": (InteractionFamily.COGNITION, NodeKind.REASONING, "reasoning"),
    "plan": (InteractionFamily.PLANNING, NodeKind.PLAN, "plan_update"),
    "command_execution": (InteractionFamily.EXECUTION, NodeKind.COMMAND, "shell_command"),
    "file_change": (InteractionFamily.FILESYSTEM, NodeKind.FILE_CHANGE, "file_change"),
    "mcp_tool_call": (InteractionFamily.MCP, NodeKind.MCP_CALL, "mcp_call"),
    "web_search": (InteractionFamily.WEB, NodeKind.WEB_ACTION, "web_search"),
    "image_view": (InteractionFamily.MEDIA, NodeKind.IMAGE_VIEW, "image_view"),
    "image_generation": (InteractionFamily.MEDIA, NodeKind.IMAGE_GENERATION, "image_generation"),
    "context_compaction": (InteractionFamily.CONTEXT_STATE, NodeKind.COMPACTION, "context_compaction"),
    "entered_review_mode": (InteractionFamily.CONTEXT_STATE, NodeKind.REVIEW, "review_mode"),
    "exited_review_mode": (InteractionFamily.CONTEXT_STATE, NodeKind.REVIEW, "review_mode"),
    "dynamic_tool_call": (InteractionFamily.TOOLING, NodeKind.TOOL_CALL, "tool_invocation"),
    "extension": (InteractionFamily.TOOLING, NodeKind.TOOL_CALL, "extension_action"),
    "collab_tool_call": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_action"),
    "collab_agent_tool_call": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_action"),
    "sub_agent_activity": (InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL, "agent_activity"),
}

# A collab item names the operation in its own `tool` field.
_COLLAB_ACTIONS = {
    "spawn_agent": "agent_spawn",
    "wait": "agent_wait",
    "wait_agent": "agent_wait",
    "resume_agent": "agent_resume",
    "close_agent": "agent_close",
    "list_agents": "agent_list",
    "send_agent_message": "agent_message",
}


_EVENT_GROUPS: list[tuple[set[str], InteractionFamily, NodeKind]] = [
    ({"error", "stream_error"}, InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.ERROR),
    ({"warning", "guardian_warning", "deprecation_notice"}, InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.WARNING),
    ({"realtime_conversation_started", "realtime_conversation_realtime", "realtime_conversation_closed", "realtime_conversation_sdp", "realtime_conversation_list_voices_response"}, InteractionFamily.MEDIA, NodeKind.MEDIA_STREAM),
    ({"model_reroute", "model_verification"}, InteractionFamily.SAFETY_SECURITY, NodeKind.MODEL_EVENT),
    ({"turn_moderation_metadata", "safety_buffering", "guardian_assessment"}, InteractionFamily.SAFETY_SECURITY, NodeKind.SAFETY),
    ({"context_compacted", "thread_rolled_back"}, InteractionFamily.CONTEXT_STATE, NodeKind.COMPACTION),
    ({"turn_started", "task_started", "turn_complete", "task_complete", "turn_aborted", "shutdown_complete", "item_started", "item_completed", "raw_response_completed"}, InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.LIFECYCLE),
    ({"agent_message", "user_message", "agent_message_content_delta"}, InteractionFamily.COMMUNICATION, NodeKind.MESSAGE),
    ({"agent_reasoning", "agent_reasoning_raw_content", "agent_reasoning_section_break", "reasoning_content_delta", "reasoning_raw_content_delta"}, InteractionFamily.COGNITION, NodeKind.REASONING),
    ({"session_configured", "environment_connected", "environment_disconnected", "thread_settings_applied", "thread_goal_updated", "thread_queue_changed", "thread_name_updated"}, InteractionFamily.CONTEXT_STATE, NodeKind.ENVIRONMENT),
    ({"mcp_startup_update", "mcp_startup_complete"}, InteractionFamily.MCP, NodeKind.MCP_SERVER),
    ({"mcp_tool_call_begin", "mcp_tool_call_end"}, InteractionFamily.MCP, NodeKind.MCP_CALL),
    ({"web_search_begin", "web_search_end"}, InteractionFamily.WEB, NodeKind.WEB_ACTION),
    ({"image_generation_begin", "image_generation_end"}, InteractionFamily.MEDIA, NodeKind.IMAGE_GENERATION),
    ({"exec_command_begin", "exec_command_output_delta", "terminal_interaction", "exec_command_end"}, InteractionFamily.EXECUTION, NodeKind.COMMAND),
    ({"view_image_tool_call"}, InteractionFamily.MEDIA, NodeKind.IMAGE_VIEW),
    ({"exec_approval_request", "apply_patch_approval_request"}, InteractionFamily.HUMAN_CONTROL, NodeKind.APPROVAL),
    ({"request_permissions"}, InteractionFamily.HUMAN_CONTROL, NodeKind.PERMISSION_REQUEST),
    ({"request_user_input", "elicitation_request"}, InteractionFamily.HUMAN_CONTROL, NodeKind.USER_INPUT_REQUEST),
    ({"dynamic_tool_call_request", "dynamic_tool_call_response"}, InteractionFamily.TOOLING, NodeKind.TOOL_CALL),
    ({"patch_apply_begin", "patch_apply_updated", "patch_apply_end", "turn_diff"}, InteractionFamily.FILESYSTEM, NodeKind.FILE_CHANGE),
    ({"plan_update", "plan_delta"}, InteractionFamily.PLANNING, NodeKind.PLAN),
    ({"entered_review_mode", "exited_review_mode"}, InteractionFamily.CONTEXT_STATE, NodeKind.REVIEW),
    ({"raw_response_item"}, InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.LIFECYCLE),
    ({"hook_started", "hook_completed"}, InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.HOOK),
    ({"collab_agent_spawn_begin", "collab_agent_spawn_end", "collab_agent_interaction_begin", "collab_agent_interaction_end", "collab_waiting_begin", "collab_waiting_end", "collab_close_begin", "collab_close_end", "collab_resume_begin", "collab_resume_end", "sub_agent_activity"}, InteractionFamily.MULTI_AGENT, NodeKind.TOOL_CALL),
    ({"token_count"}, InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.USAGE),
]

KNOWN_EVENT_TYPES = frozenset(value for values, _, _ in _EVENT_GROUPS for value in values)

KNOWN_RESPONSE_TYPES = frozenset({
    "additional_tools", "message", "agent_message", "reasoning", "local_shell_call", "function_call",
    "tool_search_call", "function_call_output", "custom_tool_call", "custom_tool_call_output",
    "tool_search_output", "web_search_call", "image_generation_call", "compaction",
    "compaction_trigger", "context_compaction", "other", "ghost_snapshot",
})

KNOWN_ROLLOUT_TYPES = frozenset({
    "session_meta", "response_item", "inter_agent_communication",
    "inter_agent_communication_metadata", "compacted", "turn_context", "world_state", "event_msg", "token_usage_record",
})


def _lifecycle_from_name(name: str, payload: JSONDict) -> LifecycleState:
    status = str(payload.get("status") or "").lower()
    if status in {"failed", "error"}:
        return LifecycleState.FAILED
    if status in {"cancelled", "canceled"}:
        return LifecycleState.CANCELLED
    if status == "aborted" or name.endswith("aborted"):
        return LifecycleState.ABORTED
    if name.endswith(("_begin", "_started")) or name in {"turn_started", "task_started", "item_started", "hook_started"}:
        return LifecycleState.STARTED
    if name.endswith("_delta") or name.endswith("_updated") or name.endswith("_realtime"):
        return LifecycleState.STREAMING
    if name in {"request_user_input", "request_permissions", "exec_approval_request", "apply_patch_approval_request", "elicitation_request", "collab_waiting_begin"}:
        return LifecycleState.WAITING
    if name.endswith(("_end", "_complete", "_completed", "_closed")):
        return LifecycleState.COMPLETED
    return LifecycleState.COMPLETED


# Events whose kind-level default would be vaguer than what they actually say.
_EVENT_ACTIONS = {
    "patch_apply_begin": "file_patch",
    "patch_apply_updated": "file_patch",
    "patch_apply_end": "file_patch",
    "turn_diff": "turn_diff",
    "view_image_tool_call": "image_view",
    "context_compacted": "context_compaction",
    "thread_rolled_back": "context_rollback",
}


def _event_spec(name: str, payload: JSONDict) -> InteractionSpec:
    for values, family, kind in _EVENT_GROUPS:
        if name in values:
            return _spec(
                family, kind, _lifecycle_from_name(name, payload), name,
                _EVENT_ACTIONS.get(name, ""), source="event_type",
            )
    return _spec(
        InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.EVENT_UNKNOWN, LifecycleState.UNKNOWN,
        name or "unknown", "unknown_event", CONFIDENCE_UNKNOWN, "event_type",
    )


def _response_spec(name: str, payload: JSONDict) -> InteractionSpec:
    lifecycle = _lifecycle_from_name(name, payload)
    mapping: dict[str, tuple[InteractionFamily, NodeKind, LifecycleState]] = {
        "additional_tools": (InteractionFamily.TOOLING, NodeKind.TOOL_SEARCH, LifecycleState.COMPLETED),
        "message": (InteractionFamily.COMMUNICATION, NodeKind.MESSAGE, LifecycleState.COMPLETED),
        "agent_message": (InteractionFamily.COMMUNICATION, NodeKind.MESSAGE, LifecycleState.COMPLETED),
        "reasoning": (InteractionFamily.COGNITION, NodeKind.REASONING, LifecycleState.COMPLETED),
        "local_shell_call": (InteractionFamily.EXECUTION, NodeKind.COMMAND, lifecycle),
        "function_call": (InteractionFamily.TOOLING, NodeKind.TOOL_CALL, LifecycleState.STARTED),
        "custom_tool_call": (InteractionFamily.TOOLING, NodeKind.TOOL_CALL, LifecycleState.STARTED),
        "function_call_output": (InteractionFamily.TOOLING, NodeKind.TOOL_CALL, LifecycleState.COMPLETED),
        "custom_tool_call_output": (InteractionFamily.TOOLING, NodeKind.TOOL_CALL, LifecycleState.COMPLETED),
        "tool_search_call": (InteractionFamily.TOOLING, NodeKind.TOOL_SEARCH, LifecycleState.STARTED),
        "tool_search_output": (InteractionFamily.TOOLING, NodeKind.TOOL_SEARCH, LifecycleState.COMPLETED),
        "web_search_call": (InteractionFamily.WEB, NodeKind.WEB_ACTION, lifecycle),
        "image_generation_call": (InteractionFamily.MEDIA, NodeKind.IMAGE_GENERATION, lifecycle),
        "compaction": (InteractionFamily.CONTEXT_STATE, NodeKind.COMPACTION, LifecycleState.COMPLETED),
        "compaction_trigger": (InteractionFamily.CONTEXT_STATE, NodeKind.COMPACTION, LifecycleState.STARTED),
        "context_compaction": (InteractionFamily.CONTEXT_STATE, NodeKind.COMPACTION, lifecycle),
        "ghost_snapshot": (InteractionFamily.CONTEXT_STATE, NodeKind.WORLD_STATE, LifecycleState.COMPLETED),
    }
    family, kind, state = mapping.get(name, (InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.EVENT_UNKNOWN, LifecycleState.UNKNOWN))
    routed = _tool_routed_spec(name, payload, state)
    if routed is not None:
        return routed
    if kind == NodeKind.EVENT_UNKNOWN:
        confidence = CONFIDENCE_UNKNOWN
    elif name in _GENERIC_TOOL_ENVELOPES:
        # "A tool was called" is transport, not meaning: the envelope must never
        # outrank a classification that knows which tool it was.
        confidence = CONFIDENCE_DERIVED
    else:
        confidence = CONFIDENCE_EXACT
    action = "tool_result" if name.endswith("_output") else ""
    return _spec(family, kind, state, name or "other", action, confidence, "response_type")


_GENERIC_TOOL_ENVELOPES = frozenset({
    "function_call", "custom_tool_call", "function_call_output",
    "custom_tool_call_output", "dynamic_tool_call_request", "dynamic_tool_call_response",
})


# What a command does to the workspace, when the command is simple enough to
# say so honestly. The family stays EXECUTION — a shell run is a shell run —
# but the action can name the effect, flagged as inferred.
_COMMAND_EFFECTS = {
    "mkdir": "file_created", "touch": "file_created",
    "mv": "file_moved", "rename": "file_moved",
    "rm": "file_deleted", "rmdir": "file_deleted", "unlink": "file_deleted",
    "cp": "file_copied",
    "cat": "file_read", "sed": "file_read", "head": "file_read", "tail": "file_read",
    "pwd": "environment_probe", "uname": "environment_probe", "whoami": "environment_probe",
    "hostname": "environment_probe", "printenv": "environment_probe", "arch": "environment_probe",
}
# Commands that only look at the workspace. A chain may contain them freely
# without making its effect ambiguous.
_NEUTRAL_COMMANDS = frozenset({"ls", "wc", "echo", "printf", "cd", "test", "true", ":", "stat", "file", "find", "tree"})
# Substitution and redirection hide effects the first token does not show.
_OPAQUE_COMMAND = re.compile(r"[|>]|\$\(|`")
_COMMAND_SEPARATORS = re.compile(r"[;\n]|&&|\|\|")
_SHELL_WRAPPERS = ("bash", "sh", "zsh", "-lc", "-c", "-l")


def _command_effect(command: Any) -> str | None:
    """Effect of a command, or None whenever a single honest answer is impossible.

    A chain qualifies only when every segment is either a known effect or a
    pure inspection, and the effects agree: `mv a b; ls -1` moved a file,
    `rm -rf build; mkdir build` did two different things and stays a command.
    """
    if isinstance(command, (list, tuple)):
        parts = [str(part) for part in command]
        while parts and (parts[0].rsplit("/", 1)[-1] in _SHELL_WRAPPERS or parts[0].startswith("-")):
            parts.pop(0)
        text = " ".join(parts)
    else:
        text = str(command or "")
    text = text.strip()
    if not text or _OPAQUE_COMMAND.search(text):
        return None
    effects = set()
    for segment in _COMMAND_SEPARATORS.split(text):
        segment = segment.strip().strip("{}()").strip()
        if not segment:
            continue
        first = segment.split()[0].rsplit("/", 1)[-1]
        if first in _NEUTRAL_COMMANDS:
            continue
        effect = _COMMAND_EFFECTS.get(first)
        if effect is None:
            return None
        effects.add(effect)
    return effects.pop() if len(effects) == 1 else None


# The unified-exec script names the tools it drives: `await tools.exec_command({…})`.
_SCRIPT_TOOL_RE = re.compile(r"\btools\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# web__run carries the actual web verb in its argument key.
_WEB_ARGUMENT_ACTIONS = (("search_query", "web_search"), ("open", "web_open"), ("find", "web_find"))
_SCRIPT_COMMAND_RE = re.compile(r"""cmd:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")


def _script_routed_spec(payload: JSONDict, subtype: str, state: LifecycleState) -> InteractionSpec | None:
    """Read the script a unified-exec call carries and adopt the tool it drives.

    A wrapper that runs one tool *is* that action for the reader; only a script
    driving several different tools stays a script.
    """
    script = payload.get("input")
    if not isinstance(script, str) or not script:
        return None
    tools = [tool for tool in dict.fromkeys(_SCRIPT_TOOL_RE.findall(script)) if tool in TOOL_ROUTING]
    routings = [TOOL_ROUTING[tool] for tool in tools]
    families = {routing[0] for routing in routings}
    # Several calls to tools of one family still describe one kind of work
    # ("discovered MCP resources"); a script crossing families is a script.
    if len(families) != 1:
        return None
    family, kind, action = routings[0]
    if action == "tool_script":
        return None
    if family == InteractionFamily.WEB:
        action = next((value for key, value in _WEB_ARGUMENT_ACTIONS if f"{key}:" in script or f'"{key}"' in script), action)
    if action == "shell_command":
        command = _SCRIPT_COMMAND_RE.search(script)
        effect = _command_effect(command.group(1)) if command else None
        if effect:
            return _spec(family, kind, state, subtype, effect, CONFIDENCE_INFERRED, "command", tuple(tools))
    return _spec(family, kind, state, subtype, action, CONFIDENCE_DERIVED, "script", tuple(tools))


def _tool_routed_spec(name: str, payload: JSONDict, state: LifecycleState) -> InteractionSpec | None:
    """Classify a tool call by what the tool does, not by how it was sent."""
    if name not in {"function_call", "custom_tool_call", "function_call_output",
                    "custom_tool_call_output", "local_shell_call", "dynamic_tool_call_request"}:
        return None
    tool = str(payload.get("name") or "").strip()
    routing = TOOL_ROUTING.get(tool)
    if routing is not None:
        family, kind, action = routing
        if action == "tool_script":
            scripted = _script_routed_spec(payload, name, state)
            if scripted is not None:
                return scripted
            # It drives several families, so it stays a script — but it can
            # still say which tools it drove.
            script = payload.get("input")
            tools = tuple(dict.fromkeys(_SCRIPT_TOOL_RE.findall(script))) if isinstance(script, str) else ()
            return _spec(family, kind, state, name, action, CONFIDENCE_DERIVED, "tool_name", tools)
        if action == "shell_command":
            _, arguments = _parse_jsonish(payload.get("arguments") or payload.get("input") or "")
            effect = _command_effect(arguments.get("command") or arguments.get("cmd") or "")
            if effect:
                return _spec(family, kind, state, name, effect, CONFIDENCE_INFERRED, "command")
        return _spec(family, kind, state, name, action, CONFIDENCE_DERIVED, "tool_name")
    # No name to go on (outputs carry only a call_id), so the content decides.
    # A patch announces itself; nothing else has to be guessed.
    text = payload.get("input") if isinstance(payload.get("input"), str) else payload.get("arguments")
    if isinstance(text, str) and text.lstrip().startswith("*** Begin Patch"):
        return _spec(
            InteractionFamily.FILESYSTEM, NodeKind.FILE_CHANGE, state, name,
            "file_patch", CONFIDENCE_INFERRED, "content",
        )
    return None


def _nested_item_spec(payload: JSONDict) -> InteractionSpec | None:
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    name = _camel_to_snake(str(item.get("type") or ""))
    lifecycle = _lifecycle_from_name(str(payload.get("type") or ""), payload)
    routing = ITEM_ROUTING.get(name)
    if routing is None:
        return _spec(
            InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.EVENT_UNKNOWN, lifecycle,
            name or "item", "unknown_event", CONFIDENCE_UNKNOWN, "item.type",
        )
    family, kind, action = routing
    if name == "command_execution":
        effect = _command_effect(item.get("command"))
        if effect:
            return _spec(family, kind, lifecycle, name, effect, CONFIDENCE_INFERRED, "command")
    if name in {"collab_agent_tool_call", "collab_tool_call"}:
        action = _COLLAB_ACTIONS.get(str(item.get("tool") or ""), action)
    elif name == "sub_agent_activity":
        action = f"agent_{_camel_to_snake(str(item.get('kind') or 'activity'))}"
    elif name == "extension":
        # An extension names its own channel: "web.search", "web.open", …
        channel = str(item.get("kind") or "")
        if channel.startswith("web."):
            family, kind = InteractionFamily.WEB, NodeKind.WEB_ACTION
            action = {"web.search": "web_search", "web.open": "web_open", "web.find": "web_find"}.get(channel, "web_search")
    return _spec(family, kind, lifecycle, name or "item", action, CONFIDENCE_EXACT, "item.type")


def _raw_record(index: int, record: JSONDict) -> RawRecord:
    raw_type = _camel_to_snake(str(record.get("type") or ""))
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    subtype = _camel_to_snake(str(payload.get("type") or ""))
    digest = hashlib.sha1(json.dumps(record, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]
    ordinal = record.get("ordinal")
    return RawRecord(
        raw_id=f"raw:{index}:{digest}", index=index,
        ordinal=int(ordinal) if isinstance(ordinal, int) else None,
        timestamp=str(record.get("timestamp") or payload.get("timestamp") or ""),
        rollout_type=raw_type,
        response_item_type=subtype if raw_type == "response_item" else "",
        event_type=subtype if raw_type == "event_msg" else "",
        record=record,
    )


def _record_spec(raw: RawRecord, payload: JSONDict) -> InteractionSpec:
    raw_type = raw.rollout_type
    subtype = raw.response_item_type or raw.event_type
    if raw_type == "response_item":
        return _response_spec(subtype, payload)
    if raw_type == "event_msg":
        nested = _nested_item_spec(payload) if subtype in {"item_started", "item_completed"} else None
        return nested or _event_spec(subtype, payload)
    rollout = {
        "inter_agent_communication": (InteractionFamily.MULTI_AGENT, NodeKind.MESSAGE, "agent_message"),
        "inter_agent_communication_metadata": (InteractionFamily.MULTI_AGENT, NodeKind.LIFECYCLE, "agent_activity"),
        "compacted": (InteractionFamily.CONTEXT_STATE, NodeKind.COMPACTION, "context_compaction"),
        "turn_context": (InteractionFamily.CONTEXT_STATE, NodeKind.ENVIRONMENT, "context_update"),
        "world_state": (InteractionFamily.CONTEXT_STATE, NodeKind.WORLD_STATE, "world_snapshot"),
        "session_meta": (InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.LIFECYCLE, "lifecycle"),
        "token_usage_record": (InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.USAGE, "token_usage"),
    }.get(raw_type)
    if rollout is not None:
        family, kind, action = rollout
        return _spec(family, kind, LifecycleState.COMPLETED, raw_type, action, CONFIDENCE_EXACT, "rollout_type")
    # A record shape this parser version has no rule for. It is kept in full;
    # saying "exact" about it would be a lie.
    return _spec(
        InteractionFamily.LIFECYCLE_OBSERVABILITY, NodeKind.EVENT_UNKNOWN, LifecycleState.UNKNOWN,
        raw_type or "unknown", "unknown_event", CONFIDENCE_UNKNOWN, "rollout_type",
    )


def _payload_text(payload: JSONDict, spec: InteractionSpec) -> tuple[str, JSONDict]:
    source = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    assert isinstance(source, dict)
    metadata: JSONDict = {}
    if spec.kind == NodeKind.MESSAGE:
        text, types = _text_from_content(source.get("content"))
        text = text or str(source.get("message") or "")
        metadata["content_types"] = types
        if isinstance(source.get("channel"), str):
            metadata["channel"] = source["channel"]
        return text, metadata
    if spec.kind == NodeKind.REASONING:
        return _reasoning_text(source), metadata
    if spec.kind in {NodeKind.TOOL_CALL, NodeKind.TOOL_SEARCH, NodeKind.COMMAND, NodeKind.MCP_CALL, NodeKind.WEB_ACTION}:
        value = source.get("input", source.get("arguments", source.get("command", source.get("query", source.get("action", "")))))
        raw, parsed = _parse_jsonish(value)
        metadata["arguments"] = parsed
        return raw, metadata
    if spec.kind == NodeKind.PLAN:
        value = source.get("plan", source.get("steps", source.get("delta", source.get("message", source))))
        if isinstance(value, list):
            metadata["steps"] = value
        return _compact(value, 2000), metadata
    if spec.kind == NodeKind.FILE_CHANGE:
        value = source.get("input", source.get("arguments", source.get("changes", source.get("patch", source.get("diff", source.get("stdout", source))))))
        return _compact(value, 4000), metadata
    for key in ("message", "text", "formatted_output", "aggregated_output", "stdout", "query", "reason", "summary"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), metadata
    return _compact(source, 2000), metadata


def _result_from_payload(payload: JSONDict, spec: InteractionSpec) -> JSONDict:
    source = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if not isinstance(source, dict):
        return {}
    result: JSONDict = {}
    for key in ("output", "aggregated_output", "formatted_output", "stdout", "stderr", "exit_code", "duration", "status", "success", "error", "results", "response"):
        if key in source:
            result[key] = source[key]
    output = result.get("output")
    if isinstance(output, str):
        try:
            decoded = json.loads(output)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            for key in ("exit_code", "status", "success", "error"):
                if key in decoded:
                    result.setdefault(key, decoded[key])
            if decoded.get("isError") is True:
                result["success"] = False
        match = re.search(r"(?m)^Process (?:exited with code|exit code:)\s*(-?\d+)\s*$", output)
        if match:
            result.setdefault("exit_code", int(match.group(1)))
    if spec.subkind.endswith(("_output", "_end", "_complete", "_completed")) and not result:
        result["value"] = source
    return result


def _status(payload: JSONDict, result: JSONDict, lifecycle: LifecycleState) -> str:
    source = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    source = source if isinstance(source, dict) else payload
    value = str(source.get("status") or result.get("status") or "").lower()
    if value in {"failed", "failure", "error"} or lifecycle == LifecycleState.FAILED:
        return "error"
    if value in {"cancelled", "canceled"} or lifecycle == LifecycleState.CANCELLED:
        return "cancelled"
    exit_code = source.get("exit_code", result.get("exit_code"))
    if isinstance(exit_code, int):
        return "success" if exit_code == 0 else "error"
    success = source.get("success")
    if isinstance(success, bool):
        return "success" if success else "error"
    output_text = " ".join(str(result.get(key) or "") for key in ("output", "aggregated_output", "stderr", "error")).lower()
    if any(token in output_text for token in ("traceback", "fatal:", "error:", "failed", "permission denied")):
        return "error"
    if lifecycle == LifecycleState.COMPLETED:
        return "success"
    if lifecycle in {LifecycleState.WAITING, LifecycleState.BLOCKED}:
        return "blocked"
    return "unknown"


def _correlation_key(payload: JSONDict, spec: InteractionSpec, turn_id: str, detail: str, raw: RawRecord) -> str:
    source = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    source = source if isinstance(source, dict) else payload
    item_id = str(source.get("id") or payload.get("item_id") or "")
    call_id = str(source.get("call_id") or payload.get("call_id") or "")
    process_id = str(source.get("process_id") or payload.get("process_id") or "")
    if spec.kind in {NodeKind.APPROVAL, NodeKind.PERMISSION_REQUEST, NodeKind.USER_INPUT_REQUEST}:
        request_id = str(source.get("request_id") or payload.get("request_id") or raw.raw_id)
        return f"control:{spec.kind.value}:{request_id}"
    if spec.kind == NodeKind.FILE_CHANGE and call_id:
        return f"call:{call_id}"
    if spec.kind in {NodeKind.USAGE, NodeKind.LIFECYCLE, NodeKind.ENVIRONMENT} and not item_id:
        return raw.raw_id
    # New Codex logs give call and output distinct record/item IDs (ctc_/ctco_)
    # while sharing call_id. That explicit relationship wins over either ID.
    if call_id:
        return f"call:{call_id}"
    if item_id.startswith("call_"):
        # A thread item reports the result of a tool call and reuses its id;
        # keeping the namespaces apart would show the call and its outcome as
        # two separate steps.
        return f"call:{item_id}"
    if item_id:
        return f"item:{item_id}"
    if call_id:
        return f"call:{call_id}"
    if process_id and spec.kind == NodeKind.COMMAND:
        return f"process:{process_id}"
    if spec.kind in {NodeKind.MESSAGE, NodeKind.REASONING} and detail:
        digest = hashlib.sha1(_compact(detail, 800).encode()).hexdigest()[:14]
        return f"mirror:{turn_id}:{spec.kind.value}:{digest}"
    return raw.raw_id


# Placeholders that only say "something happened"; a named effect beats them
# even when it was inferred rather than read from a field.
_GENERIC_ACTIONS = frozenset({
    "", "tool_invocation", "tool_result", "tool_script", "shell_command",
    "lifecycle", "unknown_event", "message",
})


def _action_rank(action: str, confidence: str) -> tuple[int, int]:
    return (0 if action in _GENERIC_ACTIONS else 1, _CONFIDENCE_RANK.get(confidence, 0))


# Why several source records became one step. The panel shows this: a parser
# that merges records without saying why is a black box.
_CORRELATION_BASIS = (
    ("item:", "item_id"),
    ("call:", "call_id"),
    ("file_change:", "call_id"),
    ("control:", "request_id"),
    ("process:", "process_id"),
    ("mirror:", "content_fingerprint"),
)


def _correlation_basis(key: str) -> str:
    for prefix, basis in _CORRELATION_BASIS:
        if key.startswith(prefix):
            return basis
    return "single_record"


def _promote(existing: Interaction, spec: InteractionSpec) -> None:
    generic = {NodeKind.TOOL_CALL, NodeKind.LIFECYCLE, NodeKind.EVENT_UNKNOWN}
    specific = spec.kind not in generic
    if existing.kind in generic and specific:
        existing.kind = spec.kind
        existing.family = spec.family
    # A call routed by tool name (derived) is later joined by its thread item,
    # which names the action outright (exact). The surer reading takes over —
    # but a generic envelope arriving late must not undo a specific one.
    # Genericity is a property of the meaning, not of the node kind: a collab
    # item is a TOOL_CALL by kind yet names its operation exactly.
    meaningful = spec.action not in _GENERIC_ACTIONS
    stronger = _action_rank(spec.action, spec.confidence) > _action_rank(existing.action, existing.action_confidence)
    if spec.action and stronger and (meaningful or not existing.action):
        existing.action = spec.action
        existing.action_confidence = spec.confidence
        existing.action_source = spec.source
        if meaningful and spec.confidence == CONFIDENCE_EXACT:
            existing.family = spec.family
    if spec.lifecycle in {LifecycleState.COMPLETED, LifecycleState.FAILED, LifecycleState.CANCELLED, LifecycleState.ABORTED}:
        existing.lifecycle = spec.lifecycle
    elif existing.lifecycle in {LifecycleState.UNKNOWN, LifecycleState.PENDING}:
        existing.lifecycle = spec.lifecycle


def parse_codex_records(records: Iterable[JSONDict], source_name: str = "session.jsonl") -> SessionDocument:
    parser = IncrementalParser(source_name)
    for record in records:
        parser.feed(record)
    return parser.snapshot()


def parse_codex_jsonl(path: str | Path) -> SessionDocument:
    source = Path(path)
    with source.open("r", encoding="utf-8", errors="replace") as stream:
        return parse_codex_records(iter_json_objects(stream), source.name)


_RAW_RECORD_FIELDS = frozenset(RawRecord.__dataclass_fields__)
_INTERACTION_FIELDS = frozenset(Interaction.__dataclass_fields__)


def _known_fields(value: JSONDict, allowed: frozenset[str]) -> JSONDict:
    """Keep the fields this schema version knows; ignore anything newer."""
    return {key: entry for key, entry in value.items() if key in allowed}


def parse_v4_json(data: Any, source_name: str = "session.v4.json") -> SessionDocument:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        found = data.get("schema_version") if isinstance(data, dict) else None
    raise ValueError(f"Unsupported session schema {found!r}; schema v4 is required.")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    raw_records: list[RawRecord] = []
    for value in data.get("raw_records", []):
        if isinstance(value, dict):
            raw_records.append(RawRecord(**_known_fields(value, _RAW_RECORD_FIELDS)))
    interactions: list[Interaction] = []
    needs_conversation_derivation = False
    for value in data.get("interactions", []):
        if not isinstance(value, dict):
            continue
        item = _known_fields(value, _INTERACTION_FIELDS)
        # A file written by a newer StepAgent must not take this one down; the
        # unrecognized fields ride along in metadata instead of raising.
        unmapped = {key: entry for key, entry in value.items() if key not in _INTERACTION_FIELDS}
        if unmapped:
            metadata = item.get("metadata")
            item["metadata"] = {**(metadata if isinstance(metadata, dict) else {}), "unmapped_fields": unmapped}
        if not {"conversation_turn_id", "conversation_turn_number", "conversation_turn_kind"} <= item.keys():
            needs_conversation_derivation = True
        item["family"] = InteractionFamily(item.get("family", InteractionFamily.LIFECYCLE_OBSERVABILITY.value))
        item["kind"] = NodeKind(item.get("kind", NodeKind.EVENT_UNKNOWN.value))
        item["lifecycle"] = LifecycleState(item.get("lifecycle", LifecycleState.UNKNOWN.value))
        item["conversation_turn_kind"] = ConversationTurnKind(
            item.get("conversation_turn_kind", ConversationTurnKind.INITIALIZATION.value)
        )
        interactions.append(Interaction(**item))
    if needs_conversation_derivation:
        assign_conversation_turns(interactions, raw_records)
    agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {"type": "codex"}
    return SessionDocument(
        session_id=str(meta.get("session_id") or Path(source_name).stem),
        label=str(meta.get("label") or Path(source_name).stem), source=str(meta.get("source") or source_name),
        description=str(meta.get("description") or ""), generated_at=str(meta.get("generated_at") or ""),
        agent=agent, raw_records=raw_records, interactions=interactions,
        metadata={key: value for key, value in meta.items() if key not in {"session_id", "label", "source", "description", "generated_at", "agent"}},
    )


def parse_session_file(path: str | Path) -> SessionDocument:
    source = Path(path)
    if source.suffix.lower() in {".jsonl", ".ndjson"}:
        return parse_codex_jsonl(source)
    data = json.loads(source.read_text(encoding="utf-8", errors="replace"))
    return parse_v4_json(data, source.name)


class IncrementalParser:
    """Keep correlation state across appended records; no replay of old JSONL."""

    def __init__(self, source_name: str = "session.jsonl") -> None:
        self.source_name = source_name
        self.raw_records: list[RawRecord] = []
        self.interactions: list[Interaction] = []
        self.meta: JSONDict = {}
        self.latest_context: JSONDict = {}
        self._consumer = self._consume()
        next(self._consumer)

    def feed(self, record: JSONDict) -> None:
        raw = _raw_record(len(self.raw_records), record)
        self.raw_records.append(raw)
        self._consumer.send(raw)

    def _consume(self):
        meta: JSONDict = {}
        interactions = self.interactions
        by_key: dict[str, Interaction] = {}
        mirror_candidates: dict[str, tuple[Interaction, int, str]] = {}
        turn_numbers: dict[str, int] = {}
        turn_number = 0
        turn_id = "turn-1"
        latest_context: JSONDict = {}

        while True:
            raw = yield
            record = raw.record
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
            assert isinstance(payload, dict)
            subtype = raw.response_item_type or raw.event_type

            if raw.rollout_type == "session_meta":
                meta.update(payload)

            candidate_turn = str(payload.get("turn_id") or "")
            if raw.rollout_type == "event_msg" and subtype in {"task_started", "turn_started"}:
                candidate_turn = candidate_turn or f"turn-{turn_number + 1}"
                if candidate_turn not in turn_numbers:
                    turn_number += 1
                    turn_numbers[candidate_turn] = turn_number
                turn_id = candidate_turn
            elif candidate_turn:
                if candidate_turn not in turn_numbers:
                    turn_number += 1
                    turn_numbers[candidate_turn] = turn_number
                turn_id = candidate_turn
            elif turn_number == 0:
                turn_number = 1
                turn_numbers[turn_id] = turn_number

            if raw.rollout_type == "turn_context":
                latest_context = payload

            spec = _record_spec(raw, payload)
            detail, extracted_meta = _payload_text(payload, spec)
            key = _correlation_key(payload, spec, turn_id, detail, raw)
            source = payload.get("item") if isinstance(payload.get("item"), dict) else payload
            assert isinstance(source, dict)
            call_id = str(source.get("call_id") or payload.get("call_id") or "")
            item_id = str(source.get("id") or payload.get("item_id") or "")
            timestamp = raw.timestamp
            timestamp_ms = _iso_to_epoch_ms(timestamp)
            started_value = payload.get("started_at_ms", payload.get("started_at", source.get("started_at")))
            completed_value = payload.get("completed_at_ms", payload.get("completed_at", source.get("completed_at")))
            started_ms = _iso_to_epoch_ms(started_value) or timestamp_ms
            completed_ms = _iso_to_epoch_ms(completed_value)
            if completed_ms is None and spec.lifecycle in {LifecycleState.COMPLETED, LifecycleState.FAILED, LifecycleState.CANCELLED, LifecycleState.ABORTED}:
                completed_ms = timestamp_ms
            result = _result_from_payload(payload, spec)
            inferred_role = "user" if subtype == "user_message" else "assistant" if subtype in {"agent_message", "agent_message_content_delta"} else ""
            role = str(source.get("role") or payload.get("role") or inferred_role or ("assistant" if spec.kind == NodeKind.REASONING else ""))
            actor_id = str(payload.get("agent_id") or payload.get("source_agent_id") or ("user" if role == "user" else "primary"))
            target_id = str(payload.get("target_agent_id") or payload.get("receiver_agent_id") or "")
            action = spec.action
            if spec.kind == NodeKind.MESSAGE and action in {"", "message"}:
                # "Who spoke" is the whole meaning of a message step.
                action = {"user": "user_message", "assistant": "agent_message", "agent": "agent_message"}.get(role, "system_message")
            label_root = spec.subkind.replace("_", " ").title() or spec.kind.value.replace("_", " ").title()
            label = f"{label_root}: {_compact(detail, 150)}" if detail else label_root

            mirror_key = ""
            if spec.kind in {NodeKind.MESSAGE, NodeKind.REASONING} and detail:
                mirror_digest = hashlib.sha1(_compact(detail, 800).encode()).hexdigest()[:14]
                mirror_key = f"mirror:{turn_id}:{spec.kind.value}:{mirror_digest}"
            raw_type = f"{raw.rollout_type}:{subtype}"
            existing = by_key.get(key) if key != mirror_key else None
            if existing is None and mirror_key:
                candidate = mirror_candidates.get(mirror_key)
                if candidate:
                    candidate_item, candidate_index, candidate_type = candidate
                    if raw.index - candidate_index == 1 and candidate_type != raw_type and candidate_item.role == role:
                        existing = candidate_item
            if existing is None:
                digest = hashlib.sha1(f"{key}|{raw.index}".encode()).hexdigest()[:12]
                existing = Interaction(
                    interaction_id=f"interaction:{raw.index}:{digest}", index=len(interactions),
                    turn_id=turn_id, turn_number=turn_numbers.get(turn_id, turn_number),
                    family=spec.family, kind=spec.kind, subkind=spec.subkind,
                    action=action, action_confidence=spec.confidence, action_source=spec.source,
                    lifecycle=spec.lifecycle, status=_status(payload, result, spec.lifecycle),
                    role=role, actor_id=actor_id, target_id=target_id,
                    label=label, detail=detail, call_id=call_id, item_id=item_id,
                    timestamp=timestamp, timestamp_ms=timestamp_ms,
                    started_at=str(started_value or timestamp), started_at_ms=started_ms,
                    completed_at=str(completed_value or ""), completed_at_ms=completed_ms,
                    result=result,
                    metadata={
                        **extracted_meta,
                        "correlation": {"basis": _correlation_basis(key), "key": key},
                        **({"script_tools": list(spec.script_tools)} if spec.script_tools else {}),
                        **({"protocol_wrapper": True} if action == "tool_script" else {}),
                        "tool_name": str(source.get("name") or payload.get("name") or ""),
                        "raw_types": [raw_type], "source_indices": [raw.index],
                    },
                    raw_record_ids=[raw.raw_id],
                )
                interactions.append(existing)
                if key != mirror_key:
                    by_key[key] = existing
                if call_id and spec.kind not in {NodeKind.APPROVAL, NodeKind.PERMISSION_REQUEST, NodeKind.USER_INPUT_REQUEST}:
                    by_key[f"call:{call_id}"] = existing
            else:
                _promote(existing, spec)
                existing.raw_record_ids.append(raw.raw_id)
                existing.metadata.setdefault("raw_types", []).append(raw_type)
                existing.metadata.setdefault("source_indices", []).append(raw.index)
                if detail and not spec.subkind.endswith("_output") and (not existing.detail or len(detail) > len(existing.detail)):
                    existing.detail = detail
                    existing.label = label
                if call_id:
                    existing.call_id = call_id
                if item_id:
                    existing.item_id = item_id
                if role and not existing.role:
                    existing.role = role
                if result:
                    existing.result.update(result)
                if started_ms is not None:
                    existing.started_at_ms = min(value for value in (existing.started_at_ms, started_ms) if value is not None)
                if completed_ms is not None:
                    existing.completed_at_ms = max(value for value in (existing.completed_at_ms, completed_ms) if value is not None)
                    existing.completed_at = str(completed_value or timestamp)
                merged_status = _status(payload, existing.result, existing.lifecycle)
                if merged_status != "unknown":
                    existing.status = merged_status
                existing.metadata.update({key: value for key, value in extracted_meta.items() if value})

            if existing.status == "error" and existing.lifecycle == LifecycleState.COMPLETED:
                existing.lifecycle = LifecycleState.FAILED

            if mirror_key and existing.metadata.get("correlation", {}).get("basis") == "single_record":
                existing.metadata["correlation"] = {"basis": "content_fingerprint", "key": mirror_key}
            if mirror_key:
                mirror_candidates[mirror_key] = (existing, raw.index, raw_type)

            duration = source.get("duration", payload.get("duration_ms"))
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                existing.duration_ms = float(duration) * (1000.0 if source.get("duration") is not None and duration < 10000 else 1.0)
            elif existing.started_at_ms is not None and existing.completed_at_ms is not None:
                existing.duration_ms = max(0.0, float(existing.completed_at_ms - existing.started_at_ms))
            if raw.rollout_type == "turn_context":
                existing.metadata["context"] = payload
            if spec.kind == NodeKind.WORLD_STATE:
                existing.metadata["full"] = bool(payload.get("full"))

            self.meta = meta
            self.latest_context = latest_context

    def snapshot(self) -> SessionDocument:
        # Presentation grouping is derived after a batch; normalization is incremental.
        meta, latest_context = self.meta, self.latest_context
        raw_records, interactions = self.raw_records, self.interactions
        source_name = self.source_name
        assign_conversation_turns(interactions, raw_records)

        session_id = str(meta.get("id") or meta.get("session_id") or Path(source_name).stem)
        generated_at = str(meta.get("timestamp") or (raw_records[-1].timestamp if raw_records else ""))
        model = latest_context.get("model") or meta.get("model") or "unknown"
        description = next((item.detail for item in interactions if item.kind == NodeKind.MESSAGE and item.role == "user"), "")
        unknown_types = sorted({
            f"{raw.rollout_type}:{raw.response_item_type or raw.event_type}"
            for raw in raw_records
            if raw.rollout_type not in KNOWN_ROLLOUT_TYPES
            or (raw.rollout_type == "response_item" and raw.response_item_type not in KNOWN_RESPONSE_TYPES)
            or (raw.rollout_type == "event_msg" and raw.event_type not in KNOWN_EVENT_TYPES)
        })
        return SessionDocument(
            session_id=session_id,
            label=f"Codex session {generated_at or session_id}",
            source=str(meta.get("originator") or source_name),
            description=_compact(description, 300), generated_at=generated_at,
            agent={
                "type": "codex", "model": model, "version": meta.get("cli_version", ""),
                "provider": meta.get("model_provider", "openai"),
            },
            raw_records=raw_records, interactions=interactions,
            metadata={"cwd": meta.get("cwd", ""), "unknown_types": unknown_types},
        )

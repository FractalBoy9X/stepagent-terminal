from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any

from .domain import (
    ConversationTurnKind,
    EdgeKind,
    ExecutionGraph,
    GraphEdge,
    GraphNode,
    Interaction,
    InteractionFamily,
    LifecycleState,
    NodeKind,
    SessionDocument,
)

_PATCH_PATH_RE = re.compile(r"^\*\*\*\s+(Update|Add|Delete) File:\s+(.+?)\s*$", re.MULTILINE)
_QUOTED_PATH_RE = re.compile(r"[\"']((?:\.{0,2}/|/)[^\"'\n\r]+?)[\"']")
_BARE_PATH_RE = re.compile(r"(?<![\w.<-])((?:\.{0,2}/|/)[\w@%+.,~:#=\-\/]+(?:\.[A-Za-z0-9]{1,12})?)(?![\w.-])")
_URL_RE = re.compile(r"https?://[^\s\]\[<>\"']+")
_MARKUP_CLOSERS = {
    "/a", "/b", "/div", "/g", "/li", "/ol", "/p", "/path", "/span",
    "/svg", "/table", "/td", "/th", "/tr", "/ul",
}

# An artifact label rides into the page payload and the graph API, so a match
# has to be a plausible path — not any run of characters that happens to hold a
# slash. Base64 in tool output (embedded images, certificates) matched
# _BARE_PATH_RE and produced single "paths" of 1.8 MB, i.e. a 30 MB page.
MAX_ARTIFACT_CHARS = 512
MAX_ARTIFACT_SEGMENT_CHARS = 128
_BASE64_SEGMENT_RE = re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$")
# Characters that a real path effectively never carries, but binary blobs
# (base85 image data, certificates) quoted inside tool output do.
_IMPLAUSIBLE_PATH_CHARS = set("`$!<>*?|\"^;")


def _is_plausible_artifact(value: str) -> bool:
    if not value or len(value) > MAX_ARTIFACT_CHARS:
        return False
    if _IMPLAUSIBLE_PATH_CHARS & set(value):
        return False
    return not any(
        len(segment) > MAX_ARTIFACT_SEGMENT_CHARS or _BASE64_SEGMENT_RE.match(segment)
        for segment in value.split("/")
    )


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode()).hexdigest()[:14]}"


def _normalise_artifact(value: str) -> str:
    """Canonical artifact string, or "" when the match is not a usable path/URL."""
    value = value.strip().strip(".,;:()[]{}")
    if value.startswith(("http://", "https://")):
        if "\\" in value or len(value) > MAX_ARTIFACT_CHARS:
            return ""
        # A query string legitimately carries token-shaped segments, so a URL is
        # only length-checked.
        return value
    value = value.replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    try:
        value = str(PurePosixPath(value))
    except Exception:
        pass
    return value if _is_plausible_artifact(value) else ""


def _operation_for(interaction: Interaction, text: str) -> EdgeKind:
    name = f"{interaction.subkind} {interaction.metadata.get('tool_name', '')} {text}".lower()
    if interaction.kind == NodeKind.WEB_ACTION:
        if "open" in name:
            return EdgeKind.OPENS
        if "find" in name:
            return EdgeKind.FINDS_IN
        return EdgeKind.SEARCHES
    if "delete" in name or "remove" in name or "rm " in name:
        return EdgeKind.DELETES
    if "copy" in name or "cp " in name:
        return EdgeKind.COPIES
    if "move" in name or "rename" in name or "mv " in name:
        return EdgeKind.MOVES
    if "*** add file" in name or "create" in name or "touch " in name:
        return EdgeKind.CREATES
    if interaction.kind == NodeKind.FILE_CHANGE or "patch" in name or "*** update file" in name:
        return EdgeKind.PATCHES
    if any(token in name for token in ("write", "tee ", "cat >", "echo >")):
        return EdgeKind.WRITES
    if any(token in name for token in ("execute", "python ", "bash ", "./")):
        return EdgeKind.EXECUTES
    if any(token in name for token in ("stat ", "status", "watch", "observe")):
        return EdgeKind.OBSERVES
    if any(token in name for token in ("read", "cat ", "sed -n", "head ", "tail ", "less ")):
        return EdgeKind.READS
    return EdgeKind.REFERENCES


def extract_artifacts(interaction: Interaction) -> list[tuple[str, EdgeKind, dict[str, Any]]]:
    if interaction.family not in {
        InteractionFamily.EXECUTION, InteractionFamily.FILESYSTEM, InteractionFamily.TOOLING,
        InteractionFamily.MCP, InteractionFamily.WEB, InteractionFamily.MEDIA,
    }:
        return []
    text = "\n".join((interaction.detail, json.dumps(interaction.result, ensure_ascii=False, default=str)))
    # json.dumps writes a line break as the two characters \ and n, which the path
    # regexes swallow — a whole multi-line command output then reads as one path.
    # Restoring the real characters lets a line break end a match, as it does in
    # `detail`.
    text = text.replace("\\n", "\n").replace("\\r", "\n").replace("\\t", "\t")
    default_operation = _operation_for(interaction, interaction.detail)
    found: list[tuple[str, EdgeKind, dict[str, Any]]] = []
    seen: set[str] = set()
    for action, match in _PATCH_PATH_RE.findall(text):
        artifact = _normalise_artifact(match)
        operation = {"Add": EdgeKind.CREATES, "Delete": EdgeKind.DELETES}.get(action, EdgeKind.PATCHES)
        if artifact and artifact not in seen:
            seen.add(artifact)
            found.append((artifact, operation, {"detected_by": "patch_header", "inferred": False}))
    for regex, detected_by in ((_QUOTED_PATH_RE, "quoted"), (_BARE_PATH_RE, "path"), (_URL_RE, "url")):
        for match in regex.findall(text):
            value = match if isinstance(match, str) else match[-1]
            artifact = _normalise_artifact(value)
            if not artifact or artifact in seen or artifact in {"/", "./", "../"} | _MARKUP_CLOSERS:
                continue
            seen.add(artifact)
            operation = default_operation
            if artifact.startswith(("http://", "https://")) and interaction.kind == NodeKind.WEB_ACTION:
                operation = default_operation
            found.append((artifact, operation, {"detected_by": detected_by, "inferred": detected_by != "url"}))
    arguments = interaction.metadata.get("arguments")
    if isinstance(arguments, dict):
        for key in ("path", "file", "filepath", "filename", "cwd", "url", "image_path"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                artifact = _normalise_artifact(value)
                if artifact and artifact not in seen:
                    seen.add(artifact)
                    found.append((artifact, default_operation, {"detected_by": f"argument:{key}", "inferred": False}))
    return found[:50]


def _interaction_node(item: Interaction) -> GraphNode:
    return GraphNode(
        node_id=item.interaction_id, kind=item.kind, family=item.family, subkind=item.subkind,
        lifecycle=item.lifecycle, label=item.label, detail=item.detail, timestamp=item.timestamp,
        timestamp_ms=item.timestamp_ms, started_at_ms=item.started_at_ms,
        completed_at_ms=item.completed_at_ms, duration_ms=item.duration_ms,
        turn_id=item.turn_id, turn_number=item.turn_number, interaction_index=item.index,
        conversation_turn_id=item.conversation_turn_id,
        conversation_turn_number=item.conversation_turn_number,
        conversation_turn_kind=item.conversation_turn_kind,
        role=item.role, call_id=item.call_id, item_id=item.item_id, actor_id=item.actor_id,
        target_id=item.target_id, status=item.status, result=item.result,
        metadata={
            **item.metadata,
            "raw_record_ids": item.raw_record_ids,
            # The semantic layer travels with the node the views render.
            "action": item.action,
            "action_confidence": item.action_confidence,
            "action_source": item.action_source,
        },
    )


def _retry_signature(item: Interaction) -> str:
    if item.family not in {InteractionFamily.EXECUTION, InteractionFamily.TOOLING, InteractionFamily.MCP}:
        return ""
    detail = re.sub(r"\s+", " ", item.detail).strip().lower()
    return f"{item.kind.value}|{item.metadata.get('tool_name', '')}|{detail[:600]}"


def build_execution_graph(session: SessionDocument) -> ExecutionGraph:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    # Membership set instead of a scan over `edges`: an edge is added once per
    # (source, target, kind), and a linear scan per insert made graph building
    # quadratic (~10 s at the 5000-interaction cap).
    edge_signatures: set[tuple[str, str, EdgeKind]] = set()
    node_ids: set[str] = set()
    node_by_id: dict[str, GraphNode] = {}

    def add_node(node: GraphNode) -> None:
        if node.node_id not in node_ids:
            node_ids.add(node.node_id)
            node_by_id[node.node_id] = node
            nodes.append(node)

    def add_edge(source: str, target: str, kind: EdgeKind, *, confidence: float = 1.0, inferred: bool = False, detected_by: str = "explicit", **metadata: Any) -> None:
        if source == target or source not in node_ids or target not in node_ids:
            return
        signature = (source, target, kind)
        if signature in edge_signatures:
            return
        edge_signatures.add(signature)
        edges.append(GraphEdge(
            source=source, target=target, kind=kind, confidence=max(0.0, min(1.0, confidence)),
            metadata={"inferred": inferred, "detected_by": detected_by, **metadata},
        ))

    session_id = f"session:{session.session_id}"
    add_node(GraphNode(
        node_id=session_id, kind=NodeKind.SESSION, label=session.label,
        detail=session.description, timestamp=session.generated_at,
        metadata={"agent": session.agent, **session.metadata},
    ))
    primary_agent_id = f"agent:{session.session_id}:primary"
    add_node(GraphNode(
        node_id=primary_agent_id, kind=NodeKind.AGENT,
        label=f"Agent: {session.agent.get('model') or 'Codex'}",
        detail=json.dumps(session.agent, ensure_ascii=False), metadata={"primary": True, **session.agent},
    ))
    add_edge(session_id, primary_agent_id, EdgeKind.CONTAINS)

    interactions = sorted(session.interactions, key=lambda item: item.index)
    by_turn: dict[str, list[Interaction]] = defaultdict(list)
    for item in interactions:
        by_turn[item.conversation_turn_id].append(item)

    turn_node_ids: dict[str, str] = {}
    grouped_turns = sorted(by_turn.items(), key=lambda entry: entry[1][0].index)
    for conversation_turn_id, items in grouped_turns:
        first = items[0]
        node_id = f"turn:{conversation_turn_id}"
        turn_node_ids[conversation_turn_id] = node_id
        is_initialization = first.conversation_turn_kind == ConversationTurnKind.INITIALIZATION
        user_text = next(
            (
                item.detail for item in items
                if item.metadata.get("conversation_message_classification") == "conversation_start"
            ),
            "",
        )
        label = "Session initialization" if is_initialization else f"Turn {first.conversation_turn_number}"
        add_node(GraphNode(
            node_id=node_id, kind=NodeKind.TURN, label=label, detail=user_text,
            turn_id=first.turn_id, turn_number=first.turn_number,
            conversation_turn_id=conversation_turn_id,
            conversation_turn_number=first.conversation_turn_number,
            conversation_turn_kind=first.conversation_turn_kind,
            started_at_ms=min((item.started_at_ms for item in items if item.started_at_ms is not None), default=None),
            completed_at_ms=max((item.completed_at_ms or item.started_at_ms for item in items if item.completed_at_ms is not None or item.started_at_ms is not None), default=None),
            metadata={
                "interaction_count": len(items),
                "source_turn_ids": list(dict.fromkeys(item.turn_id for item in items)),
                "source_turn_numbers": list(dict.fromkeys(item.turn_number for item in items)),
            },
        ))
        add_edge(session_id, node_id, EdgeKind.CONTAINS)

    for item in interactions:
        add_node(_interaction_node(item))
        add_edge(turn_node_ids[item.conversation_turn_id], item.interaction_id, EdgeKind.CONTAINS)
        if item.role != "user":
            add_edge(primary_agent_id, item.interaction_id, EdgeKind.PRODUCED)
        if item.kind == NodeKind.PLAN and isinstance(item.metadata.get("steps"), list):
            for step_index, step in enumerate(item.metadata["steps"]):
                if not isinstance(step, dict):
                    continue
                step_id = _stable_id("plan_step", f"{item.interaction_id}|{step_index}|{step}")
                add_node(GraphNode(
                    node_id=step_id, kind=NodeKind.PLAN_STEP, family=InteractionFamily.PLANNING,
                    label=str(step.get("step") or step.get("text") or f"Step {step_index + 1}"),
                    detail=json.dumps(step, ensure_ascii=False), subkind="step",
                    lifecycle=LifecycleState(str(step.get("status") or "pending").replace("inProgress", "started"))
                    if str(step.get("status") or "pending").replace("inProgress", "started") in {state.value for state in LifecycleState}
                    else LifecycleState.UNKNOWN,
                    status=str(step.get("status") or "pending"), turn_id=item.turn_id, turn_number=item.turn_number,
                    conversation_turn_id=item.conversation_turn_id,
                    conversation_turn_number=item.conversation_turn_number,
                    conversation_turn_kind=item.conversation_turn_kind,
                    metadata={"plan_id": item.interaction_id, "step_index": step_index},
                ))
                add_edge(item.interaction_id, step_id, EdgeKind.CONTAINS, detected_by="plan_steps")

    previous: Interaction | None = None
    latest_backbone: Interaction | None = None
    latest_completed_action: Interaction | None = None
    by_call_id = {
        item.call_id: item for item in interactions
        if item.call_id and item.family in {
            InteractionFamily.EXECUTION, InteractionFamily.TOOLING, InteractionFamily.MCP,
            InteractionFamily.WEB, InteractionFamily.MULTI_AGENT,
        }
    }
    artifacts: dict[str, str] = {}
    artifact_touches: Counter[str] = Counter()

    for item in interactions:
        if previous is not None:
            add_edge(previous.interaction_id, item.interaction_id, EdgeKind.NEXT, detected_by="ordinal")
        is_backbone = item.family in {
            InteractionFamily.COMMUNICATION, InteractionFamily.COGNITION, InteractionFamily.PLANNING,
        }
        if is_backbone:
            if latest_completed_action is not None and latest_completed_action.index < item.index:
                add_edge(latest_completed_action.interaction_id, item.interaction_id, EdgeKind.TRIGGERED, confidence=0.78, inferred=True, detected_by="next_backbone")
            latest_backbone = item
        elif item.kind not in {NodeKind.USAGE, NodeKind.LIFECYCLE, NodeKind.ENVIRONMENT}:
            if latest_backbone is not None:
                add_edge(latest_backbone.interaction_id, item.interaction_id, EdgeKind.INVOKES, confidence=0.82, inferred=True, detected_by="preceding_backbone")
            elif previous is not None:
                add_edge(previous.interaction_id, item.interaction_id, EdgeKind.TRIGGERED, confidence=0.55, inferred=True, detected_by="adjacency")
            if item.lifecycle in {LifecycleState.COMPLETED, LifecycleState.FAILED, LifecycleState.CANCELLED, LifecycleState.ABORTED}:
                latest_completed_action = item

        if item.kind in {NodeKind.APPROVAL, NodeKind.PERMISSION_REQUEST, NodeKind.USER_INPUT_REQUEST}:
            parent = by_call_id.get(item.call_id) if item.call_id else None
            parent = parent or latest_completed_action or latest_backbone
            relation = {
                NodeKind.APPROVAL: EdgeKind.REQUESTS_APPROVAL,
                NodeKind.PERMISSION_REQUEST: EdgeKind.REQUESTS_PERMISSION,
                NodeKind.USER_INPUT_REQUEST: EdgeKind.REQUESTS_INPUT,
            }[item.kind]
            if parent is not None and parent.interaction_id != item.interaction_id:
                add_edge(parent.interaction_id, item.interaction_id, relation, confidence=1.0 if item.call_id else 0.7, inferred=not bool(item.call_id), detected_by="call_id" if item.call_id else "adjacency")
                add_edge(item.interaction_id, parent.interaction_id, EdgeKind.BLOCKS, detected_by="lifecycle")
            decision = str(item.result.get("response") or item.result.get("status") or item.status).lower()
            if parent is not None and "declin" in decision:
                add_edge(item.interaction_id, parent.interaction_id, EdgeKind.DECLINES, detected_by="decision")
            elif parent is not None and any(value in decision for value in ("accept", "approve", "grant", "success")):
                add_edge(item.interaction_id, parent.interaction_id, EdgeKind.APPROVES, detected_by="decision")

        if item.family == InteractionFamily.MULTI_AGENT:
            action = item.subkind.lower()
            arguments = item.metadata.get("arguments") if isinstance(item.metadata.get("arguments"), dict) else {}
            child_key = item.target_id or str(arguments.get("agent_id") or arguments.get("name") or item.call_id or item.interaction_id)
            child_id = _stable_id("agent", f"{session.session_id}|{child_key}")
            add_node(GraphNode(
                node_id=child_id, kind=NodeKind.AGENT, label=f"Subagent: {child_key}",
                detail=item.detail, turn_id=item.turn_id, turn_number=item.turn_number,
                conversation_turn_id=item.conversation_turn_id,
                conversation_turn_number=item.conversation_turn_number,
                conversation_turn_kind=item.conversation_turn_kind,
                metadata={"primary": False, "source_interaction": item.interaction_id},
            ))
            relation = EdgeKind.SENDS_TO
            if "spawn" in action:
                relation = EdgeKind.SPAWNS
            elif "wait" in action:
                relation = EdgeKind.WAITS_FOR
            elif "resume" in action:
                relation = EdgeKind.RESUMES
            elif "close" in action:
                relation = EdgeKind.CLOSES
            add_edge(primary_agent_id, child_id, relation, detected_by="collab_action")
            add_edge(item.interaction_id, child_id, EdgeKind.PRODUCED, detected_by="collab_action")

        for artifact, operation, metadata in extract_artifacts(item):
            artifact_id = artifacts.get(artifact)
            if artifact_id is None:
                artifact_id = _stable_id("artifact", artifact)
                artifacts[artifact] = artifact_id
                add_node(GraphNode(
                    node_id=artifact_id, kind=NodeKind.ARTIFACT, label=artifact, detail=artifact,
                    family=InteractionFamily.FILESYSTEM if not artifact.startswith("http") else InteractionFamily.WEB,
                    turn_id=item.turn_id, turn_number=item.turn_number,
                    conversation_turn_id=item.conversation_turn_id,
                    conversation_turn_number=item.conversation_turn_number,
                    conversation_turn_kind=item.conversation_turn_kind,
                    metadata={"artifact_type": "url" if artifact.startswith("http") else "file"},
                ))
            artifact_touches[artifact] += 1
            add_edge(item.interaction_id, artifact_id, operation, confidence=0.82 if metadata.get("inferred") else 1.0, **metadata)

        if item.status == "error" or item.lifecycle == LifecycleState.FAILED:
            result_text = json.dumps(item.result, ensure_ascii=False, default=str)
            error_id = _stable_id("error", f"{item.interaction_id}|{result_text[:600]}")
            add_node(GraphNode(
                node_id=error_id, kind=NodeKind.ERROR, family=InteractionFamily.LIFECYCLE_OBSERVABILITY,
                label=f"Failure: {item.label}", detail=result_text or item.detail,
                timestamp=item.completed_at or item.timestamp, timestamp_ms=item.completed_at_ms or item.timestamp_ms,
                turn_id=item.turn_id, turn_number=item.turn_number, interaction_index=item.index,
                conversation_turn_id=item.conversation_turn_id,
                conversation_turn_number=item.conversation_turn_number,
                conversation_turn_kind=item.conversation_turn_kind,
                status="error", lifecycle=LifecycleState.FAILED,
                metadata={
                    "origin": item.kind.value,
                    "code": item.result.get("code"), "domain": item.result.get("domain"),
                    "recoverable": item.result.get("recoverable"), "retryable": item.result.get("retryable"),
                    "http_status": item.result.get("http_status"),
                },
            ))
            add_edge(item.interaction_id, error_id, EdgeKind.FAILED_WITH, detected_by="status")
        previous = item

    retries = 0
    by_signature: dict[str, list[Interaction]] = defaultdict(list)
    for item in interactions:
        signature = _retry_signature(item)
        if signature:
            by_signature[signature].append(item)
    for candidates in by_signature.values():
        for earlier, later in zip(candidates, candidates[1:]):
            if earlier.status == "error" and later.index - earlier.index <= 30:
                add_edge(earlier.interaction_id, later.interaction_id, EdgeKind.RETRIES, confidence=0.84, inferred=True, detected_by="semantic_signature")
                retries += 1

    metrics = compute_metrics(session, nodes, edges, artifact_touches, retries)
    return ExecutionGraph(session=session, nodes=nodes, edges=edges, metrics=metrics)


def _find_nested_mapping(value: Any, key: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            return candidate
        for nested in value.values():
            found = _find_nested_mapping(nested, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_nested_mapping(nested, key)
            if found is not None:
                return found
    return None


def _token_summary(session: SessionDocument) -> dict[str, int]:
    usage_items = [item for item in session.interactions if item.kind == NodeKind.USAGE]
    if not usage_items:
        return {}
    raw_id = usage_items[-1].raw_record_ids[-1] if usage_items[-1].raw_record_ids else ""
    raw = next((record.record for record in session.raw_records if record.raw_id == raw_id), {})
    cumulative = _find_nested_mapping(raw, "total_token_usage") or _find_nested_mapping(raw, "thread_token_usage") or {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "cached_input_tokens": ("cached_input_tokens", "cached_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoning_tokens"),
        "total_tokens": ("total_tokens",),
    }
    result: dict[str, int] = {}
    for canonical, keys in aliases.items():
        for key in keys:
            value = cumulative.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[canonical] = int(value)
                break
    if "total_tokens" not in result and result:
        result["total_tokens"] = result.get("input_tokens", 0) + result.get("output_tokens", 0)
    return result


def compute_metrics(session: SessionDocument, nodes: list[GraphNode], edges: list[GraphEdge], artifact_touches: Counter[str], retry_count: int) -> dict[str, Any]:
    interactions = session.interactions
    node_counts = Counter(node.kind.value for node in nodes)
    edge_counts = Counter(edge.kind.value for edge in edges)
    families = Counter(item.family.value for item in interactions)
    statuses = Counter(item.status for item in interactions)
    lifecycle = Counter(item.lifecycle.value for item in interactions)
    timestamps = [value for item in interactions for value in (item.started_at_ms, item.completed_at_ms) if value is not None]
    duration_seconds = round((max(timestamps) - min(timestamps)) / 1000.0, 3) if len(timestamps) >= 2 else None
    executable = [item for item in interactions if item.family in {InteractionFamily.EXECUTION, InteractionFamily.TOOLING, InteractionFamily.MCP, InteractionFamily.WEB}]
    successful = sum(item.status == "success" for item in executable)
    blocking = [item for item in interactions if item.family == InteractionFamily.HUMAN_CONTROL]
    blocking_ms = sum(item.duration_ms or 0 for item in blocking)
    tokens = _token_summary(session)
    unknown = sorted({item.subkind for item in interactions if item.kind == NodeKind.EVENT_UNKNOWN})
    return {
        "interaction_count": len(interactions), "event_count": len(interactions),
        "raw_record_count": len(session.raw_records),
        "turn_count": len({
            item.conversation_turn_id for item in interactions
            if item.conversation_turn_kind == ConversationTurnKind.USER_MESSAGE
        }),
        "source_turn_count": len({item.turn_id for item in interactions}),
        "initialization_interaction_count": sum(
            item.conversation_turn_kind == ConversationTurnKind.INITIALIZATION for item in interactions
        ),
        "duration_seconds": duration_seconds, "tokens": tokens, "total_tokens": tokens.get("total_tokens"),
        "node_counts": dict(node_counts), "edge_counts": dict(edge_counts),
        "families": dict(families), "statuses": dict(statuses), "lifecycle": dict(lifecycle),
        "errors": sum(item.status == "error" for item in interactions), "retries": retry_count,
        "interaction_success_rate": round(successful / len(executable), 4) if executable else None,
        "interaction_success_percent": round(successful / len(executable) * 100, 1) if executable else None,
        "human_control_count": len(blocking), "human_control_density": round(len(blocking) / len(interactions), 4) if interactions else 0,
        "blocking_time_ms": round(blocking_ms, 3), "unknown_types": unknown,
        "hot_artifacts": [{"artifact": artifact, "touches": count} for artifact, count in artifact_touches.most_common(12)],
    }

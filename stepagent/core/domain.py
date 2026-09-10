from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

JSONDict = dict[str, Any]
SCHEMA_VERSION = 4


class InteractionFamily(str, Enum):
    COMMUNICATION = "communication"
    COGNITION = "cognition"
    PLANNING = "planning"
    EXECUTION = "execution"
    FILESYSTEM = "filesystem"
    TOOLING = "tooling"
    MCP = "mcp"
    WEB = "web"
    MEDIA = "media"
    MULTI_AGENT = "multi_agent"
    HUMAN_CONTROL = "human_control"
    SAFETY_SECURITY = "safety_security"
    CONTEXT_STATE = "context_state"
    LIFECYCLE_OBSERVABILITY = "lifecycle_observability"


class LifecycleState(str, Enum):
    PENDING = "pending"
    STARTED = "started"
    STREAMING = "streaming"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABORTED = "aborted"
    UNKNOWN = "unknown"


class ConversationTurnKind(str, Enum):
    INITIALIZATION = "initialization"
    USER_MESSAGE = "user_message"


class NodeKind(str, Enum):
    SESSION = "session"
    AGENT = "agent"
    TURN = "turn"
    MESSAGE = "message"
    REASONING = "reasoning"
    PLAN = "plan"
    PLAN_STEP = "plan_step"
    COMMAND = "command"
    TERMINAL_IO = "terminal_io"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_SEARCH = "tool_search"
    MCP_SERVER = "mcp_server"
    MCP_CALL = "mcp_call"
    WEB_ACTION = "web_action"
    FILE_CHANGE = "file_change"
    ARTIFACT = "artifact"
    IMAGE_GENERATION = "image_generation"
    IMAGE_VIEW = "image_view"
    MEDIA_STREAM = "media_stream"
    APPROVAL = "approval"
    PERMISSION_REQUEST = "permission_request"
    USER_INPUT_REQUEST = "user_input_request"
    REVIEW = "review"
    COMPACTION = "compaction"
    WORLD_STATE = "world_state"
    HOOK = "hook"
    SAFETY = "safety"
    MODEL_EVENT = "model_event"
    ENVIRONMENT = "environment"
    USAGE = "usage"
    ERROR = "error"
    WARNING = "warning"
    LIFECYCLE = "lifecycle"
    EVENT_UNKNOWN = "event_unknown"


class EdgeKind(str, Enum):
    CONTAINS = "contains"
    PRODUCED = "produced"
    NEXT = "next"
    TRIGGERED = "triggered"
    INVOKES = "invokes"
    RETURNS = "returns"
    FAILED_WITH = "failed_with"
    RETRIES = "retries"
    READS = "reads"
    CREATES = "creates"
    WRITES = "writes"
    PATCHES = "patches"
    DELETES = "deletes"
    COPIES = "copies"
    MOVES = "moves"
    REFERENCES = "references"
    EXECUTES = "executes"
    OBSERVES = "observes"
    SPAWNS = "spawns"
    SENDS_TO = "sends_to"
    WAITS_FOR = "waits_for"
    RESUMES = "resumes"
    CLOSES = "closes"
    REQUESTS_APPROVAL = "requests_approval"
    APPROVES = "approves"
    DECLINES = "declines"
    REQUESTS_INPUT = "requests_input"
    RESPONDS_TO = "responds_to"
    BLOCKS = "blocks"
    REQUESTS_PERMISSION = "requests_permission"
    GRANTS_PERMISSION = "grants_permission"
    SEARCHES = "searches"
    OPENS = "opens"
    FINDS_IN = "finds_in"
    CONNECTS_TO = "connects_to"
    GENERATES = "generates"
    COMPACTS = "compacts"
    UPDATES_STATE = "updates_state"
    ENTERS_MODE = "enters_mode"
    EXITS_MODE = "exits_mode"
    TRIGGERS_HOOK = "triggers_hook"
    BLOCKED_BY = "blocked_by"
    REROUTED_TO = "rerouted_to"
    DERIVED_FROM = "derived_from"
    IMPLEMENTS_STEP = "implements_step"


@dataclass(slots=True)
class RawRecord:
    raw_id: str
    index: int
    ordinal: int | None
    timestamp: str
    rollout_type: str
    response_item_type: str = ""
    event_type: str = ""
    record: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        return asdict(self)


@dataclass(slots=True)
class Interaction:
    interaction_id: str
    index: int
    turn_id: str = "turn-1"
    turn_number: int = 1
    conversation_turn_id: str = "initialization"
    conversation_turn_number: int = 0
    conversation_turn_kind: ConversationTurnKind = ConversationTurnKind.INITIALIZATION
    family: InteractionFamily = InteractionFamily.LIFECYCLE_OBSERVABILITY
    kind: NodeKind = NodeKind.EVENT_UNKNOWN
    # `subkind` is the source protocol name; `action` is what the agent did.
    # The three layers are: family (theme) -> action (semantics) -> subkind
    # (protocol). Additive within schema v4: older exports simply lack them.
    subkind: str = ""
    action: str = ""
    action_confidence: str = ""
    action_source: str = ""
    lifecycle: LifecycleState = LifecycleState.UNKNOWN
    status: str = "unknown"
    role: str = ""
    actor_id: str = ""
    target_id: str = ""
    label: str = "Interaction"
    detail: str = ""
    call_id: str = ""
    item_id: str = ""
    parent_id: str = ""
    timestamp: str = ""
    timestamp_ms: int | None = None
    started_at: str = ""
    started_at_ms: int | None = None
    completed_at: str = ""
    completed_at_ms: int | None = None
    duration_ms: float | None = None
    result: JSONDict = field(default_factory=dict)
    metadata: JSONDict = field(default_factory=dict)
    raw_record_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> JSONDict:
        value = asdict(self)
        value["family"] = self.family.value
        value["kind"] = self.kind.value
        value["lifecycle"] = self.lifecycle.value
        value["conversation_turn_kind"] = self.conversation_turn_kind.value
        return value


@dataclass(slots=True)
class GraphNode:
    node_id: str
    kind: NodeKind
    label: str
    family: InteractionFamily | None = None
    subkind: str = ""
    lifecycle: LifecycleState = LifecycleState.UNKNOWN
    detail: str = ""
    timestamp: str = ""
    timestamp_ms: int | None = None
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    duration_ms: float | None = None
    turn_id: str = ""
    turn_number: int = 0
    conversation_turn_id: str = "initialization"
    conversation_turn_number: int = 0
    conversation_turn_kind: ConversationTurnKind = ConversationTurnKind.INITIALIZATION
    interaction_index: int | None = None
    role: str = ""
    call_id: str = ""
    item_id: str = ""
    actor_id: str = ""
    target_id: str = ""
    status: str = "unknown"
    result: JSONDict = field(default_factory=dict)
    metadata: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        value = asdict(self)
        value["kind"] = self.kind.value
        value["family"] = self.family.value if self.family else None
        value["lifecycle"] = self.lifecycle.value
        value["conversation_turn_kind"] = self.conversation_turn_kind.value
        return value


@dataclass(slots=True)
class GraphEdge:
    source: str
    target: str
    kind: EdgeKind
    label: str = ""
    confidence: float = 1.0
    metadata: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value


@dataclass(slots=True)
class SessionDocument:
    session_id: str
    label: str
    source: str = ""
    description: str = ""
    generated_at: str = ""
    agent: JSONDict = field(default_factory=dict)
    raw_records: list[RawRecord] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    metadata: JSONDict = field(default_factory=dict)

    def meta_dict(self) -> JSONDict:
        return {
            "session_id": self.session_id,
            "label": self.label,
            "source": self.source,
            "description": self.description,
            "generated_at": self.generated_at,
            "agent": self.agent,
            **self.metadata,
        }

    def to_dict(self) -> JSONDict:
        return {
            "schema_version": SCHEMA_VERSION,
            "meta": self.meta_dict(),
            "raw_records": [record.to_dict() for record in self.raw_records],
            "interactions": [interaction.to_dict() for interaction in self.interactions],
        }


@dataclass(slots=True)
class ExecutionGraph:
    session: SessionDocument
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metrics: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        return {
            "schema_version": SCHEMA_VERSION,
            "meta": self.session.meta_dict(),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "metrics": self.metrics,
        }

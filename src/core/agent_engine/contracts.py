"""Execution facts; no HTTP objects, repositories, tools or live connections."""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from typing import Any, Optional


class AgentMode(str, Enum):
    MASTER = "master"
    SUBAGENT = "subagent"
    STANDALONE = "standalone"


class TerminalDirective(str, Enum):
    STOP_EXECUTION = "stop_execution"


class Outcome(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    WAITING = "waiting"
    FAILED = "failed"
    ITERATION_LIMIT = "iteration_limit"


@dataclass(frozen=True)
class Identity:
    tenant_id: Optional[str]
    user_id: Optional[str]
    session_id: str
    source: str = "chat"
    session_kind: str = "web"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "arguments", json.loads(json.dumps(self.arguments)))

    def message_call(self) -> dict:
        return {"id": self.id, "type": "function", "function": {
            "name": self.name, "arguments": json.dumps(self.arguments, ensure_ascii=False),
        }}


@dataclass
class ToolFact:
    call: ToolCall
    phase: str = "prepared"
    result: Any = None
    invocation_id: Optional[str] = None
    result_recorded: bool = False
    success: bool = True
    preserve_content: bool = False
    images: list[dict] = field(default_factory=list)
    final_output: Optional[str] = None
    terminal_directive: Optional[TerminalDirective] = None


@dataclass(frozen=True)
class DispatchResult:
    content: Any
    success: bool = True
    preserve_content: bool = False
    wait: Optional[dict] = None
    images: tuple[dict, ...] = ()
    final_output: Optional[str] = None
    terminal_directive: Optional[TerminalDirective] = None


class CheckpointFailure(RuntimeError):
    authoritative_storage_failure = True


@dataclass
class ExecutionState:
    identity: Identity
    execution_id: str
    role: AgentMode
    system_prompt: str
    messages: list[dict]
    max_iterations: int = 20
    iteration: int = 0
    initial_len: int = 0
    pending: list[ToolCall] = field(default_factory=list)
    tools: dict[str, ToolFact] = field(default_factory=dict)
    model_calls: list[dict] = field(default_factory=list)
    output: str = ""
    images: list[dict] = field(default_factory=list)
    outcome: Outcome = Outcome.RUNNING
    waiting: Optional[dict] = None
    error_code: Optional[str] = None
    profile_id: Optional[str] = None
    profile_version: Optional[str] = None
    loaded_skills: dict[str, str] = field(default_factory=dict)
    plan_ref: Optional[str] = None
    children: dict[str, dict] = field(default_factory=dict)
    usage_watermark: int = 0
    resources: dict = field(default_factory=dict)
    followup_messages: list[dict] = field(default_factory=list)
    terminal_directive: Optional[TerminalDirective] = None
    schema_version: int = 1

    def checkpoint(self) -> dict:
        """Copy facts, never expose live mutable state to a persistence adapter."""
        return json.loads(json.dumps(asdict(self), ensure_ascii=False))

    @classmethod
    def restore(cls, data: dict) -> "ExecutionState":
        if data.get("schema_version") != 1:
            raise ValueError("CHECKPOINT_VERSION_UNSUPPORTED")
        copied = json.loads(json.dumps(data))
        def descendant_checkpoints(node):
            for child in node.get('children', {}).values():
                checkpoint = child.get('checkpoint')
                if isinstance(checkpoint, dict):
                    yield checkpoint
                    yield from descendant_checkpoints(checkpoint)

        descendants = {node.get('execution_id'): node for node in descendant_checkpoints(copied)}
        owned_calls = []
        for fact in copied.get('model_calls', []):
            explicit, legacy = fact.get('execution_id'), fact.get('child_execution_id')
            if explicit and legacy and explicit != legacy:
                raise ValueError('CHECKPOINT_MODEL_OWNER_MISMATCH')
            owner = explicit or legacy or copied['execution_id']
            if owner == copied['execution_id']:
                fact['execution_id'] = owner
                owned_calls.append(fact)
            else:
                # Old checkpoints mixed child accounting projections into parent
                # steps. The child's own checkpoint is the only response authority.
                child = descendants.get(owner)
                if child is None or not any(item.get('call_id') == fact.get('call_id')
                                            for item in child.get('model_calls', [])):
                    raise ValueError('CHECKPOINT_MODEL_OWNER_VERIFICATION_REQUIRED')
        copied['model_calls'] = owned_calls
        copied['usage_watermark'] = min(copied.get('usage_watermark', 0), len(owned_calls))
        if copied.get("terminal_directive") is not None:
            copied["terminal_directive"] = TerminalDirective(copied["terminal_directive"])
        for fact in copied["tools"].values():
            if fact.get("terminal_directive") is not None:
                fact["terminal_directive"] = TerminalDirective(fact["terminal_directive"])
        copied["identity"] = Identity(**copied["identity"])
        copied["role"] = AgentMode(copied["role"])
        copied["outcome"] = Outcome(copied["outcome"])
        copied["pending"] = [ToolCall(**call) for call in copied["pending"]]
        copied["tools"] = {key: ToolFact(**{**fact, "call": ToolCall(**fact["call"])})
                           for key, fact in copied["tools"].items()}
        return cls(**copied)

# Generated from schemas/*.schema.json. Do not edit.
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

DESKTOP_AGENT_PROTOCOL_VERSION: Literal["1.0"] = "1.0"

class AgentTurnClarification(TypedDict):
    type: Literal["clarification"]
    question: str

class AgentTurnCorrelation(TypedDict):
    task_id: str
    session_ref: str
    execution_id: str
    attempt_id: str
    action_id: str
    invocation_id: str
    artifact_id: str
    evidence_stream_id: str
    release_id: str
    policy_decision_id: str

class AgentTurnFinal(TypedDict):
    type: Literal["final"]
    message: str
    artifacts: NotRequired[list[dict[str, object]]]

class AgentTurnLocalToolCall(TypedDict):
    type: Literal["local_tool_call"]
    tool_name: str
    target: Literal["local"]
    schema_version: str
    schema_digest: str
    arguments: dict[str, object]
    authorization_ticket: str

class AgentTurnNextRequest(TypedDict):
    supported_protocol_versions: list[str]
    idempotency_key: str
    correlation: AgentTurnCorrelation
    input: AgentTurnInput

class AgentTurnNextResponse(TypedDict):
    protocol_version: Literal["1.0"]
    correlation: AgentTurnCorrelation
    outcome: AgentTurnOutcome

class AgentTurnRemoteToolCall(TypedDict):
    type: Literal["remote_tool_call"]
    tool_name: str
    target: Literal["server"]
    schema_version: str
    schema_digest: str
    arguments: dict[str, object]
    authorization_ticket: str

class AgentTurnToolResult(TypedDict):
    type: Literal["tool_result"]
    invocation_id: str
    result: dict[str, object]

class AgentTurnUserMessage(TypedDict):
    type: Literal["user_message"]
    content: str

class RemoteToolCancelResponse(TypedDict):
    accepted: bool
    reason: str

class RemoteToolCatalogResponse(TypedDict):
    protocol_version: Literal["1.0"]
    tools: list[RemoteToolDescriptor]

class RemoteToolDescriptor(TypedDict):
    tool_name: str
    description: str
    target: Literal["server"]
    schema_version: str
    schema_digest: str
    input_schema: dict[str, object]

class RemoteToolEvent(TypedDict):
    seq: int
    type: str
    at: str
    success: NotRequired[bool]

class RemoteToolEventsResponse(TypedDict):
    events: list[RemoteToolEvent]

class RemoteToolInvokeRequest(TypedDict):
    supported_protocol_versions: list[str]
    idempotency_key: str
    correlation: AgentTurnCorrelation
    tool_name: str
    target: Literal["server"]
    schema_version: str
    schema_digest: str
    arguments: dict[str, object]
    policy_revision: str
    authorization_ticket: str

class RemoteToolInvokeResponse(TypedDict):
    protocol_version: Literal["1.0"]
    correlation: AgentTurnCorrelation
    status: Literal["completed"]
    result: dict[str, object]

AgentTurnInput = AgentTurnUserMessage | AgentTurnToolResult

AgentTurnOutcome = AgentTurnFinal | AgentTurnClarification | AgentTurnLocalToolCall | AgentTurnRemoteToolCall

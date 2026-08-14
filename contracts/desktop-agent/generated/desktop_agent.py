# Generated from schemas/*.schema.json. Do not edit.
from typing import Literal, TypedDict

DESKTOP_AGENT_PROTOCOL_VERSION: Literal["1.0"] = "1.0"

class AgentTurnCorrelation(TypedDict):
    request_id: str
    session_id: str
    turn_id: str

class AgentTurnPayloadReference(TypedDict):
    schema: Literal["agent-turn-payload/placeholder"]
    data: dict[str, object]

class AgentTurnEnvelope(TypedDict):
    protocol_version: Literal["1.0"]
    kind: Literal["agent_turn"]
    correlation: AgentTurnCorrelation
    payload: AgentTurnPayloadReference

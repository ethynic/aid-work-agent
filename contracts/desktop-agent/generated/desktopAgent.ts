// Generated from schemas/*.schema.json. Do not edit.
export const DESKTOP_AGENT_PROTOCOL_VERSION = "1.0" as const

export interface AgentTurnCorrelation {
  request_id: string
  session_id: string
  turn_id: string
}

export interface AgentTurnPayloadReference {
  schema: "agent-turn-payload/placeholder"
  data: Record<string, unknown>
}

export interface AgentTurnEnvelope {
  protocol_version: "1.0"
  kind: "agent_turn"
  correlation: AgentTurnCorrelation
  payload: AgentTurnPayloadReference
}

// Generated from schemas/*.schema.json. Do not edit.
export const DESKTOP_AGENT_PROTOCOL_VERSION = "1.0" as const

export interface AgentTurnClarification {
  type: "clarification"
  question: string
}

export interface AgentTurnCorrelation {
  task_id: string
  session_ref: string
  execution_id: string
  attempt_id: string
  action_id: string
  invocation_id: string
  artifact_id: string
  evidence_stream_id: string
  release_id: string
  policy_decision_id: string
}

export interface AgentTurnFinal {
  type: "final"
  message: string
  artifacts?: Array<Record<string, unknown>>
}

export type AgentTurnInput = AgentTurnUserMessage | AgentTurnToolResult

export interface AgentTurnLocalToolCall {
  type: "local_tool_call"
  tool_name: string
  target: "local"
  schema_version: string
  schema_digest: string
  arguments: Record<string, unknown>
  authorization_ticket: string
}

export interface AgentTurnNextRequest {
  supported_protocol_versions: Array<string>
  idempotency_key: string
  correlation: AgentTurnCorrelation
  input: AgentTurnInput
}

export interface AgentTurnNextResponse {
  protocol_version: "1.0"
  correlation: AgentTurnCorrelation
  outcome: AgentTurnOutcome
}

export type AgentTurnOutcome = AgentTurnFinal | AgentTurnClarification | AgentTurnLocalToolCall | AgentTurnRemoteToolCall

export interface AgentTurnRemoteToolCall {
  type: "remote_tool_call"
  tool_name: string
  target: "server"
  schema_version: string
  schema_digest: string
  arguments: Record<string, unknown>
  authorization_ticket: string
}

export interface AgentTurnToolResult {
  type: "tool_result"
  invocation_id: string
  result: Record<string, unknown>
}

export interface AgentTurnUserMessage {
  type: "user_message"
  content: string
}

export interface RemoteToolCancelResponse {
  accepted: boolean
  reason: string
}

export interface RemoteToolCatalogResponse {
  protocol_version: "1.0"
  tools: Array<RemoteToolDescriptor>
}

export interface RemoteToolDescriptor {
  tool_name: string
  description: string
  target: "server"
  schema_version: string
  schema_digest: string
  input_schema: Record<string, unknown>
}

export interface RemoteToolEvent {
  seq: number
  type: string
  at: string
  success?: boolean
}

export interface RemoteToolEventsResponse {
  events: Array<RemoteToolEvent>
}

export interface RemoteToolInvokeRequest {
  supported_protocol_versions: Array<string>
  idempotency_key: string
  correlation: AgentTurnCorrelation
  tool_name: string
  target: "server"
  schema_version: string
  schema_digest: string
  arguments: Record<string, unknown>
  policy_revision: string
  authorization_ticket: string
}

export interface RemoteToolInvokeResponse {
  protocol_version: "1.0"
  correlation: AgentTurnCorrelation
  status: "completed"
  result: Record<string, unknown>
}

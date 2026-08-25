export interface AgentTurnTransport {
  requestTurn(envelope: unknown, signal: AbortSignal): Promise<unknown>
}

export interface LocalToolHostPort {
  invoke(request: unknown, signal: AbortSignal): Promise<unknown>
}

/** Phase B 只冻结注入边界；回合循环与调度在 D2 实现。 */
export interface AgentCoordinatorDependencies {
  readonly turnTransport: AgentTurnTransport
  readonly localToolHost: LocalToolHostPort
}

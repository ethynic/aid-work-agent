export interface LocalToolInvocation {
  readonly invocationId: string
  readonly providerId: string
  readonly toolName: string
  readonly arguments: Readonly<Record<string, unknown>>
}

/** Phase B 的 Host 壳端口；共用 Host Core、Executor 与 Provider 生命周期在 E1～E3 实现。 */
export interface LocalToolHostCore {
  invoke(request: LocalToolInvocation, signal: AbortSignal): Promise<unknown>
  shutdown(): Promise<void>
}

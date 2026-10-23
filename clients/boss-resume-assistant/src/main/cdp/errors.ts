/** Connection location and a command acknowledgement are different recovery facts. */
export class CdpCommandTimeoutError extends Error {
  constructor(readonly method: string) {
    super(`CDP request timed out: ${method}`)
    this.name = 'CdpCommandTimeoutError'
  }
}

export class CdpTargetUnavailableError extends Error {
  constructor(message = 'BOUND_BOSS_TARGET_UNAVAILABLE') {
    super(message)
    this.name = 'CdpTargetUnavailableError'
  }
}

export class CdpConnectionError extends Error {
  constructor(message = 'CDP connection unavailable') { super(message); this.name = 'CdpConnectionError' }
}

/** Transport failures only. Unsupported methods and business/page errors are final. */
export function isTransientCdpError(error: unknown): boolean {
  if (error instanceof CdpCommandTimeoutError || error instanceof CdpTargetUnavailableError || error instanceof CdpConnectionError) return true
  const value = error as { message?: string; cause?: { code?: string } } | undefined
  return ['ECONNREFUSED', 'ECONNRESET', 'EPIPE', 'UND_ERR_CONNECT_TIMEOUT'].includes(value?.cause?.code ?? '')
    || /CDP request timed out|CDP WebSocket (?:disconnected|error)|CDP socket (?:closed|is not connected)|BOUND_BOSS_TARGET_UNAVAILABLE|CDP response session mismatch|CDP error -?\d+: (?:Session with given id not found|No target with given id|Target closed)/i.test(value?.message ?? '')
}

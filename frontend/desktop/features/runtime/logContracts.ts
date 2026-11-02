export type RuntimeLogHours = 1 | 24 | 168
export interface RuntimeLogEntry {
  at: string; source: 'runtime' | 'desktop'; level: 'info' | 'error'; event: string; summary: string
  invocationId?: string; provider?: string; tool?: string; durationMs?: number
}
export interface RuntimeLogSource {
  name: string; status: 'read' | 'missing' | 'unreadable' | 'linked'; bytesRead: number; totalBytes: number
  skippedLines: number; partialLines: number; truncated: boolean
}
export interface RuntimeLogSnapshot {
  version: 1; generatedAt: string; hours: RuntimeLogHours
  versions: { app: string | null; host: string | null; core: string | null }
  range: { from: string; to: string; observedFrom: string | null; observedTo: string | null }
  entries: RuntimeLogEntry[]; sources: RuntimeLogSource[]
  health: { fileWriteFailures: number; lastFileWriteErrorAt: string | null; degraded: boolean; source: string | null; error: string | null } | null
  truncated: boolean
}
export interface RuntimeLogsPort {
  read(input?: { hours?: RuntimeLogHours }): Promise<RuntimeLogSnapshot>
  export(input?: { hours?: RuntimeLogHours }): Promise<{ canceled: boolean; fileName?: string }>
}

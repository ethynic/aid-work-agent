/**
 * IPC 通道契约 - 主进程与渲染层共享。
 * 所有 IPC 通道名集中在此，避免拼写不一致。
 * 渲染层只能通过 preload 暴露的 window.bossResume 桥调用，不直接接触 ipcRenderer。
 *
 * v2（Phase 8）：新增 Chrome 启动 / 登录确认 / 任务控制 / 岗位配置 / 复核队列 / 审计 / 导出。
 */

/** preload 暴露给渲染层的桥版本号，必须与 main 中握手一致，否则 preload 抛错 */
export const BRIDGE_VERSION = 2

export const IPC = {
  DB_HEALTH: 'boss:db:health',
  APP_VERSION: 'boss:app:version',
  APP_RUNTIME: 'boss:app:runtime',
  // Chrome 生命周期（设计 §5）
  CHROME_LAUNCH: 'boss:chrome:launch',
  // 会话控制（设计 §11 状态机）
  SESSION_CONFIRM_LOGIN: 'boss:session:confirm-login',
  SESSION_START: 'boss:session:start',
  SESSION_PAUSE: 'boss:session:pause',
  SESSION_RESUME: 'boss:session:resume',
  SESSION_STOP: 'boss:session:stop',
  SESSION_STATUS: 'boss:session:status',
  SESSION_RESET: 'boss:session:reset',
  /** 主进程 → 渲染层事件推送（webContents.send，非 invoke） */
  SESSION_EVENT: 'boss:session:event',
  // 岗位配置
  JOB_LIST: 'boss:jobs:list',
  JOB_CREATE: 'boss:jobs:create',
  JOB_UPDATE: 'boss:jobs:update',
  JOB_DELETE: 'boss:jobs:delete',
  // 人工复核
  REVIEW_LIST: 'boss:review:list',
  REVIEW_OVERRIDE: 'boss:review:override',
  // 审计日志
  AUDIT_ACTIONS: 'boss:audit:actions',
  AUDIT_CDP: 'boss:audit:cdp',
  // 导出
  EXPORT_EVALUATIONS: 'boss:export:evaluations',
} as const

export type IpcChannel = (typeof IPC)[keyof typeof IPC]

/** DB 健康检查响应 */
export interface DbHealth {
  ok: boolean
  schemaVersion: number
  dbPath: string
  error?: string
}

/** 运行时信息 */
export interface AppRuntime {
  platform: NodeJS.Platform
  electronVersion: string
  chromeVersion: string
  nodeVersion: string
  bridgeVersion: number
}

/** Chrome 启动结果（设计 §5.1） */
export interface ChromeLaunchResult {
  ok: boolean
  pid?: number
  port?: number
  profileDir?: string
  error?: string
}

/** 状态机状态（与 ScreeningSession 对齐；shared 层独立声明避免渲染层依赖主进程模块） */
export type SessionState =
  | 'IDLE'
  | 'WAITING_MANUAL_LOGIN'
  | 'CONNECTING_CDP'
  | 'READING_LIST'
  | 'OPENING_DETAIL'
  | 'CAPTURING_DETAIL'
  | 'OCR_AND_NORMALIZE'
  | 'SCREENING'
  | 'WAITING_REVIEW'
  | 'EXECUTING_ACTION'
  | 'CLOSING_DETAIL'
  | 'CHECKPOINT'
  | 'PAUSED'
  | 'STOPPED'
  | 'COMPLETED'

export interface SessionStats {
  viewed: number
  qualified: number
  rejected: number
  uncertain: number
  actionsAttempted: number
}

export interface SessionStatusPayload {
  state: SessionState
  connected: boolean
  jobId: number | null
  sessionRowId: number | null
  pauseReason: string | null
  stats: SessionStats
}

/** 主进程推送的会话事件 */
export interface SessionEventPayload {
  type: 'state' | 'log' | 'candidate'
  state?: SessionState
  level?: 'info' | 'warn' | 'error'
  message?: string
  candidateName?: string
  conclusion?: 'QUALIFIED' | 'REJECTED' | 'UNCERTAIN'
  at: string
}

/** 岗位配置记录 */
export interface JobRecord {
  id: number
  name: string
  knowledgeVersion: string | null
  ruleVersion: string | null
  hardRules: string | null
  preferences: string | null
  greetingTemplate: string | null
  actionLimitSession: number | null
  actionLimitDay: number | null
  createdAt: string
}

export interface JobInput {
  name: string
  knowledgeVersion?: string | null
  ruleVersion?: string | null
  hardRules?: string | null
  preferences?: string | null
  greetingTemplate?: string | null
  actionLimitSession?: number | null
  actionLimitDay?: number | null
}

/** 复核队列条目 */
export interface ReviewItem {
  evaluationId: number
  candidateId: number | null
  fingerprint: string | null
  candidateName: string | null
  conclusion: 'QUALIFIED' | 'REJECTED' | 'UNCERTAIN'
  reason: string | null
  evidence: string | null
  model: string | null
  createdAt: string
  override: {
    originalConclusion: string
    overrideConclusion: string
    overrideReason: string
    createdAt: string
  } | null
}

export interface ReviewOverrideInput {
  evaluationId: number
  conclusion: 'QUALIFIED' | 'REJECTED' | 'UNCERTAIN'
  reason: string
}

/** 审计日志筛选与行（与 AuditStore 对齐） */
export interface ActionLogFilters {
  jobId?: number
  dateFrom?: string
  dateTo?: string
  candidate?: string
  action?: string
  status?: string
  limit?: number
}

export interface ActionLogRow {
  id: number
  candidateId: number | null
  fingerprint: string | null
  candidateName: string | null
  jobId: number | null
  action: string
  reason: string | null
  status: string
  error: string | null
  createdAt: string
  sentAt: string | null
  confirmedAt: string | null
}

export interface CdpAuditFilters {
  method?: string
  status?: string
  dateFrom?: string
  dateTo?: string
  limit?: number
}

export interface CdpAuditRow {
  id: number
  sessionIdHash: string
  method: string
  paramsSummary: string
  durationMs: number | null
  status: string
  at: string
}

/** 导出请求/结果 */
export interface ExportRequest {
  format: 'csv' | 'json'
  jobId?: number
  dateFrom?: string
  dateTo?: string
  conclusion?: string
}

export interface ExportResult {
  ok: boolean
  /** 用户取消对话框时 cancelled=true */
  cancelled?: boolean
  path?: string
  rowCount?: number
  error?: string
}

/** window.bossResume 桥的完整类型（渲染层用） */
export interface BossResumeBridge {
  readonly version: number
  readonly db: {
    readonly health: () => Promise<DbHealth>
  }
  readonly app: {
    readonly version: () => Promise<string>
    readonly runtime: () => Promise<AppRuntime>
  }
  readonly chrome: {
    readonly launch: () => Promise<ChromeLaunchResult>
  }
  readonly session: {
    readonly confirmLogin: () => Promise<SessionStatusPayload>
    readonly start: (jobId: number) => Promise<SessionStatusPayload>
    readonly pause: () => Promise<SessionStatusPayload>
    readonly resume: () => Promise<SessionStatusPayload>
    readonly stop: () => Promise<SessionStatusPayload>
    readonly reset: () => Promise<SessionStatusPayload>
    readonly status: () => Promise<SessionStatusPayload>
    /** 订阅会话事件，返回取消订阅函数 */
    readonly onEvent: (cb: (event: SessionEventPayload) => void) => () => void
  }
  readonly jobs: {
    readonly list: () => Promise<JobRecord[]>
    readonly create: (input: JobInput) => Promise<JobRecord>
    readonly update: (id: number, input: JobInput) => Promise<JobRecord>
    readonly remove: (id: number) => Promise<void>
  }
  readonly review: {
    readonly list: (opts?: { includeResolved?: boolean }) => Promise<ReviewItem[]>
    readonly override: (input: ReviewOverrideInput) => Promise<void>
  }
  readonly audit: {
    readonly actions: (filters?: ActionLogFilters) => Promise<ActionLogRow[]>
    readonly cdp: (filters?: CdpAuditFilters) => Promise<CdpAuditRow[]>
  }
  readonly exporter: {
    readonly evaluations: (req: ExportRequest) => Promise<ExportResult>
  }
}

declare global {
  interface Window {
    bossResume: BossResumeBridge
  }
}

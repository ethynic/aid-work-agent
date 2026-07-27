/**
 * IPC 通道契约 - 主进程与渲染层共享。
 * 所有 IPC 通道名集中在此，避免拼写不一致。
 * 渲染层只能通过 preload 暴露的 window.bossResume 桥调用，不直接接触 ipcRenderer。
 */

/** preload 暴露给渲染层的桥版本号，必须与 main 中握手一致，否则 preload 抛错 */
export const BRIDGE_VERSION = 1

export const IPC = {
  DB_HEALTH: 'boss:db:health',
  APP_VERSION: 'boss:app:version',
  APP_RUNTIME: 'boss:app:runtime',
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
}

declare global {
  interface Window {
    bossResume: BossResumeBridge
  }
}

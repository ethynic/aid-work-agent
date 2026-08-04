/**
 * 窄 IPC 注册。所有 handler 经 assertTrustedIpcSender 校验来源。
 * 渲染层只通过 preload 暴露的冻结桥调用，不直接接触 ipcRenderer。
 * 业务实现全部在 runtime / storage / export 模块，本文件只做校验与转发。
 */
import { app, ipcMain, dialog, BrowserWindow, IpcMainInvokeEvent } from 'electron'
import fs from 'node:fs'
import {
  IPC,
  BRIDGE_VERSION,
  type AppRuntime,
  type DbHealth,
  type ChromeLaunchResult,
  type SessionStatusPayload,
  type SessionEventPayload,
  type JobInput,
  type ReviewOverrideInput,
  type ActionLogFilters,
  type CdpAuditFilters,
  type ExportRequest,
  type ExportResult,
} from '../shared/ipc.js'
import { healthCheck, getSchemaVersion, getDbPath, getClient } from '../../db/client.js'
import {
  launchChrome,
  confirmLogin,
  startSession,
  pauseSession,
  resumeSession,
  stopSession,
  resetSession,
  sessionStatus,
  setEventSink,
} from './runtime.js'
import { JobStore } from './storage/jobStore.js'
import { ReviewStore } from './storage/reviewStore.js'
import { AuditStore } from './storage/auditStore.js'
import { queryExportRows, buildCsv, buildJson } from './export/exporter.js'

let mainWindowRef: BrowserWindow | null = null

export function setMainWindow(window: BrowserWindow | null): void {
  mainWindowRef = window
}

/** 校验 IPC 调用来自主框架且是主窗口，拒绝子框架/弹窗伪造 */
export function assertTrustedIpcSender(event: IpcMainInvokeEvent): void {
  const senderFrame = event.senderFrame
  if (!senderFrame) {
    throw new Error('IPC rejected: no sender frame')
  }
  if (senderFrame.parent !== null) {
    throw new Error('IPC rejected: sender is not main frame')
  }
  if (mainWindowRef && event.sender !== mainWindowRef.webContents) {
    throw new Error('IPC rejected: sender is not main window')
  }
}

export function registerIpcHandlers(): void {
  // 会话事件出口：主进程 → 渲染层推送（状态推进/日志/候选人结论）
  setEventSink((event: SessionEventPayload) => {
    if (mainWindowRef && !mainWindowRef.isDestroyed()) {
      mainWindowRef.webContents.send(IPC.SESSION_EVENT, event)
    }
  })

  ipcMain.handle(IPC.DB_HEALTH, async (event): Promise<DbHealth> => {
    assertTrustedIpcSender(event)
    try {
      await healthCheck()
      return {
        ok: true,
        schemaVersion: getSchemaVersion(),
        dbPath: getDbPath(),
      }
    } catch (e) {
      return {
        ok: false,
        schemaVersion: 0,
        dbPath: getDbPath(),
        error: e instanceof Error ? e.message : String(e),
      }
    }
  })

  ipcMain.handle(IPC.APP_VERSION, async (event): Promise<string> => {
    assertTrustedIpcSender(event)
    return app.getVersion()
  })

  ipcMain.handle(IPC.APP_RUNTIME, async (event): Promise<AppRuntime> => {
    assertTrustedIpcSender(event)
    return {
      platform: process.platform,
      electronVersion: process.versions.electron ?? '',
      chromeVersion: process.versions.chrome ?? '',
      nodeVersion: process.versions.node ?? '',
      bridgeVersion: BRIDGE_VERSION,
    }
  })

  // ===== Chrome 生命周期 =====

  ipcMain.handle(IPC.CHROME_LAUNCH, async (event): Promise<ChromeLaunchResult> => {
    assertTrustedIpcSender(event)
    try {
      return await launchChrome()
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) }
    }
  })

  // ===== 会话控制 =====

  ipcMain.handle(IPC.SESSION_CONFIRM_LOGIN, async (event): Promise<SessionStatusPayload> => {
    assertTrustedIpcSender(event)
    return confirmLogin()
  })

  ipcMain.handle(IPC.SESSION_START, async (event, jobId: number): Promise<SessionStatusPayload> => {
    assertTrustedIpcSender(event)
    if (!Number.isInteger(jobId) || jobId <= 0) throw new Error(`非法岗位 id: ${jobId}`)
    return startSession(jobId)
  })

  ipcMain.handle(IPC.SESSION_PAUSE, async (event): Promise<SessionStatusPayload> => {
    assertTrustedIpcSender(event)
    return pauseSession()
  })

  ipcMain.handle(IPC.SESSION_RESUME, async (event): Promise<SessionStatusPayload> => {
    assertTrustedIpcSender(event)
    return resumeSession()
  })

  ipcMain.handle(IPC.SESSION_STOP, async (event): Promise<SessionStatusPayload> => {
    assertTrustedIpcSender(event)
    return stopSession()
  })

  ipcMain.handle(IPC.SESSION_RESET, async (event): Promise<SessionStatusPayload> => {
    assertTrustedIpcSender(event)
    return resetSession()
  })

  ipcMain.handle(IPC.SESSION_STATUS, async (event): Promise<SessionStatusPayload> => {
    assertTrustedIpcSender(event)
    return sessionStatus()
  })

  // ===== 岗位配置 =====

  ipcMain.handle(IPC.JOB_LIST, async (event) => {
    assertTrustedIpcSender(event)
    return new JobStore(getClient()).list()
  })

  ipcMain.handle(IPC.JOB_CREATE, async (event, input: JobInput) => {
    assertTrustedIpcSender(event)
    return new JobStore(getClient()).create(input)
  })

  ipcMain.handle(IPC.JOB_UPDATE, async (event, id: number, input: JobInput) => {
    assertTrustedIpcSender(event)
    if (!Number.isInteger(id) || id <= 0) throw new Error(`非法岗位 id: ${id}`)
    return new JobStore(getClient()).update(id, input)
  })

  ipcMain.handle(IPC.JOB_DELETE, async (event, id: number) => {
    assertTrustedIpcSender(event)
    if (!Number.isInteger(id) || id <= 0) throw new Error(`非法岗位 id: ${id}`)
    new JobStore(getClient()).remove(id)
  })

  // ===== 人工复核 =====

  ipcMain.handle(IPC.REVIEW_LIST, async (event, opts?: { includeResolved?: boolean }) => {
    assertTrustedIpcSender(event)
    return new ReviewStore(getClient()).list({ includeResolved: opts?.includeResolved === true })
  })

  ipcMain.handle(IPC.REVIEW_OVERRIDE, async (event, input: ReviewOverrideInput) => {
    assertTrustedIpcSender(event)
    if (!Number.isInteger(input?.evaluationId) || input.evaluationId <= 0) {
      throw new Error('非法评估 id')
    }
    new ReviewStore(getClient()).override(input)
  })

  // ===== 审计日志 =====

  ipcMain.handle(IPC.AUDIT_ACTIONS, async (event, filters?: ActionLogFilters) => {
    assertTrustedIpcSender(event)
    return new AuditStore(getClient()).queryActions(filters ?? {})
  })

  ipcMain.handle(IPC.AUDIT_CDP, async (event, filters?: CdpAuditFilters) => {
    assertTrustedIpcSender(event)
    return new AuditStore(getClient()).queryCdpAudit(filters ?? {})
  })

  // ===== 导出（用户主动触发，dialog 选路径） =====

  ipcMain.handle(IPC.EXPORT_EVALUATIONS, async (event, req: ExportRequest): Promise<ExportResult> => {
    assertTrustedIpcSender(event)
    try {
      if (req?.format !== 'csv' && req?.format !== 'json') {
        throw new Error(`非法导出格式: ${req?.format}`)
      }
      const rows = queryExportRows(getClient(), {
        ...(req.jobId !== undefined ? { jobId: req.jobId } : {}),
        ...(req.dateFrom ? { dateFrom: req.dateFrom } : {}),
        ...(req.dateTo ? { dateTo: req.dateTo } : {}),
        ...(req.conclusion ? { conclusion: req.conclusion } : {}),
      })
      if (!mainWindowRef) throw new Error('主窗口不可用')
      const { canceled, filePath } = await dialog.showSaveDialog(mainWindowRef, {
        title: '导出评估结果',
        defaultPath: `evaluations_${new Date().toISOString().slice(0, 10)}.${req.format}`,
        filters:
          req.format === 'csv'
            ? [{ name: 'CSV', extensions: ['csv'] }]
            : [{ name: 'JSON', extensions: ['json'] }],
      })
      if (canceled || !filePath) {
        return { ok: false, cancelled: true }
      }
      const content = req.format === 'csv' ? buildCsv(rows) : buildJson(rows)
      fs.writeFileSync(filePath, content, 'utf8')
      return { ok: true, path: filePath, rowCount: rows.length }
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) }
    }
  })
}

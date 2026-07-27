/**
 * 窄 IPC 注册。所有 handler 经 assertTrustedIpcSender 校验来源。
 * 渲染层只通过 preload 暴露的冻结桥调用，不直接接触 ipcRenderer。
 */
import { app, ipcMain, BrowserWindow, IpcMainInvokeEvent } from 'electron'
import { IPC, type AppRuntime, type DbHealth, BRIDGE_VERSION } from '../shared/ipc.js'
import { healthCheck, getSchemaVersion, getDbPath } from '../../db/client.js'

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
}

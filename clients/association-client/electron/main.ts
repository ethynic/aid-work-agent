/**
 * Electron 主进程 —— 窗口创建 + IPC 注册 + CLI Runner 集成。
 *
 * 设计文档 §4.2 / §4.5。
 */

import { app, BrowserWindow, dialog, ipcMain, protocol, shell } from 'electron'
import path from 'node:path'
import { existsSync } from 'node:fs'
import { CliRunner, resolveCliPath, devCliArgs, type CliEvent } from './cliRunner.js'
import { loadConfig, saveConfig, clearConfig, type ClientConfig } from './config.js'
import { CLIENT_ORIGIN, CLIENT_SCHEME, createSecureWebPreferences } from './security.js'

const dirname = __dirname
const isDev = !app.isPackaged

// 注册自定义 scheme
protocol.registerSchemesAsPrivileged([
  {
    scheme: CLIENT_SCHEME,
    privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true },
  },
])

let mainWindow: BrowserWindow | null = null
let cliRunner: CliRunner | null = null

function getCliRunner(): CliRunner {
  if (!cliRunner) {
    if (isDev) {
      cliRunner = new CliRunner(resolveCliPath(true), devCliArgs())
    } else {
      cliRunner = new CliRunner(resolveCliPath(false))
    }
  }
  return cliRunner
}

function createWindow(): void {
  const preloadPath = path.resolve(dirname, 'preload.cjs')
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    title: '协会信息收集助手',
    webPreferences: createSecureWebPreferences(preloadPath),
    show: false,
  })

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show()
  })

  // 拦截外部导航
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(CLIENT_ORIGIN)) {
      event.preventDefault()
    }
  })

  // 拦截新窗口
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url)
    }
    return { action: 'deny' }
  })

  mainWindow.loadURL(`${CLIENT_ORIGIN}/`)
}

// ============== 自定义 scheme handler（加载本地 renderer） ==============

function registerSchemeHandler(): void {
  protocol.handle(CLIENT_SCHEME, (request) => {
    const rendererRoot = path.resolve(dirname, '../renderer')
    let urlPath = new URL(request.url).pathname
    if (urlPath === '/' || urlPath === '') urlPath = '/index.html'
    const filePath = path.join(rendererRoot, urlPath)
    // 防目录穿越
    const resolved = path.resolve(filePath)
    if (!resolved.startsWith(path.resolve(rendererRoot))) {
      return new Response('Forbidden', { status: 403 })
    }
    if (!existsSync(resolved)) {
      return new Response('Not Found', { status: 404 })
    }
    return netFetch(resolved)
  })
}

// Node.js fetch 用于读本地文件
import { readFile } from 'node:fs/promises'
async function netFetch(filePath: string): Promise<Response> {
  try {
    const data = await readFile(filePath)
    const ext = path.extname(filePath).toLowerCase()
    const mime =
      ext === '.html' ? 'text/html' :
      ext === '.js' ? 'text/javascript' :
      ext === '.css' ? 'text/css' :
      ext === '.json' ? 'application/json' :
      ext === '.png' ? 'image/png' :
      ext === '.svg' ? 'image/svg+xml' :
      'application/octet-stream'
    return new Response(data, { headers: { 'Content-Type': `${mime}; charset=utf-8` } })
  } catch {
    return new Response('Not Found', { status: 404 })
  }
}

// ============== IPC 注册 ==============

function registerIpc(): void {
  // config
  ipcMain.handle('client:config:load', () => {
    const cfg = loadConfig()
    return cfg
  })
  ipcMain.handle('client:config:save', (_event, cfg: ClientConfig) => {
    saveConfig(cfg)
  })
  ipcMain.handle('client:config:clear', () => {
    clearConfig()
  })

  // cli:activate
  ipcMain.handle('client:cli:activate', async (_event, code: string, serverUrl: string, clientName?: string) => {
    const runner = getCliRunner()
    const baseArgs = ['activate', '--code', code, '--server-url', serverUrl]
    if (clientName) baseArgs.push('--client-name', clientName)
    try {
      const result = await runner.activate(code, serverUrl, clientName)
      return { ok: true, data: result }
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) }
    }
  })

  // cli:getCredits
  ipcMain.handle('client:cli:getCredits', async (_event, serverUrl: string, accessToken: string) => {
    const runner = getCliRunner()
    try {
      const result = await runner.getCredits(serverUrl, accessToken)
      return { ok: true, data: result }
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) }
    }
  })

  // cli:collect
  ipcMain.handle('client:cli:collect', async (
    _event,
    associations: string[],
    outputPath: string,
    serverUrl: string,
    accessToken: string,
  ) => {
    const runner = getCliRunner()
    // 收集模式下需要把 accessToken 传给 CLI（通过环境变量，cliRunner 已处理）
    runner.collect(associations, outputPath, serverUrl, accessToken)
    return { ok: true }
  })

  // cli:kill
  ipcMain.handle('client:cli:kill', () => {
    getCliRunner().kill()
  })

  // cli 事件转发到 renderer
  function forwardCliEvent(evt: CliEvent): void {
    mainWindow?.webContents.send('client:cli:event', evt)
  }
  function forwardCliClose(code: number): void {
    mainWindow?.webContents.send('client:cli:close', code)
  }

  // 注册一个初始化钩子，让 cliRunner 的事件能转发
  ipcMain.handle('client:cli:subscribe', () => {
    const runner = getCliRunner()
    runner.on('event', forwardCliEvent)
    runner.on('close', forwardCliClose)
  })

  // system
  ipcMain.handle('client:system:openPath', (_event, filePath: string) => {
    shell.openPath(filePath)
  })
  ipcMain.handle('client:system:openExternal', (_event, url: string) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url)
    }
  })
  ipcMain.handle('client:system:selectOutputFile', async (_event, defaultName: string) => {
    const result = await dialog.showSaveDialog(mainWindow!, {
      title: '选择输出文件',
      defaultPath: defaultName || '协会收集结果.xlsx',
      filters: [{ name: 'Excel 文件', extensions: ['xlsx'] }],
    })
    return result.canceled ? null : result.filePath
  })
}

// ============== App 生命周期 ==============

app.whenReady().then(() => {
  registerSchemeHandler()
  registerIpc()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

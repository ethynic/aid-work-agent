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
      const args = devCliArgs()
      console.log('[main] dev mode CLI:', resolveCliPath(true), args)
      cliRunner = new CliRunner(resolveCliPath(true), args)
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
    show: true,
  })

  // 调试：加载失败时输出到控制台
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error(`[did-fail-load] code=${errorCode} desc=${errorDescription} url=${validatedURL}`)
  })
  mainWindow.webContents.on('did-finish-load', () => {
    console.log('[main] renderer page loaded successfully')
  })
  mainWindow.webContents.on('console-message', (_event, level, message) => {
    console.log(`[renderer:${level}] ${message}`)
  })
  // 调试模式打开 DevTools
  if (isDev) {
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  }

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

// ============== 诊断包导出 ==============

// 脱敏：键值对里的敏感值（access_token / token / secret / key / password）→ ***，仅作用于文本
const REDACT_RE = /(["']?(?:access_token|accessTokenEnc|secret|api[_-]?key|token|password)["']?\s*[:=]\s*["']?)[^"'\r\n,'}]+/gi

async function _readTextRedacted(fsProm: any, src: string, dest: string): Promise<void> {
  try {
    const raw = await fsProm.readFile(src, 'utf-8')
    await fsProm.writeFile(dest, raw.replace(REDACT_RE, '$1***'), 'utf-8')
  } catch {
    // 源文件不存在等，跳过
  }
}

async function _copyDirCapped(fsProm: any, src: string, dest: string, perFile: number, totalCap: number): Promise<void> {
  let entries: any[]
  try {
    entries = await fsProm.readdir(src, { withFileTypes: true })
  } catch {
    return
  }
  await fsProm.mkdir(dest, { recursive: true })
  let total = 0
  for (const ent of entries) {
    const s = path.join(src, ent.name)
    const d = path.join(dest, ent.name)
    if (ent.isDirectory()) {
      await _copyDirCapped(fsProm, s, d, perFile, totalCap)
    } else if (ent.isFile()) {
      try {
        const st = await fsProm.stat(s)
        if (st.size > perFile) continue
        if (total + st.size > totalCap) continue
        await fsProm.copyFile(s, d)
        total += st.size
      } catch {
        // 跳过单文件异常
      }
    }
  }
}

/** 收集本地日志/配置（脱敏）到临时目录，用 PowerShell Compress-Archive 打成 zip。零依赖。 */
async function exportDiagnosticsBundle(outPath: string, guiLogText: string): Promise<void> {
  const fsProm = await import('node:fs/promises')
  const { spawn } = await import('node:child_process')
  const tmpDir = path.join(app.getPath('temp'), `assoc-diag-${Date.now()}`)
  await fsProm.mkdir(tmpDir, { recursive: true })

  const appdata = process.env.APPDATA || ''
  const localAppdata = process.env.LOCALAPPDATA || appdata
  const tempEnv = process.env.TEMP || app.getPath('temp')

  // 配置类（必须脱敏 token）
  await _readTextRedacted(fsProm, path.join(appdata, 'association-client', 'cli-config.json'), path.join(tmpDir, 'cli-config.json'))
  await _readTextRedacted(fsProm, path.join(app.getPath('userData'), 'client-config.json'), path.join(tmpDir, 'client-config.json'))
  // 日志类（已由 CLI 脱敏手机号，原样拷贝）
  await _readTextRedacted(fsProm, path.join(localAppdata, 'AidWorkAgent', 'association-client', 'logs', 'app.log'), path.join(tmpDir, 'app.log'))
  await _readTextRedacted(fsProm, path.join(tempEnv, 'wechat_diag.log'), path.join(tmpDir, 'wechat_diag.log'))
  // GUI 内存日志
  await fsProm.writeFile(path.join(tmpDir, 'gui-log.txt'), guiLogText || '(空)', 'utf-8')
  // 系统信息
  await fsProm.writeFile(path.join(tmpDir, 'system_info.txt'), [
    `时间: ${new Date().toISOString()}`,
    `平台: ${process.platform} ${process.arch}`,
    `Electron: ${process.versions.electron}  Node: ${process.versions.node}`,
    `应用: ${app.getName()} ${app.getVersion()}`,
    `userData: ${app.getPath('userData')}`,
  ].join('\n'), 'utf-8')
  // 微信取证产物（DPAPI 加密，按大小裁剪：单文件 ≤2MB、总计 ≤10MB）
  await _copyDirCapped(fsProm, path.join(localAppdata, 'AidWorkAgent', 'wechat-souyisou-rpa', 'artifacts'), path.join(tmpDir, 'artifacts'), 2 * 1024 * 1024, 10 * 1024 * 1024)

  // 打 zip
  await new Promise<void>((resolve, reject) => {
    const cmd = `Compress-Archive -Path '${tmpDir}\\*' -DestinationPath '${outPath}' -Force`
    const ps = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', cmd])
    ps.on('error', (e) => reject(new Error('无法启动 PowerShell: ' + e.message)))
    ps.on('close', (code) => (code === 0 ? resolve() : reject(new Error('Compress-Archive 失败，退出码 ' + String(code)))))
  })

  // 清理临时目录（best-effort）
  try { await fsProm.rm(tmpDir, { recursive: true, force: true }) } catch { /* 忽略 */ }
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

  // cli:getCreditsDetail
  ipcMain.handle('client:cli:getCreditsDetail', async (_event, serverUrl: string, accessToken: string) => {
    const runner = getCliRunner()
    try {
      const result = await runner.runSyncCommand(['credits-detail', '--server-url', serverUrl], {
        ASSOCIATION_CLIENT_ACCESS_TOKEN: accessToken,
      })
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
    inputPath?: string,
  ) => {
    const runner = getCliRunner()
    console.log('[main] collect called, associations:', associations, 'inputPath:', inputPath)
    // 每次新任务前，先移除旧的事件监听器（避免重复转发）
    runner.removeAllListeners('event')
    runner.removeAllListeners('close')
    // 注册事件转发到 renderer
    runner.on('event', (evt: CliEvent) => {
      console.log('[main] forwarding event:', evt.event)
      mainWindow?.webContents.send('client:cli:event', evt)
    })
    runner.on('close', (code: number) => {
      console.log('[main] CLI closed:', code)
      mainWindow?.webContents.send('client:cli:close', code)
    })
    runner.collect(associations, outputPath, serverUrl, accessToken, inputPath)
    return { ok: true }
  })

  // cli:kill
  ipcMain.handle('client:cli:kill', () => {
    getCliRunner().kill()
  })

  // system
  ipcMain.handle('client:system:openPath', (_event, filePath: string) => {
    shell.openPath(filePath)
  })
  ipcMain.handle('client:system:openFolder', (_event, filePath: string) => {
    shell.showItemInFolder(filePath)
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
  ipcMain.handle('client:system:selectInputFile', async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: '选择输入文件',
      filters: [
        { name: 'Excel/CSV 文件', extensions: ['xlsx', 'csv'] },
      ],
      properties: ['openFile'],
    })
    return result.canceled ? null : result.filePaths[0]
  })
  ipcMain.handle('client:system:getDesktopPath', () => {
    return app.getPath('desktop')
  })

  // 导出诊断包：收集本地日志/配置（脱敏）→ Compress-Archive 打 zip
  ipcMain.handle('client:system:exportDiagnostics', async (_event, defaultName: string, guiLogText: string) => {
    const result = await dialog.showSaveDialog(mainWindow!, {
      title: '导出诊断包',
      defaultPath: defaultName || '协会客户端诊断包.zip',
      filters: [{ name: '诊断包', extensions: ['zip'] }],
    })
    if (result.canceled || !result.filePath) return null
    try {
      await exportDiagnosticsBundle(result.filePath, guiLogText || '')
      return result.filePath
    } catch (err) {
      console.error('[main] exportDiagnostics failed:', err)
      return { error: err instanceof Error ? err.message : String(err) }
    }
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

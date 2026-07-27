/**
 * Electron 主进程入口。
 * 1. 模块顶层注册 bossresume scheme（必须在 app ready 前）
 * 2. app ready → 初始化 DB → 创建窗口 → 装安全钩子 → 注册 IPC
 * 3. fail-loud：DB 初始化失败直接阻塞，不静默降级
 */
import { app, BrowserWindow, protocol, net, nativeTheme } from 'electron'
import path from 'node:path'
import fs from 'node:fs'
import {
  BOSS_SCHEME,
  BOSS_HOST,
  BOSS_ORIGIN,
  createSecureWebPreferences,
  resolveRendererFile,
  toFileUrl,
} from './security.js'
import { loadWindowBounds, trackWindowState } from './windowState.js'
import { registerIpcHandlers, setMainWindow } from './ipc.js'
import { initDatabase } from '../../db/client.js'
import { BRIDGE_VERSION } from '../shared/ipc.js'

// 模块顶层：注册 scheme 特权（必须在 app ready 之前）
protocol.registerSchemesAsPrivileged([
  {
    scheme: BOSS_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
])

// 计算路径（编译后 main.js 在 dist/src/main/，renderer 在 dist/renderer/）
const __dirname = path.dirname(new URL(import.meta.url).pathname.replace(/^\//, ''))
const distRoot = path.resolve(__dirname, '../..') // dist/
const rendererRoot = path.join(distRoot, 'renderer') // dist/renderer/
const preloadPath = path.join(__dirname, 'preload.cjs') // dist/src/main/preload.cjs

let mainWindow: BrowserWindow | null = null

function ensureBuildArtifacts(): void {
  if (!fs.existsSync(preloadPath)) {
    throw new Error(`preload missing: ${preloadPath}`)
  }
  const indexHtml = path.join(rendererRoot, 'index.html')
  if (!fs.existsSync(indexHtml)) {
    throw new Error(`renderer index.html missing: ${indexHtml}`)
  }
}

function registerRendererProtocol(): void {
  protocol.handle(BOSS_SCHEME, async (request) => {
    const url = new URL(request.url)
    if (url.host !== BOSS_HOST) {
      return new Response('forbidden host', { status: 403 })
    }
    const filePath = resolveRendererFile(rendererRoot, url.pathname)
    if (!filePath) {
      return new Response('forbidden path', { status: 403 })
    }
    if (!fs.existsSync(filePath)) {
      return new Response('not found', { status: 404 })
    }
    return net.fetch(toFileUrl(filePath))
  })
}

function installSecurityHandlers(window: BrowserWindow): void {
  // 禁止新窗口
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  // 主框架导航白名单：只允许 scheme origin 内的 history 路由
  window.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(BOSS_ORIGIN)) {
      e.preventDefault()
    }
  })
  // 拒绝所有权限请求（session 级别）
  window.webContents.session.setPermissionCheckHandler(() => false)
  window.webContents.session.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  // 禁 webview
  window.webContents.on('will-attach-webview', (e) => e.preventDefault())
}

async function createWindow(): Promise<void> {
  ensureBuildArtifacts()
  const bounds = loadWindowBounds()
  mainWindow = new BrowserWindow({
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    minWidth: 800,
    minHeight: 600,
    show: false,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#111827' : '#ffffff',
    title: 'BOSS 简历筛选助手',
    webPreferences: {
      ...createSecureWebPreferences(preloadPath),
      additionalArguments: [
        `--boss-bridge-version=${BRIDGE_VERSION}`,
      ],
    },
  })

  if (bounds.isMaximized) {
    mainWindow.maximize()
  }
  trackWindowState(mainWindow)
  installSecurityHandlers(mainWindow)
  setMainWindow(mainWindow)

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show()
  })

  await mainWindow.loadURL(`${BOSS_ORIGIN}/`)
}

// 单实例锁
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(async () => {
    // fail-loud：DB 初始化失败直接抛出，应用不进入主界面
    initDatabase()
    registerRendererProtocol()
    registerIpcHandlers()
    await createWindow()

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        void createWindow()
      }
    })
  })

  app.on('window-all-closed', () => {
    app.quit()
  })
}

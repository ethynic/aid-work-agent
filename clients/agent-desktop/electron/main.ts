import { existsSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { app, BrowserWindow, dialog, ipcMain, nativeTheme, net, protocol, safeStorage, screen, shell } from 'electron'
import type { IpcMainInvokeEvent } from 'electron'
import { EncryptedCredentialStore, isAllowedCredentialKey, isAllowedCredentialValue } from './credentials.js'
import { normalizeDownloadUrl, normalizeExternalUrl, readDownloadBody, safeSuggestedName } from './systemCapabilities.js'
import {
  createContentSecurityPolicy,
  createSecureWebPreferences,
  DESKTOP_ORIGIN,
  DESKTOP_SCHEME,
  isAllowedMainFrameNavigation,
  isTrustedIpcSender,
  mapSchemeRequest,
  parseAgentDeepLink,
  parseApiBaseConfiguration,
  resolveInsideRoot,
} from './security.js'
import {
  focusExistingWindow,
  getZoomCommand,
  loadWindowState,
  nextZoomFactor,
  saveWindowState,
} from './windowState.js'

protocol.registerSchemesAsPrivileged([
  {
    scheme: DESKTOP_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
])

const dirname = path.dirname(fileURLToPath(import.meta.url))
const rendererRoot = path.resolve(dirname, '../renderer')
const preloadPath = path.resolve(dirname, 'preload.cjs')
const smokeMode = process.env.AID_AGENT_DESKTOP_SMOKE === '1'
if (smokeMode && process.env.AID_AGENT_DESKTOP_SMOKE_USER_DATA) {
  app.setPath('userData', path.resolve(process.env.AID_AGENT_DESKTOP_SMOKE_USER_DATA))
}
const apiConfiguration = parseApiBaseConfiguration(process.env.AID_AGENT_API_BASE_URL ?? '')
const apiBaseUrl = apiConfiguration.kind === 'valid' ? apiConfiguration.apiBaseUrl : ''
const contentSecurityPolicy = apiConfiguration.kind === 'valid' ? createContentSecurityPolicy(apiBaseUrl) : ''
let mainWindow: BrowserWindow | null = null
let credentialStore: EncryptedCredentialStore | null = null
let pendingDeepLink: string | null = null

function navigateDeepLink(argumentsList: readonly string[]): void {
  const deepLink = parseAgentDeepLink(argumentsList)
  if (!deepLink) return
  if (!mainWindow) {
    pendingDeepLink = deepLink
    return
  }
  const targetWindow = mainWindow
  void targetWindow.loadURL(deepLink).then(() => focusExistingWindow(targetWindow)).catch(() => logLifecycle('deep-link-rejected'))
}

async function fetchDownload(url: string, headers: Record<string, string>): Promise<Response> {
  const apiOrigin = new URL(apiBaseUrl).origin
  let currentUrl = normalizeDownloadUrl(url, apiOrigin)
  for (let redirects = 0; redirects <= 5; redirects += 1) {
    const response = await net.fetch(currentUrl, { headers, redirect: 'manual' })
    if (![301, 302, 303, 307, 308].includes(response.status)) return response
    const location = response.headers.get('location')
    if (!location || redirects === 5) throw new Error('download redirect rejected')
    await response.body?.cancel().catch(() => undefined)
    currentUrl = normalizeDownloadUrl(new URL(location, currentUrl).toString(), apiOrigin)
  }
  throw new Error('download redirect rejected')
}

function logLifecycle(event: string, detail?: string): void {
  const suffix = detail ? `:${detail.replace(/[^a-zA-Z0-9_.-]/g, '_').slice(0, 80)}` : ''
  console.error(`AGENT_DESKTOP:${event}${suffix}`)
}

async function registerRendererProtocol(): Promise<void> {
  await protocol.handle(DESKTOP_SCHEME, async (request) => {
    const target = mapSchemeRequest(request.url)
    if (target.kind === 'reject') {
      return new Response('Not Found', { status: 404 })
    }
    const filePath = resolveInsideRoot(rendererRoot, target.relativePath)
    try {
      if (!statSync(filePath).isFile()) return new Response('Not Found', { status: 404 })
    } catch {
      return new Response('Not Found', { status: 404 })
    }
    const fileResponse = await net.fetch(pathToFileURL(filePath).toString())
    const headers = new Headers(fileResponse.headers)
    headers.set('Content-Security-Policy', contentSecurityPolicy)
    return new Response(fileResponse.body, {
      status: fileResponse.status,
      statusText: fileResponse.statusText,
      headers,
    })
  })
}

function registerDesktopIpc(): void {
  credentialStore = new EncryptedCredentialStore(path.join(app.getPath('userData'), 'credentials.json'), safeStorage)
  const assertTrustedSender = (event: IpcMainInvokeEvent) => {
    const frame = event.senderFrame
    if (!frame || !isTrustedIpcSender(frame.url, frame === event.sender.mainFrame, event.sender === mainWindow?.webContents)) {
      throw new Error('desktop IPC sender rejected')
    }
  }
  ipcMain.handle('desktop:credentials:hydrate', (event) => {
    assertTrustedSender(event)
    return credentialStore!.load()
  })
  ipcMain.handle('desktop:credentials:set', (event, key: unknown, value: unknown) => {
    assertTrustedSender(event)
    if (typeof key !== 'string' || !isAllowedCredentialKey(key) || !isAllowedCredentialValue(value)) throw new Error('credential request rejected')
    credentialStore!.set(key, value)
  })
  ipcMain.handle('desktop:credentials:delete', (event, key: unknown) => {
    assertTrustedSender(event)
    if (typeof key !== 'string' || !isAllowedCredentialKey(key)) throw new Error('credential request rejected')
    credentialStore!.delete(key)
  })
  ipcMain.handle('desktop:open-external', (event, value: unknown) => {
    assertTrustedSender(event)
    return shell.openExternal(normalizeExternalUrl(value))
  })
  ipcMain.handle('desktop:save-download', async (event, input: unknown) => {
    assertTrustedSender(event)
    if (!input || typeof input !== 'object') throw new Error('download request rejected')
    const request = input as { url?: unknown; suggestedName?: unknown; authorization?: unknown; tenantId?: unknown }
    const url = normalizeDownloadUrl(request.url, new URL(apiBaseUrl).origin)
    const headers: Record<string, string> = {}
    if (typeof request.authorization === 'string' && /^Bearer [^\r\n]{1,8192}$/.test(request.authorization)) {
      headers.Authorization = request.authorization
    } else if (request.authorization !== undefined) {
      throw new Error('download authorization rejected')
    }
    if (typeof request.tenantId === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(request.tenantId)) {
      headers['X-Tenant-Id'] = request.tenantId
    } else if (request.tenantId !== undefined) {
      throw new Error('download tenant rejected')
    }
    const selection = await dialog.showSaveDialog({ defaultPath: safeSuggestedName(request.suggestedName) })
    if (selection.canceled || !selection.filePath) return { saved: false }
    const response = await fetchDownload(url, headers)
    if (!response.ok) throw new Error(`download failed with status ${response.status}`)
    const bytes = await readDownloadBody(response)
    const temporaryPath = `${selection.filePath}.aidagent-${process.pid}-${randomUUID()}.tmp`
    try {
      writeFileSync(temporaryPath, bytes, { flag: 'wx', mode: 0o600 })
      renameSync(temporaryPath, selection.filePath)
      return { saved: true }
    } finally {
      rmSync(temporaryPath, { force: true })
    }
  })
}

function installSecurityHandlers(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    if (!isAllowedMainFrameNavigation(url)) event.preventDefault()
  })
  window.webContents.session.setPermissionCheckHandler(() => false)
  window.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  window.webContents.on('will-attach-webview', (event) => event.preventDefault())
}

function installZoomShortcuts(window: BrowserWindow): void {
  window.webContents.on('before-input-event', (event, input) => {
    const command = getZoomCommand(input)
    if (!command) return
    event.preventDefault()
    window.webContents.setZoomFactor(nextZoomFactor(window.webContents.getZoomFactor(), command))
  })
}

function installFailLoudHandlers(window: BrowserWindow): void {
  window.webContents.on('render-process-gone', (_event, details) => {
    logLifecycle('renderer-gone', `${details.reason}-${details.exitCode}`)
    if (smokeMode) app.exit(1)
  })
  window.webContents.on('did-fail-load', (_event, errorCode, errorDescription, _validatedUrl, isMainFrame) => {
    if (!isMainFrame) return
    logLifecycle('load-failed', `${errorCode}-${errorDescription}`)
    if (smokeMode) app.exit(1)
  })
}

async function createWindow(): Promise<BrowserWindow> {
  if (!existsSync(preloadPath)) throw new Error(`preload output missing: ${preloadPath}`)
  if (!existsSync(path.join(rendererRoot, 'index.html'))) throw new Error(`renderer output missing: ${rendererRoot}`)

  const displays = screen.getAllDisplays().map((display) => display.workArea)
  const statePath = path.join(app.getPath('userData'), 'window-state.json')
  const state = loadWindowState(statePath, displays)
  const window = new BrowserWindow({
    ...state.bounds,
    minWidth: 720,
    minHeight: 500,
    show: !smokeMode,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#111827' : '#ffffff',
    webPreferences: {
      ...createSecureWebPreferences(preloadPath),
      additionalArguments: [
        '--aidagent-bridge-version=1',
        `--aidagent-api-base-url=${apiBaseUrl}`,
        `--aidagent-smoke-mode=${smokeMode ? '1' : '0'}`,
      ],
    },
  })
  mainWindow = window
  if (state.maximized) window.maximize()

  installSecurityHandlers(window)
  installZoomShortcuts(window)
  installFailLoudHandlers(window)
  window.on('close', () => {
    try {
      saveWindowState(statePath, { bounds: window.getNormalBounds(), maximized: window.isMaximized() })
    } catch (error) {
      logLifecycle('window-state-save-failed', error instanceof Error ? error.name : 'unknown')
    }
  })
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null
  })

  if (smokeMode) {
    const timeout = setTimeout(() => {
      logLifecycle('smoke-timeout')
      app.exit(2)
    }, 25_000)
    window.on('page-title-updated', (_event, title) => {
      if (title === 'AGENT_DESKTOP_SMOKE_PASS') {
        console.log(title)
        clearTimeout(timeout)
        app.exit(0)
      } else if (title.startsWith('AGENT_DESKTOP_SMOKE_FAIL')) {
        logLifecycle('smoke-failed', title)
        clearTimeout(timeout)
        app.exit(1)
      }
    })
  }

  try {
    await window.loadURL(`${DESKTOP_ORIGIN}${smokeMode ? '/t/smoke/chat' : '/'}`)
    return window
  } catch (error) {
    if (mainWindow === window) mainWindow = null
    if (!window.isDestroyed()) window.destroy()
    throw error
  }
}

const hasSingleInstanceLock = app.requestSingleInstanceLock()
if (!hasSingleInstanceLock) {
  app.quit()
} else if (apiConfiguration.kind === 'reject') {
  logLifecycle('startup-configuration-failed', apiConfiguration.reason)
  app.exit(1)
} else {
  if (!smokeMode) app.setAsDefaultProtocolClient(DESKTOP_SCHEME)
  app.on('second-instance', (_event, argv) => {
    if (parseAgentDeepLink(argv)) navigateDeepLink(argv)
    else if (mainWindow) focusExistingWindow(mainWindow)
  })

  app.whenReady().then(async () => {
    nativeTheme.themeSource = 'system'
    registerDesktopIpc()
    await registerRendererProtocol()
    await createWindow()
    if (pendingDeepLink) {
      const deepLink = pendingDeepLink
      pendingDeepLink = null
      navigateDeepLink([deepLink])
    } else {
      navigateDeepLink(process.argv)
    }
  }).catch((error) => {
    logLifecycle('startup-failed', error instanceof Error ? error.name : 'unknown')
    app.exit(1)
  })

  app.on('activate', () => {
    if (!mainWindow) {
      createWindow().catch((error) => logLifecycle('activate-failed', error instanceof Error ? error.name : 'unknown'))
    }
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })
}

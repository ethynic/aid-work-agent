import { existsSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { app, BrowserWindow, dialog, ipcMain, Menu, nativeTheme, net, protocol, safeStorage, screen, shell, Tray } from 'electron'
import type { IpcMainInvokeEvent } from 'electron'
import electronUpdater from 'electron-updater'
import { EncryptedCredentialStore, isAllowedCredentialKey, isAllowedCredentialValue } from './credentials.js'
import { resolveApiConfiguration } from './apiConfiguration.js'
import { DesktopUpdater, type UpdateAdapter } from './desktopUpdater.js'
import { resolveUpdateConfiguration } from './updateConfiguration.js'
import { resolveProductConfiguration } from './productConfiguration.js'
import { RuntimeSupervisor } from './runtimeSupervisor.js'
import { RuntimeSelections } from './runtimeSelection.js'
import { RuntimeStartup, runtimeLoginLaunch, resumeRuntimeAtLogin } from './runtimeStartup.js'
import { RuntimeUpdateIntent, runtimeUpdateExit, RUNTIME_UPDATE_EXIT_ARGUMENT, shouldResumeRuntime } from './runtimeUpdate.js'
import { applicationMenuTemplate, resolvePlatformWindowOptions, shouldQuitWhenAllWindowsClosed } from './platform.js'
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
  resolveInsideRoot,
} from './security.js'
import {
  focusExistingWindow,
  getZoomCommand,
  loadWindowState,
  nextZoomFactor,
  saveWindowState,
} from './windowState.js'

const { autoUpdater } = electronUpdater

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
const runtimeResources = app.isPackaged ? process.resourcesPath : path.resolve(dirname, '../../build/runtime-resources')
const product = resolveProductConfiguration({ resourcesPath: runtimeResources, isPackaged: app.isPackaged, appDataPath: app.getPath('appData') })
const legacyUserDataPath = app.getPath('userData')
app.setName(product.productName)
app.setPath('userData', product.userDataPath ?? legacyUserDataPath)
if (process.platform === 'win32') app.setAppUserModelId(product.appId)
const smokeMode = process.env.AID_AGENT_DESKTOP_SMOKE === '1'
const loginLaunch = runtimeLoginLaunch({ kind: product.kind, packaged: app.isPackaged, platform: process.platform, smoke: smokeMode, argv: process.argv })
const maintenanceLaunch = runtimeUpdateExit({ kind: product.kind, packaged: app.isPackaged, platform: process.platform, smoke: smokeMode, argv: process.argv })
let updateRequested = false
let maintenanceRequested = false
let updateIntent: RuntimeUpdateIntent | null = null
if (product.kind === 'runtime' && product.runtimeHome && product.hostEntry && !smokeMode) {
  // Reuse Runtime's CurrentUser DPAPI adapter. This record must survive app exit
  // without depending on Electron's asynchronously persisted Local State key.
  const protectionModule = () => import(pathToFileURL(path.join(path.dirname(product.hostEntry!), 'dpapi.js')).href)
  updateIntent = new RuntimeUpdateIntent(app.getPath('userData'), product.appId, product.runtimeHome, {
    protect: async value => (await protectionModule()).dpapiProtect(value),
    unprotect: async value => (await protectionModule()).dpapiUnprotect(value),
  })
}
if (smokeMode && product.kind === 'desktop' && process.env.AID_AGENT_DESKTOP_SMOKE_USER_DATA) {
  app.setPath('userData', path.resolve(process.env.AID_AGENT_DESKTOP_SMOKE_USER_DATA))
}
let apiBaseUrl = ''
let contentSecurityPolicy = ''
let mainWindow: BrowserWindow | null = null
let credentialStore: EncryptedCredentialStore | null = null
let desktopUpdater: DesktopUpdater | null = null
let pendingDeepLink: string | null = null
let runtimeSupervisor: RuntimeSupervisor | null = null
let runtimeSelections: RuntimeSelections | null = null
let runtimeTray: Tray | null = null
let quitting = false
let exitAllowed = false
let exitPending = false

function revealWindow(window: BrowserWindow): void {
  window.show()
  focusExistingWindow(window)
}

function navigateDeepLink(argumentsList: readonly string[]): void {
  if (product.kind !== 'desktop') return
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
  ipcMain.handle('desktop:runtime:request', async (event, request: unknown) => {
    assertTrustedSender(event)
    if (!runtimeSupervisor) throw new Error('当前产品没有本机Runtime管理能力')
    const reply = await runtimeSupervisor.request(request)
    if (!maintenanceRequested && reply.code === 0 && ['start', 'stop'].includes((request as { method: string }).method)) {
      // A new explicit user intent supersedes a cancelled upgrade's resume intent.
      try { await updateIntent?.clear() } catch { logLifecycle('runtime-update-intent-clear-failed') }
    }
    return reply
  })
  ipcMain.handle('desktop:runtime:choose-package', async (event, input: unknown) => {
    assertTrustedSender(event)
    if (!runtimeSelections || !runtimeSupervisor?.instanceId || !input || typeof input !== 'object' || Array.isArray(input)) throw new Error('选包请求无效')
    const request = input as Record<string, unknown>
    if (Object.keys(request).sort().join(',') !== 'instance_id,request_key' || request.instance_id !== runtimeSupervisor.instanceId || typeof request.request_key !== 'string' || !request.request_key || request.request_key.length > 200) throw new Error('选包实例或请求键无效')
    const window = mainWindow!
    const instance = runtimeSupervisor.instanceId
    const selection = await dialog.showOpenDialog(window, { title: '选择第一方离线插件包', properties: ['openFile'], filters: [{ name: '第一方离线插件包', extensions: ['zip'] }] })
    if (selection.canceled || !selection.filePaths[0]) return null
    assertTrustedSender(event)
    if (runtimeSupervisor.instanceId !== instance || window.isDestroyed()) throw new Error('Runtime连接已变更，请重新选择')
    return runtimeSelections.choose(selection.filePaths[0], event.sender.id, instance, request.request_key)
  })
  ipcMain.handle('desktop:startup:get-state', (event) => {
    assertTrustedSender(event)
    return { secureStorageAvailable: safeStorage.isEncryptionAvailable(), online: net.isOnline() }
  })
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
  ipcMain.handle('desktop:update:get-state', (event) => {
    assertTrustedSender(event)
    return desktopUpdater!.getState()
  })
  ipcMain.handle('desktop:update:check', async (event) => {
    assertTrustedSender(event)
    await desktopUpdater!.check()
  })
  ipcMain.handle('desktop:update:download', async (event) => {
    assertTrustedSender(event)
    await desktopUpdater!.download()
  })
  ipcMain.handle('desktop:update:restart-and-install', async (event) => {
    assertTrustedSender(event)
    if (desktopUpdater!.getState().status !== 'downloaded') throw new Error('尚无已下载的更新包')
    await runtimeSupervisor?.stopAndExit()
    desktopUpdater!.restartAndInstall()
  })
}

function initializeDesktopUpdater(): void {
  if (product.kind === 'runtime' || product.profile === 'acceptance') {
    desktopUpdater = new DesktopUpdater(null, app.getVersion(), () => {}, 'Runtime更新源尚未验收')
    return
  }
  const configuration = resolveUpdateConfiguration({
    isPackaged: app.isPackaged,
    smokeMode,
    resourcesPath: process.resourcesPath,
  })
  const emit = (state: Readonly<import('./desktopUpdater.js').DesktopUpdateState>) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('desktop:update:state', state)
  }
  desktopUpdater = configuration.enabled
    ? new DesktopUpdater(autoUpdater as unknown as UpdateAdapter, app.getVersion(), emit)
    : new DesktopUpdater(null, app.getVersion(), emit, configuration.reason)
  if (configuration.enabled) {
    desktopUpdater.configure(configuration.updateBaseUrl)
    desktopUpdater.start()
  } else {
    logLifecycle('updater-disabled', configuration.reason)
  }
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
    show: !smokeMode && !loginLaunch,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#111827' : '#ffffff',
    ...resolvePlatformWindowOptions(process.platform),
    webPreferences: {
      ...createSecureWebPreferences(preloadPath),
      additionalArguments: [
        '--aidagent-bridge-version=3',
        `--aidagent-api-base-url=${apiBaseUrl}`,
        `--aidagent-smoke-mode=${smokeMode ? '1' : '0'}`,
        `--aidagent-product-kind=${product.kind}`,
        `--aidagent-product-profile=${product.profile}`,
        `--aidagent-runtime-enabled=${product.runtimeEnabled ? '1' : '0'}`,
      ],
    },
  })
  mainWindow = window
  const runtimeOwner = window.webContents.id
  if (state.maximized && !loginLaunch) window.maximize()

  installSecurityHandlers(window)
  installZoomShortcuts(window)
  installFailLoudHandlers(window)
  window.on('close', (event) => {
    if (product.kind === 'runtime' && !quitting && !smokeMode) { event.preventDefault(); window.hide(); return }
    if (runtimeSupervisor && !exitAllowed && !smokeMode) {
      event.preventDefault()
      if (!quitting) app.quit()
      return
    }
    try {
      saveWindowState(statePath, { bounds: window.getNormalBounds(), maximized: window.isMaximized() })
    } catch (error) {
      logLifecycle('window-state-save-failed', error instanceof Error ? error.name : 'unknown')
    }
  })
  window.on('closed', () => {
    void runtimeSelections?.invalidate(runtimeOwner)
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
} else {
  if (!smokeMode && product.kind === 'desktop') app.setAsDefaultProtocolClient(DESKTOP_SCHEME)
  app.on('second-instance', (_event, argv) => {
    if (runtimeUpdateExit({ kind: product.kind, packaged: app.isPackaged, platform: process.platform, smoke: smokeMode, argv })) {
      maintenanceRequested = true
      if (mainWindow) revealWindow(mainWindow)
      updateRequested = argv.includes(RUNTIME_UPDATE_EXIT_ARGUMENT)
      app.quit(); return
    }
    if (parseAgentDeepLink(argv)) navigateDeepLink(argv)
    else if (mainWindow) revealWindow(mainWindow)
  })

  app.whenReady().then(async () => {
    // Installer rendezvous with no existing instance must not start a new Host.
    if (maintenanceLaunch) { exitAllowed = true; app.quit(); return }
    let configuration
    try {
      configuration = product.kind === 'runtime' ? { apiBaseUrl: 'https://runtime.invalid', source: 'runtime-local' } : resolveApiConfiguration({
        environmentValue: process.env.AID_AGENT_API_BASE_URL,
        userDataPath: app.getPath('userData'),
        resourcesPath: process.resourcesPath,
      })
    } catch (error) {
      logLifecycle('startup-configuration-failed', error instanceof Error ? error.message : 'unknown')
      throw error
    }
    apiBaseUrl = configuration.apiBaseUrl
    contentSecurityPolicy = createContentSecurityPolicy(apiBaseUrl)
    logLifecycle('api-configuration-loaded', configuration.source)
    nativeTheme.themeSource = 'system'
    Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenuTemplate(process.platform)))
    initializeDesktopUpdater()
    if (product.runtimeEnabled && product.nodeExecutable && product.hostEntry && product.runtimeHome) {
      runtimeSelections = new RuntimeSelections(product.runtimeHome)
      runtimeSupervisor = new RuntimeSupervisor({
        nodeExecutable: product.nodeExecutable, hostEntry: product.hostEntry, home: product.runtimeHome,
        supervisor: product.kind === 'runtime' ? 'runtime_app' : 'desktop',
        takeSelectedPackage: input => {
          if (!mainWindow || mainWindow.isDestroyed() || !runtimeSelections) throw new Error('选包窗口已失效')
          return runtimeSelections.take(input, mainWindow.webContents.id)
        },
        onEvent: event => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('desktop:runtime:event', event) },
        onDisconnect: () => {
          void runtimeSelections?.invalidate()
          if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('desktop:runtime:disconnected')
        },
      })
    }
    registerDesktopIpc()
    await registerRendererProtocol()
    await createWindow()
    if (product.kind === 'runtime' && !smokeMode) {
      const startup = app.isPackaged && process.platform === 'win32'
        ? new RuntimeStartup(app, app.getPath('userData'), process.execPath, product.appId) : null
      try { startup?.initialize() } catch { logLifecycle('runtime-login-registration-failed') }
      runtimeTray = new Tray(await app.getFileIcon(process.execPath))
      runtimeTray.setToolTip(product.productName)
      const refreshTrayMenu = () => runtimeTray!.setContextMenu(Menu.buildFromTemplate([
        { label: '打开执行节点', click: () => { if (mainWindow) revealWindow(mainWindow) } },
        ...(startup ? [{ label: '登录 Windows 后自动启动', type: 'checkbox' as const, checked: startup.enabled(), click: (item: Electron.MenuItem) => {
          try { startup.setEnabled(item.checked) } catch { logLifecycle('runtime-login-registration-failed'); if (mainWindow) revealWindow(mainWindow) }
          refreshTrayMenu()
        } }] : []),
        { type: 'separator' }, { label: '退出', click: () => app.quit() },
      ]))
      refreshTrayMenu()
      runtimeTray.on('double-click', () => { if (mainWindow) revealWindow(mainWindow) })
      if (runtimeSupervisor) {
        void (async () => {
          const intent = await updateIntent?.read()
          if (maintenanceRequested) return
          const reply = await runtimeSupervisor!.request({ request_id: randomUUID(), method: 'getState', params: {} })
          if (reply.code !== 0) throw new Error('RuntimeStartupStateRejected')
          const recovered = runtimeSupervisor!.recoveredAfterInterruption
          if (!maintenanceRequested && shouldResumeRuntime({ intent, recovered, login: loginLaunch })) await resumeRuntimeAtLogin(value => {
            if (maintenanceRequested) throw new Error('RuntimeMaintenanceInProgress')
            return runtimeSupervisor!.request(value)
          })
          if (intent !== undefined && !maintenanceRequested) await updateIntent?.clear()
        })().catch(() => {
          logLifecycle('runtime-login-resume-failed')
          if (mainWindow) revealWindow(mainWindow)
        })
      }
    }
    if (pendingDeepLink) {
      const deepLink = pendingDeepLink
      pendingDeepLink = null
      navigateDeepLink([deepLink])
    } else {
      if (product.kind === 'desktop') navigateDeepLink(process.argv)
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
    if (product.kind === 'desktop' && shouldQuitWhenAllWindowsClosed(process.platform)) app.quit()
  })

  app.on('before-quit', (event) => {
    desktopUpdater?.stop()
    if (!runtimeSupervisor || exitAllowed) { runtimeTray?.destroy(); return }
    event.preventDefault(); quitting = true
    if (exitPending) return
    exitPending = true
    void (async () => {
      await runtimeSupervisor!.stopAndExit(maintenanceRequested ? 600_000 : undefined, maintenanceRequested || updateIntent ? async state => {
        if (updateRequested && updateIntent && ['running', 'starting', 'stopped', 'draining'].includes(String(state.state))) {
          if (await updateIntent.read() === undefined) await updateIntent.save(['running', 'starting'].includes(String(state.state)))
        } else if (!updateRequested) await updateIntent?.clear()
      } : undefined)
    })().then(async () => {
      await runtimeSelections?.invalidate()
      exitAllowed = true; app.quit()
    }).catch(async () => {
      quitting = false
      maintenanceRequested = false; updateRequested = false
      if (mainWindow) revealWindow(mainWindow)
      await dialog.showMessageBox({ type: 'warning', title: '执行仍需收尾', message: '本机任务仍在收尾，升级已暂停。程序和执行记录保留，请恢复网络后重新运行安装器；旧版本异常退出请正常重启 Windows。' })
    }).finally(() => { exitPending = false })
  })
}

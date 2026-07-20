import type { ProgressInfo, UpdateInfo } from 'electron-updater'

export type DesktopUpdateStatus = 'disabled' | 'idle' | 'checking' | 'available' | 'downloading' | 'downloaded' | 'up-to-date' | 'error'

export interface DesktopUpdateState {
  status: DesktopUpdateStatus
  currentVersion: string
  availableVersion?: string
  percent?: number
  message?: string
}

export interface UpdateAdapter {
  autoDownload: boolean
  autoInstallOnAppQuit: boolean
  setFeedURL(options: { provider: 'generic'; url: string }): void
  checkForUpdates(): Promise<unknown>
  downloadUpdate(): Promise<unknown>
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void
  on(event: 'checking-for-update', listener: () => void): this
  on(event: 'update-available' | 'update-not-available' | 'update-downloaded', listener: (info: UpdateInfo) => void): this
  on(event: 'download-progress', listener: (progress: ProgressInfo) => void): this
  on(event: 'error', listener: (error: Error) => void): this
}

export class DesktopUpdater {
  private state: DesktopUpdateState
  private action: Promise<void> | null = null
  private startupTimer: NodeJS.Timeout | null = null
  private interval: NodeJS.Timeout | null = null

  constructor(
    private readonly adapter: UpdateAdapter | null,
    currentVersion: string,
    private readonly emit: (state: Readonly<DesktopUpdateState>) => void,
    disabledReason?: string,
  ) {
    this.state = disabledReason
      ? { status: 'disabled', currentVersion, message: disabledReason }
      : { status: 'idle', currentVersion }
    if (adapter) this.bindEvents(adapter)
  }

  configure(updateBaseUrl: string): void {
    if (!this.adapter) return
    this.adapter.autoDownload = false
    this.adapter.autoInstallOnAppQuit = false
    this.adapter.setFeedURL({ provider: 'generic', url: updateBaseUrl })
  }

  start(startupDelayMs = 30_000, intervalMs = 6 * 60 * 60 * 1_000): void {
    if (!this.adapter || this.state.status === 'disabled') return
    this.startupTimer = setTimeout(() => void this.check(), startupDelayMs)
    this.startupTimer.unref?.()
    this.interval = setInterval(() => void this.check(), intervalMs)
    this.interval.unref?.()
  }

  stop(): void {
    if (this.startupTimer) clearTimeout(this.startupTimer)
    if (this.interval) clearInterval(this.interval)
    this.startupTimer = null
    this.interval = null
  }

  getState(): Readonly<DesktopUpdateState> {
    return { ...this.state }
  }

  check(): Promise<void> {
    if (this.state.status === 'downloading' || this.state.status === 'downloaded') return Promise.resolve()
    return this.runOnce(async () => {
      if (!this.adapter) return
      await this.adapter.checkForUpdates()
    })
  }

  download(): Promise<void> {
    if (!this.adapter || this.state.status !== 'available') return Promise.resolve()
    return this.runOnce(async () => { await this.adapter!.downloadUpdate() })
  }

  restartAndInstall(): void {
    if (!this.adapter || this.state.status !== 'downloaded') return
    this.adapter.quitAndInstall(false, true)
  }

  private runOnce(action: () => Promise<void>): Promise<void> {
    if (this.action) return this.action
    this.action = action().catch((error: unknown) => {
      this.update({ status: 'error', message: safeErrorMessage(error) })
    }).finally(() => { this.action = null })
    return this.action
  }

  private bindEvents(adapter: UpdateAdapter): void {
    adapter.on('checking-for-update', () => this.update({ status: 'checking', message: undefined }))
    adapter.on('update-available', (info) => this.update({ status: 'available', availableVersion: info.version, percent: undefined, message: undefined }))
    adapter.on('update-not-available', () => this.update({ status: 'up-to-date', availableVersion: undefined, percent: undefined, message: undefined }))
    adapter.on('download-progress', (progress) => this.update({ status: 'downloading', percent: Math.max(0, Math.min(100, progress.percent)), message: undefined }))
    adapter.on('update-downloaded', (info) => this.update({ status: 'downloaded', availableVersion: info.version, percent: 100, message: undefined }))
    adapter.on('error', (error) => this.update({ status: 'error', availableVersion: undefined, percent: undefined, message: safeErrorMessage(error) }))
  }

  private update(patch: Partial<DesktopUpdateState> & Pick<DesktopUpdateState, 'status'>): void {
    this.state = { ...this.state, ...patch }
    this.emit(this.getState())
  }
}

function safeErrorMessage(error: unknown): string {
  // electron-updater errors may include feed URLs, local paths or signed URL tokens.
  // Renderer only needs an actionable status; diagnostics remain in the main process.
  void error
  return '更新失败，请稍后重试'
}

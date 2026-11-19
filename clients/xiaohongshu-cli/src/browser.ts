import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { chromium, type BrowserContext, type Page } from 'playwright-core'
import { BrowserFailure } from './errors.js'
import { collectionLimit, listComments, listNotes, noteId, openConversation, profileTarget } from './browsing.js'
import { sendFirstMessage, validateFirstMessage, type FirstMessageInput } from './firstMessage.js'
import { FirstMessageStore } from './firstMessageStore.js'

export { BrowserFailure } from './errors.js'

export const HOME = 'https://www.xiaohongshu.com/explore'
export const PROFILE_KEY = /^[0-9a-f]{64}$/
const SIGNED_IN = 'li.user.side-bar-component a[href^="/user/profile/"]'
const LOGIN_BUTTON = '.side-bar-component.login-btn button'
const LOGIN_MODAL = '.login-modal'
const QR_IMAGE = '.login-modal img.qrcode-img'
const UI_TIMEOUT = 8000

export function chromeExecutable(): string | undefined {
  if (process.platform !== 'win32') return undefined
  const localAppData = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
  return [process.env.ProgramFiles || 'C:\\Program Files',
    process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', localAppData]
    .map(path => join(path, 'Google', 'Chrome', 'Application', 'chrome.exe'))
    .find(existsSync)
}

export type BrowserAction = 'login' | 'open'
export type BrowserStatus = 'qr_ready' | 'opened'
type LaunchProfile = (profileKey: string) => Promise<BrowserContext>

async function launchProfile(profileKey: string): Promise<BrowserContext> {
  const executablePath = chromeExecutable()
  if (!executablePath) throw new BrowserFailure('XHS_CHROME_UNAVAILABLE', '请在所选 Windows 设备安装 Google Chrome')
  const root = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
  try {
    return await chromium.launchPersistentContext(join(root, 'AidWork', 'Xiaohongshu', 'Profiles', profileKey), {
      executablePath, headless: false, viewport: null, timeout: 10000,
    })
  } catch (error) {
    if (error instanceof Error && /ProcessSingleton|profile.*(?:in use|already)|SingletonLock/i.test(error.message)) {
      throw new BrowserFailure('XHS_PROFILE_IN_USE', '请关闭占用此连接的 Chrome 窗口后重试')
    }
    throw new BrowserFailure('XHS_BROWSER_UNAVAILABLE', '无法启动连接器的 Chrome，请检查所选设备')
  }
}

export class XiaohongshuBrowser {
  private readonly contexts = new Map<string, BrowserContext>()
  private busy = false

  constructor(private readonly launch: LaunchProfile = launchProfile,
    private readonly firstMessages = new FirstMessageStore()) {}

  async run(action: BrowserAction, profileKey: string, signal?: AbortSignal): Promise<BrowserStatus> {
    return this.execute(profileKey, page => this.openPage(page, action, signal), signal)
  }

  async listNotes(profileKey: string, profileUrl: string, limit?: number, signal?: AbortSignal) {
    const target = profileTarget(profileUrl)
    const maximum = collectionLimit(limit)
    return this.execute(profileKey, page => listNotes(page, target, maximum, signal), signal)
  }

  async listComments(profileKey: string, profileUrl: string, id: string, limit?: number, signal?: AbortSignal) {
    const target = profileTarget(profileUrl)
    const maximum = collectionLimit(limit)
    const note = noteId(id)
    return this.execute(profileKey, page => listComments(page, target, note, maximum, signal), signal)
  }

  async openConversation(profileKey: string, profileUrl: string, signal?: AbortSignal) {
    const target = profileTarget(profileUrl)
    return this.execute(profileKey, (page, context) => openConversation(page, context, target, signal), signal)
  }

  async sendFirstMessage(profileKey: string, input: FirstMessageInput, signal?: AbortSignal) {
    const validated = validateFirstMessage(input)
    return this.execute(profileKey, (page, context) =>
      sendFirstMessage(page, context, profileKey, validated, this.firstMessages, signal), signal, false)
  }

  private async execute<T>(
    profileKey: string, action: (page: Page, context: BrowserContext) => Promise<T>, signal?: AbortSignal,
    checkCancellationAfter = true,
  ): Promise<T> {
    if (!PROFILE_KEY.test(profileKey)) throw new BrowserFailure('XHS_INVALID_INPUT', '连接资料无效')
    if (this.busy) throw new BrowserFailure('XHS_BUSY', '设备正在打开小红书，请稍后重试')
    this.busy = true
    try {
      signal?.throwIfAborted()
      let context = this.contexts.get(profileKey)
      if (!context) {
        context = await this.launch(profileKey)
        this.contexts.set(profileKey, context)
        context.once('close', () => this.contexts.delete(profileKey))
      }
      signal?.throwIfAborted()
      const existing = context.pages().find(page => {
        if (page.url() === 'about:blank') return true
        const url = new URL(page.url())
        return url.origin === new URL(HOME).origin && url.pathname !== '/chat'
      })
      const page = existing ?? await context.newPage()
      const result = await action(page, context)
      if (checkCancellationAfter) signal?.throwIfAborted()
      return result
    } catch (error) {
      if (error instanceof BrowserFailure) throw error
      if (signal?.aborted) throw new BrowserFailure('XHS_CANCELLED', '操作已取消')
      throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '小红书页面未确认登录状态，请检查浏览器后重试')
    } finally {
      this.busy = false
    }
  }

  private async openPage(page: Page, action: BrowserAction, signal?: AbortSignal): Promise<BrowserStatus> {
    await page.bringToFront()
    const response = await page.goto(HOME, { waitUntil: 'domcontentloaded', timeout: 15000 })
    signal?.throwIfAborted()
    if (!response?.ok() || new URL(page.url()).origin !== new URL(HOME).origin) {
      throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '小红书页面暂时不可用')
    }

    const signedIn = page.locator(SIGNED_IN).filter({ visible: true }).first()
    const login = page.locator(LOGIN_BUTTON).filter({ visible: true }).first()
    const modal = page.locator(LOGIN_MODAL).filter({ visible: true }).first()
    await signedIn.or(login).or(modal).first().waitFor({ timeout: UI_TIMEOUT })
    signal?.throwIfAborted()
    const hasLoginModal = await modal.isVisible()
    if (!hasLoginModal && await signedIn.isVisible()) return 'opened'
    if (!hasLoginModal && !await login.isVisible()) {
      throw new BrowserFailure('XHS_PAGE_UNCONFIRMED', '小红书页面未确认登录状态，请检查浏览器后重试')
    }
    if (action === 'open') {
      throw new BrowserFailure('XHS_LOGIN_REQUIRED', '请扫码登录后再次打开小红书')
    }

    if (!hasLoginModal) await login.click({ timeout: UI_TIMEOUT })
    const qr = page.locator(QR_IMAGE).filter({ visible: true }).first()
    await qr.waitFor({ timeout: UI_TIMEOUT })
    const loaded = await qr.evaluate(el => el instanceof HTMLImageElement && el.complete && el.naturalWidth > 0)
    signal?.throwIfAborted()
    if (!loaded) {
      await page.waitForFunction(selector => {
        const image = document.querySelector(selector)
        return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0
      }, QR_IMAGE, { timeout: UI_TIMEOUT })
    }
    signal?.throwIfAborted()
    return 'qr_ready'
  }

  async close(): Promise<void> {
    await Promise.allSettled(Array.from(this.contexts.values(), context => context.close()))
  }
}

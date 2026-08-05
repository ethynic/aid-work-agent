/**
 * CLI 运行时装配（Phase 9，CLI 为唯一产品形态，Electron GUI 已随路线收敛删除）。
 * - 数据目录：clients/boss-resume-assistant/data/（DB + 截图 + 指纹密钥）
 * - Chrome 接入：attach 用户日常 Chrome（带 --remote-debugging-port 启动），
 *   绝不 spawn 临时 profile（该路径触发 BOSS 风控封号，已废弃）；
 *   登录门禁保持：attachChrome 只探测端点，confirmLogin 才建 CDP WebSocket
 * - 退出：残留会话记 INTERRUPTED + 断开 WebSocket（随进程退出），
 *   绝不 kill 用户的 Chrome、不删任何 profile
 */
import fs from 'node:fs'
import path from 'node:path'
import { createHash, randomBytes } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { initDatabaseAt, getClient, closeDatabase } from '../../db/client.js'
import { CdpGateway } from '../main/cdp/CdpGateway.js'
import { DbAuditWriter } from '../main/cdp/audit.js'
import { probeChromeDebugEndpoint, DEFAULT_CDP_PORT } from '../main/chrome/ChromeAttacher.js'
import { decodePng } from '../main/image/LongScreenshotStitcher.js'
import { TesseractJsProvider } from '../main/ocr/TesseractJsProvider.js'
import { DeepSeekLlmProvider } from '../main/screening/DeepSeekLlmProvider.js'
import {
  ScreeningSession,
  type SessionEvent,
  type SessionJobConfig,
  type SessionStatus,
} from '../main/workflow/ScreeningSession.js'
import { SessionStore } from '../main/storage/sessionStore.js'

/** CLI 数据目录：clients/boss-resume-assistant/data/ */
export function defaultDataDir(): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  // 编译后位于 dist/src/cli/，源码位于 src/cli/，向上三级均为工程根
  return path.resolve(here, '..', '..', '..', 'data')
}

let dataDir = ''
let fingerprintKey = ''
/** attach 模式记录的调试端点（http://127.0.0.1:{port}）；null = 尚未 attach */
let attachedEndpoint: string | null = null
let session: ScreeningSession | null = null
/** 当前已连接的 CDP gateway（connectCdp 成功后记录）：退出时必须显式关闭，
 *  否则打开的 WebSocket 句柄会让 Node 事件循环无法排空，任务正常完成后进程挂死 */
let cdpGateway: CdpGateway | null = null
/** OCR provider（tesseract worker 懒加载，一旦建过 worker 退出前必须 terminate，否则同样挂住事件循环） */
let ocrProvider: TesseractJsProvider | null = null
let eventSink: (event: SessionEvent) => void = () => {}
/** 与 session 共享的可变 viewport：CDP 连接后用真实截图尺寸更新 */
const viewport = { width: 1280, height: 800 }

/** CLI 启动初始化：打开 DB（迁移 fail-loud）+ 残留会话清扫。 */
export function initCliRuntime(opts: { dataDir: string }): void {
  dataDir = opts.dataDir
  fs.mkdirSync(dataDir, { recursive: true })
  initDatabaseAt(path.join(dataDir, 'boss-resume.db'))
  fingerprintKey = loadOrCreateFingerprintKey(dataDir)
  const swept = new SessionStore(getClient()).sweepInterrupted()
  if (swept > 0) {
    // fail-loud：上次进程有残留会话，显式提示
    console.warn(`CLI 运行时：清扫 ${swept} 个上次进程残留的 RUNNING/PAUSED 会话 → INTERRUPTED`)
  }
  attachedEndpoint = null
}

/**
 * 只读/轻量子命令（review/export/audit）用的 DB 打开入口。
 * 故意不做 sweepInterrupted：另一个终端可能正有 run 会话在跑，
 * 只读命令绝不能把在途会话误标 INTERRUPTED。
 */
export function openCliDb(opts: { dataDir: string }): void {
  dataDir = opts.dataDir
  fs.mkdirSync(dataDir, { recursive: true })
  initDatabaseAt(path.join(dataDir, 'boss-resume.db'))
}

/** 轻量子命令退出：只关 DB（无 Chrome/会话需要清扫） */
export function closeQuietly(): void {
  closeDatabase()
}

/** 事件出口（进度渲染注入） */
export function setEventSink(sink: (event: SessionEvent) => void): void {
  eventSink = sink
}

/**
 * attach 用户日常 Chrome（CLI 唯一接入方式）。
 * 只通过 /json/version 探测调试端点并记录，不建 CDP WebSocket（门禁在 confirmLogin）；
 * 绝不启动/杀死任何 Chrome 进程。探测失败 fail-loud（ChromeAttachError，由 CLI 转译诊断）。
 */
export async function attachChrome(opts: {
  port?: number
  /** 可注入探测实现（测试） */
  probeImpl?: typeof probeChromeDebugEndpoint
} = {}): Promise<{ httpEndpoint: string; webSocketDebuggerUrl: string; browser: string }> {
  if (!dataDir) throw new Error('CLI 运行时未初始化')
  const httpEndpoint = `http://127.0.0.1:${opts.port ?? DEFAULT_CDP_PORT}`
  const s = getSession()
  if (s.getStatus().state !== 'IDLE') {
    s.reset()
  }
  const probe = opts.probeImpl ?? probeChromeDebugEndpoint
  const info = await probe(httpEndpoint)
  attachedEndpoint = httpEndpoint
  s.markChromeLaunched()
  return { httpEndpoint, ...info }
}

export async function confirmLogin(): Promise<SessionStatus> {
  return getSession().confirmLoginReady()
}

/** 直接以岗位配置启动会话（CLI 场景：job.id 用于候选人指纹上下文） */
export function startSession(config: SessionJobConfig): SessionStatus {
  return getSession().start(config)
}

export function pauseSession(): SessionStatus {
  return getSession().pause()
}

export function resumeSession(): SessionStatus {
  return getSession().resume()
}

export function stopSession(): SessionStatus {
  return getSession().stop()
}

export function sessionStatus(): SessionStatus {
  return getSession().getStatus()
}

/** CLI 退出：在途会话记 INTERRUPTED + 断开 CDP WebSocket + 释放 OCR worker + 关 DB。
 *  attach 模式绝不 kill 用户的 Chrome、不删任何 profile（用户 Chrome 继续正常运行）；
 *  CDP WebSocket 必须显式关闭：它是活跃句柄，不关会让进程在正常完成后挂住不退。 */
export async function shutdownCliRuntime(): Promise<void> {
  try {
    new SessionStore(getClient()).sweepInterrupted()
  } catch {
    // 退出阶段 DB 异常不阻塞
  }
  if (cdpGateway) {
    const gw = cdpGateway
    cdpGateway = null
    await gw.close().catch(() => {})
  }
  if (ocrProvider) {
    const provider = ocrProvider
    ocrProvider = null
    await provider.dispose().catch(() => {})
  }
  attachedEndpoint = null
  closeDatabase()
}

// ===== 内部 =====

function getSession(): ScreeningSession {
  if (!session) {
    const db = getClient()
    ocrProvider = new TesseractJsProvider()
    const llm = new DeepSeekLlmProvider()
    if (!llm.isConfigured) {
      console.warn('CLI 运行时：未配置 DEEPSEEK_API_KEYS，LLM 阶段不可用，未决项将一律 UNCERTAIN 进人工复核')
    }
    session = new ScreeningSession({
      db,
      connect: connectCdp,
      ocrProvider,
      llmProvider: llm.isConfigured ? llm : undefined,
      viewport,
      fingerprintKey,
      saveCapture,
      emit: (e) => eventSink(e),
    })
  }
  return session
}

/** CONNECTING_CDP：连接 attach 的端点 → attach 推荐页 → Page.enable → 用截图实测视口尺寸（与 GUI runtime 同逻辑） */
async function connectCdp() {
  if (!attachedEndpoint) {
    throw new Error('Chrome 未 attach，请先确认 Chrome 已带调试端口启动并完成探测')
  }
  const gateway = new CdpGateway({ auditWriter: new DbAuditWriter(getClient()) })
  await gateway.connect(attachedEndpoint)
  try {
    await gateway.attachToRecommendPage()
    await gateway.pageEnable()
  } catch (e) {
    await gateway.close().catch(() => {})
    throw e
  }
  // 防御：重复 connect（如 COMPLETED 后 reset 再 attach）时旧 gateway 必须先关，
  // 否则旧 WebSocket 句柄丢失引用无人关闭，退出时同样挂住事件循环
  if (cdpGateway) {
    const stale = cdpGateway
    cdpGateway = null
    await stale.close().catch(() => {})
  }
  cdpGateway = gateway
  gateway.onDisconnect((err) => {
    // 只响应当前 gateway 的断线：主动关闭旧 gateway 也会触发 disconnect，
    // 不能把重连中的会话误置 PAUSED
    if (cdpGateway === gateway) session?.handleDisconnect(err.message)
  })
  // 视口尺寸：不用 Runtime.evaluate（硬禁止），用一次截图的 PNG 尺寸实测
  try {
    const shot = await gateway.captureScreenshot({ format: 'png' })
    const png = decodePng(Buffer.from(shot, 'base64'))
    if (png.width > 0 && png.height > 0) {
      viewport.width = png.width
      viewport.height = png.height
    }
  } catch {
    // 实测失败沿用默认 1280x800（安全区校验偏保守，不阻断）
  }
  return gateway
}

/** 截图/长图落盘：data/captures/{date}/{kind}_{ts}_{rand}.png */
function saveCapture(kind: string, data: Buffer): string {
  const dir = path.join(dataDir, 'captures', new Date().toISOString().slice(0, 10))
  fs.mkdirSync(dir, { recursive: true })
  const safeKind = kind.replace(/[^\w-]/g, '_')
  const file = path.join(dir, `${safeKind}_${Date.now()}_${randomBytes(4).toString('hex')}.png`)
  fs.writeFileSync(file, data)
  return file
}

/** 指纹密钥：data/fingerprint.key，首次运行生成（本地去重用） */
function loadOrCreateFingerprintKey(dir: string): string {
  const file = path.join(dir, 'fingerprint.key')
  try {
    const existing = fs.readFileSync(file, 'utf8').trim()
    if (existing) return existing
  } catch {
    // 不存在则创建
  }
  const key = createHash('sha256').update(randomBytes(32)).digest('hex')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(file, key, { encoding: 'utf8', mode: 0o600 })
  return key
}

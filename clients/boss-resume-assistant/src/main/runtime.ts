/**
 * 运行时装配（Phase 8）：把 ChromeLauncher / CdpGateway / ScreeningSession / 各 Store 接起来。
 * - 应用启动：崩溃恢复清扫（残留 RUNNING/PAUSED 会话 → INTERRUPTED）
 * - 登录门禁：launchChrome 只启动 Chrome；confirmLogin 才建 CDP 连接（设计 §5.2）
 * - 事件出口：setEventSink 由 ipc 层注入，转发 webContents.send
 * - 本模块是主进程唯一的会话单例持有者；ipc.ts 只做参数校验与转发
 */
import fs from 'node:fs'
import path from 'node:path'
import { createHash, randomBytes } from 'node:crypto'
import { getClient } from '../../db/client.js'
import { ChromeLauncher } from './chrome/ChromeLauncher.js'
import { CdpGateway } from './cdp/CdpGateway.js'
import { DbAuditWriter } from './cdp/audit.js'
import { decodePng } from './image/LongScreenshotStitcher.js'
import { TesseractJsProvider } from './ocr/TesseractJsProvider.js'
import { DeepSeekLlmProvider } from './screening/DeepSeekLlmProvider.js'
import { ScreeningSession, type SessionJobConfig, type SessionStatus } from './workflow/ScreeningSession.js'
import { SessionStore } from './storage/sessionStore.js'
import { JobStore } from './storage/jobStore.js'
import type { SessionEventPayload } from '../shared/ipc.js'

let userDataDir = ''
let fingerprintKey = ''
let launcher: ChromeLauncher | null = null
let session: ScreeningSession | null = null
let eventSink: (event: SessionEventPayload) => void = () => {}
/** 与 session 共享的可变 viewport：CDP 连接后用真实截图尺寸更新 */
const viewport = { width: 1280, height: 800 }

/** 应用启动初始化（initDatabase 之后调用）。崩溃恢复清扫在此完成。 */
export function initRuntime(opts: { userDataDir: string }): void {
  userDataDir = opts.userDataDir
  fingerprintKey = loadOrCreateFingerprintKey(userDataDir)
  const swept = new SessionStore(getClient()).sweepInterrupted()
  if (swept > 0) {
    // fail-loud：上次进程有残留会话，显式记录（渲染层启动后可在审计中查看）
    console.warn(`运行时：清扫 ${swept} 个上次进程残留的 RUNNING/PAUSED 会话 → INTERRUPTED`)
  }
  launcher = new ChromeLauncher()
}

/** 事件出口（ipc 层注册时注入，转发给渲染层） */
export function setEventSink(sink: (event: SessionEventPayload) => void): void {
  eventSink = sink
}

/** 启动 Chrome（可见窗口 + 登录 URL + 独立临时 profile），不连 CDP */
export async function launchChrome(): Promise<{ ok: boolean; pid?: number; port?: number; profileDir?: string; error?: string }> {
  if (!launcher) throw new Error('运行时未初始化')
  const s = getSession()
  const state = s.getStatus().state
  if (state !== 'IDLE') {
    // 上一轮任务已结束/无连接暂停/登录等待中放弃：允许复位后重新启动
    s.reset()
  }
  // 残留 Chrome 实例（如复位前已启动过）先回收，避免"已在运行"死局
  if (launcher.isRunning) {
    await launcher.dispose()
  }
  const instance = await launcher.launch()
  s.markChromeLaunched()
  return { ok: true, pid: instance.pid, port: instance.port, profileDir: instance.profileDir }
}

export async function confirmLogin(): Promise<SessionStatus> {
  return getSession().confirmLoginReady()
}

export function startSession(jobId: number): SessionStatus {
  const job = new JobStore(getClient()).get(jobId)
  if (!job) throw new Error(`岗位不存在: id=${jobId}`)
  const config: SessionJobConfig = {
    id: job.id,
    name: job.name,
    hardRules: job.hardRules,
    knowledgeVersion: job.knowledgeVersion,
    ruleVersion: job.ruleVersion,
    actionLimitSession: job.actionLimitSession,
    actionLimitDay: job.actionLimitDay,
  }
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

export function resetSession(): SessionStatus {
  return getSession().reset()
}

export function sessionStatus(): SessionStatus {
  return getSession().getStatus()
}

/** 应用退出：在途会话记 INTERRUPTED + 关闭本进程启动的 Chrome 并清理临时 profile */
export async function shutdownRuntime(): Promise<void> {
  try {
    new SessionStore(getClient()).sweepInterrupted()
  } catch {
    // 退出阶段 DB 异常不阻塞
  }
  if (launcher) {
    await launcher.dispose()
    launcher = null
  }
}

// ===== 内部 =====

function getSession(): ScreeningSession {
  if (!session) {
    const db = getClient()
    const ocrProvider = new TesseractJsProvider()
    const llm = new DeepSeekLlmProvider()
    if (!llm.isConfigured) {
      console.warn('运行时：未配置 DEEPSEEK_API_KEYS，LLM 阶段不可用，未决项将一律 UNCERTAIN 进人工复核')
    }
    session = new ScreeningSession({
      db,
      connect: connectCdp,
      ocrProvider,
      llmProvider: llm.isConfigured ? llm : undefined,
      viewport,
      fingerprintKey,
      saveCapture,
      emit: (e) => eventSink(e as SessionEventPayload),
    })
  }
  return session
}

/** CONNECTING_CDP：连接 → attach 推荐页 → Page.enable → 用截图实测视口尺寸 */
async function connectCdp() {
  if (!launcher || !launcher.current) {
    throw new Error('Chrome 未启动，请先点击「启动浏览器」')
  }
  const gateway = new CdpGateway({ auditWriter: new DbAuditWriter(getClient()) })
  await gateway.connect(launcher.current.httpEndpoint)
  try {
    await gateway.attachToRecommendPage()
    await gateway.pageEnable()
  } catch (e) {
    await gateway.close().catch(() => {})
    throw e
  }
  gateway.onDisconnect((err) => {
    session?.handleDisconnect(err.message)
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

/** 截图/长图落盘：userData/captures/{date}/{kind}_{ts}_{rand}.png */
function saveCapture(kind: string, data: Buffer): string {
  const dir = path.join(userDataDir, 'captures', new Date().toISOString().slice(0, 10))
  fs.mkdirSync(dir, { recursive: true })
  const safeKind = kind.replace(/[^\w-]/g, '_')
  const file = path.join(dir, `${safeKind}_${Date.now()}_${randomBytes(4).toString('hex')}.png`)
  fs.writeFileSync(file, data)
  return file
}

/** 指纹密钥：userData/fingerprint.key，首次运行生成（本地去重用，不作为全局身份） */
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

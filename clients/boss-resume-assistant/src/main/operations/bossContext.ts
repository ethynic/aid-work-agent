/**
 * BOSS operation 的运行时上下文组装（从 7 个 CLI command 抽取的公共段，实施规格 m02 §1）。
 *
 * 公共模式：CdpGateway connect → attachToRecommendPage → 组装 executor 依赖 → 执行 → finally close。
 * 默认工厂连接真实 Chrome；单测通过 BossSessionFactory 注入替身（fake gateway/clicker 原语）。
 *
 * 退出只断开 CDP 连接，绝不关闭用户的 Chrome。
 */
import { CdpGateway } from '../cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../chrome/ChromeAttacher.js'
import { WinMouseClicker } from '../input/WinMouseClicker.js'
import type { DomSnapshot, ClickPoint } from '../boss/domSnapshot.js'
import { randomUUID } from 'node:crypto'
import {
  CancelledError,
  failResult,
  okResult,
  writeEffect,
  type Effect,
  type OperationResult,
  type OpContext,
} from './types.js'
import { mapExecutorError } from './errorMapping.js'

/**
 * operation 需要的页面原语（executor 依赖的组装原料）。
 * 真实实现 = CdpGateway + WinMouseClicker；测试实现 = 脚本化 fake。
 */
export interface BossSession {
  /** 采集 fresh DOMSnapshot（页面动态变化，每次定位前重新采集） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px）。写动作按钮/筛选类控件专用：
   *  BOSS 反作弊 SDK 选择性拦截 CDP 合成点击，这类动作必须借真实光标 */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP 浏览类点击（mousePressed+mouseReleased，device px）。与 Win32 click 的区别：
   *  浏览动作（点牛人卡片打开简历详情等）真机实证 CDP 有效且不被拦、不占用真实鼠标；
   *  写动作/筛选类控件仍必须走 click（Win32），不得混用 */
  clickBrowse(point: ClickPoint): Promise<void>
  /** CDP mouseWheel 滚动（浏览类操作，不占用真实鼠标） */
  mouseWheel(x: number, y: number, deltaY: number): Promise<void>
  /** 按 Escape（CDP dispatchKey，关简历预览弹层用） */
  pressEscape(): Promise<void>
  /** CDP char 事件逐字输入（调用方保证焦点已在目标输入框） */
  typeChar(ch: string): Promise<void>
  /** 无 clip 整页截图（Page.captureScreenshot png）。输出即 device px，与 DOMSnapshot bounds 同坐标系，
   *  按 device 坐标直接裁剪即可，绝不做 DPI 换算（设计 §10.8 真机实证：整页 1249x1277 = viewport bounds） */
  captureFullpage(): Promise<Buffer>
  /** 当前 BOSS 标签页 URL（Target.getTargets 实时取） */
  getUrl(): Promise<string>
  /** 断开 CDP 连接（不关闭 Chrome）；必须幂等、绝不 throw */
  close(): Promise<void>
}

/** operation 的会话工厂：connect + attach 完成后返回原语集。可注入替身以便单测 */
export type BossSessionFactory = (ctx: OpContext) => Promise<BossSession>

/** signal 触发时让挂起的 Promise 立即以 CancelledError 拒绝（connect/attach 阶段也需要可取消） */
export function withAbort<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new CancelledError())
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new CancelledError())
    signal.addEventListener('abort', onAbort, { once: true })
    promise.then(
      (v) => {
        signal.removeEventListener('abort', onAbort)
        resolve(v)
      },
      (e) => {
        signal.removeEventListener('abort', onAbort)
        reject(e instanceof Error ? e : new Error(String(e)))
      },
    )
  })
}

/** 默认工厂：attach 用户日常登录 BOSS、带调试端口启动的 Chrome */
export const defaultSessionFactory: BossSessionFactory = async (ctx) => {
  const endpoint = `http://127.0.0.1:${ctx.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  ctx.progress({ stage: 'connect', message: `连接 Chrome 调试端口（${endpoint}）` })
  try {
    await withAbort(gw.connect(endpoint), ctx.signal)
    ctx.progress({ stage: 'attach', message: 'attach BOSS 页面' })
    await withAbort(gw.attachToRecommendPage(), ctx.signal)
  } catch (err) {
    // connect/attach 失败时 gw 不会进入 BossSession，runBossOperation 的 finally 关不到它；
    // 必须在此关闭已建立的 CDP socket，否则事件循环被挂起的 WS 拖住导致进程不退出
    await gw.close().catch(() => {})
    throw err
  }
  return {
    snapshot: async () => (await gw.captureDomSnapshot()) as DomSnapshot,
    click: (point, viewport) => clicker.click(point, viewport),
    clickBrowse: async (point) => {
      // 浏览类点击（真机 2026-08-17 实证：牛人卡片 CDP 点击有效打开简历详情；写动作按钮走 Win32 click）
      await gw.dispatchMouse({ type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 })
      await gw.dispatchMouse({ type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 })
    },
    mouseWheel: async (x, y, deltaY) => {
      await gw.dispatchMouse({ type: 'mouseWheel', x, y, deltaX: 0, deltaY })
    },
    pressEscape: async () => {
      await gw.dispatchKey({ type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
      await gw.dispatchKey({ type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
    },
    typeChar: async (ch) => {
      await gw.dispatchKey({ type: 'char', key: ch, text: ch })
    },
    captureFullpage: async () => Buffer.from(await gw.captureScreenshot({ format: 'png' }), 'base64'),
    getUrl: async () => {
      const targets = await gw.getTargets()
      return targets.find((t) => t.type === 'page' && t.url.includes('zhipin.com'))?.url ?? ''
    },
    close: () => gw.close().catch(() => {}),
  }
}

/** body 的成功产出；effect 缺省时按 kind+completed 自动计算 */
export interface OperationOutcome {
  message: string
  data?: Record<string, unknown>
  effect?: Effect
}

/** 写动作完成量追踪：operation body 经 executor onProgress 实时更新，失败时用于 partial/unknown 判定 */
export interface CompletedTracker {
  completed: number
}

/**
 * operation 执行骨架（规格 §2：永不 throw、参数校验在 connect 前、统一结果）：
 * 参数校验（validate）→ signal 入口检查 → session 工厂（connect+attach）→ body → finally close。
 * 任何异常经 errorMapping 映射为结构化失败结果；写动作失败按 tracker.completed 计算 effect。
 */
export async function runBossOperation(
  kind: 'write' | 'readonly',
  ctx: OpContext,
  sessionFactory: BossSessionFactory,
  validate: () => string | null,
  body: (session: BossSession, tracker: CompletedTracker) => Promise<OperationOutcome>,
): Promise<OperationResult> {
  const runId = randomUUID()
  const invalid = validate()
  if (invalid) return failResult(runId, 'INVALID_ARGUMENT', invalid, 'none')

  const tracker: CompletedTracker = { completed: 0 }
  let session: BossSession | undefined
  try {
    if (ctx.signal.aborted) throw new CancelledError()
    session = await sessionFactory(ctx)
    const outcome = await body(session, tracker)
    const effect = outcome.effect ?? (kind === 'readonly' ? 'none' : writeEffect(true, 'OK', tracker.completed))
    ctx.progress({ stage: 'done', message: outcome.message })
    return okResult(runId, outcome.message, effect, outcome.data ?? {})
  } catch (err) {
    const mapped = mapExecutorError(err)
    const effect = kind === 'readonly' ? 'none' : writeEffect(false, mapped.code, tracker.completed)
    const data: Record<string, unknown> = {}
    if (kind === 'write' && tracker.completed > 0) data.completed = tracker.completed
    return failResult(runId, mapped.code, mapped.message, effect, data)
  } finally {
    await session?.close()
  }
}

/**
 * accept/reject/interview 共用前置：不在沟通页时先点击左侧菜单跳转（复用 PageNavigator）。
 */
export async function ensureChatPage(session: BossSession, ctx: OpContext): Promise<void> {
  const url = await session.getUrl()
  if (url.includes('/web/chat/index')) return
  ctx.progress({ stage: 'navigate', message: '当前不在沟通页，先点击左侧菜单跳转' })
  const { PageNavigator } = await import('../boss/PageNavigator.js')
  const navigator = new PageNavigator({
    snapshot: session.snapshot,
    click: session.click,
    getUrl: session.getUrl,
    signal: ctx.signal,
  })
  await navigator.navigate('chat')
}

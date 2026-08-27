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
import { ensureDebugChrome } from '../chrome/ChromeLauncher.js'
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
  /** Win32 原子「真实鼠标点击聚焦 + 真实键盘逐字输入」（一次 ps1 调用内完成）。
   *  2026-08-26 决策：点击/输入类操作第一优先 Win32 真实事件防风控；此前 08-24 误判的
   *  「DPI 换算偏差」实为用户移动窗口致输入框不可见，换算本身无偏差 */
  clickAndType(point: ClickPoint, viewport: { width: number; height: number }, text: string): Promise<void>
  /** CDP mouseWheel 滚动（浏览类操作，不占用真实鼠标） */
  mouseWheel(x: number, y: number, deltaY: number): Promise<void>
  /** 按 Escape（CDP dispatchKey，关简历预览弹层用） */
  pressEscape(): Promise<void>
  /** 清空当前聚焦输入框（CDP ctrl+a 全选 + Delete，可选）：搜索框输入未落地的清空重试用。
   *  可选成员——旧测试替身/精简会话可不提供，缺省搜索执行器输入校验失败直接报错 */
  clearInput?(): Promise<void>
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
    // 端口不通时自动拉起固定 profile 调试实例（fail-open，拉不起则由下方 connect 报原有错误；
    // 包 withAbort 使拉起等待期间用户取消依然立即生效）
    const launched = await withAbort(ensureDebugChrome(ctx.cdpPort ?? DEFAULT_CDP_PORT), ctx.signal)
    if (launched) {
      ctx.progress({ stage: 'connect', message: '已自动拉起调试 Chrome 并打开 BOSS 直聘；首次使用请在该窗口登录一次' })
    }
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
    // 清空当前聚焦输入框：ctrl+a（modifiers=2 即 Ctrl 位掩码）全选 + Delete 删除。
    // 2026-08-27 聚焦竞态修复：搜索框 Win32 输入偶发未落地，ChatSearchExecutor 清空后重试一次
    clearInput: async () => {
      await gw.dispatchKey({ type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 })
      await gw.dispatchKey({ type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 })
      await gw.dispatchKey({ type: 'keyDown', key: 'Delete', code: 'Delete', windowsVirtualKeyCode: 46 })
      await gw.dispatchKey({ type: 'keyUp', key: 'Delete', code: 'Delete', windowsVirtualKeyCode: 46 })
    },
    clickAndType: (point, viewport, text) => clicker.clickAndType(point, viewport, text),
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

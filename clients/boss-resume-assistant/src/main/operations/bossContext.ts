/**
 * BOSS operation 的运行时上下文组装（从 7 个 CLI command 抽取的公共段，实施规格 m02 §1）。
 *
 * 公共模式：CdpGateway connect → attachToRecommendPage → 组装 executor 依赖 → 执行 → finally close。
 * 默认工厂连接真实 Chrome；单测通过 BossSessionFactory 注入替身（fake gateway/clicker 原语）。
 *
 * 退出只断开 CDP 连接，绝不关闭用户的 Chrome。
 */
import { CdpGateway, type DomSnapshotOptions } from '../cdp/CdpGateway.js'
import { CdpConnectionError } from '../cdp/errors.js'
import { DEFAULT_CDP_PORT } from '../chrome/ChromeAttacher.js'
import { ensureDebugChrome, runningChromeProfile, DEFAULT_DEBUG_PROFILE_DIR, DEFAULT_INITIAL_URL } from '../chrome/ChromeLauncher.js'
import { WinMouseClicker } from '../input/WinMouseClicker.js'
import type { DomSnapshot, ClickPoint } from '../boss/domSnapshot.js'
import { randomUUID } from 'node:crypto'
import {
  CancelledError,
  CodedOperationError,
  failResult,
  okResult,
  writeEffect,
  type Effect,
  type OperationResult,
  type OpContext,
} from './types.js'
import { mapExecutorError } from './errorMapping.js'
import { rememberBrowserTarget, resolveBrowserTarget, targetBindingStale, updateBrowserTarget, type BrowserPageContext } from '../boss/browserTargetBinding.js'
import { captureBrowserPageContext, restoreBrowserPageContext, rememberFilterResult } from '../boss/browserPageRecovery.js'
import { PerfCollector } from '../perf.js'

/**
 * operation 级失败日志（stderr，经 providerManager 转发落 runtime.log）：
 * [boss-mcp] 行只有 tool/success/code/effect/时长，失败 message（含页面现场指引与候选人姓名）
 * 只随 MCP 结果回云端——客户机日志侧此前完全看不到失败原因（2026-09-10 排障事故整改）。
 * 只记元数据与失败文案，不记页面内容/坐标/简历正文。
 */
function logOpFailure(runId: string, code: string, message: string): void {
  const trimmed = message.length > 200 ? `${message.slice(0, 200)}…` : message
  process.stderr.write(`[boss-op] 失败 run_id=${runId} code=${code} message=${trimmed}\n`)
}

/**
 * 性能埋点单行日志（stderr，与 [boss-op] 同通道经 providerManager 落 runtime.log）：
 * 每次 operation 运行结束（成功/失败都）输出一条可解析的分步耗时汇总，供数据驱动优化。
 * 只记工具名/步骤名与毫秒数，不记页面内容/坐标/候选人信息（与 logOpFailure 同口径）。
 */
function logPerfLine(name: string, runId: string, ok: boolean, totalMs: number, perf: PerfCollector): void {
  const parts = perf.summaryParts()
  process.stderr.write(
    `[boss-perf] tool=${name} run_id=${runId} ok=${ok ? 1 : 0} total_ms=${totalMs}` +
      ` connect_ms=${perf.totalMsOf('connect')} body_ms=${perf.totalMsOf('body')} close_ms=${perf.totalMsOf('close')}` +
      (parts ? ` ${parts}` : '') +
      '\n',
  )
}

/**
 * 对 session 原语逐字段包一层计时（不用 Proxy，显式包装更清晰）：
 * 测试注入的 fake session 同样被覆盖。包装函数只闭包引用 session.xxx（this 无关）。
 * 可选成员（clearInput/pageNavigate）存在才包装；close 不在此包（run 的 finally 单独计时）。
 */
function instrumentSession(session: BossSession, perf: PerfCollector): BossSession {
  const { clearInput, pageNavigate } = session
  return {
    ...(session.browserTargetRef ? { browserTargetRef: session.browserTargetRef } : {}),
    ...(session.cdpPort ? { cdpPort: session.cdpPort } : {}),
    snapshot: (opts) => perf.time('snapshot', () => session.snapshot(opts)),
    click: (point, viewport) => perf.time('win32:click', () => session.click(point, viewport)),
    ...(session.drag ? { drag: (from: ClickPoint, to: ClickPoint, viewport: { width: number; height: number }) => perf.time('win32:drag', () => session.drag!(from, to, viewport)) } : {}),
    clickBrowse: (point) => perf.time('cdp:clickBrowse', () => session.clickBrowse(point)),
    clickAndType: (point, viewport, text) => perf.time('win32:clickAndType', () => session.clickAndType(point, viewport, text)),
    mouseWheel: (x, y, deltaY) => perf.time('cdp:mouseWheel', () => session.mouseWheel(x, y, deltaY)),
    ...(session.physicalWheel ? { physicalWheel: (point: ClickPoint, viewport: { width: number; height: number }, deltaY: number, notches: number) => perf.time('win32:wheel', () => session.physicalWheel!(point, viewport, deltaY, notches)) } : {}),
    pressEscape: () => perf.time('cdp:escape', () => session.pressEscape()),
    captureFullpage: () => perf.time('cdp:screenshot', () => session.captureFullpage()),
    getUrl: () => perf.time('cdp:getUrl', () => session.getUrl()),
    ...(session.activateTarget ? { activateTarget: () => perf.time('cdp:activateTarget', () => session.activateTarget!()) } : {}),
    ...(session.getTargetId ? { getTargetId: () => session.getTargetId!() } : {}),
    ...(session.getPageMetrics ? { getPageMetrics: () => session.getPageMetrics!() } : {}),
    ...(clearInput ? { clearInput: () => perf.time('cdp:clearInput', () => clearInput()) } : {}),
    ...(pageNavigate ? { pageNavigate: (url: string) => perf.time('cdp:navigate', () => pageNavigate(url)) } : {}),
    close: () => session.close(),
  }
}

/**
 * operation 需要的页面原语（executor 依赖的组装原料）。
 * 真实实现 = CdpGateway + WinMouseClicker；测试实现 = 脚本化 fake。
 */
export interface BossSession {
  /** 采集 fresh DOMSnapshot（页面动态变化，每次定位前重新采集） */
  snapshot(opts?: DomSnapshotOptions): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px）。写动作按钮/筛选类控件专用：
   *  BOSS 反作弊 SDK 选择性拦截 CDP 合成点击，这类动作必须借真实光标 */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 可选的真实鼠标拖动，范围控件使用；旧测试/会话可不提供。 */
  drag?(from: ClickPoint, to: ClickPoint, viewport: { width: number; height: number }): Promise<void>
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
  /**
   * Win32 真实滚轮（简历 canvas 滚动专用）：BOSS 反作弊拦 CDP 合成 mouseWheel（WASM
   * 虚拟滚动不动，2026-08-14 真机实证），只有真实 mouse_event WHEEL 有效。
   * 可选成员：旧测试替身/精简会话可不提供，缺失时简历读取链路显式报错。
   */
  physicalWheel?(point: ClickPoint, viewport: { width: number; height: number }, deltaY: number, notches: number): Promise<void>
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
  /** Bound CDP target and CSS viewport; real sessions always provide these. */
  getTargetId?(): Promise<string>
  /** Browser-level target activation; does not require a responsive Page/DOM. */
  activateTarget?(): Promise<void>
  cdpPort?: number
  browserTargetRef?: string
  /** Real sessions provide trusted local account/profile evidence; absent in old/fake sessions. */
  recoveryPageContext?(prior?: BrowserPageContext): Promise<BrowserPageContext | undefined>
  getPageMetrics?(): Promise<{ width: number; height: number }>
  /** CDP 页内导航（Page.navigate）。goto 幂等兜底专用：点击菜单未触发 SPA 路由变化时，
   *  在 /web/chat 区内直跳目标页（不动鼠标键盘）。可选成员——旧测试替身可不提供，
   *  PageNavigator 缺省时点击未生效仍走原有 fail-loud 报错 */
  pageNavigate?(url: string): Promise<void>
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
  const clicker = new WinMouseClicker({ cdpPort: ctx.cdpPort ?? DEFAULT_CDP_PORT, beforeInput: () => gw.activateTarget() })
  const binding = ctx.browserTargetRef ? await resolveBrowserTarget(ctx.browserTargetRef, ctx.cdpPort ?? DEFAULT_CDP_PORT) : undefined
  let rebuilt = false
  let launched = false
  let currentTarget = binding?.target_id
  const saved = binding?.page_context && { ...binding.page_context,
    job_name: binding.page_context.job_name ?? ctx.recoveryJobName }
  ctx.progress({ stage: 'connect', message: `连接 Chrome 调试端口（${endpoint}）` })
  try {
    // A live old target needs no new metadata. Reopening a bound browser must
    // use its known persistent profile, never guess the default for an old ref.
    const reopenProfile = saved?.managed_profile_dir ?? (!binding ? DEFAULT_DEBUG_PROFILE_DIR : undefined)
    if (reopenProfile) launched = await withAbort(ensureDebugChrome(ctx.cdpPort ?? DEFAULT_CDP_PORT,
      { profileDir: reopenProfile, initialUrl: saved?.entry_url ?? DEFAULT_INITIAL_URL, signal: ctx.signal }), ctx.signal)
    if (launched) {
      ctx.progress({ stage: 'connect', message: '已自动拉起调试 Chrome 并打开 BOSS 直聘；首次使用请在该窗口登录一次' })
    }
    try { await withAbort(gw.connect(endpoint, ctx.signal), ctx.signal) }
    catch (error) {
      if (binding && !reopenProfile && error instanceof CdpConnectionError) {
        throw new CodedOperationError('UI_CHANGED', '原调试浏览器不可连接，且无法确定原持久 profile；请用原 profile 打开 Chrome 后在原会话接续')
      }
      throw error
    }
    ctx.progress({ stage: 'attach', message: 'attach BOSS 页面' })
    if (binding) {
      try { await withAbort(gw.attachToBoundBossTarget(binding.target_id, binding.restore_pending === true), ctx.signal) }
      catch (error) {
        if (error instanceof CancelledError) throw error
        if (!ctx.recoverBrowserTarget) throw error
        if (ctx.signal.aborted) throw new CancelledError()
        if (!saved) throw new CodedOperationError('UI_CHANGED', '原页面已关闭，缺少原职位及有效筛选记录；请恢复原业务页面后在原会话接续')
        ctx.progress({ stage: 'recover', message: '正在重建原 BOSS 业务页面并核验账号、职位与筛选条件' })
        await withAbort(gw.createBossPage(endpoint, saved.entry_url), ctx.signal)
        currentTarget = gw.getTargetId()!
        await updateBrowserTarget(ctx.browserTargetRef!, ctx.cdpPort ?? DEFAULT_CDP_PORT, binding.target_id,
          b => ({ ...b, target_id: currentTarget!, restore_pending: true }))
        rebuilt = true
      }
    } else await withAbort(gw.attachToRecommendPage(), ctx.signal)
  } catch (err) {
    // connect/attach 失败时 gw 不会进入 BossSession，runBossOperation 的 finally 关不到它；
    // 必须在此关闭已建立的 CDP socket，否则事件循环被挂起的 WS 拖住导致进程不退出
    await gw.close().catch(() => {})
    throw err
  }
  const session: BossSession = {
    snapshot: async (opts) => (await gw.captureDomSnapshot(opts)) as DomSnapshot,
    click: (point, viewport) => clicker.click(point, viewport),
    drag: (from, to, viewport) => clicker.drag(from, to, viewport),
    clickBrowse: async (point) => {
      // 浏览类点击（真机 2026-08-17 实证：牛人卡片 CDP 点击有效打开简历详情；写动作按钮走 Win32 click）
      await gw.dispatchMouse({ type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 })
      await gw.dispatchMouse({ type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 })
    },
    mouseWheel: async (x, y, deltaY) => {
      await gw.dispatchMouse({ type: 'mouseWheel', x, y, deltaX: 0, deltaY })
    },
    physicalWheel: (point, viewport, deltaY, notches) => clicker.wheel(point, viewport, deltaY, notches),
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
      return targets.find((t) => t.targetId === gw.getTargetId())?.url ?? ''
    },
    getTargetId: async () => gw.getTargetId() ?? '',
    activateTarget: () => gw.activateTarget(),
    cdpPort: ctx.cdpPort ?? DEFAULT_CDP_PORT,
    getPageMetrics: () => gw.getPageMetrics(),
    pageNavigate: async (url) => {
      await gw.pageEnable()
      await gw.pageNavigate(url)
    },
    close: () => gw.close().catch(() => {}),
  }
  session.recoveryPageContext = async prior => {
    const captured = await captureBrowserPageContext(session, prior)
    if (!captured) return
    const instance = gw.getBrowserInstanceId()
    if (instance) captured.browser_instance = instance
    const profile = prior?.managed_profile_dir ?? (launched ? DEFAULT_DEBUG_PROFILE_DIR : await runningChromeProfile(ctx.cdpPort ?? DEFAULT_CDP_PORT, ctx.signal))
    if (profile) captured.managed_profile_dir = profile
    return captured
  }
  try {
    if (rebuilt || binding?.restore_pending) {
      // A new tab is a connection detail. Only the original business identity can authorize its inputs.
      if (!saved) throw new CodedOperationError('UI_CHANGED', '缺少原业务页面记录，请恢复原职位及筛选后接续')
      await withAbort(restoreBrowserPageContext(session, saved, ctx), ctx.signal)
      if (ctx.signal.aborted) throw new CancelledError()
      await updateBrowserTarget(ctx.browserTargetRef!, ctx.cdpPort ?? DEFAULT_CDP_PORT, currentTarget!,
        b => ({ ...b, restore_pending: false, page_context: { ...saved, browser_instance: gw.getBrowserInstanceId() } }))
    } else if (ctx.recoverBrowserTarget && binding?.page_context) {
      const current = await session.recoveryPageContext(binding.page_context)
      if (current?.account_ref && binding.page_context.account_ref && current.account_ref !== binding.page_context.account_ref) throw targetBindingStale()
    }
    return session
  } catch (error) { await session.close(); throw error }
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
 *
 * 性能埋点（2026-09-28）：首参为 { kind, name } 对象（name = operation 名，进 [boss-perf] 行）；
 * run 内创建 PerfCollector，connect/body/close 三段与 session 各原语逐层计时，
 * 结束时（进入 body 前的参数校验失败除外——尚未发生任何 I/O）向 stderr 输出 [boss-perf] 单行。
 * body 第三参为 collector（可选使用，既有 2 参 body 不受影响）。
 */
export async function runBossOperation(
  spec: { kind: 'write' | 'readonly'; name: string },
  ctx: OpContext,
  sessionFactory: BossSessionFactory,
  validate: () => string | null,
  body: (session: BossSession, tracker: CompletedTracker, perf: PerfCollector) => Promise<OperationOutcome>,
): Promise<OperationResult> {
  const runId = randomUUID()
  const invalid = validate()
  if (invalid) {
    logOpFailure(runId, 'INVALID_ARGUMENT', invalid)
    return failResult(runId, 'INVALID_ARGUMENT', invalid, 'none')
  }

  const perf = new PerfCollector()
  const startMs = Date.now()
  const tracker: CompletedTracker = { completed: 0 }
  let session: BossSession | undefined
  let ok = false
  let browserTargetRef = ctx.browserTargetRef
  const boundData = (data: Record<string, unknown>): Record<string, unknown> => {
    if (!browserTargetRef) return data
    const result: Record<string, unknown> = { ...data, browser_target_ref: browserTargetRef }
    for (const key of ['operation_recovery', 'page_operation']) {
      const value = result[key]
      if (value && typeof value === 'object' && !Array.isArray(value)) result[key] = { ...value, browser_target_ref: browserTargetRef }
    }
    return result
  }
  try {
    if (ctx.signal.aborted) throw new CancelledError()
    session = await perf.time('connect', () => sessionFactory(ctx))
    const targetId = await session.getTargetId?.()
    if (browserTargetRef) {
      const expected = await resolveBrowserTarget(browserTargetRef, ctx.cdpPort ?? DEFAULT_CDP_PORT)
      if (targetId !== expected.target_id) throw targetBindingStale()
    } else if (targetId) browserTargetRef = await rememberBrowserTarget(targetId, ctx.cdpPort ?? DEFAULT_CDP_PORT)
    if (browserTargetRef) session.browserTargetRef = browserTargetRef
    if (browserTargetRef && session.recoveryPageContext && targetId) {
      const binding = await resolveBrowserTarget(browserTargetRef, ctx.cdpPort ?? DEFAULT_CDP_PORT)
      const page = await session.recoveryPageContext(binding.page_context).catch(() => undefined)
      if (page) await updateBrowserTarget(browserTargetRef, ctx.cdpPort ?? DEFAULT_CDP_PORT, targetId,
        b => ({ ...b, page_context: page }))
    }
    const outcome = await perf.time('body', () => body(instrumentSession(session!, perf), tracker, perf))
    const effect = outcome.effect ?? (spec.kind === 'readonly' ? 'none' : writeEffect(true, 'OK', tracker.completed))
    ctx.progress({ stage: 'done', message: outcome.message })
    ok = true
    const result = okResult(runId, outcome.message, effect, boundData(outcome.data ?? {}))
    if (session.recoveryPageContext) await rememberFilterResult(spec.name, result, ctx.cdpPort ?? DEFAULT_CDP_PORT)
    return result
  } catch (err) {
    const mapped = mapExecutorError(err)
    logOpFailure(runId, mapped.code, mapped.message)
    const data: Record<string, unknown> = err instanceof CodedOperationError ? { ...err.data } : {}
    const facts = data.operation_recovery as Record<string, unknown> | undefined
    const business = facts?.business_effect as { status?: string; completed_count?: number } | undefined
    // Preserve confirmed work when a later preview or close fails.
    const confirmed = facts?.operation_class === 'external_effect' && Number.isInteger(business?.completed_count)
      && business!.completed_count! > 0 && Array.isArray(data.confirmed_items)
      && data.confirmed_items.length >= business!.completed_count!
    const completed = confirmed ? Math.max(tracker.completed, business!.completed_count!) : tracker.completed
    const effect = spec.kind === 'readonly' ? 'none' : confirmed && business?.status === 'confirmed'
      ? 'partial' : writeEffect(false, mapped.code, completed)
    if (spec.kind === 'write' && completed > 0) data.completed = completed
    return failResult(runId, mapped.code, mapped.message, effect, boundData(data))
  } finally {
    // close 计时后统一输出 [boss-perf]（成功/失败都输出；ok 由上方分支标记）
    if (session) await perf.time('close', () => session!.close())
    logPerfLine(spec.name, runId, ok, Date.now() - startMs, perf)
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
    pageNavigate: session.pageNavigate,
    signal: ctx.signal,
  })
  await navigator.navigate('chat')
}

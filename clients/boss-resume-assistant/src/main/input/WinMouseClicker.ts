/**
 * Win32 真实物理输入通道（设计文档 §10.3 通道 2）。
 *
 * 仅 native：共享包 @aid/win32-native-input 进程内 FFI（koffi）直调 Win32，
 * 守卫/拟人序列/逐字键入/分段计时是原 PowerShell 输入脚本的 1:1 移植。
 * 2026-10-21 用户决策（docs/plans/plan-recruiting-error-containment-and-input-performance.md）：
 * 不长期维护双通道，PowerShell 输入回退脚本（点击/滚轮）已删除——
 * koffi 会话加载失败直接抛 WinClickError（fail-loud），不再静默回退。
 *
 * 语义：page 坐标（DOMSnapshot device px）+ 页面截图尺寸 →
 * 屏幕坐标实时校准（GetWindowRect）→ WindowFromPoint 落点守卫 → 拟人移动点击。
 * 守卫失败（落点被遮挡）exit 2，本类转为 WinClickError fail-loud，绝不盲点；
 * 物理输入开始后的失败带 [BOSS_INPUT_UNKNOWN]（效果未知）；
 * NativeInputError exitCode 1/2 → WinClickError 同码（与原 ps1 语义一致）。
 *
 * 用途：BOSS 反作弊 SDK 选择性拦截 CDP 合成点击的控件（筛选按钮/面板选项等，§16 决策 8）。
 * 注意：借用真实光标，点击期间（约 1s）用户手不能碰鼠标，CLI 调用前必须提示。
 */
import type { ClickPoint } from '../boss/domSnapshot.js'
import { CancelledError } from '../operations/types.js'

export class WinClickError extends Error {
  constructor(
    message: string,
    readonly exitCode?: number,
  ) {
    super(message)
    this.name = 'WinClickError'
  }
}

/** Only certified zero-input lifecycle refusals, never cursor/occlusion/unknown errors. */
export function nativeWindowInputFailure(error: unknown): 'render_widget_hidden' | 'render_widget_unavailable' | 'foreground_unavailable' | 'target_activation_failed' | undefined {
  if (!(error instanceof WinClickError) || error.exitCode !== 2) return
  if (error.message === 'STALE_OBSERVATION: render widget hidden; input refused') return 'render_widget_hidden'
  if (error.message === 'STALE_OBSERVATION: render widget unavailable; input refused') return 'render_widget_unavailable'
  if (error.message === 'STALE_OBSERVATION: Debug Chrome is not foreground; observation/input refused') return 'foreground_unavailable'
  if (error.message === 'STALE_OBSERVATION: 无法激活原 BOSS 标签，未投递物理输入') return 'target_activation_failed'
}

/** 原生输入能力会话（创建成功即 koffi 已加载） */
export interface NativeInputSession {
  perform(request: {
    x: number
    y: number
    cssW: number
    cssH: number
    text?: string
    dragX?: number
    dragY?: number
    wheelDeltaY?: number
    notches?: number
    cdpPort?: number
    op?: string
  }, signal?: AbortSignal): Promise<{ output: string[] }>
}

/** 原生会话加载器（单测注入 fake 用；默认动态 import 共享包） */
export type NativeInputOpener = () => Promise<NativeInputSession>

const defaultNativeOpener: NativeInputOpener = async () => {
  if (process.platform !== 'win32') throw new Error('win32-native-input 仅支持 win32 平台')
  const mod: typeof import('@aid/win32-native-input') = await import('@aid/win32-native-input')
  let api
  try {
    api = mod.createKoffiApi()
  } catch (error) {
    throw new Error(`koffi 加载失败: ${error instanceof Error ? error.message : String(error)}`)
  }
  return {
    perform: (request, signal) => mod.performPhysicalInput({ api, signal }, request),
  }
}

/**
 * 一次物理输入请求（native 后端消费）。
 */
interface PhysicalRequest {
  point: { x: number; y: number }
  viewport: { width: number; height: number }
  drag?: { x: number; y: number }
  text?: string
  wheel?: { deltaY: number; notches: number }
  op: string
}

export class WinMouseClicker {
  private readonly cdpPort?: number
  private readonly beforeInput?: () => Promise<void>
  private readonly nativeOpener: NativeInputOpener
  private readonly signal?: AbortSignal
  /** 原生会话缓存（含失败的 Promise：fail-loud 抛错，不重试加载、不回退） */
  private nativeSession: Promise<NativeInputSession> | null = null

  constructor(opts: {
    cdpPort?: number
    beforeInput?: () => Promise<void>
    nativeOpener?: NativeInputOpener
    signal?: AbortSignal
  } = {}) {
    if (opts.cdpPort !== undefined && (!Number.isInteger(opts.cdpPort) || opts.cdpPort < 1 || opts.cdpPort > 65535 || !opts.beforeInput)) throw new WinClickError('绑定真实输入需要合法调试端口与标签激活方法', 2)
    this.cdpPort = opts.cdpPort
    this.beforeInput = opts.beforeInput
    this.nativeOpener = opts.nativeOpener ?? defaultNativeOpener
    this.signal = opts.signal
  }

  /**
   * 真实鼠标点击 page 坐标。viewport 为页面截图尺寸（device px），用于 DPI 缩放换算。
   * 失败抛 WinClickError（含 exit code：2=落点被遮挡守卫拒绝）。
   */
  async click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void> {
    const { x, y } = point
    this.assertClickable(point, viewport)
    await this.activateBoundTarget()
    await this.perform({ point, viewport, op: 'click' })
  }

  /** 同进程真实拖动；序列复用窗口、遮挡、光标守卫并 finally 释放左键。 */
  async drag(from: ClickPoint, to: ClickPoint, viewport: { width: number; height: number }): Promise<void> {
    this.assertClickable(from, viewport)
    this.assertClickable(to, viewport)
    await this.activateBoundTarget()
    await this.perform({ point: from, viewport, drag: to, op: 'drag' })
  }

  /**
   * Win32 原子「点击聚焦 + 真实键盘逐字输入」：同一次调用内先拟人点击目标点，
   * 紧接着 SendInput KEYEVENTF_UNICODE 逐字键入 text（200ms/字）——拆成两次调用会在
   * 间隙被抢焦点。2026-08-26 决策：点击/输入类第一优先 Win32 真实事件（防爬）。
   */
  async clickAndType(
    point: ClickPoint,
    viewport: { width: number; height: number },
    text: string,
  ): Promise<void> {
    this.assertClickable(point, viewport)
    if (typeof text !== 'string' || text.length === 0) {
      throw new WinClickError('clickAndType 输入文本不能为空')
    }
    await this.activateBoundTarget()
    await this.perform({ point, viewport, text, op: 'clickAndType' })
  }

  /**
   * Win32 真实滚轮滚动 page 坐标（简历 canvas 专用）：BOSS 反作弊拦 CDP 合成 mouseWheel
   * （WASM 虚拟滚动不动，2026-08-14 真机实证），必须真实 mouse_event WHEEL。
   * deltaY 为每格 delta（负=向下，单位=格×120），notches 为格数（格间 ~150ms）。
   * 借用真实光标约 1s + notches×0.15s，调用前必须提示用户勿动鼠标。
   */
  async wheel(
    point: ClickPoint,
    viewport: { width: number; height: number },
    deltaY: number,
    notches: number,
  ): Promise<void> {
    this.assertClickable(point, viewport)
    if (!Number.isInteger(deltaY) || deltaY === 0) {
      throw new WinClickError(`滚轮 deltaY 必须为非 0 整数（当前: ${deltaY}）`)
    }
    if (!Number.isInteger(notches) || notches < 1) {
      throw new WinClickError(`滚轮 notches 必须为 >=1 的整数（当前: ${notches}）`)
    }
    await this.activateBoundTarget()
    await this.perform({ point, viewport, wheel: { deltaY, notches }, op: 'wheel' })
  }

  private async perform(request: PhysicalRequest): Promise<void> {
    const session = await this.openNative()
    try {
      if (this.signal?.aborted) throw new CancelledError()
      await session.perform(this.toNativeRequest(request), this.signal)
    } catch (error) {
      if (error instanceof CancelledError) throw error
      const mapped = toWinClickError(error)
      if (this.signal?.aborted && mapped.exitCode === 2 && !mapped.message.includes('[BOSS_INPUT_UNKNOWN]')) throw new CancelledError()
      throw mapped
    }
  }

  private toNativeRequest(request: PhysicalRequest): Parameters<NativeInputSession['perform']>[0] {
    const { point, viewport, drag, text, wheel } = request
    return {
      x: Math.round(point.x),
      y: Math.round(point.y),
      cssW: viewport.width,
      cssH: viewport.height,
      ...(drag ? { dragX: Math.round(drag.x), dragY: Math.round(drag.y) } : {}),
      ...(text !== undefined ? { text } : {}),
      ...(wheel ? { wheelDeltaY: wheel.deltaY, notches: wheel.notches } : {}),
      ...(this.cdpPort ? { cdpPort: this.cdpPort } : {}),
      op: request.op,
    }
  }

  /**
   * 打开原生会话（缓存复用，失败同样缓存——重试加载只会放大退化面）；
   * 加载失败（缺包/非 win32/koffi 失败）直接抛 WinClickError fail-loud，无回退通道。
   */
  private async openNative(): Promise<NativeInputSession> {
    if (this.nativeSession === null) this.nativeSession = this.nativeOpener()
    try {
      return await this.nativeSession
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      throw new WinClickError(`原生输入通道不可用（无 PowerShell 回退）: ${reason}`)
    }
  }

  private async activateBoundTarget(): Promise<void> {
    if (this.signal?.aborted) throw new CancelledError()
    if (!this.beforeInput) return
    try { await this.beforeInput() }
    catch { throw new WinClickError('STALE_OBSERVATION: 无法激活原 BOSS 标签，未投递物理输入', 2) }
  }

  /** 坐标/视口合法性校验（click 与 clickAndType 共用），非法即抛 WinClickError，绝不盲点 */
  private assertClickable(point: ClickPoint, viewport: { width: number; height: number }): void {
    const { x, y } = point
    if (![x, y, viewport.width, viewport.height].every(Number.isFinite) || viewport.width <= 0 || viewport.height <= 0) {
      throw new WinClickError(`非法点击参数: point=(${x},${y}) viewport=${viewport.width}x${viewport.height}`)
    }
    if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) {
      throw new WinClickError(`点击坐标 (${x},${y}) 超出视口 ${viewport.width}x${viewport.height}，拒绝盲点`)
    }
  }
}

/** 原生错误 → WinClickError（exit code 与原 ps1 语义一致：0/1/2） */
function toWinClickError(error: unknown): WinClickError {
  const native = error as { name?: string; exitCode?: 1 | 2; message?: string }
  if (native && typeof native === 'object' && native.name === 'NativeInputError' && (native.exitCode === 1 || native.exitCode === 2)) {
    return new WinClickError(native.message ?? '原生输入失败', native.exitCode)
  }
  return new WinClickError(`原生输入通道失败: ${error instanceof Error ? error.message : String(error)}`)
}

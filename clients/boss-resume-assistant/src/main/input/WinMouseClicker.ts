/**
 * Win32 真实鼠标点击通道（设计文档 §10.3 通道 2）。
 *
 * 双通道（Phase 1 输入性能修复，docs/plans/plan-recruiting-error-containment-and-input-performance.md）：
 * - native（默认）：共享包 @aid/win32-native-input 进程内 FFI（koffi）直调 Win32，
 *   守卫/拟人序列/逐字键入/分段计时是 scripts/win-click.ps1 的 1:1 移植；
 *   不再起 2 个 PowerShell 进程 + Add-Type 现场编译 C#。
 * - powershell（回退）：原路径，完整保留——包装 scripts/win-click.ps1。
 * 通道选择：构造参数 channel > 环境变量 AID_BOSS_INPUT=native|powershell > 默认 native。
 * native 加载失败（缺包/非 win32/koffi 加载失败）自动回退 powershell 并打一行日志；
 * 显式 native 同样回退（业务不中断，日志可见原因）。
 *
 * 语义（两通道一致）：page 坐标（DOMSnapshot device px）+ 页面截图尺寸 →
 * 屏幕坐标实时校准（GetWindowRect）→ WindowFromPoint 落点守卫 → 拟人移动点击。
 * 守卫失败（落点被遮挡）exit 2，本类转为 WinClickError fail-loud，绝不盲点；
 * 物理输入开始后的失败带 [BOSS_INPUT_UNKNOWN]（效果未知）。
 *
 * 用途：BOSS 反作弊 SDK 选择性拦截 CDP 合成点击的控件（筛选按钮/面板选项等，§16 决策 8）。
 * 注意：借用真实光标，点击期间（约 1s）用户手不能碰鼠标，CLI 调用前必须提示。
 */
import { execPowerShellScript, PowerShellScriptError } from './powerShell.js'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { ClickPoint } from '../boss/domSnapshot.js'

export class WinClickError extends Error {
  constructor(
    message: string,
    readonly exitCode?: number,
  ) {
    super(message)
    this.name = 'WinClickError'
  }
}

type ExecFileLike = (
  file: string,
  args: string[],
  opts: { timeout: number },
) => Promise<{ stdout: string; stderr: string }>

/** 默认实现：promisify 手动包（execFile 的 promisify 类型在 node:test 注入时不便） */
const defaultExecFile: ExecFileLike = async (_file, args, opts) => {
  const index = args.indexOf('-File')
  try { return await execPowerShellScript(args[index + 1]!, args.slice(index + 2), opts) }
  catch (error) {
    const code = error instanceof PowerShellScriptError ? error.exitCode : undefined
    throw new WinClickError(`win-click.ps1 执行失败(exit=${code ?? 'unknown'}): ${error instanceof Error ? error.message : 'PowerShell 执行失败'}`, code)
  }
}

/**
 * scripts/<name> 绝对路径（win-click.ps1 / cv-wheel.ps1 共用探测）。
 * 源码（src/main/input）向上 3 级、编译后（dist/src/main/input）向上 4 级是工程根，
 * 两个候选按存在性取一，都找不到 fail-loud。
 */
function defaultScriptPath(name: 'win-click.ps1' | 'cv-wheel.ps1' = 'win-click.ps1'): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  for (const up of ['..\\..\\..\\..', '..\\..\\..']) {
    const candidate = path.resolve(here, up, 'scripts', name)
    if (fs.existsSync(candidate)) return candidate
  }
  throw new WinClickError(`未找到 scripts/${name}（已从 ${here} 向上探测）`)
}

/** 物理输入通道 */
export type WinInputChannel = 'native' | 'powershell'

/** 读取环境变量指定的通道；未设置返回 undefined（由默认值兜底） */
export function channelFromEnv(env: NodeJS.ProcessEnv = process.env): WinInputChannel | undefined {
  const value = env.AID_BOSS_INPUT
  if (value === undefined || value === '') return undefined
  if (value === 'native' || value === 'powershell') return value
  throw new WinClickError(`AID_BOSS_INPUT 取值必须是 native|powershell，当前: ${value}`)
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
  }): Promise<{ output: string[] }>
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
    perform: (request) => mod.performPhysicalInput({ api }, request),
  }
}

/**
 * 一次物理输入请求（通道无关，两个后端各自消费）。
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
  private readonly scriptPath: string
  private readonly wheelScriptPath: string
  private readonly execFileImpl: ExecFileLike
  private readonly cdpPort?: number
  private readonly beforeInput?: () => Promise<void>
  private readonly channel: WinInputChannel
  private readonly nativeOpener: NativeInputOpener
  /** 原生会话缓存（失败也缓存，每次输入都重试加载只会放大退化面） */
  private nativeSession: Promise<NativeInputSession> | null = null
  private nativeFailed = false
  private fallbackReason = ''

  constructor(opts: {
    scriptPath?: string
    wheelScriptPath?: string
    execFileImpl?: ExecFileLike
    cdpPort?: number
    beforeInput?: () => Promise<void>
    channel?: WinInputChannel
    nativeOpener?: NativeInputOpener
    env?: NodeJS.ProcessEnv
  } = {}) {
    if (opts.cdpPort !== undefined && (!Number.isInteger(opts.cdpPort) || opts.cdpPort < 1 || opts.cdpPort > 65535 || !opts.beforeInput)) throw new WinClickError('绑定真实输入需要合法调试端口与标签激活方法', 2)
    this.cdpPort = opts.cdpPort
    this.beforeInput = opts.beforeInput
    this.scriptPath = opts.scriptPath ?? defaultScriptPath()
    this.wheelScriptPath = opts.wheelScriptPath ?? defaultScriptPath('cv-wheel.ps1')
    this.execFileImpl = opts.execFileImpl ?? defaultExecFile
    // 显式构造 > 环境变量 > 默认；注入 execFileImpl 时视为显式 powershell
    // （旧测试/调用方注入的语义是 PowerShell 路径）
    this.channel = opts.channel ?? channelFromEnv(opts.env) ?? (opts.execFileImpl ? 'powershell' : 'native')
    this.nativeOpener = opts.nativeOpener ?? defaultNativeOpener
  }

  /** 当前生效的输入通道（测试/诊断用） */
  get inputChannel(): WinInputChannel {
    return this.channel === 'native' && this.nativeFailed ? 'powershell' : this.channel
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

  /** 通道无关的执行入口：native 优先，失败回退 powershell（各一次日志） */
  private async perform(request: PhysicalRequest): Promise<void> {
    if (this.channel === 'native' && !this.nativeFailed) {
      const session = await this.openNative()
      if (session) {
        try {
          await session.perform(this.toNativeRequest(request))
          return
        } catch (error) {
          throw toWinClickError(error)
        }
      }
    }
    await this.runPowerShell(request)
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

  /** 打开原生会话；失败记录原因并返回 null（本进程不再重试） */
  private async openNative(): Promise<NativeInputSession | null> {
    if (this.nativeFailed) return null
    if (this.nativeSession === null) this.nativeSession = this.nativeOpener()
    try {
      return await this.nativeSession
    } catch (error) {
      this.nativeFailed = true
      this.nativeSession = null
      this.fallbackReason = error instanceof Error ? error.message : String(error)
      this.logFallback()
      return null
    }
  }

  private logFallback(): void {
    try { process.stderr.write(`[boss-input] 原生通道不可用，回退 PowerShell: ${this.fallbackReason}\n`) } catch { /* 日志失败不影响业务结果 */ }
  }

  private async runPowerShell(request: PhysicalRequest): Promise<void> {
    const { point, viewport, drag, text, wheel } = request
    // cv-wheel.ps1 与 win-click.ps1 参数面不同（无 -Text/-Drag/-CdpPort，多 -DeltaY/-Notches/-TitleKeyword）；
    // cv-wheel.ps1 无绑定模式，PowerShell 回退恒用标题关键字定位（现状语义，过渡期保留）
    if (wheel) {
      const args = [
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        this.wheelScriptPath,
        '-X',
        String(Math.round(point.x)),
        '-Y',
        String(Math.round(point.y)),
        '-CssW',
        String(viewport.width),
        '-CssH',
        String(viewport.height),
        '-DeltaY',
        String(wheel.deltaY),
        '-Notches',
        String(wheel.notches),
        '-TitleKeyword',
        'BOSS直聘 - Google Chrome',
      ]
      await this.execFileImpl('powershell.exe', args, { timeout: 60000 })
      return
    }
    const args = [
      '-NoProfile',
      '-ExecutionPolicy',
      'Bypass',
      '-File',
      this.scriptPath,
      ...this.bindingArgs(),
      '-X',
      String(Math.round(point.x)),
      '-Y',
      String(Math.round(point.y)),
      '-CssW',
      String(viewport.width),
      '-CssH',
      String(viewport.height),
    ]
    if (drag) args.push('-DragX', String(Math.round(drag.x)), '-DragY', String(Math.round(drag.y)))
    if (text !== undefined) args.push('-Text', text)
    // 点击 ~5s + 聚焦 0.5s + 每字 0.2s，留足余量防长消息误超时
    await this.execFileImpl('powershell.exe', args, { timeout: text !== undefined ? 30000 + text.length * 500 : 20000 })
  }

  private bindingArgs(): string[] { return this.cdpPort ? ['-CdpPort', String(this.cdpPort)] : [] }
  private async activateBoundTarget(): Promise<void> {
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

/** 原生错误 → WinClickError（exit code 与 ps1 语义一致：0/1/2） */
function toWinClickError(error: unknown): WinClickError {
  const native = error as { name?: string; exitCode?: 1 | 2; message?: string }
  if (native && typeof native === 'object' && native.name === 'NativeInputError' && (native.exitCode === 1 || native.exitCode === 2)) {
    return new WinClickError(native.message ?? '原生输入失败', native.exitCode)
  }
  return new WinClickError(`原生输入通道失败: ${error instanceof Error ? error.message : String(error)}`)
}

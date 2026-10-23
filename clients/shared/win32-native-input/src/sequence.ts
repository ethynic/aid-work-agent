/**
 * win-click.ps1 完整输入流程的 1:1 进程内移植（语义权威来源，一条守卫不删）。
 *
 * 与 ps1 的对应关系：
 * - 绑定模式（cdpPort>0）：按 cmdline 唯一性定位调试 Chrome → BossWindowExperiment.Find(pid) →
 *   Foreground(win) → 400ms → RequireForeground(win)；非绑定：EnumWindows 标题关键字找窗。
 * - 非绑定窗口最小化则 SW_RESTORE+400ms；AttachThreadInput 挂前台线程抬前台 → 解挂 → 400ms。
 * - FindRenderWidget：可见且面积最大的 Chrome_RenderWidgetHostHWND（回退第一个隐藏的）。
 * - 坐标换算：widget 物理矩形按 CssW/CssH 线性缩放（PowerShell [int] 的银行家舍入一并保真）。
 * - 落点守卫：WindowFromPoint 不属于目标 widget → 抬窗（HWND_TOP）+300ms+重算重试一次，
 *   仍失败按 REFUSED(exit 2) 拒绝（不盲点，附守卫取证）。
 * - 拟人移动：10 步 SetCursorPos（25ms/步）+500ms 悬停 → RequireCursorOnWidget → MarkInputStarted →
 *   LEFTDOWN+70ms → 拖动 16 步（每步前台校验/光标守卫/双端点重算且几何变化即失败/路径遮挡守卫）→ finally LEFTUP。
 * - clickAndType：点击后 500ms 逐字 SendInput KEYEVENTF_UNICODE（200ms/字，UTF-16 code unit 逐个，
 *   与 ps1 ToCharArray 一致），每字前前台校验；点击与键入同任务原子完成。
 * - 错误会计：MarkInputStarted 之后任何异常 → [BOSS_INPUT_UNKNOWN]（exit 1）；绑定模式输入开始前
 *   异常 → STALE_OBSERVATION（exit 2）；REFUSED 等价 ps1 的 exit 2 直退（不经过 catch 改写）；
 *   DPI 上下文失败绝不静默回退。
 */
import { NativeInputError } from './errors.js'
import {
  DpiPhysicalScope,
  moveCursor,
  occlusionDiagnostics,
  pointOnWidget,
  readCursor,
  readRect,
  requireCursorOnWidget,
} from './physical.js'
import { findBrowserPidByDebugPort } from './processes.js'
import { SegmentTimer } from './timing.js'
import type {
  LogSink,
  NativeApi,
  PhysicalInputRequest,
  PhysicalInputResult,
} from './types.js'
import { boundFind, boundForeground, findRenderWidget, findWindowByTitle, requireBoundForeground } from './windows.js'

const HWND_TOP = 0
const SWP_NOSIZE = 0x0001
const SWP_NOMOVE = 0x0002
const SW_RESTORE = 9

export interface SequenceContext {
  api: NativeApi
  /** 默认真实 setTimeout；单测注入即时 sleep 以快跑 */
  sleep?: (ms: number) => Promise<void>
  /** 默认写 stderr 单行（与 [boss-perf]/[boss-op] 同通道） */
  log?: LogSink
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))
const defaultLog: LogSink = (line) => {
  try { process.stderr.write(line + '\n') } catch { /* 日志失败不影响业务结果 */ }
}

/** PowerShell [int] 的银行家舍入（坐标换算/插值保真；.5 边界取偶） */
export function psInt(value: number): number {
  const floor = Math.floor(value)
  const diff = value - floor
  if (diff < 0.5) return floor
  if (diff > 0.5) return floor + 1
  return floor % 2 === 0 ? floor : floor + 1
}

/** ps1 Get-ScreenPoint：widget 物理矩形按 CssW/CssH 线性缩放（每次调用实时 ReadRect） */
function screenPoint(api: NativeApi, rw: number, cssW: number, cssH: number, px: number, py: number): { x: number; y: number } {
  const r = readRect(api, rw)
  const vw = r.right - r.left
  const vh = r.bottom - r.top
  return { x: psInt(r.left + px * (vw / cssW)), y: psInt(r.top + py * (vh / cssH)) }
}

/** 执行一次物理输入。成功返回输出行；失败抛 NativeInputError（exitCode 对应 ps1 退出码 0/1/2）。 */
export async function performPhysicalInput(ctx: SequenceContext, request: PhysicalInputRequest): Promise<PhysicalInputResult> {
  const api = ctx.api
  const sleep = ctx.sleep ?? defaultSleep
  const log = ctx.log ?? defaultLog
  const timer = new SegmentTimer()
  const op = request.op ?? (request.text !== undefined ? 'clickAndType' : request.wheelDeltaY !== undefined ? 'wheel' : request.dragX !== undefined ? 'drag' : 'click')
  const wheeling = op === 'wheel'
  // cv-wheel.ps1 的参数校验（exit 2 语义）：DeltaY 非 0、Notches >= 1
  if (wheeling && (!Number.isInteger(request.wheelDeltaY) || request.wheelDeltaY === 0)) {
    throw new NativeInputError(`wheelDeltaY 必须为非 0 整数（当前: ${request.wheelDeltaY}）`, 1, 'FAILED')
  }
  if (wheeling && request.notches !== undefined && (!Number.isInteger(request.notches) || request.notches < 1)) {
    throw new NativeInputError(`notches 必须为 >=1 的整数（当前: ${request.notches}）`, 1, 'FAILED')
  }
  const bound = (request.cdpPort ?? 0) > 0
  const output: string[] = []
  let completed = false

  // ps1 catch 的会计规则（已带前缀的错误不重复叠加——ps1 字面行为会双重前缀，按分类语义去重）
  const account = (error: unknown, inputStarted: boolean): NativeInputError => {
    const raw = error instanceof Error ? error.message : String(error)
    if (inputStarted) {
      return new NativeInputError(raw.startsWith('[BOSS_INPUT_UNKNOWN]') ? raw : `[BOSS_INPUT_UNKNOWN] ${raw}`, 1, 'BOSS_INPUT_UNKNOWN')
    }
    if (bound) {
      const message = raw.startsWith('STALE_OBSERVATION:') ? raw : `STALE_OBSERVATION: ${raw}`
      return new NativeInputError(message, 2, 'STALE_OBSERVATION')
    }
    if (error instanceof NativeInputError) return error
    return new NativeInputError(raw, 1, 'FAILED')
  }

  let scope: DpiPhysicalScope | undefined
  try {
    scope = DpiPhysicalScope.enter(api)
    let win = 0
    let rw = 0
    // 自定义目标定位只调用一次（默认 Chrome 语义按阶段分别找主窗/widget）
    const customTarget = request.resolveTarget?.(api)

    // ---- 前台激活（ps1 153~190）----
    await timer.measureAsync('activate', async () => {
      if (bound) {
        const pid = findBrowserPidByDebugPort(api, request.cdpPort!)
        win = boundFind(api, pid)
        boundForeground(api, win)
        await sleep(400)
        requireBoundForeground(api, win)
      } else {
        win = customTarget ? customTarget.win : findWindowByTitle(api, request.titleKeyword ?? 'BOSS直聘 - Google Chrome')
        if (win === 0) {
          throw new NativeInputError(`未找到标题含「${request.titleKeyword ?? 'BOSS直聘 - Google Chrome'}」的窗口`, 1, 'FAILED')
        }
        // Chrome 最小化时 render widget 被销毁（EnumChildWindows 枚举不到，真机 2026-08-17 实证），
        // 必须先还原 + 前台化再定位 render widget
        if (api.isIconic(win)) {
          api.showWindow(win, SW_RESTORE)
          await sleep(400)
        }
        // 强制前台：后台进程直接 SetForegroundWindow 常被前台锁静默拒绝；
        // AttachThreadInput 挂到当前前台线程的输入队列后即可成功
        const foreground = api.getForegroundWindow()
        const foregroundThread = api.getWindowThreadProcessId(foreground).thread
        const thisThread = api.getCurrentThreadId()
        api.attachThreadInput(thisThread, foregroundThread, true)
        api.bringWindowToTop(win)
        api.setForegroundWindow(win)
        api.attachThreadInput(thisThread, foregroundThread, false)
        await sleep(400)
      }
      if (bound) requireBoundForeground(api, win)
    })

    // ---- 窗口探查：render widget + 坐标换算（ps1 191~197）----
    let sx = 0
    let sy = 0
    await timer.measureAsync('discover', async () => {
      rw = customTarget ? customTarget.rw : findRenderWidget(api, win)
      if (rw === 0) {
        if (bound) throw new NativeInputError('STALE_OBSERVATION: render widget unavailable; input refused', 2, 'STALE_OBSERVATION')
        throw new NativeInputError('未找到 Chrome 渲染子窗口（窗口已还原仍无渲染子窗口）', 1, 'FAILED')
      }
      if (!api.isWindowVisible(rw)) {
        if (bound) throw new NativeInputError('STALE_OBSERVATION: render widget hidden; input refused', 2, 'STALE_OBSERVATION')
        throw new NativeInputError('渲染子窗口不可见：BOSS 标签页可能在后台，请切换到前台后重试', 1, 'FAILED')
      }
      const target = screenPoint(api, rw, request.cssW, request.cssH, request.x, request.y)
      sx = target.x
      sy = target.y
      output.push(`目标屏幕坐标: (${sx}, ${sy})`)
    })

    // ---- 落点守卫：抬窗重试一次，仍遮挡 exit 2（ps1 199~211）----
    await timer.measureAsync('guard', async () => {
      if (!pointOnWidget(api, rw, sx, sy)) {
        output.push('落点被其他窗口遮挡，抬窗重试')
        if (bound) requireBoundForeground(api, win)
        api.setWindowPos(win, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        await sleep(300)
        const retry = screenPoint(api, rw, request.cssW, request.cssH, request.x, request.y)
        sx = retry.x
        sy = retry.y
        if (!pointOnWidget(api, rw, sx, sy)) {
          // 守卫取证：两项谓词 + 预期坐标 + 目标 widget/命中/前台 HWND（无标题/页面内容）
          const d = occlusionDiagnostics(api, rw, sx, sy)
          const detail =
            `expected_screen=(${d.expectedX},${d.expectedY}); widget=0x${(d.widget >>> 0).toString(16)}` +
            `; hit=0x${((d.hit ?? 0) >>> 0).toString(16)}; foreground=0x${((d.foreground ?? 0) >>> 0).toString(16)}` +
            `; cursor_match=0; ownership_match=0`
          log(`[boss-input-guard] kind=occlusion op=${op} ${detail}`)
          throw new NativeInputError(`落点仍被遮挡（WindowFromPoint 校验失败），中止点击; ${detail}`, 2, 'REFUSED', detail)
        }
      }
    })

    // ---- 光标就位：wheel 单步直移 + 200ms（cv-wheel.ps1 MoveCursor 语义，滚动不拟人）；
    //      点击/拖动/键入走拟人 10 步 + 500ms 悬停（ps1 213~223）----
    await timer.measureAsync('move', async () => {
      if (wheeling) {
        moveCursor(api, sx, sy)
        await sleep(200)
        return
      }
      const cursor = readCursor(api)
      for (let i = 1; i <= 10; i++) {
        const mx = psInt(cursor.x + (sx - cursor.x) * i / 10)
        const my = psInt(cursor.y + (sy - cursor.y) * i / 10)
        moveCursor(api, mx, my)
        await sleep(25)
      }
      await sleep(500)
      const expected = screenPoint(api, rw, request.cssW, request.cssH, request.x, request.y)
      requireCursorOnWidget(api, rw, expected.x, expected.y)
    })
    if (bound) requireBoundForeground(api, win)

    // ---- 滚轮（cv-wheel.ps1 150~160）：每格前实时重算落点并双谓词守卫，MarkInputStarted
    //      划定效果未知边界后注入；格间 150ms（最后一格不等）。负 DeltaY 的 uint wrap 在绑定层 ----
    if (wheeling) {
      const notches = request.notches ?? 6
      await timer.measureAsync('wheel', async () => {
        for (let i = 1; i <= notches; i++) {
          if (bound) requireBoundForeground(api, win)
          const expected = screenPoint(api, rw, request.cssW, request.cssH, request.x, request.y)
          requireCursorOnWidget(api, rw, expected.x, expected.y)
          scope!.markInputStarted()
          api.mouseEventWheel(request.wheelDeltaY!)
          if (i < notches) await sleep(150)
        }
        output.push(`已滚动 ${notches} 格 × ${request.wheelDeltaY}`)
      })
      completed = true
      return { output }
    }

    // ---- 按下（ps1 224~236）：MarkInputStarted 划定“效果未知”会计边界 ----
    let dragEnd: { x: number; y: number } | undefined
    let leftHeld = false
    try {
      await timer.measureAsync('press', async () => {
        scope!.markInputStarted()
        const { dragX, dragY } = request
        const dragging = dragX !== undefined || dragY !== undefined
        if ((dragX !== undefined) !== (dragY !== undefined)) throw new Error('拖动终点必须同时提供 X/Y')
        if (dragging) {
          if ((request.text?.length ?? 0) > 0 || dragX! > request.cssW || dragY! > request.cssH) {
            throw new Error('非法拖动参数')
          }
          dragEnd = screenPoint(api, rw, request.cssW, request.cssH, dragX!, dragY!)
          if (!pointOnWidget(api, rw, dragEnd.x, dragEnd.y)) throw new Error('拖动终点被遮挡')
        }
        leftHeld = true
        api.mouseEventLeftDown()
        await sleep(70)
      })

      // ---- 拖动 16 步（ps1 237~253）----
      if (dragEnd) {
        await timer.measureAsync('drag', async () => {
          let lastX = sx
          let lastY = sy
          for (let step = 1; step <= 16; step++) {
            if (bound) requireBoundForeground(api, win)
            requireCursorOnWidget(api, rw, lastX, lastY)
            const liveStart = screenPoint(api, rw, request.cssW, request.cssH, request.x, request.y)
            const liveEnd = screenPoint(api, rw, request.cssW, request.cssH, request.dragX!, request.dragY!)
            if (liveStart.x !== sx || liveStart.y !== sy || liveEnd.x !== dragEnd!.x || liveEnd.y !== dragEnd!.y) {
              throw new Error('拖动期间窗口几何变化')
            }
            lastX = psInt(sx + (dragEnd!.x - sx) * step / 16)
            lastY = psInt(sy + (dragEnd!.y - sy) * step / 16)
            if (!pointOnWidget(api, rw, lastX, lastY)) throw new Error('拖动路径被遮挡')
            moveCursor(api, lastX, lastY)
            await sleep(25)
          }
          requireCursorOnWidget(api, rw, lastX, lastY)
          output.push('已拖动')
        })
      } else {
        output.push('已点击')
      }
    } finally {
      // ps1 finally（254~256）：try 块结束即释放左键——先于键入阶段（ps1 260 起
      // 在 finally 之后）。点击后按住左键输入会变成拖选，绝不能把键入放进本 try。
      if (leftHeld) api.mouseEventLeftUp()
    }

    // ---- 逐字键入（ps1 258~268）：LEFTUP 之后，点击聚焦稳定 500ms 后 200ms/字 ----
    const text = request.text
    if (text !== undefined && text.length > 0) {
      await timer.measureAsync('type', async () => {
        await sleep(500)
        // UTF-16 code unit 逐个（与 ps1 ToCharArray 一致，emoji 代理项成对发送由应用重组）
        for (let i = 0; i < text.length; i++) {
          if (bound) requireBoundForeground(api, win)
          api.typeUnicode(text.charCodeAt(i))
          await sleep(200)
        }
        output.push(`已输入 ${text.length} 字`)
      })
    }
    completed = true
    return { output }
  } catch (error) {
    // ps1 的 exit 2（落点守卫）不经过 catch：REFUSED 原样直退，不被改写为其他类别
    if (error instanceof NativeInputError && error.kind === 'REFUSED') throw error
    throw account(error, scope?.inputStarted ?? false)
  } finally {
    // 收尾：DPI 上下文恢复（ps1 finally Dispose）。失败顶替在途异常（ps1 字面语义），
    // 统一收敛为 NativeInputError exit 1；inputStarted 后的消息自带 [BOSS_INPUT_UNKNOWN] 前缀。
    try {
      timer.measure('finalize', () => scope?.dispose())
    } catch (disposeError) {
      const raw = disposeError instanceof Error ? disposeError.message : String(disposeError)
      throw new NativeInputError(raw, 1, raw.startsWith('[BOSS_INPUT_UNKNOWN]') ? 'BOSS_INPUT_UNKNOWN' : 'FAILED')
    } finally {
      log(timer.summaryLine(op, completed))
    }
  }
}

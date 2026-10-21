/**
 * Win32 原生输入能力的类型契约。
 *
 * NativeApi 是唯一的外部世界边界：生产实现见 bindings/koffi.ts（进程内 FFI），
 * 单元测试注入内存 fake（不发任何真实输入）。所有 HWND/句柄用 JS number 表示
 * （Win64 HANDLE 在实际取值范围内可安全落在 Number 精度内，绑定层负责归一化）。
 *
 * 语义权威来源：clients/boss-resume-assistant/scripts/win-click.ps1 与
 * BossPhysicalInput.cs / BossWindowExperiment.cs（1:1 移植，一条守卫不删）。
 */

/** 物理屏幕矩形（GetWindowRect 语义，left/top 为屏幕坐标） */
export interface Rect {
  left: number
  top: number
  right: number
  bottom: number
}

/** 物理屏幕点 */
export interface Point {
  x: number
  y: number
}

/** 进程快照条目（Toolhelp32 语义） */
export interface ProcessEntry {
  pid: number
  exeName: string
}

/**
 * 可注入的 Win32 原生 API 集。函数失败语义与 Win32 一致：
 * 返回 0/false/undefined 表示调用失败（fail-loud 由守卫层负责，不在此吞错）。
 */
export interface NativeApi {
  // ---- DPI 线程上下文（BossPhysicalInput.Native）----
  /** SetThreadDpiAwarenessContext：返回旧上下文，0=失败 */
  setThreadDpiAwarenessContext(context: number): number
  getThreadDpiAwarenessContext(): number
  areDpiAwarenessContextsEqual(a: number, b: number): boolean
  getCurrentThreadId(): number

  // ---- 几何 / 光标 / 命中（物理坐标，需在 PMv2 线程上下文内调用）----
  /** GetWindowRect：失败返回 undefined */
  getWindowRect(hwnd: number): Rect | undefined
  /** GetPhysicalCursorPos：失败返回 undefined */
  getPhysicalCursorPos(): Point | undefined
  setCursorPos(x: number, y: number): boolean
  /** WindowFromPoint：返回命中窗口，0=无 */
  windowFromPoint(x: number, y: number): number

  // ---- 窗口枚举 / 属性 ----
  enumWindows(visit: (hwnd: number) => boolean): boolean
  enumChildWindows(parent: number, visit: (hwnd: number) => boolean): boolean
  isWindowVisible(hwnd: number): boolean
  getWindowText(hwnd: number): string
  getClassName(hwnd: number): string
  setForegroundWindow(hwnd: number): boolean
  bringWindowToTop(hwnd: number): boolean
  getForegroundWindow(): number
  getWindowThreadProcessId(hwnd: number): { thread: number; pid: number }
  attachThreadInput(current: number, target: number, attach: boolean): boolean
  isIconic(hwnd: number): boolean
  showWindow(hwnd: number, command: number): boolean
  setWindowPos(
    hwnd: number,
    insertAfter: number,
    x: number,
    y: number,
    cx: number,
    cy: number,
    flags: number,
  ): boolean
  getAncestor(hwnd: number, flags: number): number
  getWindow(hwnd: number, command: number): number

  // ---- 输入注入 ----
  /** mouse_event(MOUSEEVENTF_LEFTDOWN) */
  mouseEventLeftDown(): void
  /** mouse_event(MOUSEEVENTF_LEFTUP) */
  mouseEventLeftUp(): void
  /**
   * SendInput KEYEVENTF_UNICODE down+up 成对注入一个 UTF-16 code unit
   * （中文/emoji 按代理项逐半发送，与 win-click.ps1 的 ToCharArray 语义一致）。
   * 发送数 != 2 抛错（GetLastError 由绑定层附加）。
   */
  typeUnicode(codeUnit: number): void
  /**
   * mouse_event(MOUSEEVENTF_WHEEL)：deltaY 为有符号值（非 0，-120=向下一格）。
   * Win32 dwData 是 uint——负值 wrap 到无符号区间的责任在绑定层（cv-wheel.ps1 真机坑：
   * 负数直接按 uint 解释会静默变 0，完全不滚动），调用方一律传有符号值。
   */
  mouseEventWheel(deltaY: number): void

  // ---- 进程命令行（绑定模式 WMI/Get-CimInstance 的进程内替代）----
  /** CreateToolhelp32Snapshot 快照枚举全部进程 */
  snapshotProcesses(): ProcessEntry[]
  /**
   * 读目标进程 PEB 命令行（NtQueryInformationProcess + ReadProcessMemory）。
   * 打不开/读不到返回 undefined（调用方按“无法证明唯一性”fail-loud）。
   */
  readProcessCommandLine(pid: number): string | undefined

  // ---- 桌面探测（desktopCheck 进程内化）----
  /** OpenInputDesktop(0, false, 0x10000000)：0=不可交互（锁屏等） */
  openInputDesktop(): number
  closeDesktop(handle: number): boolean
}

/** 一次物理输入请求（与 win-click.ps1 参数一一对应） */
export interface PhysicalInputRequest {
  /** page 坐标 X（DOMSnapshot device px，起点） */
  x: number
  /** page 坐标 Y */
  y: number
  /** 视口宽（页面截图 device px，DPI 缩放换算分母） */
  cssW: number
  /** 视口高 */
  cssH: number
  /** 点击后逐字键入的文本（clickAndType）；不传则仅点击 */
  text?: string
  /** 拖动终点 page 坐标；与 dragY 必须同时提供 */
  dragX?: number
  dragY?: number
  /** 滚轮：每格 delta（非 0 整数，负=向下，单位=格×120）；提供时 op 为 wheel */
  wheelDeltaY?: number
  /** 滚轮格数（>=1；缺省 6，与 cv-wheel.ps1 默认一致） */
  notches?: number
  /** 绑定模式：调试端口 >0 时启用（按 cmdline 唯一性 + BossWindowExperiment 语义） */
  cdpPort?: number
  /** 非绑定模式窗口标题关键字 */
  titleKeyword?: string
  /** 计时行 op 字段（click/drag/clickAndType） */
  op?: string
  /**
   * 目标窗口定位（默认 Chrome 语义：标题找主窗 + Chrome_RenderWidgetHostHWND 面积最大子窗）。
   * 供真实输入测试等复用方以自有目标窗口驱动同一套守卫/序列。
   */
  resolveTarget?: (api: NativeApi) => { win: number; rw: number }
}

/** 输入结果（与 ps1 stdout 行对应的输出收集） */
export interface PhysicalInputResult {
  output: string[]
}

/** 守卫失败取证（只含原生几何/身份，不含标题与页面内容） */
export interface GuardDiagnostics {
  /** 光标坐标谓词：实际光标 == 期望点 */
  cursorMatch: boolean
  /** WindowFromPoint 归属谓词：命中窗口 == 目标 widget */
  ownershipMatch: boolean
  expectedX: number
  expectedY: number
  actualCursorX?: number
  actualCursorY?: number
  widget: number
  hit?: number
  foreground?: number
}

/** 日志 sink：默认写 stderr（与 [boss-perf]/[boss-op] 同通道） */
export type LogSink = (line: string) => void

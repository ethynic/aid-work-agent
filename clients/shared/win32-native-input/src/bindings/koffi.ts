/**
 * koffi FFI 绑定层：NativeApi 的生产实现（进程内直调 Win32，零子进程）。
 *
 * - 选型结论（2026-10-20 本机验证）：koffi 3.3.3，npm 可装、--ignore-scripts 下
 *   prebuild（@koromix/koffi-win32-x64）可直接加载，N-API 8（Node/Electron ABI 稳定）。
 * - 本文件是唯一触碰 koffi 的生产代码；上层（守卫/序列/计时）只依赖 NativeApi 接口，
 *   单元测试注入内存 fake 即可全覆盖，无需真实系统调用。
 * - 结构体名加 Wni 前缀，避免与同进程其他 koffi 使用方注册的类型冲突。
 */
import koffi from 'koffi'
import type { NativeApi, ProcessEntry, Rect } from '../types.js'

// ---- 结构体（x64 布局）----

const WniPOINT = koffi.struct('WniPOINT', { x: 'long', y: 'long' })
const WniRECT = koffi.struct('WniRECT', { left: 'long', top: 'long', right: 'long', bottom: 'long' })
const WniMOUSEINPUT = koffi.struct('WniMOUSEINPUT', {
  dx: 'long', dy: 'long', mouseData: 'uint32', dwFlags: 'uint32', time: 'uint32', dwExtraInfo: 'uintptr',
})
const WniKEYBDINPUT = koffi.struct('WniKEYBDINPUT', {
  wVk: 'uint16', wScan: 'uint16', dwFlags: 'uint32', time: 'uint32', dwExtraInfo: 'uintptr',
})
// 官方 INPUT 是联合体：type(4)+pad(4)+最大成员 MOUSEINPUT(32) = 40 字节（x64）。
// cbSize 必须是 40，否则 SendInput 拒绝。
const WniINPUTUNION = koffi.union('WniINPUTUNION', { mi: WniMOUSEINPUT, ki: WniKEYBDINPUT })
const WniINPUT = koffi.struct('WniINPUT', { type: 'uint32', pad: 'uint32', u: WniINPUTUNION })
const WniPROCESSENTRY32W = koffi.struct('WniPROCESSENTRY32W', {
  dwSize: 'uint32', cntUsage: 'uint32', th32ProcessID: 'uint32', th32DefaultHeapID: 'uintptr',
  th32ModuleID: 'uint32', cntThreads: 'uint32', th32ParentProcessID: 'uint32', pcPriClassBase: 'int32',
  dwFlags: 'uint32', szExeFile: koffi.array('char16_t', 260),
})
const WniEnumWindowsProc = koffi.proto('bool WniEnumWindowsProc(intptr hWnd, intptr lParam)')

// ---- 常量 ----

const MOUSEEVENTF_LEFTDOWN = 0x0002
const MOUSEEVENTF_LEFTUP = 0x0004
const INPUT_KEYBOARD = 1
const KEYEVENTF_KEYUP = 0x0002
const KEYEVENTF_UNICODE = 0x0004
const HWND_TOP = 0
const SWP_NOSIZE = 0x0001
const SWP_NOMOVE = 0x0002
const TH32CS_SNAPPROCESS = 0x00000002
const PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
const PROCESS_VM_READ = 0x0010
const PROCESS_BASIC_INFORMATION = 0
const DESKTOP_GENERIC_ALL = 0x10000000

/** 创建 koffi 实现的 NativeApi。dll 加载失败或符号缺失会抛错（调用方决定回退）。 */
export function createKoffiApi(): NativeApi {
  const user32 = koffi.load('user32.dll')
  const kernel32 = koffi.load('kernel32.dll')
  const ntdll = koffi.load('ntdll.dll')

  // DPI 线程上下文
  const setThreadDpiAwarenessContext = user32.func('intptr __stdcall SetThreadDpiAwarenessContext(intptr context)')
  const getThreadDpiAwarenessContext = user32.func('intptr __stdcall GetThreadDpiAwarenessContext()')
  const areDpiAwarenessContextsEqual = user32.func('bool __stdcall AreDpiAwarenessContextsEqual(intptr a, intptr b)')
  const getCurrentThreadId = kernel32.func('uint32 __stdcall GetCurrentThreadId()')

  // 几何 / 光标 / 命中
  const getWindowRect = user32.func('bool __stdcall GetWindowRect(intptr hWnd, _Out_ WniRECT *rect)')
  const getPhysicalCursorPos = user32.func('bool __stdcall GetPhysicalCursorPos(_Out_ WniPOINT *pt)')
  const setCursorPos = user32.func('bool __stdcall SetCursorPos(int x, int y)')
  const windowFromPoint = user32.func('intptr __stdcall WindowFromPoint(WniPOINT pt)')

  // 窗口枚举 / 属性
  const enumWindows = user32.func('bool __stdcall EnumWindows(WniEnumWindowsProc *cb, intptr lParam)')
  const enumChildWindows = user32.func('bool __stdcall EnumChildWindows(intptr hWnd, WniEnumWindowsProc *cb, intptr lParam)')
  const isWindowVisible = user32.func('bool __stdcall IsWindowVisible(intptr hWnd)')
  const getWindowTextW = user32.func('int __stdcall GetWindowTextW(intptr hWnd, _Out_ uint16_t *buf, int max)')
  const getClassNameW = user32.func('int __stdcall GetClassNameW(intptr hWnd, _Out_ uint16_t *buf, int max)')
  const setForegroundWindow = user32.func('bool __stdcall SetForegroundWindow(intptr hWnd)')
  const bringWindowToTop = user32.func('bool __stdcall BringWindowToTop(intptr hWnd)')
  const getForegroundWindow = user32.func('intptr __stdcall GetForegroundWindow()')
  const getWindowThreadProcessId = user32.func('uint32 __stdcall GetWindowThreadProcessId(intptr hWnd, _Out_ uint32_t *pid)')
  const attachThreadInput = user32.func('bool __stdcall AttachThreadInput(uint32_t current, uint32_t target, bool attach)')
  const isIconic = user32.func('bool __stdcall IsIconic(intptr hWnd)')
  const showWindow = user32.func('bool __stdcall ShowWindow(intptr hWnd, int command)')
  const setWindowPos = user32.func('bool __stdcall SetWindowPos(intptr hWnd, intptr after, int x, int y, int cx, int cy, uint32_t flags)')
  const getAncestor = user32.func('intptr __stdcall GetAncestor(intptr hWnd, uint32_t flags)')
  const getWindow = user32.func('intptr __stdcall GetWindow(intptr hWnd, uint32_t command)')

  // 输入注入
  const mouseEvent = user32.func('void __stdcall mouse_event(uint32_t flags, uint32_t dx, uint32_t dy, uint32_t data, uintptr_t extra)')
  const sendInput = user32.func('uint32 __stdcall SendInput(uint32_t n, WniINPUT *inputs, int cbSize)')
  const getLastError = kernel32.func('uint32 __stdcall GetLastError()')

  // 进程快照 / PEB 命令行
  const createToolhelp32Snapshot = kernel32.func('intptr __stdcall CreateToolhelp32Snapshot(uint32_t flags, uint32_t pid)')
  const process32FirstW = kernel32.func('bool __stdcall Process32FirstW(intptr snap, _Inout_ WniPROCESSENTRY32W *entry)')
  const process32NextW = kernel32.func('bool __stdcall Process32NextW(intptr snap, _Inout_ WniPROCESSENTRY32W *entry)')
  const openProcess = kernel32.func('intptr __stdcall OpenProcess(uint32_t access, bool inherit, uint32_t pid)')
  const closeHandle = kernel32.func('bool __stdcall CloseHandle(intptr handle)')
  const readProcessMemory = kernel32.func('bool __stdcall ReadProcessMemory(intptr handle, intptr base, _Out_ uint8_t *buf, size_t size, _Out_ size_t *read)')
  const ntQueryInformationProcess = ntdll.func('int32_t NtQueryInformationProcess(intptr handle, int32_t cls, _Out_ uint8_t *buf, size_t size, _Out_ size_t *ret)')

  // 桌面探测
  const openInputDesktop = user32.func('intptr __stdcall OpenInputDesktop(uint32_t flags, bool inherit, uint32_t access)')
  const closeDesktop = user32.func('bool __stdcall CloseDesktop(intptr handle)')

  function readWindowText(read: (hwnd: number, buf: Buffer, max: number) => number, hwnd: number): string {
    const buf = Buffer.alloc(512 * 2)
    const length = read(hwnd, buf, 512)
    return buf.toString('utf16le', 0, Math.max(0, length) * 2)
  }

  /** ReadProcessMemory：失败抛错（调用方归入 unreadable） */
  function rpm(handle: number, address: bigint, size: number): Buffer {
    const buf = Buffer.alloc(size)
    const read = [0n]
    if (!readProcessMemory(handle, address, buf, size, read)) throw new Error(`ReadProcessMemory failed @0x${address.toString(16)}`)
    return buf.subarray(0, Number(read[0]))
  }

  /** 读目标进程 PEB 的命令行（x64：PEB+0x20 → ProcessParameters，+0x70 → CommandLine） */
  function readCommandLine(pid: number): string | undefined {
    const handle = openProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, false, pid)
    if (handle === 0) return undefined
    try {
      const pbi = Buffer.alloc(48)
      if (ntQueryInformationProcess(handle, PROCESS_BASIC_INFORMATION, pbi, pbi.length, [0n]) !== 0) return undefined
      const peb = pbi.readBigUInt64LE(8)
      const params = rpm(handle, peb + 0x20n, 8).readBigUInt64LE(0)
      const unicodeString = rpm(handle, params + 0x70n, 16)
      const length = unicodeString.readUInt16LE(0)
      const buffer = unicodeString.readBigUInt64LE(8)
      if (length === 0 || buffer === 0n) return ''
      return rpm(handle, buffer, length).toString('utf16le')
    } catch {
      return undefined
    } finally {
      closeHandle(handle)
    }
  }

  const api: NativeApi = {
    setThreadDpiAwarenessContext: (context) => Number(setThreadDpiAwarenessContext(BigInt(context))),
    getThreadDpiAwarenessContext: () => Number(getThreadDpiAwarenessContext()),
    areDpiAwarenessContextsEqual: (a, b) => areDpiAwarenessContextsEqual(BigInt(a), BigInt(b)),
    getCurrentThreadId: () => Number(getCurrentThreadId()),

    getWindowRect: (hwnd): Rect | undefined => {
      const rect: Rect = { left: 0, top: 0, right: 0, bottom: 0 }
      if (!getWindowRect(hwnd, rect)) return undefined
      return rect
    },
    getPhysicalCursorPos: () => {
      const point = { x: 0, y: 0 }
      if (!getPhysicalCursorPos(point)) return undefined
      return { x: point.x, y: point.y }
    },
    setCursorPos: (x, y) => setCursorPos(x, y),
    windowFromPoint: (x, y) => Number(windowFromPoint({ x, y })),

    enumWindows: (visit) => enumWindows((hwnd: bigint | number) => visit(Number(hwnd)), 0),
    enumChildWindows: (parent, visit) => enumChildWindows(parent, (hwnd: bigint | number) => visit(Number(hwnd)), 0),
    isWindowVisible: (hwnd) => isWindowVisible(hwnd),
    getWindowText: (hwnd) => readWindowText((h, buf, max) => getWindowTextW(h, buf, max), hwnd),
    getClassName: (hwnd) => readWindowText((h, buf, max) => getClassNameW(h, buf, max), hwnd),
    setForegroundWindow: (hwnd) => setForegroundWindow(hwnd),
    bringWindowToTop: (hwnd) => bringWindowToTop(hwnd),
    getForegroundWindow: () => Number(getForegroundWindow()),
    getWindowThreadProcessId: (hwnd) => {
      const pid = [0]
      const thread = getWindowThreadProcessId(hwnd, pid)
      return { thread: Number(thread), pid: pid[0]! }
    },
    attachThreadInput: (current, target, attach) => attachThreadInput(current, target, attach),
    isIconic: (hwnd) => isIconic(hwnd),
    showWindow: (hwnd, command) => showWindow(hwnd, command),
    setWindowPos: (hwnd, insertAfter, x, y, cx, cy, flags) =>
      setWindowPos(hwnd, BigInt(insertAfter), x, y, cx, cy, flags),
    getAncestor: (hwnd, flags) => Number(getAncestor(hwnd, flags)),
    getWindow: (hwnd, command) => Number(getWindow(hwnd, command)),

    mouseEventLeftDown: () => mouseEvent(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0n),
    mouseEventLeftUp: () => mouseEvent(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0n),
    typeUnicode: (codeUnit) => {
      // KEYEVENTF_UNICODE down+up 成对（wScan 16 位装下中文/代理项；§17 坑10 的「禁 SendInput」
      // 仅指鼠标绝对坐标，键盘无多显示器 VIRTUALDESK 问题——与 ps1 注释一致）
      const inputs = [
        { type: INPUT_KEYBOARD, u: { ki: { wVk: 0, wScan: codeUnit, dwFlags: KEYEVENTF_UNICODE, time: 0, dwExtraInfo: 0n } } },
        { type: INPUT_KEYBOARD, u: { ki: { wVk: 0, wScan: codeUnit, dwFlags: KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time: 0, dwExtraInfo: 0n } } },
      ]
      const sent = Number(sendInput(inputs.length, inputs, koffi.sizeof(WniINPUT)))
      if (sent !== inputs.length) throw new Error(`SendInput 键入失败（GetLastError=${getLastError()}）`)
    },

    snapshotProcesses: (): ProcessEntry[] => {
      const snapshot = createToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
      if (snapshot === 0) throw new Error('CreateToolhelp32Snapshot failed')
      try {
        const entries: ProcessEntry[] = []
        const entry: { dwSize: number; th32ProcessID: number; szExeFile: string } = { dwSize: koffi.sizeof(WniPROCESSENTRY32W), th32ProcessID: 0, szExeFile: '' }
        if (process32FirstW(snapshot, entry)) {
          do {
            entries.push({
              pid: entry.th32ProcessID,
              exeName: String(entry.szExeFile).replace(/\0.*$/s, ''),
            })
          } while (process32NextW(snapshot, entry))
        }
        return entries
      } finally {
        closeHandle(snapshot)
      }
    },
    readProcessCommandLine: (pid) => readCommandLine(pid),

    openInputDesktop: () => Number(openInputDesktop(0, false, DESKTOP_GENERIC_ALL)),
    closeDesktop: (handle) => closeDesktop(handle),
  }
  return api
}

/** 供测试/复用方使用的常量重导出（与 ps1 语义一致） */
export const WIN32_INPUT_CONSTANTS = { HWND_TOP, SWP_NOSIZE, SWP_NOMOVE } as const

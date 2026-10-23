/**
 * 锁屏/不可交互桌面检测（MVP 版 + Phase 1 进程内化）。
 *
 * 双通道（与 boss-resume-assistant 的 Win32 物理输入共用一个开关）：
 * - native（默认）：惰性动态 import 共享包 @aid/win32-native-input（koffi）
 *   单调用 OpenInputDesktop —— 失败（返回 0）视为锁屏/不可交互桌面。
 *   缺包（host-core 零运行时依赖，不声明它）或 koffi 加载失败时回退 PowerShell。
 * - powershell：原路径（起 PowerShell + Add-Type 编译 P/Invoke），完整保留。
 * 环境变量 AID_BOSS_INPUT=native|powershell 控制通道，未设置默认 native。
 *
 * 触发点：doctor 必检 + 每次 invocation 开始前检一次。
 * 检测本身出错（两种通道都不可用等）按不可交互处理（写动作宁可拒绝不可误动）。
 */
import { execFile } from 'node:child_process'

const CHECK_SCRIPT = [
  // C# 源放 PS 单引号字符串内（双引号经 argv 转义后还原，不受 -Command 解析影响）
  '$def = \'[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess); [System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool CloseDesktop(System.IntPtr hDesktop);\'',
  'Add-Type -Namespace Win32 -Name InputDesktop -MemberDefinition $def',
  '$h = [Win32.InputDesktop]::OpenInputDesktop(0, $false, 0x10000000)',
  'if ($h -eq [System.IntPtr]::Zero) { [Console]::Out.Write("0") } else { [Win32.InputDesktop]::CloseDesktop($h) | Out-Null; [Console]::Out.Write("1") }',
].join('; ')

export interface DesktopCheckResult {
  interactive: boolean
  /** 检测失败原因（interactive=false 且非锁屏场景时给出） */
  error?: string
}

/**
 * 原生探测的最小接口（@aid/win32-native-input 的子集）。
 * 用结构化声明避免编译期依赖：host-core 不引入 koffi（零运行时依赖约束）。
 */
interface NativeInputApi {
  openInputDesktop(): number
  closeDesktop(handle: number): boolean
}
interface NativeInputDesktopModule {
  createKoffiApi(): NativeInputApi
  probeInputDesktop(api: NativeInputApi): { interactive: boolean }
}

/** 已加载的原生能力（null = 不可用，本进程不再重试；成功则复用同一 API 实例） */
let nativeLoaded: Promise<{ module: NativeInputDesktopModule; api: NativeInputApi } | null> | null = null

/** 通道选择：显式 powershell 返回 false；未设置/native 返回 true；非法值 fail-loud */
function nativeChannelSelected(env: NodeJS.ProcessEnv = process.env): boolean {
  const value = env.AID_BOSS_INPUT
  if (value === undefined || value === '') return true
  if (value === 'powershell') return false
  if (value === 'native') return true
  throw new Error(`AID_BOSS_INPUT 取值必须是 native|powershell，当前: ${value}`)
}

/** 惰性加载原生能力；不可用（缺包/非 win32/koffi 失败）返回 null 并记录一次 */
function loadNativeDesktopProbe(): Promise<{ module: NativeInputDesktopModule; api: NativeInputApi } | null> {
  if (nativeLoaded === null) {
    nativeLoaded = (async () => {
      if (process.platform !== 'win32') return null
      // 字面量动态 import（依赖边界扫描要求可静态扫描；包已声明为 file: 依赖可编译期解析）。
      // 运行时缺包/加载失败的回退语义不变：抛错进下方 catch 走 PowerShell
      const mod = await import('@aid/win32-native-input') as unknown as NativeInputDesktopModule
      // 构造即加载 dll（koffi 版本不符/dll 缺失在此抛错，进 catch 回退）
      const api = mod.createKoffiApi()
      return { module: mod, api }
    })().catch((error: unknown) => {
      const reason = error instanceof Error ? error.message : String(error)
      try { process.stderr.write(`[host-desktop-check] 原生通道不可用，回退 PowerShell: ${reason}\n`) } catch { /* 日志失败不影响业务 */ }
      return null
    })
  }
  return nativeLoaded
}

export function checkDesktopInteractive(): Promise<DesktopCheckResult> {
  if (process.platform !== 'win32') {
    // 非 Windows：无锁屏概念（Runtime 目标平台为 win32，开发/测试放行）
    return Promise.resolve({ interactive: true })
  }
  if (nativeChannelSelected()) {
    return loadNativeDesktopProbe().then((loaded) => {
      if (loaded === null) return runPowerShellCheck()
      // OpenInputDesktop 单调用：0 = 不可交互（锁屏等）；不视为检测失败
      return loaded.module.probeInputDesktop(loaded.api)
    })
  }
  return runPowerShellCheck()
}

/** 原 PowerShell+Add-Type 路径（保留为回退，逻辑不变） */
function runPowerShellCheck(): Promise<DesktopCheckResult> {
  return new Promise((resolve) => {
    execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', CHECK_SCRIPT],
      { timeout: 15_000 },
      (err, stdout) => {
        if (err) {
          resolve({ interactive: false, error: `桌面检测失败: ${err.message}` })
          return
        }
        resolve({ interactive: stdout.trim() === '1' })
      },
    )
  })
}

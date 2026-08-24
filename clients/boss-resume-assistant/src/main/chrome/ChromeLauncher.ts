/**
 * 调试 Chrome 自动拉起（ChromeAttacher 决策 6 修订二，2026-08-24）。
 *
 * 背景：CLI 原则「绝不启动/杀死任何 Chrome 进程、绝不触碰任何 profile」源于
 * 「spawn 全新临时 profile 扫码登录触发 BOSS 风控封号」的教训——但临时 profile 的
 * 风控根源是「每次全新设备指纹 + 无登录态」。本模块拉起的是**固定持久 profile**
 * （默认 C:\chrome-debug，与部署手册快捷方式完全同一目录、同一参数），指纹与登录态
 * 跨次稳定，与用户手动双击快捷方式等价，不属当初禁区。
 *
 * 仍保留的铁律：
 * - 只在调试端口连不上时拉起，绝不杀任何 Chrome 进程（哪怕端口被别的程序占用失败也不动）
 * - 绝不触碰用户日常 Chrome profile
 * - fail-open：任何一步失败（找不到 chrome.exe / spawn 失败 / 端口等待超时）都静默
 *   返回 false，让调用方继续走原连接路径报原有错误——本模块绝不引入新的失败形态
 */
import { execSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { spawn } from 'node:child_process'

/** 固定持久 profile 目录（与 clients/README.md §四、release 安装手册的快捷方式参数一致） */
export const DEFAULT_DEBUG_PROFILE_DIR = 'C:\\chrome-debug'

/** 拉起后打开的初始页（attach 逻辑按 zhipin.com 识别页面） */
export const DEFAULT_INITIAL_URL = 'https://www.zhipin.com'

/** 端口就绪等待上限：覆盖冷启动慢的机器 */
const READY_TIMEOUT_MS = 15_000
const PROBE_INTERVAL_MS = 500

export interface ChromeLauncherDeps {
  /** 注入探测（测试） */
  fetchImpl?: typeof fetch
  /** 注入注册表查询（测试）；返回 undefined 表示查询失败或无值 */
  queryAppPaths?: (hive: string) => string | undefined
  /** 注入文件存在检查（测试） */
  existsSyncImpl?: (p: string) => boolean
  /** 注入 spawn（测试）；只需返回带 unref 的对象 */
  spawnImpl?: (exe: string, args: string[]) => { unref(): void }
  /** 注入 sleep（测试） */
  sleep?: (ms: number) => Promise<void>
}

/** 解析 reg query 输出中的 REG_SZ 默认值路径 */
function parseRegPath(output: string): string | undefined {
  // 输出形如：
  //   HKEY_CURRENT_USER\...\chrome.exe
  //       (默认)    REG_SZ    C:\Program Files\...\chrome.exe
  for (const line of output.split('\n')) {
    const m = line.match(/REG_SZ\s+(.+)$/)
    const p = m?.[1]?.trim()
    if (p && p.toLowerCase().endsWith('chrome.exe')) return p
  }
  return undefined
}

/** 查 chrome.exe：App Paths 注册表（HKCU → HKLM 64 位 → Wow6432Node）→ 常见安装路径 */
export function findChromeExe(deps: ChromeLauncherDeps = {}): string | undefined {
  const query = deps.queryAppPaths
    ?? ((hive: string): string | undefined => {
        try {
          return parseRegPath(execSync(`reg query "${hive}" /ve`, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }))
        } catch {
          return undefined
        }
      })
  const exists = deps.existsSyncImpl ?? existsSync
  const hives = [
    'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe',
    'HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe',
    'HKLM\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe',
  ]
  for (const hive of hives) {
    const p = query(hive)
    if (p && exists(p)) return p
  }
  // 注册表无果 → 常见路径兜底（LOCALAPPDATA 是用户级安装位置，无管理员权限也常装在这）
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    `${process.env.LOCALAPPDATA ?? ''}\\Google\\Chrome\\Application\\chrome.exe`,
  ]
  return candidates.find((p) => p && exists(p))
}

/** 端口 /json/version 是否可达（超时或任何异常都视为不可达） */
async function probePort(port: number, fetchImpl: typeof fetch): Promise<boolean> {
  try {
    // 2s 超时：端口被非 Chrome 程序占用（接受 TCP 但不应答）时不能挂死整个 operation
    const r = await fetchImpl(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(2000) })
    return r.ok
  } catch {
    return false
  }
}

/**
 * 确保「带调试端口的 Chrome」在运行：端口通 → 什么都不做；不通 → 拉起固定 profile
 * 调试实例并等待端口就绪。
 *
 * @returns 是否由本次调用拉起（true=刚拉起；false=本来就在跑，或拉起失败——调用方
 *          继续原连接流程即可，失败会在原路径报出原有错误）
 */
export async function ensureDebugChrome(
  port: number,
  opts: { profileDir?: string; initialUrl?: string } = {},
  deps: ChromeLauncherDeps = {},
): Promise<boolean> {
  // 显式退出开关：测试隔离（mcp 测试子进程设 0 防真拉 Chrome）与用户偏好「我自己管理 Chrome」两用
  if (process.env.AID_BOSS_AUTO_CHROME === '0') return false
  const fetchImpl = deps.fetchImpl ?? fetch
  const doSpawn = deps.spawnImpl
    ?? ((exe: string, args: string[]): { unref(): void } => {
        // detached + ignore：CLI 退出不带走 Chrome；Chrome 独立成会话在用户桌面正常显示
        const child = spawn(exe, args, { detached: true, stdio: 'ignore' })
        child.unref()
        return child
      })
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))

  try {
    if (await probePort(port, fetchImpl)) return false
    const exe = findChromeExe(deps)
    if (!exe) return false
    doSpawn(exe, [
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${opts.profileDir ?? DEFAULT_DEBUG_PROFILE_DIR}`,
      opts.initialUrl ?? DEFAULT_INITIAL_URL,
    ])
    const deadline = Date.now() + READY_TIMEOUT_MS
    while (Date.now() < deadline) {
      await sleep(PROBE_INTERVAL_MS)
      if (await probePort(port, fetchImpl)) return true
    }
    return false
  } catch {
    return false // fail-open：绝不因本模块让 operation 多一种失败形态
  }
}

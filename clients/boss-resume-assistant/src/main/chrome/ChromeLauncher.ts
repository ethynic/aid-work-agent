/**
 * Chrome 启动器（设计文档 §5.1）。
 * - child_process.spawn 启动系统 Chrome，独立临时 profile，不保存登录态
 * - --remote-debugging-port=0 让 Chrome 自选端口；只读 DevToolsActivePort 文件获取端口
 * - 用户在客户端确认"登录完成"前，绝不建立 CDP WebSocket（设计 §5.2 手动登录门禁）
 * - 应用退出时 kill 本进程启动的 Chrome 并清理临时 profile
 *
 * fail-loud：找不到 Chrome / DevToolsActivePort 超时 → 抛错，不静默降级。
 */
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** BOSS 登录页（设计 §5.1） */
export const BOSS_LOGIN_URL = 'https://www.zhipin.com/web/user/?ka=header-login'

export interface ChromeInstance {
  pid: number
  /** Chrome 自选调试端口 */
  port: number
  /** http 端点，形如 http://127.0.0.1:{port} */
  httpEndpoint: string
  /** 本次会话独立 profile 目录 */
  profileDir: string
}

export interface ChromeLauncherOptions {
  /** 候选 chrome.exe 路径（测试注入；默认走 Windows 常见安装路径探测） */
  chromePathCandidates?: string[]
  /** profile 根目录（默认 os.tmpdir()/boss-resume-chrome） */
  profileRoot?: string
  /** 登录 URL（默认 BOSS_LOGIN_URL） */
  loginUrl?: string
  /** 等待 DevToolsActivePort 出现的超时 ms（默认 15000） */
  devToolsPortTimeoutMs?: number
  /** 轮询间隔 ms（默认 200） */
  pollIntervalMs?: number
  /** 可注入 spawn（测试） */
  spawnImpl?: typeof spawn
}

/** Windows 常见 Chrome 安装路径 */
function defaultChromeCandidates(): string[] {
  const pf = process.env['ProgramFiles'] ?? 'C:\\Program Files'
  const pfx86 = process.env['ProgramFiles(x86)'] ?? 'C:\\Program Files (x86)'
  const local = process.env['LOCALAPPDATA'] ?? path.join(os.homedir(), 'AppData', 'Local')
  return [
    path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    path.join(pfx86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ]
}

/** 探测系统 Chrome 路径，找不到 fail-loud */
export function findChromeExecutable(candidates?: string[]): string {
  const list = candidates ?? defaultChromeCandidates()
  for (const p of list) {
    try {
      if (p && fs.existsSync(p)) return p
    } catch {
      // 单个路径探测失败继续
    }
  }
  throw new Error(`未找到 Chrome 可执行文件（已探测 ${list.length} 个常见路径），请安装 Chrome 或配置路径`)
}

/** 解析 DevToolsActivePort 文件内容：第一行端口，第二行 ws 路径 */
export function parseDevToolsActivePort(content: string): { port: number } {
  const firstLine = content.split('\n')[0]?.trim() ?? ''
  const port = Number(firstLine)
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error(`DevToolsActivePort 内容非法: ${JSON.stringify(firstLine)}`)
  }
  return { port }
}

export class ChromeLauncher {
  private child: ChildProcess | null = null
  private instance: ChromeInstance | null = null

  constructor(private readonly opts: ChromeLauncherOptions = {}) {}

  /** 是否由本启动器启动了 Chrome 且尚未退出 */
  get isRunning(): boolean {
    return this.child !== null && this.child.exitCode === null
  }

  get current(): ChromeInstance | null {
    return this.instance
  }

  /**
   * 启动 Chrome（可见窗口、登录 URL、独立临时 profile）。
   * 只读 DevToolsActivePort 拿端口；返回后 Chrome 处于完全未连接状态，
   * 等待用户手动登录并在客户端点击确认（设计 §5.2）。
   */
  async launch(): Promise<ChromeInstance> {
    if (this.isRunning && this.instance) {
      throw new Error('Chrome 已在运行，不能重复启动')
    }
    const chromePath = findChromeExecutable(this.opts.chromePathCandidates)
    const profileRoot = this.opts.profileRoot ?? path.join(os.tmpdir(), 'boss-resume-chrome')
    const profileDir = path.join(profileRoot, `session-${Date.now()}`)
    fs.mkdirSync(profileDir, { recursive: true })

    const spawnImpl = this.opts.spawnImpl ?? spawn
    const child = spawnImpl(
      chromePath,
      [
        '--remote-debugging-port=0',
        `--user-data-dir=${profileDir}`,
        '--no-first-run',
        '--no-default-browser-check',
        '--new-window',
        this.opts.loginUrl ?? BOSS_LOGIN_URL,
      ],
      { stdio: 'ignore', detached: false },
    )
    child.on('error', () => {
      // spawn 失败（如权限）通过 DevToolsActivePort 超时统一 fail-loud
    })
    this.child = child

    let port: number
    try {
      port = await this.waitForDevToolsPort(profileDir)
    } catch (e) {
      // 启动失败必须回收已 spawn 的 Chrome：否则进程残留且 this.child 被下次
      // launch 覆盖后永远无法 kill（进程泄漏 + 临时 profile 残留）
      this.child = null
      try {
        child.kill()
      } catch {
        // 进程可能已退出
      }
      try {
        fs.rmSync(profileDir, { recursive: true, force: true })
      } catch {
        // 清理失败不阻塞报错
      }
      throw e
    }
    const instance: ChromeInstance = {
      pid: child.pid ?? 0,
      port,
      httpEndpoint: `http://127.0.0.1:${port}`,
      profileDir,
    }
    this.instance = instance
    child.on('exit', () => {
      this.child = null
    })
    return instance
  }

  /** 轮询等待 Chrome 写入 DevToolsActivePort 并解析端口。超时 fail-loud。 */
  private async waitForDevToolsPort(profileDir: string): Promise<number> {
    const timeoutMs = this.opts.devToolsPortTimeoutMs ?? 15000
    const interval = this.opts.pollIntervalMs ?? 200
    const file = path.join(profileDir, 'DevToolsActivePort')
    const deadline = Date.now() + timeoutMs
    for (;;) {
      try {
        if (fs.existsSync(file)) {
          const content = fs.readFileSync(file, 'utf8')
          return parseDevToolsActivePort(content).port
        }
      } catch {
        // 文件刚写入未就绪，继续轮询
      }
      if (Date.now() > deadline) {
        throw new Error(`等待 Chrome DevToolsActivePort 超时（${timeoutMs}ms）：${file}`)
      }
      await new Promise((r) => setTimeout(r, interval))
    }
  }

  /**
   * 关闭本启动器启动的 Chrome 并清理临时 profile。
   * 只 kill 自己 spawn 的进程，绝不动用户自行启动的 Chrome。
   */
  async dispose(): Promise<void> {
    const child = this.child
    this.child = null
    if (child && child.exitCode === null) {
      try {
        child.kill()
      } catch {
        // 进程可能已退出
      }
    }
    const instance = this.instance
    this.instance = null
    if (instance) {
      // profile 清理等 Chrome 完全退出后再做，最多等 3s
      const deadline = Date.now() + 3000
      while (child && child.exitCode === null && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 100))
      }
      try {
        fs.rmSync(instance.profileDir, { recursive: true, force: true })
      } catch {
        // 清理失败不阻塞退出（临时目录，下次启动新建）
      }
    }
  }
}

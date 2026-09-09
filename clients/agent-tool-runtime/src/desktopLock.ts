/**
 * 桌面资源互斥锁（设计 §5 / R24：同机（同 Windows 用户+交互会话）全部 Provider 排队
 * 互斥，多 Runtime 实例共享 OS 锁，锁标识不依赖云端 device_id）。
 *
 * 实现：Windows 命名管道 `\\.\pipe\AidWorkAgent.DesktopResource.<resource_key 派生名>`
 * （复用 weixin-cli namedMutex 已验证模式：net.createServer listen 管道名成功即持有，
 * EADDRINUSE 即被其它进程占用；持有进程退出/崩溃时句柄被 OS 回收，名称自动释放）。
 *
 * - acquire 在超时窗口内重试等待（进程内并发同样经 listen EADDRINUSE 排队，天然互斥）
 * - withDesktopLock(fn)：持有期间执行 fn，结束/异常必释放（try/finally）
 * - 非 win32 仅开发/测试可能跑到，退化为 tmpdir 下 unix socket（占用前清理陈旧文件）
 */
import net from 'node:net'
import { createHash } from 'node:crypto'
import { unlinkSync } from 'node:fs'
import os, { tmpdir } from 'node:os'
import { join } from 'node:path'

const PIPE_PREFIX = '\\\\.\\pipe\\AidWorkAgent.DesktopResource.'
/** resource_key 派生名为 sha256 hex（64 位 [0-9a-f]），管道名只允许该安全字符集 */
const NAME_PATTERN = /^[A-Za-z0-9_-]+$/

/** 仲裁等待默认超时（宪章：常量即可，如 120s；等待超时按未提交处理回 DESKTOP_RESOURCE_BUSY） */
export const DESKTOP_LOCK_DEFAULT_TIMEOUT_MS = 120_000
/** 占用重试间隔 */
const RETRY_INTERVAL_MS = 25

/** 等待桌面资源锁超时（调用方映射 DESKTOP_RESOURCE_BUSY 终态，effect=none 可重试） */
export class DesktopLockTimeoutError extends Error {
  constructor(readonly lockName: string, readonly timeoutMs: number) {
    super(`等待桌面资源锁超时（${timeoutMs}ms）: ${lockName}`)
    this.name = 'DesktopLockTimeoutError'
  }
}

/** 锁名（resource_key 已是安全字符集时直接拼接；win32 管道 / 其它平台 unix socket） */
export function desktopLockName(resourceKey: string, platform: NodeJS.Platform = process.platform): string {
  if (!NAME_PATTERN.test(resourceKey)) throw new Error(`非法桌面锁 resource_key：${resourceKey}`)
  if (platform === 'win32') return `${PIPE_PREFIX}${resourceKey}`
  return join(tmpdir(), `AidWorkAgent.DesktopResource.${resourceKey}.sock`)
}

/**
 * resource_key 派生：sha256(hostname | 用户名 | 会话 id)（R24：不含 device_id——
 * 命名管道是本机作用域，同一 Windows 用户 + 交互会话的桌面恒为同一把锁，不因云端
 * 配对出不同 device_id 而分裂）。会话 id 缺省空（多会话隔离需求出现时经
 * AIDWORK_DESKTOP_SESSION_ID 注入，测试栈以显式 sessionId 参数隔离），
 * 只输出 hash，不外发明文分量。
 */
export function deriveResourceKey(sessionId?: string): string {
  const sid = sessionId ?? process.env.AIDWORK_DESKTOP_SESSION_ID ?? ''
  return createHash('sha256')
    .update(`${os.hostname()}|${os.userInfo().username}|${sid}`)
    .digest('hex')
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 单次 listen 尝试：成功返回 server；被占用返回 null；其它错误抛出 */
function tryListenOnce(name: string, platform: NodeJS.Platform): Promise<net.Server | null> {
  return new Promise((resolve, reject) => {
    const server = net.createServer((sock) => sock.destroy())
    const onError = (err: NodeJS.ErrnoException) => {
      if (err.code === 'EADDRINUSE') {
        resolve(null)
      } else {
        // 非占用的 listen 失败：best-effort 关闭，避免半初始化 server 泄漏（未 listen 时 close 安全为空操作）
        server.close(() => {})
        reject(err)
      }
    }
    server.once('error', onError)
    server.listen(name, () => {
      server.removeListener('error', onError)
      // 常驻 error 监听：listen 成功后的运行期异步 error 若无人监听会变 unhandled 拖崩进程；
      // 占位语义下无需处理，最坏退化为本进程丢失互斥而非进程崩溃
      server.on('error', () => {})
      resolve(server)
    })
  })
}

/** unix socket 持有者活性探测：可连上=有活跃持有者；连接失败=残留文件（可清理） */
function isUnixSocketAlive(name: string): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = net.connect(name)
    const finish = (alive: boolean) => {
      sock.destroy()
      resolve(alive)
    }
    sock.once('connect', () => finish(true))
    sock.once('error', () => finish(false))
    // 持有者挂起不 accept 时保守按占用处理
    setTimeout(() => finish(true), 500).unref()
  })
}

async function acquireServer(
  name: string,
  platform: NodeJS.Platform,
  deadline: number,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<net.Server> {
  for (;;) {
    if (signal?.aborted) throw new Error(`桌面锁等待被中止: ${name}`)
    const server = await tryListenOnce(name, platform)
    if (server) {
      // 不拖住事件循环：锁仅是占位句柄，不对外提供服务
      server.unref()
      return server
    }
    if (Date.now() >= deadline) throw new DesktopLockTimeoutError(name, timeoutMs)
    // unix socket：listen 撞上残留文件时先探活性，仅清理确认无持有者的陈旧文件
    //（盲删会破坏与活跃持有者的互斥语义；win32 命名管道无文件形态，无需处理）
    if (platform !== 'win32') {
      if (!(await isUnixSocketAlive(name))) {
        try {
          unlinkSync(name)
        } catch {
          /* 已被清理则忽略 */
        }
      }
    }
    await sleep(RETRY_INTERVAL_MS)
  }
}

function releaseServer(server: net.Server): Promise<void> {
  return new Promise((resolve) => {
    try {
      server.close(() => resolve())
    } catch {
      resolve()
    }
  })
}

/**
 * 持有桌面资源锁执行 fn：等待超时抛 DesktopLockTimeoutError；fn 结束/异常后必释放。
 * 同进程并发调用同一锁名同样排队（listen 竞争），不并行执行。
 */
export async function withDesktopLock<T>(
  lockName: string,
  fn: () => Promise<T>,
  opts: { timeoutMs?: number; signal?: AbortSignal; platform?: NodeJS.Platform } = {},
): Promise<T> {
  const timeoutMs = opts.timeoutMs ?? DESKTOP_LOCK_DEFAULT_TIMEOUT_MS
  const platform = opts.platform ?? process.platform
  const deadline = Date.now() + timeoutMs
  const server = await acquireServer(lockName, platform, deadline, timeoutMs, opts.signal)
  try {
    return await fn()
  } finally {
    await releaseServer(server)
  }
}

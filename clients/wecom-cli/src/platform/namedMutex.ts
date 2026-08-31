/**
 * 跨进程互斥（一个进程内单飞，加跨进程命名对象）。
 *
 * 实现：Windows 命名管道 `\\.\pipe\AidWorkAgent.AidWecom.<scope>`。
 * net.createServer().listen(管道名) 成功即持有；EADDRINUSE 即被其它进程占用；
 * 持有进程死亡（含崩溃）时管道句柄被 OS 回收，名称自动释放——
 * 这正是不能用「创建锁文件」方案的原因（进程崩溃会残留锁文件）。
 *
 * 非 win32 仅在开发/测试时可能跑到，退化为 tmpdir 下 unix socket（占用前清理陈旧文件）。
 *
 * 占用期间不对外提供任何服务：收到连接立即销毁。server.unref() 保证不拖住事件循环。
 */
import net from 'node:net'
import { unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const PIPE_PREFIX = '\\\\.\\pipe\\AidWorkAgent.AidWecom.'
const SCOPE_PATTERN = /^[A-Za-z0-9_-]+$/

/** 互斥 scope：默认 session（保护交互会话/前台这一共享资源）；测试可用 env 覆盖做并行隔离 */
export function resolveMutexScope(): string {
  return process.env.AID_WECOM_MUTEX_SCOPE || 'session'
}

export function mutexPath(scope: string, platform: NodeJS.Platform = process.platform): string {
  if (!SCOPE_PATTERN.test(scope)) throw new Error(`非法 mutex scope：${scope}`)
  if (platform === 'win32') return `${PIPE_PREFIX}${scope}`
  return join(tmpdir(), `AidWorkAgent.AidWecom.${scope}.sock`)
}

export class NamedMutex {
  private server: net.Server | null = null

  constructor(
    readonly scope: string,
    private readonly platform: NodeJS.Platform = process.platform,
  ) {
    if (!SCOPE_PATTERN.test(scope)) throw new Error(`非法 mutex scope：${scope}`)
  }

  get held(): boolean {
    return this.server !== null
  }

  /**
   * 尝试占用。成功 true；已被占用（EADDRINUSE）false；其它错误抛出（由上层映射 INTERNAL_ERROR）。
   * 幂等：已持有时重复调用直接返回 true。
   */
  async acquire(): Promise<boolean> {
    if (this.server) return true
    const path = mutexPath(this.scope, this.platform)
    if (this.platform !== 'win32') {
      // unix socket 崩溃后残留文件，占用前 best-effort 清理（仅非 win32 的开发/测试路径）
      try {
        unlinkSync(path)
      } catch {
        /* 不存在则忽略 */
      }
    }
    const server = net.createServer((sock) => sock.destroy())
    const acquired = await new Promise<boolean>((resolve, reject) => {
      const onError = (err: NodeJS.ErrnoException) => {
        if (err.code === 'EADDRINUSE') {
          resolve(false)
        } else {
          // 非占用的 listen 失败：best-effort 关闭，避免半初始化 server 泄漏（未 listen 时 close 安全为空操作）
          server.close(() => {})
          reject(err)
        }
      }
      server.once('error', onError)
      server.listen(path, () => {
        server.removeListener('error', onError)
        // 常驻 error 监听：listen 成功后的运行期异步 error 若无人监听会变 unhandled，
        // 直接崩溃整个 MCP server 进程。占位语义下无需处理——进程内单飞仍兜底，
        // 最坏情况退化为本进程丢失跨进程互斥而非进程崩溃。
        server.on('error', () => {})
        resolve(true)
      })
    })
    if (!acquired) return false
    server.unref()
    this.server = server
    return true
  }

  /** 释放（无连接时立即完成）；幂等 */
  async release(): Promise<void> {
    const server = this.server
    this.server = null
    if (!server) return
    // 连接在 createServer 回调里即被 destroy，正常无挂起连接，close 立即完成
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
}

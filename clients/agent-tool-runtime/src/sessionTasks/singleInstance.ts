/**
 * 会话任务单实例保护（C2，设计 §7）。
 *
 * 按 runtime home / Windows 用户会话使用 OS 级命名管道互斥：同机第二个实例
 * acquire 返回 null（不得重放同一本地任务日志）；持有进程死亡 OS 自动回收。
 * 与既有 desktopLock 同一「listen 占位」模式（复刻自 desktopLock.ts:62-83）。
 */
import { createServer, type Server } from 'node:net'
import { createHash } from 'node:crypto'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export interface SingleInstanceGuard {
  release(): Promise<void>
}

function pipeNameFor(runtimeHome: string, platform: NodeJS.Platform = process.platform): string {
  const scope = createHash('sha256').update(runtimeHome).digest('hex').slice(0, 16)
  if (platform === 'win32') return `\\\\.\\pipe\\AidWorkAgent.SessionTasks.${scope}`
  // 非 win32（CI/开发）：unix socket 放 tmpdir
  const { tmpdir } = require('node:os') as typeof import('node:os')
  const { join } = require('node:path') as typeof import('node:path')
  return join(tmpdir(), `aidwork-session-tasks-${scope}.sock`)
}

async function tryListenOnce(name: string, platform: NodeJS.Platform): Promise<Server | null> {
  const server = createServer()
  server.unref()
  return new Promise((resolve) => {
    server.once('error', () => resolve(null)) // EADDRINUSE / 权限 → 已有实例
    server.listen(name, () => resolve(server))
    void platform
  })
}

/** 获取单实例锁；已有实例持有返回 null（调用方必须拒绝启动会话任务引擎）。 */
export async function acquireSessionTasksSingleInstance(
  runtimeHome: string,
  platform: NodeJS.Platform = process.platform,
): Promise<SingleInstanceGuard | null> {
  const server = await tryListenOnce(pipeNameFor(runtimeHome, platform), platform)
  if (server === null) return null
  server.on('error', () => {
    /* 常驻监听防 unhandled（持有期管道异常不中断进程） */
  })
  return {
    async release(): Promise<void> {
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
  }
}

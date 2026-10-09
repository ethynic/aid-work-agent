/** CLI-only compatibility paths; the shared Host has no implicit business package. */
export * from '@aid/local-tool-host-core/legacy/config'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { RuntimeConfig } from '@aid/local-tool-host-core/legacy/config'

/**
 * 默认 boss CLI 入口。优先级：
 * 1. 随包捆绑的依赖：node_modules/boss-resume-assistant（npm 全局安装 tgz 的场景）
 * 2. 开发仓库兄弟目录：../boss-resume-assistant（源码构建直接跑的场景）
 */
export function defaultBossCliEntry(): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  // dist/src/config.js → 包根是上两级
  const pkgRoot = path.resolve(here, '..', '..')
  const bundled = path.resolve(pkgRoot, 'node_modules', 'boss-resume-assistant', 'dist', 'src', 'cli', 'index.js')
  if (existsSync(bundled)) return bundled
  return path.resolve(pkgRoot, '..', 'boss-resume-assistant', 'dist', 'src', 'cli', 'index.js')
}

export function resolveBossCliEntry(config: RuntimeConfig | null): string {
  return config?.bossCliEntry ?? defaultBossCliEntry()
}

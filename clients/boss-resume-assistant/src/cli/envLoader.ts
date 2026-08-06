/**
 * .env 加载（DeepSeek key 等）。已设置的环境变量优先；探测仓库根目录/客户端目录。
 */
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

export function tryLoadEnv(): void {
  if (process.env.DEEPSEEK_API_KEYS) return
  const here = path.dirname(fileURLToPath(import.meta.url))
  // dist/src/cli → 上 5 级 = 仓库根；上 3 级 = 客户端根
  for (const up of ['..\\..\\..\\..\\..', '..\\..\\..']) {
    const candidate = path.resolve(here, up, '.env')
    if (!fs.existsSync(candidate)) continue
    try {
      process.loadEnvFile(candidate)
      return
    } catch {
      // .env 解析失败不阻塞，走环境变量
    }
  }
}

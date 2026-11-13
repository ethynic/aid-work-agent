/**
 * 测试产物新鲜度门禁（提速专项）：dist 产物缺失或任一源文件比全部产物都新时执行重建命令，
 * 产物新鲜则跳过构建直接返回。供 `npm run test` 使用；`npm run test:full` 保留无条件全量重建。
 *
 * 判定口径（保守）：取「源文件集合的最大 mtime」与「dist 内产物的最大 mtime」比较，
 * 源更新才重建。极端场景（新增源文件后又有旧产物被触碰）由 test:full 兜底；
 * 日常开发循环里改动源码后必然触发重建，不会跑到旧代码。
 *
 * 用法：node scripts/ensure-build.mjs [--pairs <源目录>:<产物目录>,...] -- <build命令...>
 *   --pairs：追加检查对（如依赖包 src 与其 dist），任一对过旧即执行重建。
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')

function newestMtime(dir, acc = { mtime: 0, count: 0 }) {
  if (!existsSync(dir)) return acc
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) newestMtime(full, acc)
    else { acc.count++; if (st.mtimeMs > acc.mtime) acc.mtime = st.mtimeMs }
  }
  return acc
}

/** 解析参数：-- 与其后的内容是 build 命令 */
const argv = process.argv.slice(2)
const sep = argv.indexOf('--')
const flags = sep >= 0 ? argv.slice(0, sep) : argv
const buildCmd = sep >= 0 ? argv.slice(sep + 1) : []
if (buildCmd.length === 0) { console.error('用法: ensure-build.mjs [--pairs src:dist,...] -- <build命令...>'); process.exit(2) }
const pairsFlag = (key) => { const i = flags.indexOf(key); return i >= 0 ? flags[i + 1] : undefined }

const src = newestMtime(path.join(root, 'src'))
const testsDir = newestMtime(path.join(root, 'tests'))
const dist = newestMtime(path.join(root, 'dist'))
const newestSrc = Math.max(src.mtime, testsDir.mtime)

let stale = dist.count === 0 || newestSrc > dist.mtime
const extraPairs = (pairsFlag('--pairs') ?? '').split(',').map((s) => s.trim()).filter(Boolean)
for (const pair of extraPairs) {
  const [sourceDir, distDir] = pair.split(':')
  const s = newestMtime(path.resolve(root, sourceDir))
  const d = newestMtime(path.resolve(root, distDir))
  if (d.count === 0 || (s.count > 0 && s.mtime > d.mtime)) stale = true
}

if (!stale) {
  console.log('[ensure-build] 产物新鲜，跳过重建')
  process.exit(0)
}
console.error('[ensure-build] 产物缺失或源码较新，执行构建：' + buildCmd.join(' '))
const result = spawnSync(buildCmd[0], buildCmd.slice(1), { stdio: 'inherit', cwd: root, shell: process.platform === 'win32' })
process.exit(result.status ?? 1)

/**
 * 测试产物新鲜度门禁（提速专项）：dist 产物缺失或任一源文件比全部产物都新时执行重建命令，
 * 产物新鲜则跳过构建直接返回。供 `npm run test` 使用；`npm run test:full` 保留无条件全量重建。
 *
 * 判定口径（保守）：取「源文件集合的最大 mtime」与「dist 内产物的最大 mtime」比较，
 * 源更新才重建。极端场景（新增源文件后又有旧产物被触碰）由 test:full 兜底；
 * 日常开发循环里改动源码后必然触发重建，不会跑到旧代码。
 *
 * 成功戳（R6 修复）：tsc 编译报错时默认仍会写出部分 JS 产物，仅凭 mtime 新鲜度会把失败
 * 构建的残留产物当成有效缓存（同一份带类型错误的源码第二次跑「跳过重建」假绿）。因此
 * 跳过还要求 dist/.build-ok.json 成功戳在场且与当前输入指纹一致：构建命令成功才写戳，
 * 失败（非零退出/命令不存在）即删戳并透传退出码——连续两次失败必然两次都失败。
 *
 * 编译配置指纹（R7 修复）：编译配置不在 src/tests mtime 口径内，改编译配置后旧产物
 * 依旧「新鲜」，会沿用旧产物假绿。因此把 build 命令实际 `-p` 的 tsconfig 原始字节哈希
 * 纳入输入指纹，并沿 extends 递归纳入被继承文件；配置字节一变指纹即失配，触发重建。
 *
 * 用法：node scripts/ensure-build.mjs [--pairs <源目录>:<产物目录>,...] -- <build命令...>
 *   --pairs：追加检查对（如依赖包 src 与其 dist），任一对过旧即执行重建。
 */
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const stampPath = path.join(root, 'dist', '.build-ok.json')

function newestMtime(dir, acc = { mtime: 0, count: 0 }, exclude) {
  if (!existsSync(dir)) return acc
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (full === exclude) continue
    const st = statSync(full)
    if (st.isDirectory()) newestMtime(full, acc, exclude)
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

const extraPairs = (pairsFlag('--pairs') ?? '').split(',').map((s) => s.trim()).filter(Boolean)

const toPosix = (p) => p.split(path.sep).join('/')

/**
 * 定位 build 命令实际使用的 tsconfig（R7）：从 package.json scripts 提取首个
 * `tsc -p/--project <file>`（boss/runtime: tsconfig.main.json，host-core: tsconfig.json，
 * 以 package.json 为准自动定位，三份脚本保持同款）。找不到时按 tsc 无 -p 的默认约定
 * 回退 tsconfig.json；该文件也不存在时配置指纹按空处理（不抛错）。
 */
function resolveConfigEntry() {
  let scripts = []
  try {
    const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'))
    scripts = Object.values(pkg.scripts ?? {}).filter((s) => typeof s === 'string')
  } catch { /* package.json 不可读时按无显式配置处理 */ }
  for (const cmd of scripts) {
    const m = cmd.match(/tsc\s+(?:[\w:@=-]+\s+)*?(?:-p|--project)\s+([^\s&|]+)/)
    if (m) return m[1]
  }
  return 'tsconfig.json'
}

/**
 * 采样编译配置指纹链（R7）：首层为 build 实际 `-p` 的 tsconfig，之后沿 extends 递归纳入。
 * tsconfig 允许 JSONC（注释/尾逗号），故不做 JSON 解析：直接对原始字节取哈希，
 * extends 值用容错正则从原文提取字符串。相对 extends 按 TS 语义相对当前文件目录解析；
 * 包名式 extends（如 @tsconfig/...）本仓库无此用法，非相对路径不做 node_modules 解析，
 * 跳过该层（简单方案留档）。链上文件缺失/不可读 → 到此为止按空处理，不抛错；
 * 深度封顶 5 层防环。
 */
function sampleConfigChain() {
  const chain = []
  const seen = new Set()
  let entry = resolveConfigEntry()
  for (let depth = 0; entry && depth < 5; depth++) {
    const full = path.resolve(root, entry)
    const rel = toPosix(path.relative(root, full))
    if (seen.has(rel)) break
    seen.add(rel)
    let bytes
    try { bytes = readFileSync(full) } catch { break }
    chain.push({ file: rel, hash: createHash('sha256').update(bytes).digest('hex') })
    const m = bytes.toString('utf8').match(/"extends"\s*:\s*"([^"]+)"/)
    if (!m || !m[1].startsWith('.')) break
    entry = path.join(path.dirname(full), m[1])
  }
  return chain
}

/**
 * 采样源侧输入指纹：src/tests 最大 mtime 与文件数 + 编译配置链字节哈希（R7） +
 * 各 --pairs 的 src/dist mtime 与文件数。
 * 在调用时采样而非只采一次：build 命令本身会重建 --pairs 依赖包（更新其 dist mtime），
 * 成功戳必须记录构建完成后的输入状态，否则下次指纹永不匹配、跳过提速失效。
 * 本包 dist 不进指纹：成功戳写在 dist 内，计入会导致自引用失配。
 */
function sampleFingerprint() {
  const src = newestMtime(path.join(root, 'src'))
  const testsDir = newestMtime(path.join(root, 'tests'))
  const pairs = extraPairs.map((pair) => {
    const [sourceDir, distDir] = pair.split(':')
    return { src: newestMtime(path.resolve(root, sourceDir)), dist: newestMtime(path.resolve(root, distDir)) }
  })
  const config = sampleConfigChain()
  const fingerprint = JSON.stringify({
    srcMtime: Math.max(src.mtime, testsDir.mtime),
    srcCount: src.count + testsDir.count,
    config,
    pairs: pairs.map(({ src, dist }) => ({ srcMtime: src.mtime, srcCount: src.count, distMtime: dist.mtime, distCount: dist.count })),
  })
  return { src, testsDir, pairs, config, fingerprint }
}

// —— 跳过判定：产物新鲜 且 成功戳在场且指纹一致，二者缺一不可 ——
const current = sampleFingerprint()
const newestSrc = Math.max(current.src.mtime, current.testsDir.mtime)
const distState = newestMtime(path.join(root, 'dist'), { mtime: 0, count: 0 }, stampPath)

let stale = distState.count === 0 || newestSrc > distState.mtime
for (const { src, dist } of current.pairs) {
  if (dist.count === 0 || (src.count > 0 && src.mtime > dist.mtime)) stale = true
}

let stampOk = false
try {
  stampOk = JSON.parse(readFileSync(stampPath, 'utf8')).fingerprint === current.fingerprint
} catch { stampOk = false }

if (!stale && stampOk) {
  console.log('[ensure-build] 产物新鲜且上次构建成功，跳过重建')
  process.exit(0)
}
if (!stale) {
  console.error('[ensure-build] 产物新鲜但成功戳缺失或不匹配（上次构建可能失败），执行重建：' + buildCmd.join(' '))
} else {
  console.error('[ensure-build] 产物缺失或源码较新，执行构建：' + buildCmd.join(' '))
}
const result = spawnSync(buildCmd[0], buildCmd.slice(1), { stdio: 'inherit', cwd: root, shell: process.platform === 'win32' })
const status = result.status ?? 1
if (status === 0) {
  mkdirSync(path.join(root, 'dist'), { recursive: true })
  writeFileSync(stampPath, JSON.stringify({ fingerprint: sampleFingerprint().fingerprint, builtAt: new Date().toISOString() }) + '\n')
} else {
  // 失败构建也会被 tsc 写出部分产物，删戳确保下次不因「产物新鲜」而跳过（R6 假绿修复）
  rmSync(stampPath, { force: true })
}
process.exit(status)

/**
 * node:test 收集器。执行当前测试源码对应的 dist/tests 文件（先 build:main 编译）。
 * 与 boss-resume-assistant 同一范式：零第三方测试框架。
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')

function collectTestFiles(dir, acc = []) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) {
      collectTestFiles(full, acc)
    } else if (entry.endsWith('.test.js') && existsSync(path.join(sourceTests, path.relative(testsDir, full).replace(/\.js$/, '.ts')))) {
      acc.push(full)
    }
  }
  return acc
}

const testsDir = path.join(root, 'dist', 'tests')
const sourceTests = path.join(root, 'tests')
let files
try {
  files = collectTestFiles(testsDir)
} catch {
  console.error(`no tests dir found: ${testsDir} — run build:main first`)
  process.exit(1)
}

if (files.length === 0) {
  console.error('no test files matched dist/tests/**/*.test.js')
  process.exit(1)
}

console.log(`running ${files.length} test file(s):`)
for (const f of files) console.log('  ' + path.relative(root, f))

const result = spawnSync(process.execPath, ['--test', ...files], {
  stdio: 'inherit',
  cwd: root,
})

process.exit(result.status ?? 1)

/**
 * build-ocr-python.mjs — 构建捆绑便携 OCR Python 环境（方案 A，2026-09-02）
 *
 * 产物：clients/boss-resume-assistant/ocr-python/（python.org 可嵌入发行版 + 预装全家桶），
 * 随 boss 包 files 白名单打进发布 tgz → 客户机零 Python/pip 知识要求（RapidOCR 主引擎开箱即用）。
 *
 * 步骤（幂等：ocr-python 已存在且自检通过则跳过；--force 强制重建）：
 *   1. 下载 python-3.12.6-embed-amd64.zip（与仓库 venv 同 3.12.6；缓存在 clients/.cache/，已存在不重复下载）
 *      → 解压到 ocr-python/（Windows 10+ 自带 tar 可解 zip）
 *   2. pip 预装依赖（用仓库根 venv 的 python，3.12.6 win_amd64 wheel 兼容）：
 *      - rapidocr-onnxruntime==1.4.4 用 --no-deps 装（其元数据声明 opencv-python/onnxruntime——
 *        前者带 113MB Qt 废料、后者无 DML），再手动装齐真实依赖链：
 *        numpy opencv-python-headless Pillow PyYAML pyclipper Shapely six tqdm + onnxruntime-directml
 *      - onnxruntime-directml==1.20.1：含 DmlExecutionProvider + CPUExecutionProvider，
 *        客户机任意 DX12 显卡/核显加速开箱即用（无需 NVIDIA）
 *   3. 编辑 python312._pth 加一行 Lib/site-packages（可嵌入发行版默认隔离 site-packages，标准必做步骤）
 *   4. 自检（fail-loud，构建不过=不发货）：import 全家桶 + providers 必须含 DML+CPU +
 *      cv-ocr-rapid.py --bench 跑通输出 bench= 行
 *
 * 运行：npm run build:ocr-python（= node scripts/build-ocr-python.mjs）
 * 版本升级（Python/wheel）：改下方 EMBEDDED_PYTHON_URL 与 PINNED 常量后 --force 重建。
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
/** 包根（clients/boss-resume-assistant） */
const pkgRoot = path.resolve(__dirname, '..')
/** 仓库根（venv 与 clients/.cache 所在） */
const repoRoot = path.resolve(pkgRoot, '..', '..')
const outDir = path.join(pkgRoot, 'ocr-python')
const cacheDir = path.join(repoRoot, 'clients', '.cache')
/** 开发兜底解释器：仓库根 venv（与发行版同 3.12.6，wheel 全兼容；也是被搬运依赖的验证基准） */
const venvPython = path.join(repoRoot, 'venv', 'Scripts', 'python.exe')
/** 仓库布局的适配器脚本（自检用；该脚本同时随包 files 分发） */
const cvOcrScript = path.join(pkgRoot, 'scripts', 'cv-ocr-rapid.py')

const PY_VERSION = '3.12.6'
const EMBEDDED_PYTHON_URL = `https://www.python.org/ftp/python/${PY_VERSION}/python-${PY_VERSION}-embed-amd64.zip`

/** 版本与仓库 venv 对齐（真机验证过的组合，2026-09-02；rapidocr 元数据声明 opencv-python/onnxruntime → 用 headless/directml 替代） */
const RAPIDOCR_PIN = 'rapidocr-onnxruntime==1.4.4'
const DIRECTML_PIN = 'onnxruntime-directml==1.20.1'
/** rapidocr 真实依赖链（等价其 Requires-Dist，仅 opencv→headless、onnxruntime→directml 两处替换） */
const RAPIDOCR_REAL_DEPS = [
  'numpy<3.0.0,>=1.19.5',
  'opencv-python-headless>=4.5.1.48',
  'Pillow',
  'PyYAML',
  'pyclipper>=1.2.0',
  'Shapely!=2.0.4,>=1.7.1',
  'six>=1.15.0',
  'tqdm',
]

/** --force 显式重建；已存在产物自检失败也置 true（见下方幂等块）——重建必须清目录，
 * 否则 tar 覆盖解压 + pip --target（隐含 --ignore-installed）叠加安装会残留导致自检失败的旧包 */
let force = process.argv.includes('--force')

const log = (msg) => console.log(`[build-ocr-python] ${msg}`)
const die = (msg) => {
  console.error(`[build-ocr-python] FAIL：${msg}`)
  process.exit(1)
}

function run(cmd, args, opts = {}) {
  log(`$ ${cmd} ${args.join(' ').slice(0, 200)}${args.join(' ').length > 200 ? ' …' : ''}`)
  return execFileSync(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], encoding: 'utf8', ...opts })
}

/** 目录体积（bytes）：递归累加，效果同 du -sb（Windows 无 du） */
function dirSizeBytes(dir) {
  let total = 0
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    total += entry.isDirectory() ? dirSizeBytes(full) : fs.statSync(full).size
  }
  return total
}

const human = (bytes) => {
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(1)} ${units[i]}`
}

/** 自检（fail-loud）：import 全家桶 + providers 含 DML+CPU + 适配器 --bench 跑通。失败抛错（调用方决定重建/终止） */
function selfCheck() {
  const py = path.join(outDir, 'python.exe')
  const providers = run(py, [
    '-c',
    'import rapidocr_onnxruntime, onnxruntime, PIL, cv2, shapely, yaml; print(onnxruntime.get_available_providers())',
  ]).trim()
  log(`providers = ${providers}`)
  if (!providers.includes('DmlExecutionProvider') || !providers.includes('CPUExecutionProvider')) {
    throw new Error(`onnxruntime providers 不含 DmlExecutionProvider + CPUExecutionProvider：${providers}（directml wheel 装错/被顶掉）`)
  }
  const bench = run(py, [cvOcrScript, '--bench'], { timeout: 120000 })
  const m = /bench=([\d.]+)s/.exec(bench)
  if (!m) throw new Error(`cv-ocr-rapid.py --bench 输出缺少 bench= 行：${bench.trim()}`)
  const engine = /engine=(dml|cpu)/.exec(bench)?.[1] ?? 'unknown'
  log(`适配器自检通过：engine=${engine} bench=${m[1]}s`)
  return true
}

// ---------- 幂等：已存在且自检通过 → 跳过 ----------
if (!force && fs.existsSync(path.join(outDir, 'python.exe'))) {
  log(`ocr-python 已存在，跑自检确认可用（--force 强制重建）…`)
  try {
    if (selfCheck()) {
      log(`自检通过，跳过构建（幂等）。体积：${human(dirSizeBytes(outDir))}`)
      process.exit(0)
    }
  } catch (err) {
    log(`已存在的 ocr-python 自检失败，重建：${err instanceof Error ? err.message.split('\n')[0] : err}`)
    force = true
  }
}

// ---------- 前置检查 ----------
if (!fs.existsSync(venvPython)) {
  die(`未找到仓库根 venv 解释器 ${venvPython}（pip 预装用；先按仓库文档建好 venv 或手动装 3.12.6 x64 Python）`)
}
if (!fs.existsSync(cvOcrScript)) die(`未找到 ${cvOcrScript}`)

// ---------- 1. 下载 + 解压可嵌入发行版 ----------
fs.mkdirSync(cacheDir, { recursive: true })
const zipPath = path.join(cacheDir, `python-${PY_VERSION}-embed-amd64.zip`)
if (!fs.existsSync(zipPath)) {
  log(`下载 ${EMBEDDED_PYTHON_URL} → ${zipPath}`)
  const res = await fetch(EMBEDDED_PYTHON_URL)
  if (!res.ok) die(`下载失败：HTTP ${res.status} ${res.statusText}（${EMBEDDED_PYTHON_URL}；不缓存错误响应，重跑重试）`)
  const buf = Buffer.from(await res.arrayBuffer())
  // 先写临时文件再原子改名：进程中途被杀不会留下截断的 zip 被后续运行当有效缓存命中
  fs.writeFileSync(`${zipPath}.tmp`, buf)
  fs.renameSync(`${zipPath}.tmp`, zipPath)
  log(`下载完成：${human(buf.length)}`)
} else {
  log(`命中缓存：${zipPath}`)
}

// Windows 10+ 自带 bsdtar（System32\tar.exe）可直接解 zip。注意必须用绝对路径：
// 开发机 Git Bash 的 GNU tar 在 PATH 里时会把 `C:\...` 当「远程主机」报
// "Cannot connect to C: resolve failed"。System32 tar 不存在时回退 PowerShell Expand-Archive。
function extractZip(zipPath, destDir) {
  const winTar = 'C:\\Windows\\System32\\tar.exe'
  if (fs.existsSync(winTar)) {
    execFileSync(winTar, ['-xf', zipPath, '-C', destDir], { stdio: 'inherit' })
    return
  }
  log('未找到 System32 tar.exe，回退 PowerShell Expand-Archive')
  execFileSync(
    'powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
      `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${destDir}' -Force`],
    { stdio: 'inherit' },
  )
}

if (force && fs.existsSync(outDir)) {
  log(`--force：删除旧产物 ${outDir}`)
  fs.rmSync(outDir, { recursive: true, force: true })
}
fs.mkdirSync(outDir, { recursive: true })
log(`解压到 ${outDir}`)
try {
  extractZip(zipPath, outDir)
} catch (err) {
  die(
    `解压失败：${err instanceof Error ? err.message.split('\n')[0] : err}。` +
      `若缓存 zip 损坏（下载中断残留/磁盘问题），删除 ${zipPath} 后重跑即可重新下载`,
  )
}

// ---------- 2. pip 预装（--target 进 ocr-python/Lib/site-packages） ----------
const target = path.join(outDir, 'Lib', 'site-packages')
const pipBase = ['-m', 'pip', 'install', '--target', target, '--no-cache-dir', '--no-warn-script-location', '--disable-pip-version-check']
log(`预装 ${RAPIDOCR_PIN}（--no-deps：其声明的 opencv-python 带 Qt 废料、onnxruntime 无 DML，替代品单独装齐）`)
run(venvPython, [...pipBase, '--no-deps', RAPIDOCR_PIN])
log(`装齐真实依赖链 + DirectML 加速（${RAPIDOCR_REAL_DEPS.length + 1} 项）`)
run(venvPython, [...pipBase, DIRECTML_PIN, ...RAPIDOCR_REAL_DEPS])

// ---------- 3. python312._pth 放开 Lib/site-packages（可嵌入发行版默认隔离，标准必做步骤） ----------
const pthPath = path.join(outDir, `python${PY_VERSION.split('.').slice(0, 2).join('')}._pth`)
if (!fs.existsSync(pthPath)) die(`未找到 ${pthPath}（可嵌入发行版解压产物异常）`)
const pth = fs.readFileSync(pthPath, 'utf8')
if (!/^Lib\/site-packages$/m.test(pth)) {
  fs.writeFileSync(pthPath, `${pth.replace(/\r?\n?$/, '\n')}Lib/site-packages\n`)
  log(`已编辑 ${path.basename(pthPath)}：追加 Lib/site-packages`)
}

// ---------- 4. 自检（fail-loud） ----------
log('自检开始（import + providers + 适配器 bench）…')
try {
  selfCheck()
} catch (err) {
  die(`自检失败：${err instanceof Error ? err.message : err}`)
}

log(`构建完成：${outDir}`)
log(`最终体积：${human(dirSizeBytes(outDir))}（${(dirSizeBytes(outDir) / 1024 / 1024).toFixed(0)} MB）`)

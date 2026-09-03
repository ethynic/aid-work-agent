/**
 * P2 OCR 引擎选择与批量 OCR 单测（resolveOcrEngine / ocrBatch，bossResumeDetail.ts）。
 * 全部子进程经注入的 fake ExecRunner（不起真进程、不触达 Chrome/页面）：
 * - 探测（`<py> -c "import rapidocr_onnxruntime, PIL"`）与适配器调用（cv-ocr-rapid.py + 全部段）
 *   都发给 fake runner，按 args 形状区分；
 * - rapid 成功路径的 <img>.rapid.txt 由 fake runner 落盘到真实临时目录（ocrBatch 会读回）；
 * - WinRT 回退路径注入 fake winrt 单图 OCR（绝不跑真 powershell）。
 * 引擎解析有模块级缓存：beforeEach/afterEach 重置（resetOcrEngineCacheForTest），env 存取还原。
 */
import assert from 'node:assert/strict'
import test, { beforeEach, afterEach } from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import {
  benchRapidOcr,
  ocrBatch,
  resolveOcrEngine,
  resetOcrEngineCacheForTest,
  rapidPythonCandidates,
  RAPID_BENCH_TIMEOUT_MS,
  type ExecRunner,
} from '../src/main/operations/bossResumeDetail.js'
import { ResumeReadError } from '../src/main/boss/ResumeReader.js'

/** fake runner：记录调用 + 脚本化响应。args[0]==='-c' 视为探测，否则为适配器调用 */
function makeRunner(
  respond: (file: string, args: string[]) => Promise<{ stdout: string; stderr: string }>,
): { runner: ExecRunner; calls: Array<{ file: string; args: string[]; timeout: number }> } {
  const calls: Array<{ file: string; args: string[]; timeout: number }> = []
  const runner: ExecRunner = async (file, args, opts) => {
    calls.push({ file, args, timeout: opts.timeout })
    return respond(file, args)
  }
  return { runner, calls }
}

const isProbe = (args: string[]): boolean => args[0] === '-c'

/** 捕获 process.stderr.write 的行（ocrBatch 回退日志断言用；用完必须 restore） */
function captureStderr(): { lines: string[]; restore(): void } {
  const lines: string[] = []
  const orig = process.stderr.write
  process.stderr.write = ((chunk: Uint8Array | string): boolean => {
    lines.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString())
    return true
  }) as typeof orig
  return { lines, restore: () => { process.stderr.write = orig } }
}

/** 恒失败 runner（探测不可用/进程失败路径） */
const failRunner = (): ExecRunner => async (_file, args) => {
  if (isProbe(args)) throw new Error('ModuleNotFoundError: No module named \'rapidocr_onnxruntime\'')
  throw new Error('子进程执行失败(python exit=3)')
}

let savedEngine: string | undefined
let savedPy: string | undefined
const FAKE_PY = 'C:/fake-venv/Scripts/python.exe'

beforeEach(() => {
  resetOcrEngineCacheForTest()
  savedEngine = process.env.AID_BOSS_OCR_ENGINE
  savedPy = process.env.AID_BOSS_RAPIDOCR_PY
  delete process.env.AID_BOSS_OCR_ENGINE
  process.env.AID_BOSS_RAPIDOCR_PY = FAKE_PY
})

afterEach(() => {
  if (savedEngine === undefined) delete process.env.AID_BOSS_OCR_ENGINE
  else process.env.AID_BOSS_OCR_ENGINE = savedEngine
  if (savedPy === undefined) delete process.env.AID_BOSS_RAPIDOCR_PY
  else process.env.AID_BOSS_RAPIDOCR_PY = savedPy
  resetOcrEngineCacheForTest()
})

// ---------- resolveOcrEngine：env 强制 / auto 探测 / 缓存 ----------

test('auto + 探测成功 → rapid；python = AID_BOSS_RAPIDOCR_PY 指定的解释器', async () => {
  const { runner, calls } = makeRunner(async () => ({ stdout: '', stderr: '' }))
  const plan = await resolveOcrEngine(runner)
  assert.equal(plan.engine, 'rapid')
  assert.equal(plan.python, FAKE_PY)
  assert.equal(plan.forced, undefined) // auto：非强制（运行期失败允许整批回退 WinRT）
  assert.match(plan.reason, /RapidOCR 可用/)
  // 探测调用形状：`<py> -c "import rapidocr_onnxruntime, PIL"`，超时 15s
  assert.equal(calls.length, 1)
  assert.equal(calls[0]!.file, FAKE_PY)
  assert.deepEqual(calls[0]!.args, ['-c', 'import rapidocr_onnxruntime, PIL'])
  assert.equal(calls[0]!.timeout, 15000)
})

test('模块级缓存：第二次 resolve 不再起探测子进程（缓存一次）', async () => {
  const { runner, calls } = makeRunner(async () => ({ stdout: '', stderr: '' }))
  await resolveOcrEngine(runner)
  const plan2 = await resolveOcrEngine(runner) // 生产路径（不传 runner 也走缓存，不会再起真进程）
  assert.equal(plan2.engine, 'rapid')
  assert.equal(calls.length, 1)
})

test('auto + 探测失败（未装）→ 静默回退 winrt，reason 带原因与安装提示', async () => {
  const plan = await resolveOcrEngine(failRunner())
  assert.equal(plan.engine, 'winrt')
  assert.equal(plan.python, undefined)
  assert.match(plan.reason, /RapidOCR 不可用/)
  assert.match(plan.reason, /pip install rapidocr-onnxruntime/)
})

test('探测按候选顺序推进：首个候选失败自动试下一个（不因单候选失败误判不可用）', async () => {
  const { runner, calls } = makeRunner(async (file) => {
    if (file === FAKE_PY) throw new Error('ModuleNotFoundError')
    return { stdout: '', stderr: '' }
  })
  const plan = await resolveOcrEngine(runner)
  assert.equal(plan.engine, 'rapid')
  assert.notEqual(plan.python, FAKE_PY) // 落到后续候选（仓库 venv / PATH python）
  assert.equal(calls.length, 2)
})

test('AID_BOSS_OCR_ENGINE=winrt → 强制 WinRT，不起任何探测子进程', async () => {
  process.env.AID_BOSS_OCR_ENGINE = 'winrt'
  const { runner, calls } = makeRunner(async () => ({ stdout: '', stderr: '' }))
  const plan = await resolveOcrEngine(runner)
  assert.equal(plan.engine, 'winrt')
  assert.match(plan.reason, /AID_BOSS_OCR_ENGINE=winrt/)
  assert.equal(calls.length, 0)
})

test('AID_BOSS_OCR_ENGINE=rapid 强制而探测失败 → fail-loud（ResumeReadError + 部署要求文案）', async () => {
  process.env.AID_BOSS_OCR_ENGINE = 'rapid'
  await assert.rejects(resolveOcrEngine(failRunner()), (e: unknown) => {
    assert.ok(e instanceof ResumeReadError)
    assert.match(e.message, /AID_BOSS_OCR_ENGINE=rapid/)
    assert.match(e.message, /pip install rapidocr-onnxruntime/)
    return true
  })
})

test('AID_BOSS_OCR_ENGINE=rapid 强制且可用 → rapid（reason 标注强制来源；forced 标记供 ocrBatch fail-loud）', async () => {
  process.env.AID_BOSS_OCR_ENGINE = 'rapid'
  const { runner } = makeRunner(async () => ({ stdout: '', stderr: '' }))
  const plan = await resolveOcrEngine(runner)
  assert.equal(plan.engine, 'rapid')
  assert.equal(plan.forced, true)
  assert.match(plan.reason, /强制 RapidOCR/)
})

// ---------- rapidPythonCandidates：候选顺序 env > 捆绑 ocr-python > 仓库 venv > PATH ----------
//（exists 注入 fake，免起子进程、免依赖开发机是否构建过 ocr-python 的机器状态）

/** fake exists：按路径形状分类（捆绑 ocr-python / 仓库 venv），其余一律不存在 */
function fakeExists(opts: { bundled?: boolean; venv?: boolean }) {
  return (p: string): boolean => {
    if (/ocr-python[\\/]python\.exe$/.test(p)) return opts.bundled ?? false
    if (/venv[\\/]Scripts[\\/]python\.exe$/.test(p)) return opts.venv ?? false
    return false
  }
}

test('候选顺序：env > 捆绑 ocr-python > 仓库 venv > PATH python（全部存在时逐一断言）', () => {
  const candidates = rapidPythonCandidates(fakeExists({ bundled: true, venv: true }))
  assert.equal(candidates.length, 4)
  assert.equal(candidates[0], FAKE_PY) // env AID_BOSS_RAPIDOCR_PY 恒第一
  assert.match(candidates[1]!, /ocr-python[\\/]python\.exe$/) // 捆绑便携环境次之
  assert.match(candidates[2]!, /[\\/]venv[\\/]Scripts[\\/]python\.exe$/) // 仓库 venv 开发兜底
  assert.equal(candidates[3], 'python') // PATH 收尾
})

test('捆绑环境缺席（npm 安装布局未构建/客户机旧包）→ 跳过不报错，候选退化为 env > venv > PATH', () => {
  const candidates = rapidPythonCandidates(fakeExists({ bundled: false, venv: true }))
  assert.equal(candidates.length, 3)
  assert.equal(candidates[0], FAKE_PY)
  assert.match(candidates[1]!, /[\\/]venv[\\/]Scripts[\\/]python\.exe$/)
  assert.equal(candidates[2], 'python')
})

test('捆绑与 venv 都缺席 → [env, PATH python]（客户机最小布局）', () => {
  const candidates = rapidPythonCandidates(fakeExists({}))
  assert.deepEqual(candidates, [FAKE_PY, 'python'])
})

test('env 未设时捆绑环境即为首候选（客户机缺省路径：直接用包内自带解释器）', () => {
  delete process.env.AID_BOSS_RAPIDOCR_PY
  const candidates = rapidPythonCandidates(fakeExists({ bundled: true, venv: true }))
  assert.equal(candidates.length, 3)
  assert.match(candidates[0]!, /ocr-python[\\/]python\.exe$/)
  assert.equal(candidates[1]!.endsWith('python.exe'), true) // 随后 venv
  assert.equal(candidates[2], 'python')
})

test('布局探测：dist（上溯4级）与 src（上溯3级）两种布局的捆绑路径都会被探测（先 dist 后 src）', () => {
  const seen: string[] = []
  rapidPythonCandidates((p) => {
    seen.push(p)
    return false
  })
  const bundled = seen.filter((p) => /ocr-python[\\/]python\.exe$/.test(p))
  assert.equal(bundled.length, 2) // 两种编译布局各探测一次，命中即 break
  assert.notEqual(bundled[0], bundled[1])
})

test('探测行为联动：env 失败 → 下一候选为捆绑环境（fake runner 收到的第二个探测目标）', async () => {
  // 真实 fs：开发机构建过 ocr-python 后，探测序列第二位必是捆绑解释器（本用例同时验证
  // 「开发机也走捆绑环境（DML）」这一有意行为——跑的就是发货物）；未构建的机器上该断言退化为
  // 第二位是 venv/python，仍不失败（只断言 ≠ env、按序推进）
  const { runner, calls } = makeRunner(async (file) => {
    if (file === FAKE_PY) throw new Error('ModuleNotFoundError')
    return { stdout: '', stderr: '' }
  })
  const plan = await resolveOcrEngine(runner)
  assert.equal(plan.engine, 'rapid')
  assert.equal(calls.length, 2)
  assert.notEqual(calls[1]!.file, FAKE_PY)
  const expectedOrder = rapidPythonCandidates()
  assert.equal(calls[1]!.file, expectedOrder[1]) // 实际探测序与候选序一致
})

// ---------- ocrBatch：rapid 成功 / 失败整批回退 WinRT ----------

/** rapid 适配器 fake runner：探测成功；适配器调用把每段文本写到 <img>.rapid.txt 后成功返回 */
function makeRapidAdapterRunner(
  texts: string[],
  opts: { failProcess?: boolean; skipFile?: number; stdoutPrefix?: string } = {},
) {
  return makeRunner(async (file, args) => {
    if (isProbe(args)) return { stdout: '', stderr: '' }
    if (opts.failProcess) throw new Error('子进程执行失败(python exit=5): OCR 失败')
    const imgs = args.slice(1)
    for (const [i, img] of imgs.entries()) {
      if (opts.skipFile === i) continue // 模拟该段结果文件缺失
      await fs.writeFile(`${img}.rapid.txt`, texts[i] ?? `第${i}段文本`, 'utf8')
    }
    return {
      stdout: (opts.stdoutPrefix ?? '') + imgs.map((_, i) => `file=${i} chars=${(texts[i] ?? '').length}`).join('\n') + `\ndone=${imgs.length}`,
      stderr: '',
    }
  })
}

/** fake winrt 单图 OCR（绝不跑真 powershell） */
function makeFakeWinrt(text: string) {
  const calls: string[] = []
  const winrt = async (imgFile: string): Promise<string> => {
    calls.push(imgFile)
    return text
  }
  return { winrt, calls }
}

test('rapid 成功路径：一次子进程跑适配器（全部段一批），读回各 .rapid.txt', async () => {
  const tmpDir = path.join(os.tmpdir(), `ocr-engine-test-${randomUUID()}`)
  await fs.mkdir(tmpDir, { recursive: true })
  try {
    const files = ['crop-00.png', 'crop-01.png', 'crop-02.png'].map((n) => path.join(tmpDir, n))
    const texts = ['第零段\n内容', '第一段内容', '第 二 段']
    const { runner, calls } = makeRapidAdapterRunner(texts)
    const { winrt, calls: winrtCalls } = makeFakeWinrt('不应走到 WinRT')
    const res = await ocrBatch(files, { runner, winrt })
    assert.equal(res.engine, 'rapid')
    assert.deepEqual(res.texts, texts)
    // 调用形状：探测 1 次 + 适配器 1 次（一次批量、超时 300s、args = 脚本 + 全部段）
    assert.equal(calls.length, 2)
    assert.equal(calls[1]!.file, FAKE_PY)
    assert.match(calls[1]!.args[0]!, /cv-ocr-rapid\.py$/)
    assert.deepEqual(calls[1]!.args.slice(1), files)
    assert.equal(calls[1]!.timeout, 300000)
    assert.equal(winrtCalls.length, 0)
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
  }
})

test('rapid 进程失败（依赖/推理异常退出）→ 整批回退 WinRT（逐文件 ocrImage 形状），engine=winrt；stderr 留一行原因', async () => {
  const files = ['a.png', 'b.png'].map((n) => path.join(os.tmpdir(), `ocr-engine-fail-${randomUUID()}-${n}`))
  const { runner, calls } = makeRapidAdapterRunner(['x', 'y'], { failProcess: true })
  const { winrt, calls: winrtCalls } = makeFakeWinrt('WinRT 兜底文本')
  const stderr = captureStderr()
  try {
    const res = await ocrBatch(files, { runner, winrt })
    assert.equal(res.engine, 'winrt')
    assert.deepEqual(res.texts, ['WinRT 兜底文本', 'WinRT 兜底文本'])
    assert.equal(winrtCalls.length, 2) // 逐文件回退
    assert.deepEqual(winrtCalls, files)
    assert.equal(calls.length, 2) // 探测 + 适配器各一次（适配器失败后不再重试 rapid）
    // P2 回退不再静默：stderr 恰好一行，含引擎与失败原因摘要（供排障，不改变回退语义）
    assert.equal(stderr.lines.length, 1)
    assert.match(stderr.lines[0]!, /RapidOCR 批量执行失败/)
    assert.match(stderr.lines[0]!, /回退 WinRT/)
    assert.match(stderr.lines[0]!, /exit=5/)
  } finally {
    stderr.restore()
  }
})

test('rapid 进程成功但某段 .rapid.txt 缺失 → 整批回退 WinRT（不返回半批 rapid 结果），stderr 留一行原因', async () => {
  const tmpDir = path.join(os.tmpdir(), `ocr-engine-miss-${randomUUID()}`)
  await fs.mkdir(tmpDir, { recursive: true })
  const stderr = captureStderr()
  try {
    const files = ['a.png', 'b.png'].map((n) => path.join(tmpDir, n))
    const { runner } = makeRapidAdapterRunner(['x', 'y'], { skipFile: 1 }) // 只写第 0 段结果文件
    const { winrt, calls: winrtCalls } = makeFakeWinrt('兜底')
    const res = await ocrBatch(files, { runner, winrt })
    assert.equal(res.engine, 'winrt')
    assert.deepEqual(res.texts, ['兜底', '兜底'])
    assert.equal(winrtCalls.length, 2)
    assert.equal(stderr.lines.length, 1) // 文件缺失路径同样留原因（ENOENT）
    assert.match(stderr.lines[0]!, /RapidOCR 批量执行失败/)
  } finally {
    stderr.restore()
    await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
  }
})

test('AID_BOSS_OCR_ENGINE=rapid 强制 + 批量执行失败 → fail-loud（ResumeReadError 含原因与 auto 提示），绝不静默回退 WinRT', async () => {
  process.env.AID_BOSS_OCR_ENGINE = 'rapid'
  const files = ['a.png', 'b.png'].map((n) => path.join(os.tmpdir(), `ocr-engine-forced-${randomUUID()}-${n}`))
  const { runner } = makeRapidAdapterRunner(['x', 'y'], { failProcess: true })
  const { winrt, calls: winrtCalls } = makeFakeWinrt('不应走到 WinRT')
  const stderr = captureStderr()
  try {
    await assert.rejects(ocrBatch(files, { runner, winrt }), (e: unknown) => {
      assert.ok(e instanceof ResumeReadError)
      assert.match(e.message, /AID_BOSS_OCR_ENGINE=rapid/)
      assert.match(e.message, /批量执行失败/)
      assert.match(e.message, /exit=5/) // 失败原因如实带入（评估项 a：不吞原因）
      assert.match(e.message, /AID_BOSS_OCR_ENGINE=auto/) // 指引到 auto 才允许回退
      return true
    })
    assert.equal(winrtCalls.length, 0) // 「强制」不被软化：绝不静默降级 WinRT
    assert.equal(stderr.lines.length, 0) // fail-loud 路径不打回退日志（报错本身即原因）
  } finally {
    stderr.restore()
  }
})

test('auto + 探测失败 → 不调适配器直接 WinRT 兜底（零依赖可用性）', async () => {
  const files = ['a.png']
  const { calls: winrtCalls, winrt } = makeFakeWinrt('winrt 文本')
  const res = await ocrBatch(files, { runner: failRunner(), winrt })
  assert.equal(res.engine, 'winrt')
  assert.deepEqual(res.texts, ['winrt 文本'])
  assert.equal(winrtCalls.length, 1) // failRunner 只记录了探测调用；无适配器形状调用
})

test('空批（0 段）：既不起适配器子进程也不调 WinRT（引擎随解析结果返回）', async () => {
  process.env.AID_BOSS_OCR_ENGINE = 'winrt'
  const { runner, calls } = makeRunner(async () => ({ stdout: '', stderr: '' }))
  const { winrt, calls: winrtCalls } = makeFakeWinrt('不应被调')
  const res = await ocrBatch([], { runner, winrt })
  assert.deepEqual(res.texts, [])
  assert.equal(res.engine, 'winrt')
  assert.equal(calls.length, 0)
  assert.equal(winrtCalls.length, 0)
})

// ---------- benchRapidOcr：doctor 单次推理实测（--bench 输出解析） ----------

test('benchRapidOcr：解析 bench=<秒> + engine=dml，调用形状 = <py> <适配器> --bench', async () => {
  const { runner, calls } = makeRunner(async () => ({ stdout: 'engine=dml\nbench=1.23s\ndone=1\n', stderr: '' }))
  const { seconds, accel } = await benchRapidOcr(FAKE_PY, runner)
  assert.equal(seconds, 1.23)
  assert.equal(accel, 'dml')
  assert.equal(calls.length, 1)
  assert.match(calls[0]!.args[0]!, /cv-ocr-rapid\.py$/)
  assert.deepEqual(calls[0]!.args.slice(1), ['--bench'])
  assert.equal(calls[0]!.timeout, RAPID_BENCH_TIMEOUT_MS)
})

test('benchRapidOcr：无 engine= 行 → accel=cpu（CPU 版 onnxruntime 的适配器输出）', async () => {
  const { runner } = makeRunner(async () => ({ stdout: 'engine=cpu\nbench=0.80s\ndone=1\n', stderr: '' }))
  const { seconds, accel } = await benchRapidOcr(FAKE_PY, runner)
  assert.equal(seconds, 0.8)
  assert.equal(accel, 'cpu')
})

test('benchRapidOcr：输出缺 bench= 行 → 抛错（doctor 侧降级不展示耗时，不炸装机）', async () => {
  const { runner } = makeRunner(async () => ({ stdout: 'done=1\n', stderr: '' }))
  await assert.rejects(benchRapidOcr(FAKE_PY, runner), /缺少 bench=/)
})

test('benchRapidOcr：进程失败原样上抛', async () => {
  const { runner } = makeRunner(async () => { throw new Error('子进程执行失败 exit=8') })
  await assert.rejects(benchRapidOcr(FAKE_PY, runner), /exit=8/)
})

// ---------- ocrBatch 的 accel 透传：适配器 engine= 行 → 返回值 accel ----------

// 注意：img 文件必须落 tmpdir 绝不能用裸相对路径——fake 适配器会写 <img>.rapid.txt，
// 裸路径会在包根残留 a.png.rapid.txt 垃圾文件（CR 修复：此前三个 accel 用例即如此）

test('ocrBatch：适配器 stdout 含 engine=dml → accel=dml（DirectML 加速）', async () => {
  const tmpDir = path.join(os.tmpdir(), `ocr-engine-accel-${randomUUID()}`)
  await fs.mkdir(tmpDir, { recursive: true })
  try {
    const { runner } = makeRapidAdapterRunner(['dml 文本'], { stdoutPrefix: 'engine=dml\n' })
    const { winrt } = makeFakeWinrt('不应走到 WinRT')
    const res = await ocrBatch([path.join(tmpDir, 'a.png')], { runner, winrt })
    assert.equal(res.engine, 'rapid')
    assert.equal(res.accel, 'dml')
    assert.deepEqual(res.texts, ['dml 文本'])
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
  }
})

test('ocrBatch：适配器 stdout 无 engine= 行 → accel=cpu（宽容解析，不因缺行回退）', async () => {
  const tmpDir = path.join(os.tmpdir(), `ocr-engine-accel-${randomUUID()}`)
  await fs.mkdir(tmpDir, { recursive: true })
  try {
    const { runner } = makeRapidAdapterRunner(['cpu 文本'])
    const { winrt } = makeFakeWinrt('不应走到 WinRT')
    const res = await ocrBatch([path.join(tmpDir, 'a.png')], { runner, winrt })
    assert.equal(res.engine, 'rapid')
    assert.equal(res.accel, 'cpu')
    assert.deepEqual(res.texts, ['cpu 文本'])
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
  }
})

test('ocrBatch：DML 失败整批回退 CPU 重跑成功（stdout 先后 engine=dml/engine=cpu 两行）→ accel=cpu（取最后一行=最终引擎）', async () => {
  // 复刻适配器真实回退 stdout 形状：首行 engine=dml + DML 轮已写的 file= 行，
  // 回退后 engine=cpu + CPU 轮重写全部 file= 行（exit 0、结果文件全部为 CPU 产出）
  const tmpDir = path.join(os.tmpdir(), `ocr-engine-accel-${randomUUID()}`)
  await fs.mkdir(tmpDir, { recursive: true })
  try {
    const { runner } = makeRapidAdapterRunner(['回退后文本'], {
      stdoutPrefix: 'engine=dml\nfile=0 chars=5\nengine=cpu\n',
    })
    const { winrt } = makeFakeWinrt('不应走到 WinRT')
    const res = await ocrBatch([path.join(tmpDir, 'a.png')], { runner, winrt })
    assert.equal(res.engine, 'rapid')
    assert.equal(res.accel, 'cpu') // 实际产出文本的是 CPU：按任意行/首行 grep 会误报 dml
    assert.deepEqual(res.texts, ['回退后文本'])
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true }).catch(() => {})
  }
})

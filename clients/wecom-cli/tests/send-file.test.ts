/**
 * wecom_send_file M8 契约测试（全 mock，绝不触达真实企微窗口/驱动脚本——runDriverFn
 * 注入按脚本名分发的 mock，verifyRefFn/createRefFn 注入临时 keyDir 的真实签发/验证；
 * CLI 退出码用例只走参数/文件校验失败路径，不 spawn 驱动）。
 *
 * 覆盖：参数校验矩阵（文件不存在 / 是目录 / 超 100MB / 无坐标旧 ref / 相对路径 /
 * 极短文件名（去空白后 <3 字符），均 INVALID_ARGUMENT 且不调驱动；a.txt 不在拒绝范围）/
 * 快路径成功（navigate_required=false → 透传粘贴判据字段 paste_check 与 file 摘要）/
 * 分发路径编排顺序（send-file → search → select → send-file，两轮驱动参数一致）/
 * 两轮 navigate_required → TARGET_NOT_FOUND（未发送文件）/ 驱动剪贴板失败
 * CONFIG_MISSING 透传 / 驱动超时 RESULT_TIMEOUT → EXECUTION_UNKNOWN（effect=unknown）/
 * send-file --json 退出码（缺参数与文件不存在均退出码 2）。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test, { after } from 'node:test'
import { createWecomSendFileOperation } from '../src/operations/sendFile.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../src/platform/targetRef.js'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const tempDirs: string[] = []
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

function makeKeyDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-send-file-key-'))
  tempDirs.push(dir)
  return dir
}

function makeArtifactRoot(): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-send-file-test-'))
  tempDirs.push(dir)
  return dir
}

/** 建一个真实小文件（内容任意——TS 只校验存在/大小/绝对路径，扩展名不限） */
function makeFile(name = 'test-file.txt', size = 64): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-send-file-f-'))
  tempDirs.push(dir)
  const p = join(dir, name)
  writeFileSync(p, Buffer.alloc(size, 0x41))
  return p
}

/** 真实签发 + 真实验证（临时 keyDir）：内部 chatSearch 签发的 ref 由同一 key 验证 */
function realDeps(keyDir: string): { verifyRefFn: VerifyTargetRefFn; createRefFn: CreateTargetRefFn } {
  return {
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir }),
    createRefFn: (name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { keyDir, coords }),
  }
}

/** 按脚本名分发的驱动 mock：send-file.ps1 消费 sendStages 队列（逐次出队） */
interface DriverMock {
  calls: PowerShellScriptOptions[]
  sendStages: Array<Record<string, unknown> | Error>
  searchResult: Record<string, unknown> | Error
  selectResult: Record<string, unknown> | Error
}

function makeRunDriver(mock: DriverMock): RunPowerShellDriverFn {
  return (async (opts: PowerShellScriptOptions) => {
    mock.calls.push(opts)
    const base = opts.script.replace(/^.*[\\/]/, '')
    if (base === 'send-file.ps1') {
      const stage = mock.sendStages.shift()
      if (stage === undefined) throw new Error('send-file.ps1 出现未预期的额外调用')
      if (stage instanceof Error) throw stage
      return stage
    }
    if (base === 'chat-search.ps1') {
      if (mock.searchResult instanceof Error) throw mock.searchResult
      return mock.searchResult
    }
    if (base === 'chat-select.ps1') {
      if (mock.selectResult instanceof Error) throw mock.selectResult
      return mock.selectResult
    }
    throw new Error(`未预期的驱动脚本：${base}`)
  }) as RunPowerShellDriverFn
}

function makeOp(keyDir: string, root: string, mock: DriverMock) {
  return createWecomSendFileOperation({
    runDriverFn: makeRunDriver(mock),
    artifactDirFn: () => root,
    ...realDeps(keyDir),
  })
}

const NAV_REQUIRED = { navigate_required: true, reason: '标题归一化规则未命中（当前标题「张三」≠ 目标「文件传输助手」）', title: '张三', timing_ms: { precheck: 900 }, screenshot_paths: [] }

const SENT_OK = {
  navigate_required: false,
  target: { name: '文件传输助手', subtitle: '', section: 'other' },
  title: '文件传输助手',
  sent_verification: { method: 'jev', result: 'sent' },
  paste_check: { stem: 'e3-test-fil', paste_hit: true, input_gone: true, list_hit: true },
  input_point: { x: 640, y: 648, source: 'jev' },
  timing_ms: { precheck: 900, jev1: 812, clipboard: 300, paste: 3100, send_wait: 1900, total: 9200 },
  screenshot_paths: ['step1-precheck.png', 'step2-baseline.png', 'step3-pasted.png', 'step4-after.png'],
}

const SEARCH_OK = {
  query: '文件传输助手',
  items: [{ name: '文件传输助手', subtitle: '', section: '应用提醒', x: 200, y: 60, probability: 0.9 }],
  best_index: 0,
  overlay: { x: 100, y: 50, w: 400, h: 542 },
  jev: { used: true, latency_ms: 700, is_ambiguous: false, best_confidence: 0.9 },
  timing_ms: { focus: 250, total: 5300 },
}

const SELECT_OK = {
  target: { name: '文件传输助手', subtitle: '', section: 'other' },
  title: '文件传输助手',
  clicked: { x: 300, y: 110 },
  timing_ms: { overlay_check: 30, total: 4600 },
  screenshot_paths: ['overlay-preclick.png', 'main-opened.png'],
}

function sendFileArtifactDir(call: PowerShellScriptOptions): string {
  const i = call.args?.indexOf('-ArtifactDir') ?? -1
  assert.ok(i !== -1 && typeof call.args?.[i + 1] === 'string', 'send-file 驱动参数应含 -ArtifactDir')
  return call.args![i + 1]! as string
}

function argAfter(call: PowerShellScriptOptions, flag: string): string {
  const i = call.args?.indexOf(flag) ?? -1
  assert.ok(i !== -1, `驱动参数应含 ${flag}`)
  return call.args![i + 1]! as string
}

test('M8 参数校验矩阵：文件不存在 / 是目录 / 超 100MB / 无坐标旧 ref / 相对路径 → INVALID_ARGUMENT，均不调驱动', async () => {
  const keyDir = makeKeyDir()
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const oldRef = createTargetRef('文件传输助手', 'other', '', { keyDir }) // 无坐标（旧版签发）
  const mock: DriverMock = { calls: [], sendStages: [], searchResult: {}, selectResult: {} }
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const filePath = makeFile()

  const r1 = await op.execute({ target_ref: ref, file_path: join(filePath, '..', 'missing.txt') }, silentCtx())
  assert.equal(r1.code, 'INVALID_ARGUMENT')
  assert.match(r1.message, /不存在/)
  assert.equal(r1.effect, 'none')

  const dirPath = mkdtempSync(join(tmpdir(), 'wecom-send-file-dir-'))
  tempDirs.push(dirPath)
  const r2 = await op.execute({ target_ref: ref, file_path: dirPath }, silentCtx())
  assert.equal(r2.code, 'INVALID_ARGUMENT')
  assert.match(r2.message, /不是常规文件/)

  const bigPath = makeFile('big.zip', 100 * 1024 * 1024 + 1)
  const r3 = await op.execute({ target_ref: ref, file_path: bigPath }, silentCtx())
  assert.equal(r3.code, 'INVALID_ARGUMENT')
  assert.match(r3.message, /100MB/)

  const r4 = await op.execute({ target_ref: oldRef, file_path: filePath }, silentCtx())
  assert.equal(r4.code, 'INVALID_ARGUMENT')
  assert.match(r4.message, /不含坐标/)

  const r5 = await op.execute({ target_ref: ref, file_path: 'relative.txt' }, silentCtx())
  assert.equal(r5.code, 'INVALID_ARGUMENT')
  assert.match(r5.message, /绝对路径/)

  // 极短文件名（去空白后 <3 字符）：主干/全名都只有 1-2 字符，OCR contains 判据无法
  // 与无关 token 区分 → 拒发（注意 a.txt 不在拒绝范围——驱动回退全名匹配键）
  const r6 = await op.execute({ target_ref: ref, file_path: makeFile('ab') }, silentCtx())
  assert.equal(r6.code, 'INVALID_ARGUMENT')
  assert.match(r6.message, /文件名过短/)
  assert.equal(r6.effect, 'none')
  const r7 = await op.execute({ target_ref: ref, file_path: makeFile('a') }, silentCtx())
  assert.equal(r7.code, 'INVALID_ARGUMENT')
  assert.match(r7.message, /文件名过短/)

  assert.equal(mock.calls.length, 0, '校验失败绝不 spawn 驱动')
})

test('M8 快路径：navigate_required=false → 成功透传粘贴判据字段与文件摘要，不触发 search/select', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = { calls: [], sendStages: [{ ...SENT_OK }], searchResult: {}, selectResult: {} }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, root, mock)
  const filePath = makeFile('report.pdf', 4096)
  const r = await op.execute({ target_ref: ref, file_path: filePath }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'applied')
  assert.equal(r.retryable, false)
  assert.equal(r.data.target, '文件传输助手')
  assert.equal(r.data.title, '文件传输助手')
  assert.equal(r.data.navigated, false)
  assert.deepEqual(r.data.sent_verification, { method: 'jev', result: 'sent' })
  assert.deepEqual(r.data.paste_check, { stem: 'e3-test-fil', paste_hit: true, input_gone: true, list_hit: true })
  const f = r.data.file as { name: string; size_bytes: number; sha256: string }
  assert.equal(f.name, 'report.pdf')
  assert.equal(f.size_bytes, 4096)
  assert.match(f.sha256, /^[0-9a-f]{64}$/)
  assert.equal((r.data.timing_ms as Record<string, number>).total, 9200)
  assert.equal((r.data.screenshot_paths as string[]).length, 4)
  assert.match(r.message, /已向「文件传输助手」发送文件消息/)
  assert.match(r.message, /当前会话直发/)
  // 只调一次 send-file 驱动；参数含身份 + FilePath/FileHash + send-file-<ts> artifact 目录（先建后用）
  assert.equal(mock.calls.length, 1)
  assert.ok(mock.calls[0]!.script.endsWith('send-file.ps1'))
  assert.deepEqual(
    mock.calls[0]!.args?.slice(0, 10),
    ['-TargetName', '文件传输助手', '-Subtitle', '', '-Section', 'other', '-FilePath', filePath, '-FileHash', f.sha256],
  )
  const dir = sendFileArtifactDir(mock.calls[0]!)
  assert.ok(dir.startsWith(root) && /send-file-\d{4}-\d{2}-\d{2}T/.test(dir), dir)
  assert.ok(existsSync(dir), 'artifact 目录先建后用')
})

test('M8 分发路径：第一轮 navigate_required=true → chatSearch → chatSelect → 二次 send-file 成功', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = {
    calls: [],
    sendStages: [
      { ...NAV_REQUIRED },
      { navigate_required: false, title: '文件传输助手', sent_verification: { method: 'rule_2of2', result: 'sent', rule_checks_passed: 2 }, paste_check: { stem: 'report', paste_hit: true, input_gone: true, list_hit: true }, input_point: { x: 640, y: 648, source: 'ratio' }, timing_ms: { total: 14000 }, screenshot_paths: ['a.png'] },
    ],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, root, mock)
  const filePath = makeFile()
  const r = await op.execute({ target_ref: ref, file_path: filePath }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'applied')
  assert.equal(r.data.navigated, true)
  assert.equal((r.data.sent_verification as Record<string, unknown>).method, 'rule_2of2')
  assert.deepEqual(r.data.paste_check, { stem: 'report', paste_hit: true, input_gone: true, list_hit: true })
  assert.match(r.message, /已自动搜索并切换会话/)
  // 编排顺序：send-file → search → select → send-file
  assert.equal(mock.calls.length, 4)
  assert.ok(mock.calls[0]!.script.endsWith('send-file.ps1'))
  assert.ok(mock.calls[1]!.script.endsWith('chat-search.ps1'))
  assert.ok(mock.calls[2]!.script.endsWith('chat-select.ps1'))
  assert.ok(mock.calls[3]!.script.endsWith('send-file.ps1'))
  assert.deepEqual(mock.calls[1]!.args?.slice(0, 2), ['-Query', '文件传输助手'])
  // 两次 send-file 驱动身份/文件参数一致、artifact 目录相互独立
  assert.equal(argAfter(mock.calls[0]!, '-FilePath'), filePath)
  assert.equal(argAfter(mock.calls[3]!, '-FilePath'), filePath)
  assert.equal(argAfter(mock.calls[0]!, '-FileHash'), argAfter(mock.calls[3]!, '-FileHash'))
  const dir1 = sendFileArtifactDir(mock.calls[0]!)
  const dir2 = sendFileArtifactDir(mock.calls[3]!)
  assert.notEqual(dir1, dir2, '两轮 send-file 的 artifact 目录必须独立')
  assert.ok(existsSync(dir1) && existsSync(dir2))
})

test('M8 短主干文件（a.txt）不在 TS 拒发范围：驱动侧回退完整文件名匹配键，正常放行', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = { calls: [], sendStages: [{ ...SENT_OK }], searchResult: {}, selectResult: {} }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, file_path: makeFile('a.txt', 32) }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'applied')
  assert.equal(mock.calls.length, 1, '「a.txt」去空白后 5 字符，应放行到驱动（全名匹配键）')
})

test('M8 两轮都 navigate_required → TARGET_NOT_FOUND（message 写明两轮 + 未发送文件）', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }, { navigate_required: true, reason: 'Jev 判定当前会话非目标（unclear）', title: '张三', timing_ms: {}, screenshot_paths: [] }],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, file_path: makeFile() }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.match(r.message, /两轮/)
  assert.match(r.message, /未发送文件/)
  assert.equal(mock.calls.length, 4)
})

test('M8 驱动剪贴板失败（CONFIG_MISSING）→ 透传，effect=none（粘贴阶段失败，未发送）', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [new CodedOperationError('CONFIG_MISSING', '剪贴板写入文件列表失败（重试 5 次均失败），无法经剪贴板粘贴通道输入文件')],
    searchResult: {},
    selectResult: {},
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, file_path: makeFile() }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CONFIG_MISSING')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /剪贴板写入文件列表失败/)
  assert.equal(mock.calls.length, 1, '不重试')
})

test('M8 粘贴校验失败（UI_CHANGED：文件卡片未出现）→ 透传，effect=none（未按 Enter 无发送副作用）', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [new CodedOperationError('UI_CHANGED', '文件卡片未出现（粘贴可能未生效）：输入区 OCR 未读到文件名主干「e3-test-fil」…已中止且未按 Enter 发送')],
    searchResult: {},
    selectResult: {},
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, file_path: makeFile('e3-test-file.txt') }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'UI_CHANGED')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /文件卡片未出现/)
  assert.equal(mock.calls.length, 1, '不重试')
})

test('M8 驱动超时（RESULT_TIMEOUT）→ EXECUTION_UNKNOWN + effect=unknown，绝不自动重试', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [new CodedOperationError('RESULT_TIMEOUT', 'PowerShell 驱动执行超时（>300s），已终止子进程')],
    searchResult: {},
    selectResult: {},
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, file_path: makeFile() }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'EXECUTION_UNKNOWN')
  assert.equal(r.effect, 'unknown')
  assert.equal(r.retryable, false)
  assert.match(r.message, /文件消息可能已发出/)
  assert.equal(mock.calls.length, 1, '超时不触发第二次 send-file')
})

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

function runCli(args: string[]): { status: number | null; stdout: string } {
  const r = spawnSync(process.execPath, [CLI, ...args], { encoding: 'utf8', timeout: 30000 })
  return { status: r.status, stdout: r.stdout ?? '' }
}

test('M8 send-file --json：缺 --target-ref/--file → INVALID_ARGUMENT，退出码 2，不触达企微', () => {
  const { status, stdout } = runCli(['send-file', '--file', makeFile(), '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout) as { success: boolean; code: string }
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
})

test('M8 send-file --json：--file 指向不存在文件 → INVALID_ARGUMENT，退出码 2（文件校验阶段即失败）', () => {
  const { status, stdout } = runCli(['send-file', '--target-ref', 'ref-not-verified-but-file-fails-first', '--file', join(tmpdir(), 'wecom-send-file-missing.txt'), '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout) as { success: boolean; code: string }
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
  assert.match((JSON.parse(stdout) as { message: string }).message, /不存在/)
})

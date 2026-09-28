/**
 * wecom_send_image M7 契约测试（全 mock，绝不触达真实企微窗口/驱动脚本——runDriverFn
 * 注入按脚本名分发的 mock，verifyRefFn/createRefFn 注入临时 keyDir 的真实签发/验证；
 * CLI 退出码用例只走参数/文件校验失败路径，不 spawn 驱动）。
 *
 * 覆盖：参数校验矩阵（图片不存在 / 扩展名不符 / 超 20MB / 无坐标旧 ref，均
 * INVALID_ARGUMENT 且不调驱动）/ 快路径成功（navigate_required=false → 透传方差字段
 * input_stddev 与 image 摘要）/ 分发路径编排顺序（send-image → search → select →
 * send-image，两轮驱动参数一致）/ 两轮 navigate_required → TARGET_NOT_FOUND（未发送
 * 图片）/ 驱动剪贴板失败 CONFIG_MISSING 透传 / 驱动超时 RESULT_TIMEOUT →
 * EXECUTION_UNKNOWN（effect=unknown）/ send-image --json 退出码（缺参数与图片不存在
 * 均退出码 2）。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test, { after } from 'node:test'
import { createWecomSendImageOperation } from '../src/operations/sendImage.js'
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
  const dir = mkdtempSync(join(tmpdir(), 'wecom-send-image-key-'))
  tempDirs.push(dir)
  return dir
}

function makeArtifactRoot(): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-send-image-test-'))
  tempDirs.push(dir)
  return dir
}

/** 建一张真实小图片文件（内容不必是合法 png——TS 只校验存在/大小/扩展名） */
function makeImageFile(name = 'test.png', size = 64): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-send-image-img-'))
  tempDirs.push(dir)
  const p = join(dir, name)
  writeFileSync(p, Buffer.alloc(size, 0x89))
  return p
}

/** 真实签发 + 真实验证（临时 keyDir）：内部 chatSearch 签发的 ref 由同一 key 验证 */
function realDeps(keyDir: string): { verifyRefFn: VerifyTargetRefFn; createRefFn: CreateTargetRefFn } {
  return {
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir }),
    createRefFn: (name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { keyDir, coords }),
  }
}

/** 按脚本名分发的驱动 mock：send-image.ps1 消费 sendStages 队列（逐次出队） */
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
    if (base === 'send-image.ps1') {
      const stage = mock.sendStages.shift()
      if (stage === undefined) throw new Error('send-image.ps1 出现未预期的额外调用')
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
  return createWecomSendImageOperation({
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
  input_stddev: { before: 9.8, paste: 36.5, after: 9.9 },
  input_point: { x: 640, y: 648, source: 'jev' },
  timing_ms: { precheck: 900, jev1: 812, clipboard: 300, paste: 1800, send_wait: 1900, total: 8600 },
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

function sendImageArtifactDir(call: PowerShellScriptOptions): string {
  const i = call.args?.indexOf('-ArtifactDir') ?? -1
  assert.ok(i !== -1 && typeof call.args?.[i + 1] === 'string', 'send-image 驱动参数应含 -ArtifactDir')
  return call.args![i + 1]! as string
}

function argAfter(call: PowerShellScriptOptions, flag: string): string {
  const i = call.args?.indexOf(flag) ?? -1
  assert.ok(i !== -1, `驱动参数应含 ${flag}`)
  return call.args![i + 1]! as string
}

test('M7 参数校验矩阵：图片不存在 / 扩展名不符 / 超 20MB / 无坐标旧 ref → INVALID_ARGUMENT，均不调驱动', async () => {
  const keyDir = makeKeyDir()
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const oldRef = createTargetRef('文件传输助手', 'other', '', { keyDir }) // 无坐标（旧版签发）
  const mock: DriverMock = { calls: [], sendStages: [], searchResult: {}, selectResult: {} }
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const imagePath = makeImageFile()

  const r1 = await op.execute({ target_ref: ref, image_path: join(imagePath, '..', 'missing.png') }, silentCtx())
  assert.equal(r1.code, 'INVALID_ARGUMENT')
  assert.match(r1.message, /不存在/)
  assert.equal(r1.effect, 'none')

  const r2 = await op.execute({ target_ref: ref, image_path: makeImageFile('notes.txt') }, silentCtx())
  assert.equal(r2.code, 'INVALID_ARGUMENT')
  assert.match(r2.message, /png\/jpg\/jpeg\/bmp\/gif/)

  const bigPath = makeImageFile('big.png', 20 * 1024 * 1024 + 1)
  const r3 = await op.execute({ target_ref: ref, image_path: bigPath }, silentCtx())
  assert.equal(r3.code, 'INVALID_ARGUMENT')
  assert.match(r3.message, /20MB/)

  const r4 = await op.execute({ target_ref: oldRef, image_path: imagePath }, silentCtx())
  assert.equal(r4.code, 'INVALID_ARGUMENT')
  assert.match(r4.message, /不含坐标/)

  const r5 = await op.execute({ target_ref: ref, image_path: 'relative.png' }, silentCtx())
  assert.equal(r5.code, 'INVALID_ARGUMENT')
  assert.match(r5.message, /绝对路径/)

  assert.equal(mock.calls.length, 0, '校验失败绝不 spawn 驱动')
})

test('M7 快路径：navigate_required=false → 成功透传方差字段与图片摘要，不触发 search/select', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = { calls: [], sendStages: [{ ...SENT_OK }], searchResult: {}, selectResult: {} }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, root, mock)
  const imagePath = makeImageFile('chart.png', 4096)
  const r = await op.execute({ target_ref: ref, image_path: imagePath }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'applied')
  assert.equal(r.retryable, false)
  assert.equal(r.data.target, '文件传输助手')
  assert.equal(r.data.title, '文件传输助手')
  assert.equal(r.data.navigated, false)
  assert.deepEqual(r.data.sent_verification, { method: 'jev', result: 'sent' })
  assert.deepEqual(r.data.input_stddev, { before: 9.8, paste: 36.5, after: 9.9 })
  const img = r.data.image as { name: string; size_bytes: number; sha256: string }
  assert.equal(img.name, 'chart.png')
  assert.equal(img.size_bytes, 4096)
  assert.match(img.sha256, /^[0-9a-f]{64}$/)
  assert.equal((r.data.timing_ms as Record<string, number>).total, 8600)
  assert.equal((r.data.screenshot_paths as string[]).length, 4)
  assert.match(r.message, /已向「文件传输助手」发送图片消息/)
  assert.match(r.message, /当前会话直发/)
  // 只调一次 send-image 驱动；参数含身份 + ImagePath/ImageHash + send-image-<ts> artifact 目录（先建后用）
  assert.equal(mock.calls.length, 1)
  assert.ok(mock.calls[0]!.script.endsWith('send-image.ps1'))
  assert.deepEqual(
    mock.calls[0]!.args?.slice(0, 10),
    ['-TargetName', '文件传输助手', '-Subtitle', '', '-Section', 'other', '-ImagePath', imagePath, '-ImageHash', img.sha256],
  )
  const dir = sendImageArtifactDir(mock.calls[0]!)
  assert.ok(dir.startsWith(root) && /send-image-\d{4}-\d{2}-\d{2}T/.test(dir), dir)
  assert.ok(existsSync(dir), 'artifact 目录先建后用')
})

test('M7 分发路径：第一轮 navigate_required=true → chatSearch → chatSelect → 二次 send-image 成功', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = {
    calls: [],
    sendStages: [
      { ...NAV_REQUIRED },
      { navigate_required: false, title: '文件传输助手', sent_verification: { method: 'rule_2of2', result: 'sent', rule_checks_passed: 2 }, input_stddev: { before: 9.8, paste: 36.5, after: 9.7 }, input_point: { x: 640, y: 648, source: 'ratio' }, timing_ms: { total: 14000 }, screenshot_paths: ['a.png'] },
    ],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, root, mock)
  const imagePath = makeImageFile()
  const r = await op.execute({ target_ref: ref, image_path: imagePath }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'applied')
  assert.equal(r.data.navigated, true)
  assert.equal((r.data.sent_verification as Record<string, unknown>).method, 'rule_2of2')
  assert.deepEqual(r.data.input_stddev, { before: 9.8, paste: 36.5, after: 9.7 })
  assert.match(r.message, /已自动搜索并切换会话/)
  // 编排顺序：send-image → search → select → send-image
  assert.equal(mock.calls.length, 4)
  assert.ok(mock.calls[0]!.script.endsWith('send-image.ps1'))
  assert.ok(mock.calls[1]!.script.endsWith('chat-search.ps1'))
  assert.ok(mock.calls[2]!.script.endsWith('chat-select.ps1'))
  assert.ok(mock.calls[3]!.script.endsWith('send-image.ps1'))
  assert.deepEqual(mock.calls[1]!.args?.slice(0, 2), ['-Query', '文件传输助手'])
  // 两次 send-image 驱动身份/图片参数一致、artifact 目录相互独立
  assert.equal(argAfter(mock.calls[0]!, '-ImagePath'), imagePath)
  assert.equal(argAfter(mock.calls[3]!, '-ImagePath'), imagePath)
  assert.equal(argAfter(mock.calls[0]!, '-ImageHash'), argAfter(mock.calls[3]!, '-ImageHash'))
  const dir1 = sendImageArtifactDir(mock.calls[0]!)
  const dir2 = sendImageArtifactDir(mock.calls[3]!)
  assert.notEqual(dir1, dir2, '两轮 send-image 的 artifact 目录必须独立')
  assert.ok(existsSync(dir1) && existsSync(dir2))
})

test('M7 两轮都 navigate_required → TARGET_NOT_FOUND（message 写明两轮 + 未发送图片）', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }, { navigate_required: true, reason: 'Jev 判定当前会话非目标（unclear）', title: '张三', timing_ms: {}, screenshot_paths: [] }],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, image_path: makeImageFile() }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.match(r.message, /两轮/)
  assert.match(r.message, /未发送图片/)
  assert.equal(mock.calls.length, 4)
})

test('M7 驱动剪贴板失败（CONFIG_MISSING）→ 透传，effect=none（粘贴阶段失败，未发送）', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [new CodedOperationError('CONFIG_MISSING', '剪贴板写入图片失败（重试 5 次均失败），无法经剪贴板粘贴通道输入图片')],
    searchResult: {},
    selectResult: {},
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, image_path: makeImageFile() }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CONFIG_MISSING')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /剪贴板写入图片失败/)
  assert.equal(mock.calls.length, 1, '不重试')
})

test('M7 驱动超时（RESULT_TIMEOUT）→ EXECUTION_UNKNOWN + effect=unknown，绝不自动重试', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [new CodedOperationError('RESULT_TIMEOUT', 'PowerShell 驱动执行超时（>300s），已终止子进程')],
    searchResult: {},
    selectResult: {},
  }
  const ref = createTargetRef('文件传输助手', 'other', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, image_path: makeImageFile() }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'EXECUTION_UNKNOWN')
  assert.equal(r.effect, 'unknown')
  assert.equal(r.retryable, false)
  assert.match(r.message, /图片消息可能已发出/)
  assert.equal(mock.calls.length, 1, '超时不触发第二次 send-image')
})

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

function runCli(args: string[]): { status: number | null; stdout: string } {
  const r = spawnSync(process.execPath, [CLI, ...args], { encoding: 'utf8', timeout: 30000 })
  return { status: r.status, stdout: r.stdout ?? '' }
}

test('M7 send-image --json：缺 --target-ref/--image → INVALID_ARGUMENT，退出码 2，不触达企微', () => {
  const { status, stdout } = runCli(['send-image', '--image', makeImageFile(), '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout) as { success: boolean; code: string }
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
})

test('M7 send-image --json：--image 指向不存在文件 → INVALID_ARGUMENT，退出码 2（文件校验阶段即失败）', () => {
  const { status, stdout } = runCli(['send-image', '--target-ref', 'ref-not-verified-but-image-fails-first', '--image', join(tmpdir(), 'wecom-send-image-missing.png'), '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout) as { success: boolean; code: string }
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
  assert.match((JSON.parse(stdout) as { message: string }).message, /不存在/)
})

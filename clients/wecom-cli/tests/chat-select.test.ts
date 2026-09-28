/**
 * wecom_chat_select M5 契约测试（点击 search 结果进入会话；全 mock，绝不触达真实
 * 企微窗口/驱动脚本——runDriverFn 注入 mock，verifyRefFn 注入临时 keyDir 或直接抛错）。
 *
 * 覆盖：成功透传（target/title/clicked/timing/screenshot + 驱动参数与超时预算）/
 * 旧版无坐标 ref → INVALID_ARGUMENT / verifyRef 过期 → TARGET_REF_STALE /
 * 驱动 TARGET_REF_STALE（面板关闭）与 UI_CHANGED 透传 / 驱动超时 → EXECUTION_UNKNOWN /
 * 取消 → CANCELLED / 参数校验。CLI --json 渲染退出码见 cli-commands.test.ts。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomChatSelectOperation } from '../src/operations/chatSelect.js'
import { createTargetRef, verifyTargetRef, type VerifyTargetRefFn } from '../src/platform/targetRef.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const tempDirs: string[] = []
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

function makeKeyDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-chat-select-key-'))
  tempDirs.push(dir)
  return dir
}

function makeArtifactRoot(): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-chat-select-test-'))
  tempDirs.push(dir)
  return dir
}

/** 真实签发 + 真实验证（临时 keyDir），ref 携带 overlay 相对坐标 */
function realVerify(keyDir: string): VerifyTargetRefFn {
  return (ref) => verifyTargetRef(ref, { keyDir })
}

test('成功：驱动字段透传（target/title/clicked/timing），驱动参数含坐标与 120s 超时', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir, coords: { x: 200, y: 120 } })
  const calls: PowerShellScriptOptions[] = []
  const op = createWecomChatSelectOperation({
    runDriverFn: (async (opts) => {
      calls.push(opts)
      return {
        target: { name: '陆伟@微信', subtitle: '微信联系人', section: 'contact' },
        title: '陆伟',
        clicked: { x: 356, y: 172 },
        timing_ms: { overlay_check: 30, verify: 1800, click: 180, close_wait: 320, title_check: 2100, total: 4600 },
        screenshot_paths: [join(root, 'overlay-preclick.png'), join(root, 'main-opened.png')],
      }
    }) as RunPowerShellDriverFn,
    verifyRefFn: realVerify(keyDir),
    artifactDirFn: () => root,
  })
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  const target = r.data.target as Record<string, unknown>
  assert.equal(target.name, '陆伟@微信')
  assert.equal(target.subtitle, '微信联系人')
  assert.equal(target.section, 'contact')
  assert.equal(r.data.title, '陆伟')
  assert.deepEqual(r.data.clicked, { x: 356, y: 172 })
  assert.deepEqual(r.data.timing_ms, { overlay_check: 30, verify: 1800, click: 180, close_wait: 320, title_check: 2100, total: 4600 })
  assert.equal((r.data.screenshot_paths as string[]).length, 2)
  assert.match(r.message, /已进入会话「陆伟」/)
  assert.match(r.message, /清除其未读角标/)
  // 驱动调用契约：chat-select.ps1 + payload 坐标 + select-<ts> artifact 目录 + 120s 超时预算
  assert.equal(calls.length, 1)
  assert.ok(calls[0]!.script.endsWith('chat-select.ps1'))
  assert.deepEqual(
    calls[0]!.args?.slice(0, 10),
    ['-TargetName', '陆伟@微信', '-Subtitle', '微信联系人', '-Section', 'contact', '-X', '200', '-Y', '120'],
  )
  assert.equal(calls[0]!.args?.[10], '-ArtifactDir')
  const artifactArg = calls[0]!.args?.[11]
  assert.ok(typeof artifactArg === 'string' && artifactArg.startsWith(root) && /select-\d{4}-\d{2}-\d{2}T/.test(artifactArg))
  assert.ok(typeof artifactArg === 'string' && existsSync(artifactArg), 'artifact 目录先建后用')
  assert.equal(calls[0]!.timeoutMs, 120_000)
})

test('旧版无坐标 ref → INVALID_ARGUMENT（提示重新 search），不调驱动', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const oldRef = createTargetRef('张三', 'contact', '', { keyDir }) // 无 coords：老格式
  let driverCalled = false
  const op = createWecomChatSelectOperation({
    runDriverFn: (async () => {
      driverCalled = true
      return {}
    }) as RunPowerShellDriverFn,
    verifyRefFn: realVerify(keyDir),
    artifactDirFn: () => root,
  })
  const r = await op.execute({ target_ref: oldRef }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /不含坐标/)
  assert.match(r.message, /重新 search/)
  assert.equal(driverCalled, false)
})

test('verifyRef 过期 → TARGET_REF_STALE 透传，不调驱动', async () => {
  const root = makeArtifactRoot()
  let driverCalled = false
  const op = createWecomChatSelectOperation({
    runDriverFn: (async () => {
      driverCalled = true
      return {}
    }) as RunPowerShellDriverFn,
    verifyRefFn: () => {
      throw new CodedOperationError('TARGET_REF_STALE', 'target_ref 已过期（有效期 5 分钟），请重新搜索获取')
    },
    artifactDirFn: () => root,
  })
  const r = await op.execute({ target_ref: 'whatever' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_REF_STALE')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.equal(driverCalled, false)
})

test('驱动 TARGET_REF_STALE（面板已关闭）→ code/message 透传（含「重新 search」指引）', async () => {
  const keyDir = makeKeyDir()
  const ref = createTargetRef('陆伟@微信', 'contact', '', { keyDir, coords: { x: 200, y: 120 } })
  const op = createWecomChatSelectOperation({
    runDriverFn: (async () => {
      throw new CodedOperationError(
        'TARGET_REF_STALE',
        '搜索结果面板已关闭（SearchResultWindow2 不可见），target_ref 失效，请重新 search 获取新的 target_ref',
      )
    }) as RunPowerShellDriverFn,
    verifyRefFn: realVerify(keyDir),
    artifactDirFn: () => makeArtifactRoot(),
  })
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_REF_STALE')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /SearchResultWindow2 不可见/)
  assert.match(r.message, /重新 search/)
})

test('驱动 UI_CHANGED（面板内容/标题不符）→ 透传，effect=none', async () => {
  const keyDir = makeKeyDir()
  const ref = createTargetRef('陆伟', 'contact', '', { keyDir, coords: { x: 200, y: 60 } })
  const op = createWecomChatSelectOperation({
    runDriverFn: (async () => {
      throw new CodedOperationError('UI_CHANGED', '会话标题「陆伟民」与目标「陆伟」不一致，进入的会话与 target_ref 不符，已判失败')
    }) as RunPowerShellDriverFn,
    verifyRefFn: realVerify(keyDir),
    artifactDirFn: () => makeArtifactRoot(),
  })
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'UI_CHANGED')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /陆伟民/)
})

test('驱动超时（RESULT_TIMEOUT）→ EXECUTION_UNKNOWN（不自动重试），effect=none', async () => {
  const keyDir = makeKeyDir()
  const ref = createTargetRef('产品讨论群', 'group', '3人', { keyDir, coords: { x: 210, y: 200 } })
  const op = createWecomChatSelectOperation({
    runDriverFn: (async () => {
      throw new CodedOperationError('RESULT_TIMEOUT', 'PowerShell 驱动执行超时（>120s），已终止子进程')
    }) as RunPowerShellDriverFn,
    verifyRefFn: realVerify(keyDir),
    artifactDirFn: () => makeArtifactRoot(),
  })
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'EXECUTION_UNKNOWN')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /超时/)
  assert.match(r.message, /人工核对/)
})

test('驱动被取消（CancelledError）→ CANCELLED，effect=none', async () => {
  const keyDir = makeKeyDir()
  const ref = createTargetRef('产品讨论群', 'group', '', { keyDir, coords: { x: 210, y: 200 } })
  const op = createWecomChatSelectOperation({
    runDriverFn: (async () => {
      throw new CancelledError()
    }) as RunPowerShellDriverFn,
    verifyRefFn: realVerify(keyDir),
    artifactDirFn: () => makeArtifactRoot(),
  })
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'none')
})

test('参数校验：target_ref 缺失/非字符串 → INVALID_ARGUMENT，不调驱动', async () => {
  let driverCalled = false
  const op = createWecomChatSelectOperation({
    runDriverFn: (async () => {
      driverCalled = true
      return {}
    }) as RunPowerShellDriverFn,
    artifactDirFn: () => makeArtifactRoot(),
  })
  const r1 = await op.execute({} as { target_ref: string }, silentCtx())
  assert.equal(r1.success, false)
  assert.equal(r1.code, 'INVALID_ARGUMENT')
  const r2 = await op.execute({ target_ref: '' }, silentCtx())
  assert.equal(r2.code, 'INVALID_ARGUMENT')
  assert.equal(driverCalled, false)
})

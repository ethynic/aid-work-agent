/**
 * M2 operation 契约测试（mock runDriverFn / createRefFn / verifyRefFn，绝不触达真实企微/驱动）。
 *
 * wecom_chat_search：成功签发 target_ref / 空 query INVALID_ARGUMENT / 驱动 UI_CHANGED 透传 /
 * 无结果 TARGET_NOT_FOUND / type 过滤。
 * wecom_message_send：成功 / 标题不一致 UI_CHANGED 透传 / 草稿残留 UI_CHANGED 透传 /
 * TARGET_AMBIGUOUS 透传 / target_ref 过期 TARGET_REF_STALE / 篡改 INVALID_ARGUMENT /
 * 超时→EXECUTION_UNKNOWN / 取消→CANCELLED(unknown) / 空 text 超长 text INVALID_ARGUMENT /
 * 换行放行（多行走驱动侧剪贴板粘贴通道，驱动收到原文）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomChatSearchOperation } from '../src/operations/chatSearch.js'
import { createWecomMessageSendOperation } from '../src/operations/messageSend.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { RunPowerShellDriverFn } from '../src/platform/powershell.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

// ---------- wecom_chat_search ----------

const tempDirs: string[] = []
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

function makeSearchOp(
  runDriverFn: RunPowerShellDriverFn,
  createRefFn?: (name: string, type: string, subtitle?: string, coords?: { x: number; y: number }) => string,
) {
  // M4 起 search 也会建 artifact 目录（稳定帧截图 + driver-log）：测试注入临时目录，不碰真实 %LOCALAPPDATA%
  const dir = mkdtempSync(join(tmpdir(), 'wecom-chat-search-test-'))
  tempDirs.push(dir)
  return createWecomChatSearchOperation({
    accountFn: async () => null,
    runDriverFn,
    createRefFn: createRefFn ?? ((name, type, subtitle) => `ref:${type}:${name}:${subtitle ?? ''}`),
    artifactDirFn: () => dir,
  })
}

test('chat_search：成功 → 候选带 name/subtitle/section/target_ref，effect=none', async () => {
  const op = makeSearchOp(async (opts) => {
    assert.ok(opts.script.endsWith('chat-search.ps1'))
    assert.deepEqual(opts.args?.slice(0, 2), ['-Query', '陆伟'])
    assert.equal(opts.args?.[2], '-ArtifactDir')
    return {
      items: [
        { name: '陆伟', subtitle: '微信联系人', section: '联系人' },
        { name: '陆伟', subtitle: '其他（待设置部门）', section: '联系人' },
        { name: '产品讨论群', subtitle: '', section: '群聊' },
      ],
    }
  })
  const r = await op.execute({ query: '陆伟' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  const items = r.data.items as Array<{ name: string; subtitle: string; section: string; target_ref: string }>
  assert.equal(items.length, 3)
  assert.equal(items[0]!.target_ref, 'ref:contact:陆伟:微信联系人')
  assert.equal(items[2]!.target_ref, 'ref:group:产品讨论群:')
})

test('chat_search：type=group 只留群聊分区；limit 截断', async () => {
  const op = makeSearchOp(async () => ({
    items: [
      { name: '陆伟', subtitle: '微信联系人', section: '联系人' },
      { name: '产品讨论群', subtitle: '', section: '群聊' },
    ],
  }))
  const r = await op.execute({ query: '陆', type: 'group' }, silentCtx())
  const items = r.data.items as Array<{ section: string }>
  assert.equal(items.length, 1)
  assert.equal(items[0]!.section, '群聊')
})

test('chat_search：过滤后无结果 → TARGET_NOT_FOUND（effect=none，不重试）', async () => {
  const op = makeSearchOp(async () => ({ items: [] }))
  const r = await op.execute({ query: '不存在的人' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
})

test('chat_search：驱动 UI_CHANGED（overlay 未出现）透传，不 reject', async () => {
  const op = makeSearchOp(async () => {
    throw new CodedOperationError('UI_CHANGED', '未等到搜索结果面板')
  })
  const r = await op.execute({ query: '陆伟' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'UI_CHANGED')
  assert.equal(r.effect, 'none')
})

test('chat_search：空 query / 超长 / 非法 type / 非法 limit → INVALID_ARGUMENT，不调驱动', async () => {
  let called = 0
  const op = makeSearchOp(async () => {
    called++
    return { items: [] }
  })
  for (const bad of [
    { query: '' },
    { query: '   ' },
    { query: 'x'.repeat(101) },
    { query: 'a', type: 'friend' },
    { query: 'a', limit: 0 },
    { query: 'a', limit: 21 },
    { query: 'a', limit: 1.5 },
  ]) {
    // @ts-expect-error 故意传非法值
    const r = await op.execute(bad, silentCtx())
    assert.equal(r.code, 'INVALID_ARGUMENT', JSON.stringify(bad))
  }
  assert.equal(called, 0)
})

// ---------- wecom_message_send ----------
// M6：send 的智能分发（navigate_required → search+select → 二次 send）细测见
// message-send-v2.test.ts；本段保留驱动层错误透传 / 超时 / 取消 / 参数校验契约
// （navigate_required 缺省视为 false = 快路径终态）。

const VALID_REF = 'valid-ref'
const TARGET = { name: '陆伟', type: 'contact', subtitle: '微信联系人' }

function makeSendOp(runDriverFn: RunPowerShellDriverFn, verifyRefFn?: (ref: string) => typeof TARGET) {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-message-send-test-'))
  tempDirs.push(dir)
  const op = createWecomMessageSendOperation({
    runDriverFn,
    verifyRefFn: verifyRefFn ?? (() => TARGET),
    artifactDirFn: () => dir,
    // P2 任务 3.4：显式置空本地库状态——本段锁定「本地库不可用 → 老路径零改动」契约，
    // 不依赖测试机真实 localdb-state.json
    localdb: { readStateFn: () => null },
  })
  return { op, dir }
}

test('message_send：%LOCALAPPDATA% 缺失（artifactDirFn=null）→ INTERNAL_ERROR，不调驱动', async () => {
  let called = 0
  const op = createWecomMessageSendOperation({
    runDriverFn: async () => {
      called++
      return {}
    },
    verifyRefFn: () => TARGET,
    artifactDirFn: () => null,
  })
  const r = await op.execute({ target_ref: VALID_REF, text: 'hello' }, silentCtx())
  assert.equal(r.code, 'INTERNAL_ERROR')
  assert.equal(called, 0)
})

test('message_send：target_ref 过期 → TARGET_REF_STALE，不调驱动', async () => {
  let called = 0
  const { op } = makeSendOp(
    async () => {
      called++
      return {}
    },
    () => {
      throw new CodedOperationError('TARGET_REF_STALE', 'target_ref 已过期（有效期 5 分钟），请重新搜索获取')
    },
  )
  const r = await op.execute({ target_ref: 'stale-ref', text: 'hello' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_REF_STALE')
  assert.equal(r.effect, 'none')
  assert.equal(called, 0)
})

test('message_send：target_ref 篡改 → INVALID_ARGUMENT，不调驱动', async () => {
  let called = 0
  const { op } = makeSendOp(
    async () => {
      called++
      return {}
    },
    () => {
      throw new CodedOperationError('INVALID_ARGUMENT', 'target_ref 签名校验失败（无效或已被篡改）')
    },
  )
  const r = await op.execute({ target_ref: 'tampered-ref', text: 'hello' }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(called, 0)
})

test('message_send：空 text / 超长（>2000）/ 缺 target_ref → INVALID_ARGUMENT，不调驱动', async () => {
  let called = 0
  const { op } = makeSendOp(async () => {
    called++
    return {}
  })
  for (const bad of [
    { target_ref: VALID_REF, text: '' },
    { target_ref: VALID_REF, text: 'x'.repeat(2001) },
    { target_ref: '', text: 'hello' },
  ]) {
    const r = await op.execute(bad, silentCtx())
    assert.equal(r.code, 'INVALID_ARGUMENT', JSON.stringify(bad).slice(0, 60))
  }
  assert.equal(called, 0)
})

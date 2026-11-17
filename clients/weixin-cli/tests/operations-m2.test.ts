/**
 * M2 四个 operation 单测：参数校验（缺参/错型/超限 → INVALID_ARGUMENT 且不触达 runner）、
 * message_send 的 DRIVER_JSON 失败码 → OperationResult code 映射、写动作 effect 语义、
 * history_read 的原生分页与截断、unread_list 的名字过滤。
 *
 * runner/verifyRef/writeFile 全部用注入替身，不启动真实 PowerShell、不依赖微信。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createWeixinChatSearchOperation } from '../src/operations/chatSearch.js'
import { createWeixinMessageSendOperation } from '../src/operations/messageSend.js'
import { createWeixinHistoryReadOperation } from '../src/operations/historyRead.js'
import { createWeixinUnreadListOperation } from '../src/operations/unreadList.js'
import {
  parseDriverOutcome,
  type PowerShellScriptOptions,
} from '../src/platform/powershell.js'
import { createTargetRef, verifyTargetRef } from '../src/platform/targetRef.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

/** mock 驱动 runner：记录调用，返回固定 data 或抛固定错误 */
function mockDriver(outcome: Record<string, unknown> | CodedOperationError) {
  const calls: PowerShellScriptOptions[] = []
  const fn = async (opts: PowerShellScriptOptions) => {
    calls.push(opts)
    if (outcome instanceof CodedOperationError) throw outcome
    return outcome
  }
  return { fn, calls }
}

function tmpKeyDir(): string {
  return mkdtempSync(join(tmpdir(), 'weixin-m2-test-'))
}

// ---------- weixin_chat_search 参数校验 ----------

test('chat_search：缺参/错型/超限 → INVALID_ARGUMENT，不调用 runner', async () => {
  const { fn, calls } = mockDriver({ items: [] })
  const op = createWeixinChatSearchOperation({ runDriverFn: fn, createRefFn: () => 'ref' })
  const badArgs: unknown[] = [
    {},
    { query: '' },
    { query: '  ' },
    { query: 123 },
    { query: 'x', type: 'bad' },
    { query: 'x', limit: 0 },
    { query: 'x', limit: 21 },
    { query: 'x', limit: 1.5 },
    { query: 'x', limit: '10' },
    { query: 'a'.repeat(101) },
    'nope',
  ]
  for (const bad of badArgs) {
    // @ts-expect-error 故意传非法值模拟 Host 侧绕过 schema
    const r = await op.execute(bad, silentCtx())
    assert.equal(r.success, false, `应拒绝：${JSON.stringify(bad)}`)
    assert.equal(r.code, 'INVALID_ARGUMENT')
    assert.equal(r.effect, 'none')
  }
  assert.equal(calls.length, 0)
})

test('chat_search：成功路径归一 items 并签发 target_ref（section → type 映射）', async () => {
  const keyDir = tmpKeyDir()
  const { fn, calls } = mockDriver({
    items: [
      { label: '张三', section: '联系人' },
      { label: '产品群', section: '群聊' },
      { label: '文件传输助手', section: '功能' },
    ],
  })
  const op = createWeixinChatSearchOperation({
    runDriverFn: fn,
    createRefFn: (name, type) => createTargetRef(name, type, { keyDir }),
  })
  const r = await op.execute({ query: '张' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  const items = r.data.items as Array<{ label: string; section: string; target_ref: string }>
  assert.equal(items.length, 3)
  assert.equal(verifyTargetRef(items[0]!.target_ref, { keyDir }).type, 'friend')
  assert.equal(verifyTargetRef(items[1]!.target_ref, { keyDir }).type, 'group')
  assert.equal(verifyTargetRef(items[2]!.target_ref, { keyDir }).type, 'other')
  // 驱动调用参数透传
  assert.deepEqual(calls[0]!.args, ['-Query', '张', '-Type', 'any', '-Limit', '10'])
})

test('chat_search：PS 单元素数组解包怪癖防御（items 为对象时归一为数组）', async () => {
  const { fn } = mockDriver({ items: { label: '张三', section: '联系人' } })
  const op = createWeixinChatSearchOperation({ runDriverFn: fn, createRefFn: () => 'ref' })
  const r = await op.execute({ query: '张' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal((r.data.items as unknown[]).length, 1)
})

test('chat_search：驱动失败码透传（TARGET_NOT_FOUND）', async () => {
  const { fn } = mockDriver(new CodedOperationError('TARGET_NOT_FOUND', '没有匹配结果'))
  const op = createWeixinChatSearchOperation({ runDriverFn: fn, createRefFn: () => 'ref' })
  const r = await op.execute({ query: '不存在' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
})

// ---------- weixin_message_send ----------

test('message_send：缺参/错型/超限 → INVALID_ARGUMENT，不调用 runner', async () => {
  const { fn, calls } = mockDriver({ title: '张三' })
  const op = createWeixinMessageSendOperation({ runDriverFn: fn, verifyRefFn: () => ({ name: '张三', type: 'friend' }) })
  const badArgs: unknown[] = [
    {},
    { target_ref: 'r' },
    { text: 't' },
    { target_ref: '', text: 't' },
    { target_ref: 'r', text: '' },
    { target_ref: 'r', text: 'a'.repeat(501) },
    { target_ref: 'r', text: 'a\nb' },
    { target_ref: 'r', text: 'a\rb' },
    { target_ref: 1, text: 't' },
    'nope',
  ]
  for (const bad of badArgs) {
    // @ts-expect-error 故意传非法值模拟 Host 侧绕过 schema
    const r = await op.execute(bad, silentCtx())
    assert.equal(r.code, 'INVALID_ARGUMENT', `应拒绝：${JSON.stringify(bad)}`)
  }
  assert.equal(calls.length, 0)
})

test('message_send：过期 ref → TARGET_REF_STALE，不调用 runner', async () => {
  const keyDir = tmpKeyDir()
  const stale = createTargetRef('张三', 'friend', { keyDir, now: 1_000_000 })
  const { fn, calls } = mockDriver({ title: '张三' })
  const op = createWeixinMessageSendOperation({
    runDriverFn: fn,
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir, now: 1_000_000 + 600 }),
  })
  const r = await op.execute({ target_ref: stale, text: '你好' }, silentCtx())
  assert.equal(r.code, 'TARGET_REF_STALE')
  assert.equal(calls.length, 0)
})

test('message_send：篡改 ref → INVALID_ARGUMENT，不调用 runner', async () => {
  const { fn, calls } = mockDriver({ title: '张三' })
  const op = createWeixinMessageSendOperation({
    runDriverFn: fn,
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir: tmpKeyDir() }),
  })
  const r = await op.execute({ target_ref: 'forged.payload', text: '你好' }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(calls.length, 0)
})

test('message_send：成功 → effect=applied，data 含 target', async () => {
  const { fn, calls } = mockDriver({ title: '张三', verified: true })
  const op = createWeixinMessageSendOperation({ runDriverFn: fn, verifyRefFn: () => ({ name: '张三', type: 'friend' }) })
  const r = await op.execute({ target_ref: 'valid', text: '你好' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'applied')
  assert.equal(r.data.target, '张三')
  assert.equal(calls[0]!.args, undefined)
  assert.deepEqual(JSON.parse(calls[0]!.stdin!), { target_name: '张三', target_type: 'friend', text: '你好' })
})

test('message_send：驱动 UI_CHANGED（标题不符/残留草稿）→ effect=none', async () => {
  const { fn } = mockDriver(new CodedOperationError('UI_CHANGED', '输入框存在残留草稿'))
  const op = createWeixinMessageSendOperation({ runDriverFn: fn, verifyRefFn: () => ({ name: '张三', type: 'friend' }) })
  const r = await op.execute({ target_ref: 'valid', text: '你好' }, silentCtx())
  assert.equal(r.code, 'UI_CHANGED')
  assert.equal(r.effect, 'none')
})

test('message_send：发送后校验失败 → EXECUTION_UNKNOWN + effect=unknown + 不可重试', async () => {
  const { fn } = mockDriver(new CodedOperationError('EXECUTION_UNKNOWN', '发送后校验失败'))
  const op = createWeixinMessageSendOperation({ runDriverFn: fn, verifyRefFn: () => ({ name: '张三', type: 'friend' }) })
  const r = await op.execute({ target_ref: 'valid', text: '你好' }, silentCtx())
  assert.equal(r.code, 'EXECUTION_UNKNOWN')
  assert.equal(r.effect, 'unknown')
  assert.equal(r.retryable, false)
})

test('message_send：驱动超时归并 EXECUTION_UNKNOWN（写可能已落地，不可误报 none）', async () => {
  const { fn } = mockDriver(new CodedOperationError('RESULT_TIMEOUT', '超时'))
  const op = createWeixinMessageSendOperation({ runDriverFn: fn, verifyRefFn: () => ({ name: '张三', type: 'friend' }) })
  const r = await op.execute({ target_ref: 'valid', text: '你好' }, silentCtx())
  assert.equal(r.code, 'EXECUTION_UNKNOWN')
  assert.equal(r.effect, 'unknown')
})

test('message_send：驱动执行中被取消 → CANCELLED + effect=unknown（设计 §6.2，不可误报 none）', async () => {
  const fn = async (): Promise<Record<string, unknown>> => {
    throw new CancelledError()
  }
  const op = createWeixinMessageSendOperation({ runDriverFn: fn, verifyRefFn: () => ({ name: '张三', type: 'friend' }) })
  const r = await op.execute({ target_ref: 'valid', text: '你好' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'unknown')
  assert.equal(r.retryable, false)
})

test('message_send：runner 非零退出无 DRIVER_JSON → INTERNAL_ERROR + effect=unknown', async () => {
  // 经真实 parseDriverOutcome 走映射，模拟「驱动崩溃无结构化输出」
  const fn = async () => parseDriverOutcome({ stdout: 'progress only', stderr: '参数绑定失败', exitCode: 1 })
  const op = createWeixinMessageSendOperation({ runDriverFn: fn, verifyRefFn: () => ({ name: '张三', type: 'friend' }) })
  const r = await op.execute({ target_ref: 'valid', text: '你好' }, silentCtx())
  assert.equal(r.code, 'INTERNAL_ERROR')
  assert.equal(r.effect, 'unknown')
  assert.ok(r.message.includes('参数绑定失败'))
})

// ---------- weixin_history_read ----------

test('history_read：缺参/错型/超限 → INVALID_ARGUMENT，不调用 runner', async () => {
  const { fn, calls } = mockDriver({ title: '张三' })
  const op = createWeixinHistoryReadOperation({
    runDriverFn: fn,
    verifyRefFn: () => ({ name: '张三', type: 'friend' }),
  })
  const badArgs: unknown[] = [
    {},
    { target_ref: '' },
    { target_name: ' ' },
    { target_ref: 'r', target_name: '张三' },
    { target_ref: 'r', since_days: 0 },
    { target_ref: 'r', since_days: -1 },
    { target_ref: 'r', since_days: '3' },
    { target_ref: 'r', max_pages: 0 },
    { target_ref: 'r', max_pages: 11 },
    { target_ref: 'r', max_pages: 2.5 },
    'nope',
  ]
  for (const bad of badArgs) {
    // @ts-expect-error 故意传非法值模拟 Host 侧绕过 schema
    const r = await op.execute(bad, silentCtx())
    assert.equal(r.code, 'INVALID_ARGUMENT', `应拒绝：${JSON.stringify(bad)}`)
  }
  assert.equal(calls.length, 0)
})

function nativeHistoryRow(
  runtime_id: string,
  text: string,
  sender: 'self' | 'peer' | 'system' | 'unknown',
  extra = {},
) {
  return { kind: sender === 'system' ? 'time' : 'text', runtime_id, text, sender, clipped: false, ...extra }
}

test('history_read：只调用本机历史驱动，保留时间及方向，并按同一消息去除跨页重叠', async () => {
  const { fn, calls } = mockDriver({
    title: '张三', is_group: false, pages: [
      [nativeHistoryRow('2', '你好', 'unknown', { clipped: true }), nativeHistoryRow('3', '你好', 'self')],
      [nativeHistoryRow('1', '14:23', 'system'), nativeHistoryRow('2', '你好', 'peer')],
    ],
  })
  const op = createWeixinHistoryReadOperation({
    runDriverFn: fn,
  })
  const r = await op.execute({ target_name: '张三', since_days: 7, max_pages: 5 }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.total, 3)
  assert.deepEqual(r.data.messages, [
    { sender: 'time', text: '14:23' },
    { sender: 'peer', text: '你好' },
    { sender: 'me', text: '你好' },
  ])
  assert.equal(r.data.truncated, undefined)
  assert.equal(calls.length, 1)
  assert.match(calls[0]!.script, /history-read\.ps1$/)
  assert.equal(calls[0]!.useVisionProxy, false)
  assert.deepEqual(JSON.parse(calls[0]!.stdin!), { target_name: '张三', max_pages: 5, since_days: 7 })
})

test('history_read：组内昵称依据与方向分开；缺少昵称证据明确返回 null', async () => {
  const op = createWeixinHistoryReadOperation({
    runDriverFn: async () => ({
      title: '产品群', is_group: true, pages: [[
        nativeHistoryRow('1', '第一条', 'peer', { sender_name: '李四' }),
        nativeHistoryRow('2', '第二条', 'peer'),
        nativeHistoryRow('3', '第三条', 'unknown', { clipped: true }),
      ]],
    }),
  })
  const r = await op.execute({ target_name: '产品群' }, silentCtx())
  assert.equal(r.success, true)
  assert.deepEqual(r.data.messages, [
    { sender: 'peer', text: '第一条', sender_name: '李四' },
    { sender: 'peer', text: '第二条', sender_name: null },
    { sender: 'unknown', text: '第三条', sender_name: null },
  ])
})

test('history_read：手动打开的会话可直接读取，既不签发引用也不导航', async () => {
  const { fn, calls } = mockDriver({ title: '产品群', is_group: true, pages: [[]] })
  const op = createWeixinHistoryReadOperation({
    runDriverFn: fn,
    verifyRefFn: () => { throw new Error('当前会话不需要引用') },
  })
  const r = await op.execute({ target_name: '产品群', max_pages: 1 }, silentCtx())
  assert.equal(r.success, true)
  assert.deepEqual(JSON.parse(calls[0]!.stdin!), { target_name: '产品群', max_pages: 1 })
  assert.equal(calls[0]!.useVisionProxy, false)
})

test('history_read：过去签发的名称引用无法区分重名会话，拒绝调用驱动', async () => {
  const { fn, calls } = mockDriver({ title: '产品群', is_group: true, pages: [[]] })
  const op = createWeixinHistoryReadOperation({
    runDriverFn: fn,
    verifyRefFn: () => ({ name: '产品群', type: 'group' }),
  })
  const r = await op.execute({ target_ref: 'valid' }, silentCtx())
  assert.equal(r.code, 'TARGET_AMBIGUOUS')
  assert.match(r.message, /target_name/)
  assert.equal(calls.length, 0)
})

test('history_read：缺少跨页锚点时不能返回看似连续的记录', async () => {
  const op = createWeixinHistoryReadOperation({
    runDriverFn: async () => ({
      title: '张三', is_group: false, pages: [[nativeHistoryRow('2', '好', 'peer')], [nativeHistoryRow('1', '好', 'peer')]],
    }),
  })
  const r = await op.execute({ target_name: '张三' }, silentCtx())
  assert.equal(r.code, 'CONTENT_UNAVAILABLE')
})

test('history_read：超过 200 条截断为最新 200 条，完整记录写入本机文件', async () => {
  let saved: { path: string; contents: string } | undefined
  const op = createWeixinHistoryReadOperation({
    runDriverFn: async () => ({
      title: '张三', is_group: false,
      pages: [Array.from({ length: 250 }, (_, index) => nativeHistoryRow(String(index), `消息${index}`, 'self'))],
    }),
    writeFileFn: async (path, contents) => { saved = { path, contents } },
  })
  const r = await op.execute({ target_name: '张三' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.total, 250)
  assert.equal(r.data.truncated, true)
  assert.equal(r.data.file, saved?.path)
  assert.equal(saved?.contents.split('\n').length, 250)
  const messages = r.data.messages as Array<{ text: string }>
  assert.equal(messages.length, 200)
  assert.equal(messages[199]!.text, '消息249')
})

test('history_read：驱动返回不同会话标题时不得返回消息', async () => {
  const op = createWeixinHistoryReadOperation({
    runDriverFn: async () => ({ title: '另一会话', is_group: true, pages: [[]] }),
  })
  const r = await op.execute({ target_name: '张三' }, silentCtx())
  assert.equal(r.code, 'TARGET_AMBIGUOUS')
})

// ---------- weixin_unread_list ----------

test('unread_list：name 错型 → INVALID_ARGUMENT，不调用 runner', async () => {
  const { fn, calls } = mockDriver({ unread: [] })
  const op = createWeixinUnreadListOperation({ runDriverFn: fn })
  // @ts-expect-error 故意传非法值
  const r = await op.execute({ name: 123 }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(calls.length, 0)
})

test('unread_list：成功路径 + name 子串过滤', async () => {
  const { fn } = mockDriver({
    unread: [
      { name: '产品群', preview: '收到', unread_count: 3 },
      { name: '张三', preview: '好', unread_count: 1 },
    ],
  })
  const op = createWeixinUnreadListOperation({ runDriverFn: fn })
  const all = await op.execute({}, silentCtx())
  assert.equal(all.success, true)
  assert.equal((all.data.unread as unknown[]).length, 2)
  const filtered = await op.execute({ name: '产品' }, silentCtx())
  const unread = filtered.data.unread as Array<{ name: string }>
  assert.equal(unread.length, 1)
  assert.equal(unread[0]!.name, '产品群')
})

test('unread_list：单栏模式 → UI_CHANGED（message 说明恢复两栏）', async () => {
  const { fn } = mockDriver(new CodedOperationError('UI_CHANGED', '微信主窗口当前为单栏模式（无左侧会话列表），请先手动恢复两栏布局后重试'))
  const op = createWeixinUnreadListOperation({ runDriverFn: fn })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.code, 'UI_CHANGED')
  assert.equal(r.effect, 'none')
  assert.ok(r.message.includes('两栏'))
})

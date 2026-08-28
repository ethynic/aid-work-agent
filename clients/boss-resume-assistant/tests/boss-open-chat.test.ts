/**
 * boss_open_chat operation 测试（仿 boss-read-chat.test.ts）：fake session 注入替身，
 * URL 前置校验（chat/index 走通 / 其它页 WRONG_PAGE 不自动跳转）、contact 校验 fail-fast、
 * already/search/list 三种 via、executor 错误映射（不存在 → WRONG_PAGE 可重试 / 头部未切换 →
 * UI_CHANGED）。全程离线 fixture，不连 Chrome。
 *
 * 注意：operation 层无注入 sleep（真延时），场景挑选避开重延时路径——搜索全链路在
 * chat-open-executor.test.ts 已用 fake sleep 覆盖，此处只验 operation 编排与错误映射。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossOpenChatOperation } from '../src/main/operations/bossOpenChat.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import { yangSnapshot } from './chatReadFixture.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

/** fake 工厂：固定 URL + 固定 snapshot（already/不存在等无重延时场景够用） */
function fakeFactory(url: string) {
  const calls: string[] = []
  const session: BossSession = {
    snapshot: async () => {
      calls.push('snapshot')
      return yangSnapshot()
    },
    click: async () => {
      calls.push('click')
    },
    clickBrowse: async () => {},
    mouseWheel: async () => {
      calls.push('wheel')
    },
    pressEscape: async () => {
      calls.push('escape')
    },
    clickAndType: async () => {
      calls.push('type')
    },
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => {
      calls.push('getUrl')
      return url
    },
    close: async () => {
      calls.push('close')
    },
  }
  return { factory: async () => session, calls }
}

const CHAT_URL = 'https://www.zhipin.com/web/chat/index'
const RECOMMEND_URL = 'https://www.zhipin.com/web/chat/recommend'

test('chat/index + 头部已是目标联系人 → via=already，effect=none（零点击）', async () => {
  const f = fakeFactory(CHAT_URL)
  const op = createBossOpenChatOperation(f.factory)
  const r = await op.execute({ contact: '杨鸿杰' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  assert.equal(r.data.via, 'already')
  assert.equal(r.data.contact, '杨鸿杰')
  assert.match(r.message, /已在目标会话/)
  assert.match(r.message, /read-chat/)
  // already 快速路径：不点击、不输入、不按 Escape
  assert.deepEqual(f.calls, ['getUrl', 'snapshot', 'close'])
})

test('URL 不含 /web/chat/index → WRONG_PAGE（可重试），提示 goto chat 且不自动跳转', async () => {
  const f = fakeFactory(RECOMMEND_URL)
  const op = createBossOpenChatOperation(f.factory)
  const r = await op.execute({ contact: '杨鸿杰' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, true)
  assert.match(r.message, /goto chat/)
  assert.match(r.message, /不自动跳转/)
  assert.deepEqual(f.calls, ['getUrl', 'close'])
})

test('contact 空白字符串 → INVALID_ARGUMENT，且不连 Chrome', async () => {
  const f = fakeFactory(CHAT_URL)
  const op = createBossOpenChatOperation(f.factory)
  const r = await op.execute({ contact: '   ' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(r.effect, 'none')
  assert.deepEqual(f.calls, [])
})

test('联系人不存在（搜索无入口 + 列表 0 命中）→ WRONG_PAGE 可重试，文案列可用联系人', async () => {
  const f = fakeFactory(CHAT_URL)
  const op = createBossOpenChatOperation(f.factory)
  const r = await op.execute({ contact: '张三丰' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.retryable, true)
  assert.equal(r.effect, 'none')
  assert.match(r.message, /会话列表中不存在「张三丰」/)
  assert.match(r.message, /可用联系人/)
  assert.match(r.message, /杨鸿杰/)
})

test('点击列表项后头部未切换（轮询 8 次超时）→ UI_CHANGED（不可自动重试）', async () => {
  const f = fakeFactory(CHAT_URL)
  const op = createBossOpenChatOperation(f.factory)
  const r = await op.execute({ contact: '席彬玮' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'UI_CHANGED')
  assert.equal(r.retryable, false)
  assert.equal(r.effect, 'none')
  assert.match(r.message, /头部未切换为「席彬玮」/)
})

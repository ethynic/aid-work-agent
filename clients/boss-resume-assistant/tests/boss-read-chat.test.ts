/**
 * boss_read_chat operation 测试（仿 operations-result.test.ts）：fake session 注入替身，
 * URL 前置校验（chat/index 走通 / 其它页 WRONG_PAGE 不自动跳转）、contact 校验 fail-fast、
 * executor 错误映射（WRONG_PAGE 可重试）。全程离线 fixture，不连 Chrome。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossReadChatOperation } from '../src/main/operations/bossReadChat.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { yangSnapshot } from './chatReadFixture.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

/** fake 工厂：固定 URL + 单次 snapshot（记录调用供断言） */
function fakeFactory(snap: DomSnapshot, url: string) {
  const calls: string[] = []
  const session: BossSession = {
    snapshot: async () => {
      calls.push('snapshot')
      return snap
    },
    click: async () => {},
    clickBrowse: async () => {},
    mouseWheel: async () => {},
    pressEscape: async () => {},
    clickAndType: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => {
      calls.push('getUrl')
      return url
    },
    close: async () => {
      calls.push('close')
    },
  }
  return { factory: async () => session, calls, session }
}

const CHAT_URL = 'https://www.zhipin.com/web/chat/index'
const RECOMMEND_URL = 'https://www.zhipin.com/web/chat/recommend'

test('chat/index 走通：返回 contact/messages/unread/totalUnreadBadge，effect=none（只读）', async () => {
  const f = fakeFactory(yangSnapshot(), CHAT_URL)
  const op = createBossReadChatOperation(f.factory)
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  assert.equal(r.data.contact, '杨鸿杰')
  assert.equal((r.data.messages as unknown[]).length, 3)
  assert.equal((r.data.unread as unknown[]).length, 2)
  assert.equal(r.data.totalUnreadBadge, 214)
  assert.match(r.message, /杨鸿杰/)
  assert.match(r.message, /未读会话 2 个/)
  // 只读链路：getUrl + snapshot 各 1 次，绝不触达任何写原语（fake 里写原语均为空实现，calls 可证调用面）
  assert.deepEqual(f.calls, ['getUrl', 'snapshot', 'close'])
})

test('URL 不含 /web/chat/index → WRONG_PAGE（可重试），提示 goto chat 且不自动跳转', async () => {
  const f = fakeFactory(yangSnapshot(), RECOMMEND_URL)
  const op = createBossReadChatOperation(f.factory)
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, true)
  assert.match(r.message, /goto chat/)
  assert.match(r.message, /不自动跳转/)
  // 前置校验挡在 snapshot 之前：连 Chrome 页面结构都不读（避免在错误页面上误判）
  assert.deepEqual(f.calls, ['getUrl', 'close'])
})

test('contact 若提供必须非空：空白字符串 → INVALID_ARGUMENT，且不连 Chrome', async () => {
  const f = fakeFactory(yangSnapshot(), CHAT_URL)
  const op = createBossReadChatOperation(f.factory)
  const r = await op.execute({ contact: '   ' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(r.effect, 'none')
  assert.deepEqual(f.calls, [])
})

test('contact=当前会话姓名 → 正常读取', async () => {
  const f = fakeFactory(yangSnapshot(), CHAT_URL)
  const op = createBossReadChatOperation(f.factory)
  const r = await op.execute({ contact: '杨鸿杰' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.contact, '杨鸿杰')
})

test('contact=列表中存在但未打开 → WRONG_PAGE（可重试），文案含「存在但未打开」与「不自动切换」', async () => {
  const f = fakeFactory(yangSnapshot(), CHAT_URL)
  const op = createBossReadChatOperation(f.factory)
  const r = await op.execute({ contact: '席彬玮' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.retryable, true)
  assert.match(r.message, /会话「席彬玮」存在但未打开/)
  assert.match(r.message, /不自动切换/)
})

test('contact=不存在 → WRONG_PAGE，文案列可用联系人', async () => {
  const f = fakeFactory(yangSnapshot(), CHAT_URL)
  const op = createBossReadChatOperation(f.factory)
  const r = await op.execute({ contact: '张三丰' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.match(r.message, /可用联系人/)
  assert.match(r.message, /杨鸿杰/)
})

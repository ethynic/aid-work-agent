import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatRejectExecutor } from '../src/main/boss/ChatRejectExecutor.js'
import { actionSnapshot, text } from './chatActionFixture.js'

const TARGET = '合成目标'
const peers = Array.from({ length: 38 }, (_, i) => `合成会话${String(i).padStart(2, '0')}`)
const original = [...peers.slice(0, 8), TARGET, ...peers.slice(8)]
const reject = text('不合适', 1100, 1000, 80, 36)

function rejection(afterNames: string[], opts: { changedScroll?: boolean; loading?: boolean; beforeNames?: string[] } = {}) {
  let clicks = 0
  const before = actionSnapshot({ contact: TARGET, list: opts.beforeNames ?? original, extra: [reject] })
  const after = actionSnapshot({ contact: '下一会话', list: afterNames,
    extra: opts.loading ? [text('正在加载', 300, 300)] : [] })
  if (opts.changedScroll) after.documents[0]!.scrollOffsetY = 80
  const executor = new ChatRejectExecutor({ snapshot: async () => clicks ? after : before,
    click: async () => { clicks++ }, sleep: async () => {} })
  return { executor, clicks: () => clicks }
}

test('independent: removing one of 39 loaded conversations may refill the tail after all 38 original survivors', async () => {
  const h = rejection([...peers, '新尾补会话'])
  await h.executor.rejectCurrent()
  assert.equal(h.clicks(), 1)
  assert.equal(h.executor.state.businessEffect.status, 'confirmed')
  assert.equal(h.executor.state.businessEffect.completed_count, 1)
})

test('independent: multiple new tail rows still preserve the original ordered surviving prefix', async () => {
  const h = rejection([...peers, '新尾补甲', '新尾补乙'])
  await h.executor.rejectCurrent()
  assert.equal(h.clicks(), 1)
  assert.equal(h.executor.state.businessEffect.status, 'confirmed')
})

const negativeCases: Array<[string, string[], { changedScroll?: boolean; loading?: boolean; beforeNames?: string[] }?]> = [
  ['a new row prepended ahead of the old survivors', ['新插入会话', ...peers]],
  ['a new row inserted into the middle of the old survivors', [...peers.slice(0, 8), '新插入会话', ...peers.slice(8)]],
  ['one other original row also disappearing', [...peers.slice(1), '新尾补会话']],
  ['the original survivors changing order while new tail rows arrive', [peers[1]!, peers[0]!, ...peers.slice(2), '新尾补会话']],
  ['the full loaded list being replaced', Array.from({ length: 39 }, (_, i) => `替换会话${i}`)],
  ['the visible window merely scrolling', [...peers, '新尾补会话'], { changedScroll: true }],
  ['the list still loading during the disappearance', [...peers, '新尾补会话'], { loading: true }],
  ['the original target still present', [...original, '新尾补会话']],
  ['the original target name being ambiguous before the action', [...peers, '新尾补会话'], { beforeNames: [...original, TARGET] }],
  ['only a new replacement row remains without any original survivor', ['新尾补会话'], { beforeNames: [TARGET] }],
]

for (const [why, names, options] of negativeCases) {
  test(`independent: ${why} cannot prove the original rejection succeeded`, async () => {
    const h = rejection(names, options)
    await assert.rejects(h.executor.rejectCurrent())
    assert.equal(h.clicks(), 1, 'ambiguous poststate must never repeat the external rejection')
    assert.equal(h.executor.state.businessEffect.status, 'unknown')
    assert.equal(h.executor.state.execution.ordinaryRetriesUsed, 0)
  })
}

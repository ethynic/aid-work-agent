import assert from 'node:assert/strict'
import test from 'node:test'
import {
  ChatOrchestrator,
  classifyIntent,
  ChatError,
  CAPABILITY_HINT,
  type ChatHandlers,
  type ChatAction,
} from '../src/main/boss/ChatOrchestrator.js'
import type { PanelRowInfo } from '../src/main/boss/FilterSetter.js'

/** 真机 probe 词表（同 nl-filter-translator 测试） */
const PANEL: PanelRowInfo[] = [
  { label: '经验要求', options: ['经验不限', '1-3年', '5-10年'].map((text) => ({ text, point: { x: 0, y: 0 } })) },
]

function llmReturning(payload: unknown) {
  return (async () =>
    new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(payload) } }] }), { status: 200 })) as typeof fetch
}

const OPTS = { apiKey: 'test-key' }

// ---------- classifyIntent ----------

test('分类：描述筛选条件 → filter_and_greet，带 filter_request 和 limit', async () => {
  const action = await classifyIntent('本科以上学历，5年工作经验，月薪15000到20000，筛选简历', [], {
    ...OPTS,
    fetchImpl: llmReturning({ action: 'filter_and_greet', filter_request: '本科以上学历，5年工作经验，月薪15000到20000', limit: 1, target: null }),
  })
  assert.deepEqual(action, {
    type: 'filter_and_greet',
    filterRequest: '本科以上学历，5年工作经验，月薪15000到20000',
    limit: 1,
  })
})

test('分类：「再打一个」→ greet limit 1（省略句依赖上文，history 传入 prompt）', async () => {
  let capturedBody = ''
  const fetchImpl = (async (_url: string, init: { body: string }) => {
    capturedBody = init.body
    return new Response(
      JSON.stringify({ choices: [{ message: { content: '{"action":"greet","filter_request":null,"limit":1,"target":null}' } }] }),
      { status: 200 },
    )
  }) as unknown as typeof fetch
  const action = await classifyIntent('再打一个', ['本科以上筛选简历', '给最近1个人打招呼'], { ...OPTS, fetchImpl })
  assert.deepEqual(action, { type: 'greet', limit: 1 })
  // history 必须进 prompt，否则「再打一个」无从理解
  assert.match(capturedBody, /本科以上筛选简历/)
})

test('分类：「这个不合适」→ reject', async () => {
  const action = await classifyIntent('这个不合适', [], {
    ...OPTS,
    fetchImpl: llmReturning({ action: 'reject', filter_request: null, limit: null, target: null }),
  })
  assert.deepEqual(action, { type: 'reject' })
})

test('分类：limit 越界收敛到 1-100（演示安全：绝不一次打几百个）', async () => {
  const big = await classifyIntent('给1000个人打招呼', [], {
    ...OPTS,
    fetchImpl: llmReturning({ action: 'greet', filter_request: null, limit: 1000, target: null }),
  })
  assert.deepEqual(big, { type: 'greet', limit: 100 })
  const zero = await classifyIntent('打招呼', [], {
    ...OPTS,
    fetchImpl: llmReturning({ action: 'greet', filter_request: null, limit: 0, target: null }),
  })
  assert.deepEqual(zero, { type: 'greet', limit: 1 })
})

test('分类：LLM 返回非法 JSON / 未知动作 / goto 缺 target → 一律 unknown（宁可不动作，不乱执行写动作）', async () => {
  const badJson = (async () =>
    new Response(JSON.stringify({ choices: [{ message: { content: '这不是JSON' } }] }), { status: 200 })) as typeof fetch
  assert.deepEqual(await classifyIntent('你好', [], { ...OPTS, fetchImpl: badJson }), { type: 'unknown' })
  assert.deepEqual(
    await classifyIntent('你好', [], { ...OPTS, fetchImpl: llmReturning({ action: 'delete_everything' }) }),
    { type: 'unknown' },
  )
  assert.deepEqual(
    await classifyIntent('去页面', [], { ...OPTS, fetchImpl: llmReturning({ action: 'goto', target: 'somewhere' }) }),
    { type: 'unknown' },
  )
})

test('分类：未配置 API key → ChatError 明确提示', async () => {
  const old = process.env.DEEPSEEK_API_KEYS
  delete process.env.DEEPSEEK_API_KEYS
  try {
    await assert.rejects(classifyIntent('打招呼', [], { fetchImpl: llmReturning({}) }), ChatError)
  } finally {
    if (old !== undefined) process.env.DEEPSEEK_API_KEYS = old
  }
})

// ---------- ChatOrchestrator ----------

/** 记录调用顺序的 mock handlers */
function mockHandlers(calls: string[], overrides: Partial<ChatHandlers> = {}): ChatHandlers {
  return {
    gotoPage: async (t) => void calls.push(`goto:${t}`),
    probePanel: async () => (calls.push('probe'), PANEL),
    translateFilter: async (req) => (calls.push(`translate:${req}`), { experience: '5-10年' }),
    applyFilter: async () => (calls.push('apply'), { filterCount: 1 }),
    greet: async (limit) => (calls.push(`greet:${limit}`), { greeted: limit, reachedEnd: false }),
    acceptResumes: async () => (calls.push('accept'), { accepted: 2, previewed: 2 }),
    rejectCurrent: async () => void calls.push('reject'),
    interviewDemo: async () => (calls.push('interview'), { remark: '请带好身份证和简历准时面试', date: '2026-08-07' }),
    clearFilter: async () => void calls.push('clear'),
    ...overrides,
  }
}

function orchestratorWith(action: ChatAction, calls: string[], overrides: Partial<ChatHandlers> = {}) {
  return new ChatOrchestrator({
    handlers: mockHandlers(calls, overrides),
    classify: async () => action,
  })
}

test('编排：filter_and_greet 按 跳页→probe→翻译→应用→打招呼 顺序执行，回答含筛选摘要和人数', async () => {
  const calls: string[] = []
  const orch = orchestratorWith({ type: 'filter_and_greet', filterRequest: '5年经验', limit: 1 }, calls)
  const lines = await orch.handle('5年经验，筛选简历')
  assert.deepEqual(calls, ['goto:recommend', 'probe', 'translate:5年经验', 'apply', 'greet:1'])
  assert.match(lines[0]!, /筛选已生效（徽章 筛选·1）/)
  assert.match(lines[0]!, /经验=5-10年/)
  assert.match(lines[1]!, /成功打招呼 1 人/)
})

test('编排：greet 先确保在推荐页再打招呼', async () => {
  const calls: string[] = []
  const lines = await orchestratorWith({ type: 'greet', limit: 2 }, calls).handle('再打两个')
  assert.deepEqual(calls, ['goto:recommend', 'greet:2'])
  assert.match(lines[0]!, /成功打招呼 2 人/)
})

test('编排：accept 先跳沟通页；有简历报数量，没有则明确说没有', async () => {
  const calls: string[] = []
  const lines = await orchestratorWith({ type: 'accept' }, calls).handle('看看谁给我发简历了')
  assert.deepEqual(calls, ['goto:chat', 'accept'])
  assert.match(lines[0]!, /同意接收了 2 人/)

  const calls2: string[] = []
  const lines2 = await orchestratorWith({ type: 'accept' }, calls2, {
    acceptResumes: async () => (calls2.push('accept'), { accepted: 0, previewed: 0 }),
  }).handle('看看谁给我发简历了')
  assert.match(lines2[0]!, /没有等待你同意接收/)
})

test('编排：reject 先跳沟通页再标记不合适', async () => {
  const calls: string[] = []
  const lines = await orchestratorWith({ type: 'reject' }, calls).handle('这个不合适')
  assert.deepEqual(calls, ['goto:chat', 'reject'])
  assert.match(lines[0]!, /已把当前会话的候选人标记为不合适/)
})

test('编排：interview 先跳沟通页再填充演示，回答明确说没有发送', async () => {
  const calls: string[] = []
  const lines = await orchestratorWith({ type: 'interview' }, calls).handle('约面试')
  assert.deepEqual(calls, ['goto:chat', 'interview'])
  assert.match(lines[0]!, /备注「请带好身份证和简历准时面试」/)
  assert.match(lines[0]!, /没有发送/)
})

test('编排：goto/clear_filter/help 各自回答', async () => {
  const calls: string[] = []
  assert.match((await orchestratorWith({ type: 'goto', target: 'chat' }, calls).handle('去沟通页'))[0]!, /沟通/ )
  assert.match((await orchestratorWith({ type: 'clear_filter' }, calls).handle('清除筛选'))[0]!, /已全部清除/)
  assert.deepEqual(calls, ['goto:chat', 'goto:recommend', 'clear'])
  const helpLines = await orchestratorWith({ type: 'help' }, calls).handle('你能做什么')
  assert.match(helpLines[0]!, /筛选简历/)
})

test('编排：unknown 不调用任何 handler，只回答能力提示（安全兜底）', async () => {
  const calls: string[] = []
  const lines = await orchestratorWith({ type: 'unknown' }, calls).handle('今天天气怎么样')
  assert.deepEqual(calls, [])
  assert.match(lines[0]!, /没听懂/)
})

test('编排：用户输入进入 history 传给下一轮 classify（「再打一个」靠它理解）', async () => {
  const seen: string[][] = []
  const orch = new ChatOrchestrator({
    handlers: mockHandlers([]),
    classify: async (_t, h) => {
      seen.push(h)
      return { type: 'greet', limit: 1 }
    },
  })
  await orch.handle('筛选简历')
  await orch.handle('再打一个')
  assert.deepEqual(seen[0], [])
  assert.deepEqual(seen[1], ['筛选简历'])
})

test('编排：handler 抛错原样上抛（CLI 层打印 ❌ 但 REPL 存活）', async () => {
  const orch = orchestratorWith({ type: 'greet', limit: 1 }, [], {
    greet: async () => {
      throw new Error('未找到可点击的「打招呼」按钮')
    },
  })
  await assert.rejects(orch.handle('打招呼'), /未找到可点击的「打招呼」按钮/)
})

test('能力提示覆盖全部动作入口', () => {
  for (const kw of ['筛选简历', '打招呼', '推荐牛人', '沟通', '附件简历', '不合适', '约面试', '清除筛选', '退出']) {
    assert.ok(CAPABILITY_HINT.includes(kw), `CAPABILITY_HINT 应包含「${kw}」`)
  }
})

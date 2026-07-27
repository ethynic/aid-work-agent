import assert from 'node:assert/strict'
import test from 'node:test'
import { DeepSeekLlmProvider } from '../src/main/screening/DeepSeekLlmProvider.js'
import type { ScreeningInput, Evidence } from '../src/main/screening/ScreeningEngine.js'

const input: ScreeningInput = { markdown: '简历：张三，前端，5年Vue', fields: {} }
const undecided: Evidence[] = [{ field: 'skill', rule: '必须 Vue', result: 'UNDECIDED' }]

/** 构造 mock fetch，返回指定 content */
function mockFetch(content: string, status = 200): typeof fetch {
  return (async () => {
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => ({ choices: [{ message: { content } }] }),
      text: async () => content,
    } as Response
  }) as typeof fetch
}

test('未配置 key → screenUndecided 抛错', async () => {
  const p = new DeepSeekLlmProvider({ apiKey: '', fetchImpl: mockFetch('x') })
  assert.equal(p.isConfigured, false)
  await assert.rejects(p.screenUndecided(input, undecided), /not configured/)
})

test('正常 JSON 响应 → 采信结论', async () => {
  const content = JSON.stringify({
    conclusion: 'QUALIFIED',
    reason: '简历明确写了 Vue 5年',
    evidence: [{ field: 'skill', rule: '必须 Vue', result: 'PASS', detail: '5年Vue' }],
  })
  const p = new DeepSeekLlmProvider({ apiKey: 'sk-test', fetchImpl: mockFetch(content) })
  const r = await p.screenUndecided(input, undecided)
  assert.equal(r.conclusion, 'QUALIFIED')
  assert.equal(r.evidence.length, 1)
  assert.equal(r.evidence[0]!.result, 'PASS')
})

test('conclusion 非法值 → 归一化为 UNCERTAIN', async () => {
  const content = JSON.stringify({
    conclusion: 'MAYBE',
    reason: '不确定',
    evidence: [{ field: 'skill', rule: 'x', result: 'PASS' }],
  })
  const p = new DeepSeekLlmProvider({ apiKey: 'sk-test', fetchImpl: mockFetch(content) })
  const r = await p.screenUndecided(input, undecided)
  assert.equal(r.conclusion, 'UNCERTAIN')
})

test('非 JSON 响应 → 抛错（供 ScreeningEngine 降级 UNCERTAIN）', async () => {
  const p = new DeepSeekLlmProvider({ apiKey: 'sk-test', fetchImpl: mockFetch('not json at all') })
  await assert.rejects(p.screenUndecided(input, undecided), /non-JSON/)
})

test('HTTP 错误 → 抛错', async () => {
  const p = new DeepSeekLlmProvider({ apiKey: 'sk-test', fetchImpl: mockFetch('rate limited', 429) })
  await assert.rejects(p.screenUndecided(input, undecided), /HTTP 429/)
})

test('多 key 轮询：第一个失败时用第二个', async () => {
  let calls = 0
  const fetchImpl = (async () => {
    calls++
    if (calls === 1) {
      return { ok: false, status: 429, json: async () => ({}), text: async () => 'limited' } as Response
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        choices: [{ message: { content: JSON.stringify({ conclusion: 'REJECTED', reason: 'r', evidence: [{ field: 'f', rule: 'x', result: 'FAIL' }] }) } },
        ],
      }),
      text: async () => '',
    } as Response
  }) as typeof fetch
  const p = new DeepSeekLlmProvider({ apiKey: 'key1,key2', fetchImpl })
  const r = await p.screenUndecided(input, undecided)
  assert.equal(calls, 2)
  assert.equal(r.conclusion, 'REJECTED')
})

test('model 默认 deepseek-v4-flash', () => {
  const p = new DeepSeekLlmProvider({ apiKey: 'k', fetchImpl: mockFetch('{}') })
  // 通过请求体间接验证：发一次请求捕获 body
  let capturedBody: any
  const f = (async (_url: string, init: any) => {
    capturedBody = JSON.parse(init.body)
    return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content: JSON.stringify({ conclusion: 'UNCERTAIN', reason: '', evidence: [] }) } }] }), text: async () => '' } as Response
  }) as typeof fetch
  const p2 = new DeepSeekLlmProvider({ apiKey: 'k', fetchImpl: f })
  void p2
  return p.screenUndecided(input, undecided).then(async () => {
    const p3 = new DeepSeekLlmProvider({ apiKey: 'k', fetchImpl: f })
    await p3.screenUndecided(input, undecided)
    assert.equal(capturedBody.model, 'deepseek-v4-flash')
    assert.equal(capturedBody.response_format.type, 'json_object')
    assert.ok(capturedBody.temperature < 0.3, 'screening should use low temperature')
  })
})

test('prompt 包含岗位版本、未决条件、简历 Markdown', async () => {
  let capturedBody: any
  const f = (async (_url: string, init: any) => {
    capturedBody = JSON.parse(init.body)
    return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content: JSON.stringify({ conclusion: 'UNCERTAIN', reason: '', evidence: [] }) } }] }), text: async () => '' } as Response
  }) as typeof fetch
  const p = new DeepSeekLlmProvider({ apiKey: 'k', fetchImpl: f })
  const input2: ScreeningInput = { markdown: '我的简历内容XYZ', fields: {}, knowledgeVersion: 'kv9', ruleVersion: 'rv3' }
  await p.screenUndecided(input2, [{ field: 'city', rule: '必须北京', result: 'UNDECIDED' }])
  const userMsg = capturedBody.messages[1].content
  assert.match(userMsg, /kv9/)
  assert.match(userMsg, /rv3/)
  assert.match(userMsg, /city/)
  assert.match(userMsg, /我的简历内容XYZ/)
})

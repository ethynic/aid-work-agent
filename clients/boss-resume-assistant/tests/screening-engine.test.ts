import assert from 'node:assert/strict'
import test from 'node:test'
import { ScreeningEngine, type HardRule, type LlmProvider, type ScreeningInput } from '../src/main/screening/ScreeningEngine.js'

const input: ScreeningInput = { markdown: 'md', fields: { city: '北京', years: '3' } }

const cityRule: HardRule = {
  field: 'city',
  description: 'city must be 北京',
  judge: (d) => (d.fields.city ? d.fields.city === '北京' : null),
}
const yearsRule: HardRule = {
  field: 'years',
  description: 'years >= 3',
  judge: (d) => (d.fields.years ? Number(d.fields.years) >= 3 : null),
}
const undecidedRule: HardRule = {
  field: 'skill',
  description: 'must have Vue (undecided in hard rule)',
  judge: () => null,
}

test('硬规则全 PASS → QUALIFIED', async () => {
  const engine = new ScreeningEngine([cityRule, yearsRule])
  const r = await engine.screen(input)
  assert.equal(r.conclusion, 'QUALIFIED')
  assert.equal(r.reason, 'all hard rules passed')
})

test('任一硬规则 FAIL → REJECTED', async () => {
  const engine = new ScreeningEngine([cityRule, yearsRule])
  const r = await engine.screen({ markdown: 'md', fields: { city: '上海', years: '5' } })
  assert.equal(r.conclusion, 'REJECTED')
  assert.equal(r.reason, 'hard rule failed')
  assert.ok(r.evidence.some((e) => e.result === 'FAIL'))
})

test('有未决项且无 LLM → UNCERTAIN', async () => {
  const engine = new ScreeningEngine([cityRule, undecidedRule])
  const r = await engine.screen(input)
  assert.equal(r.conclusion, 'UNCERTAIN')
  assert.match(r.reason, /no LLM provider/)
})

test('LLM 处理未决项并给证据 → 采信结论', async () => {
  const llm: LlmProvider = {
    name: 'fake-llm',
    screenUndecided: async () => ({
      conclusion: 'QUALIFIED',
      reason: 'vue found in projects',
      evidence: [{ field: 'skill', rule: 'vue', result: 'PASS', detail: '项目里有 Vue' }],
    }),
  }
  const engine = new ScreeningEngine([cityRule, undecidedRule], llm)
  const r = await engine.screen(input)
  assert.equal(r.conclusion, 'QUALIFIED')
  assert.equal(r.model, 'fake-llm')
})

test('LLM 给结论但无证据 → 降级 UNCERTAIN', async () => {
  const llm: LlmProvider = {
    name: 'fake-llm',
    screenUndecided: async () => ({
      conclusion: 'QUALIFIED',
      reason: 'looks good',
      evidence: [],
    }),
  }
  const engine = new ScreeningEngine([cityRule, undecidedRule], llm)
  const r = await engine.screen(input)
  assert.equal(r.conclusion, 'UNCERTAIN')
  assert.match(r.reason, /without evidence/)
})

test('LLM 抛错 → UNCERTAIN', async () => {
  const llm: LlmProvider = {
    name: 'fake-llm',
    screenUndecided: async () => {
      throw new Error('network')
    },
  }
  const engine = new ScreeningEngine([cityRule, undecidedRule], llm)
  const r = await engine.screen(input)
  assert.equal(r.conclusion, 'UNCERTAIN')
  assert.match(r.reason, /LLM failed/)
})

test('硬规则 judge 抛错 → 视为 UNDECIDED 不崩溃', async () => {
  const throwRule: HardRule = {
    field: 'x',
    description: 'throws',
    judge: () => {
      throw new Error('boom')
    },
  }
  const engine = new ScreeningEngine([cityRule, throwRule])
  const r = await engine.screen(input)
  assert.equal(r.conclusion, 'UNCERTAIN')
  assert.ok(r.evidence.some((e) => e.result === 'UNDECIDED'))
})

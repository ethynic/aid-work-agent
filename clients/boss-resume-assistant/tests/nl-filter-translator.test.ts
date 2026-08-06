import assert from 'node:assert/strict'
import test from 'node:test'
import { translateFilterRequest, NlFilterError } from '../src/main/boss/NlFilterTranslator.js'
import type { PanelRowInfo } from '../src/main/boss/FilterSetter.js'

/** 真机 probe 词表（2026-08-05） */
const PANEL: PanelRowInfo[] = [
  { label: '经验要求', options: ['经验不限', '在校/应届', '1年以内', '1-3年', '3-5年', '5-10年', '10年以上'].map((text) => ({ text, point: { x: 0, y: 0 } })) },
  { label: '学历要求', options: ['学历不限', '中专/中技', '高中', '大专', '本科', '硕士', '博士'].map((text) => ({ text, point: { x: 0, y: 0 } })) },
  // 真机 probe：薪资待遇行标签带「[单选]」后缀（2026-08-06），校验必须前缀匹配
  { label: '薪资待遇[单选]', options: ['3K以下', '3-5K', '5-10K', '10-20K', '20-50K', '50K以上'].map((text) => ({ text, point: { x: 0, y: 0 } })) },
]

function llmReturning(payload: unknown) {
  return (async () =>
    new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(payload) } }] }), { status: 200 })) as typeof fetch
}

const OPTS = { apiKey: 'test-key' }

test('合法翻译：本科以上展开为多档，5年经验→5-10年，15-20K→10-20K（演示场景原样）', async () => {
  const spec = await translateFilterRequest('本科以上学历，5年工作经验，月薪15000到20000', PANEL, {
    ...OPTS,
    fetchImpl: llmReturning({ experience: '5-10年', educations: ['本科', '硕士', '博士'], salary: '10-20K' }),
  })
  assert.deepEqual(spec, { experience: '5-10年', educations: ['本科', '硕士', '博士'], salary: '10-20K' })
})

test('未提及的行为 null → 不设置该条件', async () => {
  const spec = await translateFilterRequest('只要本科', PANEL, {
    ...OPTS,
    fetchImpl: llmReturning({ experience: null, educations: ['本科'], salary: null }),
  })
  assert.deepEqual(spec, { educations: ['本科'] })
})

test('LLM 返回词表外选项 → fail-loud 列出合法值，绝不静默应用', async () => {
  await assert.rejects(
    translateFilterRequest('要天才', PANEL, { ...OPTS, fetchImpl: llmReturning({ experience: '天才', educations: null, salary: null }) }),
    (e: unknown) => {
      assert.ok(e instanceof NlFilterError)
      assert.match(e.message, /非法选项「天才」/)
      assert.match(e.message, /5-10年/) // 合法值列出来
      return true
    },
  )
})

test('全部条件为 null（要求与筛选无关）→ 报错提示说具体点', async () => {
  await assert.rejects(
    translateFilterRequest('今天天气怎么样', PANEL, { ...OPTS, fetchImpl: llmReturning({ experience: null, educations: null, salary: null }) }),
    /未能从要求中翻译出任何筛选条件/,
  )
})

test('LLM 返回非法 JSON → NlFilterError', async () => {
  const badJson = (async () =>
    new Response(JSON.stringify({ choices: [{ message: { content: '这不是JSON' } }] }), { status: 200 })) as typeof fetch
  await assert.rejects(translateFilterRequest('本科', PANEL, { ...OPTS, fetchImpl: badJson }), /不是合法 JSON/)
})

test('未配置 API key → 明确报错提示配置方式', async () => {
  const old = process.env.DEEPSEEK_API_KEYS
  delete process.env.DEEPSEEK_API_KEYS
  try {
    await assert.rejects(translateFilterRequest('本科', PANEL, { fetchImpl: llmReturning({}) }), /未配置 DEEPSEEK_API_KEYS/)
  } finally {
    if (old !== undefined) process.env.DEEPSEEK_API_KEYS = old
  }
})

test('HTTP 错误换下一个 key 重试', async () => {
  let calls = 0
  const fetchImpl = (async () => {
    calls++
    if (calls === 1) return new Response('rate limited', { status: 429 })
    return new Response(JSON.stringify({ choices: [{ message: { content: '{"educations":["本科"]}' } }] }), { status: 200 })
  }) as typeof fetch
  const spec = await translateFilterRequest('本科', PANEL, { apiKey: 'key1,key2', fetchImpl })
  assert.deepEqual(spec, { educations: ['本科'] })
  assert.equal(calls, 2)
})

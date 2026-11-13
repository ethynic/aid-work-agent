/**
 * boss_filter 输入清洗（设计「BOSS 筛选参数容错」/ 计划 Phase 2）：单一规则服务
 * MCP z.preprocess、CLI filter 命令与 operation 入口三处。
 *
 * 覆盖：归一化规则表逐字段（experience/salary/educations/filters/age/first_degree_only/
 * options_ref/membership_hint/membership_account_ref）、零输入结构化回执（不触达 Chrome）、
 * 清洗告知进入回执 message/data、CLI 命令级同规则。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import net from 'node:net'
import { sanitizeBossFilterInput } from '../src/main/operations/bossFilter.js'
import { createBossFilterOperation } from '../src/main/operations/bossFilter.js'
import { filterCommand, type FilterCommandOptions } from '../src/cli/commands/filter.js'
import { OPERATIONS } from '../src/main/operations/index.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

/** 记录调用次数的 fake 工厂：零输入时工厂不得被调用（无任何页面自动化动作） */
function fakeFactory(opts: { urlError?: unknown } = {}) {
  const calls: string[] = []
  const factory = async (): Promise<BossSession> => {
    calls.push('create')
    return {
      snapshot: async () => ({} as DomSnapshot),
      click: async () => {},
      clickBrowse: async () => {},
      mouseWheel: async () => {},
      pressEscape: async () => {},
      clickAndType: async () => {},
      captureFullpage: async () => Buffer.alloc(0),
      getUrl: async () => {
        if (opts.urlError) throw opts.urlError
        return 'https://www.zhipin.com/web/chat/recommend'
      },
      close: async () => {},
    }
  }
  return { factory, calls }
}

// ---------- 归一化规则表：逐字段 ----------

test('experience/salary：null、空串、纯空白、非字符串删除维度，非空文本去首尾空白', () => {
  for (const raw of [null, '', '   ', 123, {}, ['5-10年']]) {
    const s = sanitizeBossFilterInput({ experience: raw, salary: raw })
    assert.equal(s.input.experience, undefined, `experience=${JSON.stringify(raw)} 应删除`)
    assert.equal(s.input.salary, undefined, `salary=${JSON.stringify(raw)} 应删除`)
    assert.deepEqual(s.notices, [], '经验/薪资维度删除不告知')
  }
  const s = sanitizeBossFilterInput({ experience: ' 5-10年 ', salary: ' 10-20K ' })
  assert.equal(s.input.experience, '5-10年')
  assert.equal(s.input.salary, '10-20K')
  assert.equal(s.hasAnyCondition, true)
})

test('educations：非数组删除；数组仅留去空白非空字符串项；清洗后空删除', () => {
  assert.equal(sanitizeBossFilterInput({ educations: '本科' }).input.educations, undefined)
  assert.equal(sanitizeBossFilterInput({ educations: {} }).input.educations, undefined)
  assert.equal(sanitizeBossFilterInput({ educations: 123 }).input.educations, undefined)
  assert.equal(sanitizeBossFilterInput({ educations: [] }).input.educations, undefined)
  assert.equal(sanitizeBossFilterInput({ educations: ['', '  ', null, 7] }).input.educations, undefined)
  const s = sanitizeBossFilterInput({ educations: [' 本科 ', '', '硕士', null, 7, '博士'] })
  assert.deepEqual(s.input.educations, ['本科', '硕士', '博士'])
  assert.equal(s.hasAnyCondition, true)
})

test('filters：非数组/空数组删除；无有效标签或选项的行跳过；选项留非空字符串；行重建为 label+options', () => {
  for (const raw of [null, 'x', {}, 3, []]) {
    assert.equal(sanitizeBossFilterInput({ filters: raw }).input.filters, undefined, `filters=${JSON.stringify(raw)} 应删除`)
  }
  const s = sanitizeBossFilterInput({
    filters: [
      { label: '性别', options: ['男'] },
      { label: '  ', options: ['男'] },
      { label: '空选项行', options: ['', '  ', null, 5] },
      { label: '院校', options: [' 985 ', '不限', '', 9] },
      null,
      { label: '混合类型行', options: ['全职', '兼职'] },
    ],
  })
  assert.deepEqual(s.input.filters, [
    { label: '性别', options: ['男'] },
    { label: '院校', options: ['985', '不限'] },
    { label: '混合类型行', options: ['全职', '兼职'] },
  ])
})

test('age：非对象/空对象/无有效边界静默删除；{min:25,max:null} 保留；空值边界按未填处理', () => {
  for (const raw of [null, '25-40', [], 25, true, {}, { min: undefined, max: undefined }, { min: null, max: null }, { max: null }, { min: null, foo: 1 }]) {
    const s = sanitizeBossFilterInput({ age: raw })
    assert.equal(s.input.age, undefined, `age=${JSON.stringify(raw)} 应删除`)
    assert.deepEqual(s.notices, [], `age=${JSON.stringify(raw)} 无有效边界属静默删除`)
  }
  const kept = sanitizeBossFilterInput({ age: { min: 25, max: null } })
  assert.deepEqual(kept.input.age, { min: 25, max: null })
  assert.deepEqual(kept.notices, [])
  // 空值下限按未填：{min:null,max:40} → 40以下；多余键剥除
  assert.deepEqual(sanitizeBossFilterInput({ age: { min: null, max: 40 } }).input.age, { max: 40 })
  assert.deepEqual(sanitizeBossFilterInput({ age: { min: 25, foo: 'x' } }).input.age, { min: 25 })
})

test('age：非法实际值（超范围/倒序/错误类型/非整数）删除整个 age 并告知', () => {
  for (const raw of [{ min: 50 }, { min: 10 }, { min: '25' }, { min: 16.5 }, { max: 46 }, { max: '40' }, { min: 25, max: 99 }]) {
    const s = sanitizeBossFilterInput({ age: raw, experience: '5-10年' })
    assert.equal(s.input.age, undefined, `age=${JSON.stringify(raw)} 应整体删除`)
    assert.equal(s.notices.length, 1, `age=${JSON.stringify(raw)} 应告知`)
    assert.ok(s.notices[0]!.includes('年龄'), `告知应提到年龄：${s.notices[0]}`)
    assert.equal(s.hasAnyCondition, true, '同批有效条件不受影响')
  }
  const reversed = sanitizeBossFilterInput({ age: { min: 30, max: 20 } })
  assert.equal(reversed.input.age, undefined)
  assert.equal(reversed.notices.length, 1)
})

test('first_degree_only：仅布尔保留（false 保留），其余删除并告知；未提供不告知', () => {
  assert.deepEqual(sanitizeBossFilterInput({ first_degree_only: true }).input.first_degree_only, true)
  assert.deepEqual(sanitizeBossFilterInput({ first_degree_only: false }).input.first_degree_only, false)
  for (const raw of ['true', null, 1]) {
    const s = sanitizeBossFilterInput({ first_degree_only: raw, educations: ['本科'] })
    assert.equal(s.input.first_degree_only, undefined, `first_degree_only=${JSON.stringify(raw)} 应删除`)
    assert.equal(s.notices.length, 1, `first_degree_only=${JSON.stringify(raw)} 应告知`)
    assert.ok(s.notices[0]!.includes('first_degree_only'))
  }
  const absent = sanitizeBossFilterInput({ first_degree_only: undefined, educations: ['本科'] })
  assert.equal(absent.input.first_degree_only, undefined)
  assert.deepEqual(absent.notices, [], '未提供等同未填写，不告知')
})

test('options_ref/membership_hint/membership_account_ref：空值按未提供，非空值原样保留（格式错误仍由原规则拒绝）', () => {
  for (const raw of [null, undefined, '', '   ']) {
    const s = sanitizeBossFilterInput({ options_ref: raw, membership_hint: raw, membership_account_ref: raw })
    assert.equal(s.input.options_ref, undefined)
    assert.equal(s.input.membership_hint, undefined)
    assert.equal(s.input.membership_account_ref, undefined)
    assert.deepEqual(s.notices, [])
  }
  const s = sanitizeBossFilterInput({ options_ref: 'keep-ref', membership_hint: 'vip', membership_account_ref: `${'a'.repeat(64)}` })
  assert.equal(s.input.options_ref, 'keep-ref')
  assert.equal(s.input.membership_hint, 'vip')
  assert.equal(s.input.membership_account_ref, 'a'.repeat(64))
  // 非空但格式错误的值原样保留，清洗不补默认值、不拒绝（拒绝由原校验负责）
  const bad = sanitizeBossFilterInput({ membership_hint: 'gold', membership_account_ref: 'zzz' })
  assert.equal(bad.input.membership_hint, 'gold')
  assert.equal(bad.input.membership_account_ref, 'zzz')
  assert.deepEqual(bad.notices, [])
})

test('清洗后无法形成动作 → hasAnyCondition=false；清洗对已规范输入幂等', () => {
  assert.equal(sanitizeBossFilterInput({}).hasAnyCondition, false)
  assert.equal(sanitizeBossFilterInput({ membership_hint: 'vip', options_ref: 'r' }).hasAnyCondition, false, '引用/提示不构成筛选条件')
  assert.equal(sanitizeBossFilterInput({ first_degree_only: true }).hasAnyCondition, false, '附属开关不构成独立筛选条件')
  const clean = { experience: '5-10年', educations: ['本科'], salary: '10-20K', filters: [{ label: '性别', options: ['男'] }], age: { min: 25, max: null } }
  const once = sanitizeBossFilterInput(clean)
  const twice = sanitizeBossFilterInput(once.input)
  assert.deepEqual(twice.input, once.input)
  assert.deepEqual(twice.notices, [])
  assert.equal(twice.hasAnyCondition, true)
})

test('total：null/非对象/未知键输入不抛错，返回空清洗结果', () => {
  for (const raw of [null, undefined, 3, 'x', [], { unknown_key: 1, experience: null }]) {
    const s = sanitizeBossFilterInput(raw)
    assert.equal(typeof s.hasAnyCondition, 'boolean')
    assert.deepEqual(s.notices, [])
  }
  const s = sanitizeBossFilterInput({ unknown_key: 1 })
  assert.equal(s.input.experience, undefined)
  assert.equal('unknown_key' in s.input, false, '清洗结果只含已知字段')
})

// ---------- operation：零输入回执与告知 ----------

test('boss_filter：清洗后全空 → 零输入结构化回执，不触达 Chrome', async () => {
  const f = fakeFactory()
  const op = createBossFilterOperation(f.factory)
  // @ts-expect-error 故意传非法值模拟模型垃圾参数（清洗前 operation 入口）
  const r = await op.execute({ experience: null, salary: '  ', educations: [null] }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.ok(r.message.includes('没有可应用的筛选条件'))
  assert.ok(r.run_id.length > 0)
  assert.equal(f.calls.length, 0, '零输入不得发起任何页面自动化动作')
})

test('boss_filter：非法年龄+同批有效条件 → 继续执行并在回执中告知', async () => {
  const f = fakeFactory({ urlError: Object.assign(new Error('fetch failed: ECONNREFUSED'), {}) })
  const op = createBossFilterOperation(f.factory)
  const r = await op.execute({ experience: ' 5-10年 ', age: { min: 50 } }, silentCtx())
  assert.equal(f.calls.length, 1, '有效经验条件应继续执行')
  const notices = r.data.filter_input_notices as string[] | undefined
  assert.ok(Array.isArray(notices) && notices.length === 1, '非法年龄删除须在 data 中告知')
  assert.ok(notices[0]!.includes('年龄'))
  assert.ok(r.message.includes('年龄'), '告知须进入 message')
})

// ---------- CLI filter 命令：与 MCP 同规则 ----------

async function freePort(): Promise<number> {
  const srv = net.createServer()
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
  const port = (srv.address() as net.AddressInfo).port
  await new Promise<void>((r) => srv.close(() => r()))
  return port
}

test('CLI filter：混合垃圾+有效条件交由 operation 清洗（原始参数透传，命令级同一规则）', async () => {
  // boss_filter 真实 operation 对连接类失败有限退避重连（withLocalRecovery 既有行为），
  // 命令级用 spy operation 观测透传参数与退出码映射，不触发长循环。
  const original = OPERATIONS.boss_filter!.operation
  const received: Array<Record<string, unknown>> = []
  OPERATIONS.boss_filter!.operation = {
    name: 'boss_filter',
    async execute(args) {
      received.push(args as Record<string, unknown>)
      return { success: false, code: 'CHROME_UNAVAILABLE', message: 'spy', effect: 'none', data: {}, retryable: true, run_id: 'spy-run' }
    },
  }
  try {
    const opts = { experience: ' 5-10年 ', age: { min: 50 }, cdpPort: 1 } as unknown as FilterCommandOptions
    const exit = await filterCommand(opts)
    assert.equal(exit, 1, 'spy 回执 CHROME_UNAVAILABLE → exit 1（执行到 operation，非参数拒绝 exit 2）')
    assert.deepEqual(received.length, 1)
    assert.deepEqual(received[0], {
      experience: ' 5-10年 ',
      educations: undefined,
      salary: undefined,
      filters: undefined,
      age: { min: 50 },
      membership_hint: undefined,
      membership_account_ref: undefined,
      first_degree_only: undefined,
    }, '命令把原始参数透传给 operation（清洗与告知在 operation 内统一产生）')
  } finally {
    OPERATIONS.boss_filter!.operation = original
  }
})

test('CLI filter：全空输入走零输入回执（exit 2、零输入文案、不连 Chrome、无鼠标警示）', async () => {
  process.env.AID_BOSS_AUTO_CHROME = '0'
  const errors: string[] = []
  const logs: string[] = []
  const originalError = console.error
  const originalLog = console.log
  console.error = (...parts: unknown[]) => { errors.push(parts.join(' ')) }
  console.log = (...parts: unknown[]) => { logs.push(parts.join(' ')) }
  try {
    const port = await freePort()
    const opts = { experience: null, age: { max: null }, cdpPort: port } as unknown as FilterCommandOptions
    const exit = await filterCommand(opts)
    assert.equal(exit, 2)
    assert.ok(errors.join('\n').includes('没有可应用的筛选条件'), '零输入回执文案应经 renderer 输出')
    assert.ok(!logs.join('\n').includes('请勿移动鼠标'), '零输入不触碰鼠标，不得打印鼠标警示')
    assert.ok(!logs.join('\n').includes('连接 Chrome'), '零输入不得尝试连接 Chrome')
  } finally {
    console.error = originalError
    console.log = originalLog
  }
})

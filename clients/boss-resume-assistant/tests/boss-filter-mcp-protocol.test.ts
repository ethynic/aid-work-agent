/**
 * boss_filter 协议回归（计划 Phase 2/R5：协议测试须走实际 MCP SDK 注册入口，覆盖解析后实际参数）。
 *
 * 进程内 McpServer.registerBossTools + InMemoryTransport 真实 callTool（stdio 子进程版
 * mcp-server.test 自 2026-10-20 起登记为挂死基线，本文件不派生子进程、close 不挂起）。
 * 覆盖：R5 schema 放宽四处（experience/salary null、educations 任意字符串项、first_degree_only
 * null）原始形态到达 operation、回执 filter_evidence 跳过证据、全空调用零输入结构化回执、
 * tools/list 与 manifest 同源、schema 结构断言。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import net from 'node:net'
import { z } from 'zod'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { TOOL_DEFS } from '../src/mcp/toolDefs.js'
import { registerBossTools } from '../src/mcp/server.js'
import { jsonSchemaOf } from '../src/mcp/manifest.js'
import { OPERATIONS } from '../src/main/operations/index.js'
import { createBossFilterOperation } from '../src/main/operations/bossFilter.js'
import type { OperationResult } from '../src/main/operations/types.js'

// 测试隔离：默认工厂不得真拉起 Chrome（连接死端口应快速 CHROME_UNAVAILABLE）
process.env.AID_BOSS_AUTO_CHROME = '0'

const FILTER_DEF = TOOL_DEFS.find((t) => t.name === 'boss_filter')!
const FILTER_SHAPE = z.object(FILTER_DEF.zodShape)

function structuredOf(result: unknown): Record<string, unknown> {
  const sc = (result as { structuredContent?: Record<string, unknown> }).structuredContent
  assert.ok(sc, 'call_tool 结果缺少 structuredContent')
  return sc!
}

async function freePort(): Promise<number> {
  const srv = net.createServer()
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
  const port = (srv.address() as net.AddressInfo).port
  await new Promise<void>((r) => srv.close(() => r()))
  return port
}

/** 真实注册入口 + 内存传输：同一进程内完成 registerTool/callTool，close 不派生子进程 */
async function startClient(cdpPort?: number): Promise<Client> {
  const server = new McpServer({ name: 'boss-recruiting', version: 'test' }, {})
  registerBossTools(server, { cdpPort }, { current: null })
  const client = new Client({ name: 'boss-filter-protocol-test', version: '0.1.0' }, { capabilities: {} })
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair()
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)])
  return client
}

/** 解析后的业务参数（剥除内部绑定字段与 zod 保留的 undefined 可选键） */
function businessArgsOf(parsed: unknown): Record<string, unknown> {
  const { _boss_target_ref: _t, _boss_expected_contact_ref: _c, ...business } = (parsed ?? {}) as Record<string, unknown>
  return Object.fromEntries(Object.entries(business).filter(([, v]) => v !== undefined))
}

test('boss_filter schema 放宽（R5）：云端可派发的原始形态经解析原样到达，云端拒绝形态仍被严格校验拒绝', () => {
  // 事故场景：experience:null 与同批有效条件——放宽后 null/空白不再被 zod 预处理删除，
  // 原样到达 operation 清洗并出具 filter_evidence（设计「BOSS 筛选参数容错」）
  const incident = businessArgsOf(FILTER_SHAPE.parse({
    experience: null,
    educations: [' 本科 ', ' ', '硕士'],
    salary: null,
    first_degree_only: null,
    age: { min: 25, max: null },
    filters: [{ label: '性别', options: ['男'] }],
  }))
  assert.deepEqual(incident, {
    experience: null,
    educations: [' 本科 ', ' ', '硕士'],
    salary: null,
    first_degree_only: null,
    age: { min: 25, max: null },
    filters: [{ label: '性别', options: ['男'] }],
  }, '放宽形态（experience/salary/educations/first_degree_only 接受 null、educations 任意字符串项）原样保留')
  // educations 显式 null 与云端 Optional[List[str]] 同形态放行（姊妹事故形态，不得再报 MCP invalid-params）
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ educations: null })), { educations: null })

  // 云端本就拒绝的形态不放宽：Agent 收到明确 zod 校验错误可自纠，不产生核验卡
  assert.throws(() => FILTER_SHAPE.parse({ salary: 123 }), '非字符串薪资云端即拒绝')
  assert.throws(() => FILTER_SHAPE.parse({ educations: ['本科', null] }), 'null 学历项云端 List[str] 即拒绝')
  assert.throws(() => FILTER_SHAPE.parse({ first_degree_only: 'true' }), '非布尔 first_degree_only 云端 strict 即拒绝')

  // 动态行空选项行仍由预处理跳过（filters 保持现状）
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ filters: [{ label: '性别', options: [] }, { label: '院校', options: ['985'] }] })),
    { filters: [{ label: '院校', options: ['985'] }] })

  // age 维持现状：{min:25,max:null} 保留；zod 可表达的非法形态仍由预处理删除（保持 R3 行为）
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ age: { min: 25, max: null } })), { age: { min: 25, max: null } })
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ age: { min: 30, max: 20 } })), {}, '倒序年龄由预处理删除（该形态经 zod 可表达，MCP 路径无告知，CLI 路径有 operation 层告知）')
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ age: { min: 50 } })), {}, '超范围年龄由预处理删除')
})

test('tools/list 的 boss_filter inputSchema 与 manifest 同源且如实反映放宽后的公开 schema', async () => {
  const client = await startClient()
  try {
    const { tools } = await client.listTools()
    const listed = tools.find((t) => t.name === 'boss_filter')!
    assert.ok(listed, 'boss_filter 必须在 tools/list')
    assert.deepEqual(listed.inputSchema, jsonSchemaOf(FILTER_DEF), 'Host 看到的 schema 与 manifest 推导同源')
  } finally {
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
  // 结构断言：放宽四处如实进入公开 schema（与云端 BossFilterInput 可放行形态对齐）
  const schema = jsonSchemaOf(FILTER_DEF) as {
    type: string
    required?: string[]
    properties: Record<string, { type?: string; description?: string; minLength?: number; enum?: string[]; pattern?: string; anyOf?: Array<{ type?: string }> } & Record<string, unknown>>
  }
  assert.equal(schema.type, 'object')
  assert.equal(schema.required, undefined, 'boss_filter 全可选')
  const p = schema.properties
  assert.deepEqual(p.experience!.anyOf, [{ type: 'string' }, { type: 'null' }], 'experience 放宽为字符串或 null（云端 Optional[str] 同形态）')
  assert.ok(p.experience!.description!.includes('经验档位'), '描述必须保留')
  assert.deepEqual(p.salary!.anyOf, [{ type: 'string' }, { type: 'null' }], 'salary 放宽为字符串或 null')
  const educationsProp = (p.educations as { anyOf?: Array<{ type?: string; items?: { type?: string; minLength?: number } }> })!
  assert.deepEqual(educationsProp.anyOf, [
    { type: 'array', items: { type: 'string' } },
    { type: 'null' },
  ], 'educations 放宽为数组（项任意字符串）或 null（云端 Optional[List[str]] 同形态）；项由 operation 清洗并出具证据')
  assert.equal(p.filters!.type, 'array')
  assert.equal((p.filters as { items?: { type?: string; additionalProperties?: boolean } }).items!.type, 'object')
  assert.equal((p.filters as { items?: { additionalProperties?: boolean } }).items!.additionalProperties, false)
  assert.deepEqual([p.options_ref!.type, p.options_ref!.minLength], ['string', 1], 'options_ref 保持现状（不放宽）')
  assert.deepEqual(p.age!.type, 'object')
  assert.deepEqual((p.age as { additionalProperties?: boolean }).additionalProperties, false)
  assert.deepEqual(p.membership_hint!.enum, ['vip', 'non_vip'])
  assert.equal(p.membership_account_ref!.pattern, '^[a-f0-9]{64}$')
  assert.deepEqual(p.first_degree_only!.anyOf, [{ type: 'boolean' }, { type: 'null' }], 'first_degree_only 放宽为布尔或 null（云端 strict Optional[bool] 同形态）')
})

test('事故场景经真实 callTool 到达业务逻辑：放宽形态原样透传 operation（清洗在 operation 层）', async () => {
  // 直接观测真实 MCP 注册管道（registerTool + zod 校验 + 单飞 + handler 剥离绑定字段）
  // 递给 operation 的实际参数；boss_filter 真实 operation 对 CHROME_UNAVAILABLE 有限退避重连
  // （withLocalRecovery 既有行为），测试注入 spy 避免死端口触发的长循环。
  const client = await startClient(await freePort())
  const original = OPERATIONS.boss_filter!.operation
  const received: Array<Record<string, unknown>> = []
  OPERATIONS.boss_filter!.operation = {
    name: 'boss_filter',
    async execute(args) {
      received.push(args as Record<string, unknown>)
      return { success: true, code: 'OK', message: 'spy', effect: 'none', data: {}, retryable: false, run_id: 'spy-run' }
    },
  }
  try {
    const result = await client.callTool({
      name: 'boss_filter',
      arguments: {
        experience: null,
        educations: ['本科', '硕士'],
        salary: ' 10-20K ',
        age: { min: 25, max: null },
        filters: [{ label: '性别', options: ['男'] }],
      },
    })
    assert.notEqual(result.isError, true, '放宽形态不得被 MCP 严格校验整单拒绝')
    assert.equal(received.length, 1, '业务逻辑应被调用一次')
    // R5 放宽后 null/空白原样到达 operation（operation 清洗并出具 filter_evidence）
    assert.deepEqual(businessArgsOf(received[0]), {
      experience: null,
      educations: ['本科', '硕士'],
      salary: ' 10-20K ',
      age: { min: 25, max: null },
      filters: [{ label: '性别', options: ['男'] }],
    }, 'operation 收到的实际参数应保留原始形态')
    const s = structuredOf(result)
    assert.equal(s.code, 'OK')
  } finally {
    OPERATIONS.boss_filter!.operation = original
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
})

test('educations:null 经真实 callTool 放行：不再 MCP invalid-params，原始 null 到达 operation 清洗', async () => {
  // 姊妹事故形态：云端 Optional[List[str]] 放行 educations:null，插件 schema 曾拒绝 →
  // INTERNAL_ERROR/effect=unknown 核验卡；放宽后必须原样到达 operation（静默省略，无证据项）。
  const client = await startClient(await freePort())
  const original = OPERATIONS.boss_filter!.operation
  const received: Array<Record<string, unknown>> = []
  OPERATIONS.boss_filter!.operation = {
    name: 'boss_filter',
    async execute(args) {
      received.push(args as Record<string, unknown>)
      return { success: true, code: 'OK', message: 'spy', effect: 'none', data: {}, retryable: false, run_id: 'spy-run' }
    },
  }
  try {
    const result = await client.callTool({ name: 'boss_filter', arguments: { educations: null, experience: '5-10年' } })
    assert.notEqual(result.isError, true, 'educations:null 不得被 MCP 严格校验整单拒绝')
    assert.equal(received.length, 1, '业务逻辑应被调用一次')
    assert.deepEqual(businessArgsOf(received[0]), { educations: null, experience: '5-10年' },
      'zod 保留显式 null；operation 清洗静默省略（无证据项，sanitize 单测覆盖）')
    const s = structuredOf(result)
    assert.equal(s.code, 'OK')
  } finally {
    OPERATIONS.boss_filter!.operation = original
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
})

test('放宽形态经真实 callTool 由 operation 清洗：回执携带 filter_evidence', async () => {
  // 包装真实 operation（快速失败工厂）观测终态回执；协议层只验证 null/空白穿过 zod，
  // 证据由 operation 的统一清洗产生（与 CLI 直调路径同源）。
  const client = await startClient(await freePort())
  const original = OPERATIONS.boss_filter!.operation
  const real = createBossFilterOperation(async () => {
    throw Object.assign(new Error('fetch failed: ECONNREFUSED'), {})
  })
  let receipt: OperationResult | undefined
  OPERATIONS.boss_filter!.operation = {
    name: 'boss_filter',
    async execute(args, ctx) {
      receipt = await real.execute(args, ctx)
      return receipt
    },
  }
  try {
    const result = await client.callTool({
      name: 'boss_filter',
      arguments: { experience: null, educations: ['本科', ' '] },
    })
    assert.notEqual(result.isError, true)
    assert.equal(receipt?.success, false, '快速失败工厂 → 连接失败终态')
    const s = structuredOf(result)
    assert.deepEqual((s.data as { filter_evidence?: unknown }).filter_evidence, { version: 1, skipped: [
      { field: 'experience', path: 'experience', reason: 'invalid_value' },
      { field: 'educations', path: 'educations[1]', reason: 'invalid_value' },
    ] }, 'R5 复现场景回执必须携带跳过证据（云端核对豁免依据）')
  } finally {
    OPERATIONS.boss_filter!.operation = original
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
})

test('全空调用返回零输入结构化回执（effect none、认可码、不发起连接）', async () => {
  const client = await startClient(await freePort())
  try {
    const started = Date.now()
    const result = await client.callTool({ name: 'boss_filter', arguments: { experience: null, salary: '' } })
    assert.notEqual(result.isError, true, '零输入是结构化回执，不是 MCP isError')
    const s = structuredOf(result)
    assert.equal(s.success, false)
    assert.equal(s.code, 'INVALID_ARGUMENT')
    assert.equal(s.effect, 'none')
    assert.equal(s.retryable, false)
    assert.ok(String(s.message).includes('没有可应用的筛选条件'))
    assert.deepEqual((s.data as { filter_evidence?: unknown }).filter_evidence, { version: 1, skipped: [
      { field: 'experience', path: 'experience', reason: 'invalid_value' },
      { field: 'salary', path: 'salary', reason: 'invalid_value' },
    ] }, '零输入回执携带本请求跳过证据')
    assert.ok(Date.now() - started < 3000, '零输入不得尝试连接 Chrome')

    const nullOnly = await client.callTool({ name: 'boss_filter', arguments: { educations: null } })
    assert.notEqual(nullOnly.isError, true, 'educations:null 单独调用是合法的「未填写」形态')
    const ns = structuredOf(nullOnly)
    assert.equal(ns.success, false)
    assert.equal(ns.code, 'INVALID_ARGUMENT')
    assert.equal(ns.effect, 'none')
    assert.equal('filter_evidence' in (ns.data as object), false, 'null 是「未填写」语义，静默省略不出具证据')
  } finally {
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
})

test('非法年龄单独调用：预处理删除后同样得到零输入回执（无请求项证据）', async () => {
  const client = await startClient(await freePort())
  try {
    const result = await client.callTool({ name: 'boss_filter', arguments: { age: { min: 50 } } })
    assert.notEqual(result.isError, true)
    const s = structuredOf(result)
    assert.equal(s.code, 'INVALID_ARGUMENT')
    assert.equal(s.effect, 'none')
    assert.ok(String(s.message).includes('没有可应用的筛选条件'))
    assert.equal('filter_evidence' in (s.data as object), false, 'age 在 zod 预处理层删除，operation 无请求项证据（R5 范围外）')
  } finally {
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
})

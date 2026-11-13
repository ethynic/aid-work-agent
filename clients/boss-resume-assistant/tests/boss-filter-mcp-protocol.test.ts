/**
 * boss_filter 协议回归（计划 Phase 2：协议测试须走实际 MCP SDK 注册入口，覆盖解析后实际参数）。
 *
 * 进程内 McpServer.registerBossTools + InMemoryTransport 真实 callTool（stdio 子进程版
 * mcp-server.test 自 2026-10-20 起登记为挂死基线，本文件不派生子进程、close 不挂起）。
 * 覆盖：事故场景（experience:null + 同批有效条件）清洗后到达业务逻辑、全空调用零输入
 * 结构化回执、tools/list 与 manifest 同源、schema 等价性结构断言。
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

test('boss_filter 严格校验前的清洗：解析后实际参数符合规则表', () => {
  // 事故场景：experience:null 与同批有效条件
  const incident = businessArgsOf(FILTER_SHAPE.parse({
    experience: null,
    educations: ['本科', '硕士'],
    salary: ' 10-20K ',
    age: { min: 25, max: null },
    filters: [{ label: '性别', options: ['男'] }],
  }))
  assert.deepEqual(incident, {
    educations: ['本科', '硕士'],
    salary: '10-20K',
    age: { min: 25, max: null },
    filters: [{ label: '性别', options: ['男'] }],
  }, 'experience 应被清洗删除，其余原样保留')

  // salary 非字符串 / educations 混合数组 / 动态行空选项 / first_degree_only 非布尔
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ salary: 123 })), {})
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ educations: [' 本科 ', '', null] })), { educations: ['本科'] })
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ filters: [{ label: '性别', options: [] }, { label: '院校', options: ['985'] }] })),
    { filters: [{ label: '院校', options: ['985'] }] })
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ first_degree_only: 'true', experience: ' 5-10年 ' })), { experience: '5-10年' })

  // age：{min:25,max:null} 保留；非法年龄删除（zod 不可表达的超范围值由预处理删除）
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ age: { min: 25, max: null } })), { age: { min: 25, max: null } })
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ age: { min: 30, max: 20 } })), {}, '倒序年龄由预处理删除（该形态经 zod 可表达，MCP 路径无告知，CLI 路径有 operation 层告知）')
  assert.deepEqual(businessArgsOf(FILTER_SHAPE.parse({ age: { min: 50 } })), {}, '超范围年龄由预处理删除')
})

test('tools/list 的 boss_filter inputSchema 与 manifest 同源且结构等价（预处理不改变公开 schema）', async () => {
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
  // 结构等价断言：预处理后公开 schema 仍逐字段与原契约一致（含描述与约束）
  const schema = jsonSchemaOf(FILTER_DEF) as {
    type: string
    required?: string[]
    properties: Record<string, { type?: string; description?: string; minLength?: number; enum?: string[]; pattern?: string; anyOf?: Array<{ type?: string }> } & Record<string, unknown>>
  }
  assert.equal(schema.type, 'object')
  assert.equal(schema.required, undefined, 'boss_filter 全可选')
  const p = schema.properties
  assert.deepEqual([p.experience!.type, p.experience!.minLength], ['string', 1])
  assert.ok(p.experience!.description!.includes('经验档位'), '描述必须保留')
  assert.deepEqual([p.salary!.type, p.salary!.minLength], ['string', 1])
  assert.equal(p.educations!.type, 'array')
  assert.equal(p.filters!.type, 'array')
  assert.equal((p.filters as { items?: { type?: string; additionalProperties?: boolean } }).items!.type, 'object')
  assert.equal((p.filters as { items?: { additionalProperties?: boolean } }).items!.additionalProperties, false)
  assert.deepEqual([p.options_ref!.type, p.options_ref!.minLength], ['string', 1])
  assert.deepEqual(p.age!.type, 'object')
  assert.deepEqual((p.age as { additionalProperties?: boolean }).additionalProperties, false)
  assert.deepEqual(p.membership_hint!.enum, ['vip', 'non_vip'])
  assert.equal(p.membership_account_ref!.pattern, '^[a-f0-9]{64}$')
  assert.deepEqual([p.first_degree_only!.type], ['boolean'])
})

test('事故场景经真实 callTool 到达业务逻辑：解析后实际参数无 experience、其余原样', async () => {
  // 直接观测真实 MCP 注册管道（registerTool + zod 预处理 + 单飞 + handler 剥离绑定字段）
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
    assert.notEqual(result.isError, true, '清洗后不得被 MCP 严格校验整单拒绝')
    assert.equal(received.length, 1, '业务逻辑应被调用一次')
    // zod 解析输出会保留 experience: undefined 键；业务语义为未提供
    assert.deepEqual(businessArgsOf(received[0]), {
      educations: ['本科', '硕士'],
      salary: '10-20K',
      age: { min: 25, max: null },
      filters: [{ label: '性别', options: ['男'] }],
    }, 'operation 收到的实际参数应无 experience、其余原样')
    const s = structuredOf(result)
    assert.equal(s.code, 'OK')
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
    assert.ok(Date.now() - started < 3000, '零输入不得尝试连接 Chrome')
  } finally {
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
})

test('非法年龄单独调用：清洗删除后同样得到零输入回执', async () => {
  const client = await startClient(await freePort())
  try {
    const result = await client.callTool({ name: 'boss_filter', arguments: { age: { min: 50 } } })
    assert.notEqual(result.isError, true)
    const s = structuredOf(result)
    assert.equal(s.code, 'INVALID_ARGUMENT')
    assert.equal(s.effect, 'none')
    assert.ok(String(s.message).includes('没有可应用的筛选条件'))
  } finally {
    await client.close()
    await new Promise((r) => setTimeout(r, 0))
  }
})

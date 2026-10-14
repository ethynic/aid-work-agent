import assert from 'node:assert/strict'
import test from 'node:test'
import { z } from 'zod'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { TOOL_DEFS } from '../src/mcp/toolDefs.js'
import { OPERATIONS } from '../src/main/operations/index.js'
import { parseArgs } from '../src/cli/args.js'
import { validateCommandArgs } from '../src/cli/validate.js'

const ref = `boss-card-v1:1791850000000:${'a'.repeat(64)}`
const selected = TOOL_DEFS.find((tool) => tool.name === 'boss_greet_selected')!
const lookup = TOOL_DEFS.find((tool) => tool.name === 'boss_lookup_candidates')!
const validate = (argv: string[]) => validateCommandArgs(argv[0], parseArgs(argv))

test('新增独立工具有 operation，读写分类与 MCP 注解一致', () => {
  assert.ok(OPERATIONS.boss_lookup_candidates)
  assert.ok(OPERATIONS.boss_greet_selected)
  assert.equal(OPERATIONS.boss_lookup_candidates!.cli.write, false)
  assert.equal(OPERATIONS.boss_greet_selected!.cli.write, true)
  assert.equal(lookup.annotations.readOnlyHint, true)
  assert.equal(selected.annotations.readOnlyHint, false)
  assert.equal(selected.annotations.idempotentHint, false)
  assert.match(selected.description, /无法唯一对应继续询问/)
})

test('MCP 严格引用与单人参数，拒绝缺省/伪造类型/换行/非规范引用', () => {
  const schema = z.object(selected.zodShape)
  assert.deepEqual(schema.parse({ name: '张测试', candidate_ref: ref }), { name: '张测试', candidate_ref: ref })
  for (const args of [{ name: '张测试' }, { name: '张测试', candidate_ref: ref + '\n' },
    { name: '张测试', candidate_ref: '张测试' }, { name: '张测试', candidate_ref: 123 },
    { name: ['张测试'], candidate_ref: ref }, { name: ' ', candidate_ref: ref },
    { name: '张'.repeat(31), candidate_ref: ref }]) assert.equal(schema.safeParse(args).success, false)
})

test('只读查询姓名 1-3 人，初始不需优势', () => {
  const schema = z.object(lookup.zodShape)
  assert.deepEqual(schema.parse({ names: ['张测试'] }), { names: ['张测试'] })
  for (const names of [[], ['a', 'b', 'c', 'd'], [' '], [1], ['张'.repeat(31)], '张测试']) {
    assert.equal(schema.safeParse({ names }).success, false)
  }
})

test('CLI 新命令支持精确参数；未知参数/多目标/缺省引用不能发生写动作', () => {
  assert.equal(validate(['greet-selected', '张测试', '--candidate-ref', ref]).ok, true)
  assert.equal(validate(['lookup-candidates', '--names', '张测试,李测试']).ok, true)
  for (const argv of [ ['greet-selected', '张测试'], ['greet-selected', '张测试', '--candidate-ref'],
    ['greet-selected', '张测试', '--candidate-ref', ref + '\n'],
    ['greet-selected', '张测试', '李测试', '--candidate-ref', ref],
    ['greet-selected', '张测试', '--candidate-ref', ref, '--all'],
    ['greet-selected', ' ', '--candidate-ref', ref],
    ['lookup-candidates'], ['lookup-candidates', '--names'],
    ['lookup-candidates', '--names', '张测试,,李测试'],
    ['lookup-candidates', '--names', 'a,b,c,d'], ['lookup-candidates', '--names', '张测试', '--all'],
    ['lookup-candidates', '张测试', '--names', '张测试'] ]) assert.equal(validate(argv).ok, false, JSON.stringify(argv))
  assert.equal(validate(['greet-selected', '--help']).ok, true)
})


test('真实 CLI 入口拒绝无引用或额外参数，exit2且不连接 Chrome', () => {
  const cli = fileURLToPath(new URL('../src/cli/index.js', import.meta.url))
  for (const argv of [['greet-selected', '张测试'], ['greet-selected', '张测试', '--candidate-ref', 'bad'],
    ['lookup-candidates', '--names'], ['lookup-candidates', '--names', '张测试', '--all']]) {
    const output = spawnSync(process.execPath, [cli, ...argv], { encoding: 'utf8', env: { ...process.env, AID_BOSS_AUTO_CHROME: '0' }, timeout: 5000 })
    assert.equal(output.status, 2, output.stderr)
    assert.doesNotMatch(output.stdout + output.stderr, /CHROME_UNAVAILABLE|连接Chrome|boss-session/)
  }
})

test('真实 MCP list_tools 发布新工具，缺少精确引用被 schema 拒绝', async () => {
  const cli = fileURLToPath(new URL('../src/cli/index.js', import.meta.url))
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [cli, 'mcp', '--stdio', '--cdp-port', '1'], stderr: 'pipe',
    env: { ...Object.fromEntries(Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined)), AID_BOSS_AUTO_CHROME: '0' } })
  transport.stderr?.on('data', () => {})
  const client = new Client({ name: 'candidate-selection-test', version: '1' }, { capabilities: {} })
  try {
    await client.connect(transport)
    const tools = (await client.listTools()).tools
    assert.equal(tools.find(tool => tool.name === selected.name)?.annotations?.readOnlyHint, false)
    assert.equal(tools.find(tool => tool.name === lookup.name)?.annotations?.readOnlyHint, true)
    const result = await client.callTool({ name: selected.name, arguments: { name: '张测试' } })
    assert.equal(result.isError, true)
    assert.doesNotMatch(JSON.stringify(result), /CHROME_UNAVAILABLE/)
    assert.match(JSON.stringify(result), /candidate_ref/)
  } finally { await client.close() }
})

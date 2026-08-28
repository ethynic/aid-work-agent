/**
 * manifest digest 稳定性 + 字段完整性 + version --json 与 digest 函数同源（规格 m02 §7）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  buildManifest,
  canonicalSerialize,
  computeSchemaDigest,
  manifestTools,
} from '../src/mcp/manifest.js'
import { TOOL_NAMES } from '../src/mcp/toolDefs.js'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')

test('digest 稳定：同输入同 digest，格式 sha256:<64 hex>', () => {
  const d1 = computeSchemaDigest()
  const d2 = computeSchemaDigest()
  assert.equal(d1, d2)
  assert.match(d1, /^sha256:[0-9a-f]{64}$/)
})

test('canonicalSerialize：key 递归排序，与声明顺序无关', () => {
  assert.equal(canonicalSerialize({ b: 1, a: { d: 2, c: 3 } }), canonicalSerialize({ a: { c: 3, d: 2 }, b: 1 }))
  assert.equal(canonicalSerialize({ b: 1, a: 2 }), '{"a":2,"b":1}')
  // undefined 字段被剔除（与 JSON.stringify 对对象的行为一致）
  assert.equal(canonicalSerialize({ a: undefined, b: 1 }), '{"b":1}')
})

test('manifest 字段完整（标准 §6）', () => {
  const m = buildManifest()
  assert.equal(m.provider_id, 'ai.aidwork.boss-recruiting')
  const pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
  assert.equal(m.provider_version, pkg.version)
  assert.equal(m.protocol, 'mcp')
  assert.equal(m.transport, 'stdio')
  assert.deepEqual(m.platforms, ['win32-x64'])
  assert.deepEqual(m.entrypoint, ['boss-recruiting.exe', 'mcp', '--stdio'])
  assert.equal(m.execution_target, 'local_required')
  assert.equal(m.min_mcp_protocol_version, '2024-11-05')
  assert.equal(m.schema_digest, computeSchemaDigest())
  // 16 个 tool，名称与设计 §10.2 / §10.6 / §10.7 / §10.8 / §10.9 一致（boss_open_chat 2026-08-27）
  assert.deepEqual(
    m.tools.map((t) => t.name).sort(),
    [
      'boss_accept_resume',
      'boss_clear_filter',
      'boss_filter',
      'boss_goto',
      'boss_greet',
      'boss_interview_demo',
      'boss_list_jobs',
      'boss_read_chat',
      'boss_open_chat',
      'boss_resume_batch',
      'boss_resume_detail',
      'boss_reject_current',
      'boss_select_job',
      'boss_filter_options',
      'boss_send_current',
      'boss_send_to',
    ].sort(),
  )
  assert.equal(m.tools.length, 16)
  for (const tool of m.tools) {
    assert.ok(tool.title.length > 0 && tool.description.length > 0)
    assert.equal(tool.inputSchema.type, 'object')
    assert.ok(tool.annotations)
  }
})

test('写动作硬上限进入 schema（设计 §14）：greet 最大 3 / accept 最大 1 / reject 无 limit 字段', () => {
  const tools = manifestTools()
  const greet = tools.find((t) => t.name === 'boss_greet')!
  const props = greet.inputSchema.properties as Record<string, { maximum?: number; default?: number }>
  assert.equal(props.limit!.maximum, 3)
  assert.equal(props.limit!.default, 1)
  const accept = tools.find((t) => t.name === 'boss_accept_resume')!
  const acceptProps = accept.inputSchema.properties as Record<string, { maximum?: number; default?: number }>
  assert.equal(acceptProps.limit!.maximum, 1)
  assert.equal(acceptProps.limit!.default, 1)
  const reject = tools.find((t) => t.name === 'boss_reject_current')!
  assert.deepEqual(reject.inputSchema.properties, {})
  assert.equal(TOOL_NAMES.length, 16)
})

test('version --json 输出与 buildManifest/computeSchemaDigest 同源', () => {
  const cli = path.join(ROOT, 'dist', 'src', 'cli', 'index.js')
  const out = execFileSync(process.execPath, [cli, 'version', '--json'], { encoding: 'utf8' })
  const parsed = JSON.parse(out)
  assert.equal(parsed.schema_digest, computeSchemaDigest())
  assert.equal(parsed.provider_id, 'ai.aidwork.boss-recruiting')
  assert.equal(parsed.tools.length, 16)
})

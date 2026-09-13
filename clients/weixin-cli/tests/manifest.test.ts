/**
 * manifest digest 稳定性 + 字段完整性 + 静态 provider-manifest.json 同源 + version --json（上位规范 §6）。
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
  staticManifestFields,
} from '../src/mcp/manifest.js'
import { TOOL_NAMES } from '../src/mcp/toolDefs.js'
import { getOperationEntry } from '../src/operations/registry.js'

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
  assert.equal(canonicalSerialize({ a: undefined, b: 1 }), '{"b":1}')
})

test('manifest 字段完整且与静态 provider-manifest.json 同源（上位规范 §6）', () => {
  const m = buildManifest()
  const staticFields = staticManifestFields()
  const staticFile = JSON.parse(readFileSync(path.join(ROOT, 'provider-manifest.json'), 'utf8'))

  // 身份字段：buildManifest 与静态文件一致（设计 §4.1）
  assert.equal(m.provider_id, 'ai.aidwork.weixin')
  assert.equal(staticFile.provider_id, 'ai.aidwork.weixin')
  assert.deepEqual(
    { protocol: m.protocol, transport: m.transport, platforms: m.platforms, entrypoint: m.entrypoint, execution_target: m.execution_target, min_mcp_protocol_version: m.min_mcp_protocol_version },
    { protocol: staticFields.protocol, transport: staticFields.transport, platforms: staticFields.platforms, entrypoint: staticFields.entrypoint, execution_target: staticFields.execution_target, min_mcp_protocol_version: staticFields.min_mcp_protocol_version },
  )
  assert.deepEqual(m.platforms, ['win32-x64'])
  assert.deepEqual(m.entrypoint, ['aid-weixin.exe', 'mcp', '--stdio'])
  assert.equal(m.execution_target, 'local_required')

  // 运行时字段
  const pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
  assert.equal(m.provider_version, pkg.version)
  assert.equal(m.schema_digest, computeSchemaDigest())

  // M2 + C2：weixin_probe + chat_search/message_send/history_read/unread_list + session_observe
  assert.deepEqual(TOOL_NAMES, [
    'weixin_probe',
    'weixin_chat_search',
    'weixin_message_send',
    'weixin_history_read',
    'weixin_session_observe',
    'weixin_unread_list',
  ])
  assert.equal(m.tools.length, 6)
  const probe = m.tools[0]!
  assert.equal(probe.name, 'weixin_probe')
  assert.ok(probe.title.length > 0 && probe.description.length > 0)
  assert.equal(probe.inputSchema.type, 'object')
  assert.equal(probe.annotations.readOnlyHint, true)

  // M2 注解语义：message_send 是唯一写动作（readOnly=false 且非幂等），其余只读
  const byName = new Map(m.tools.map((t) => [t.name, t]))
  const send = byName.get('weixin_message_send')!
  assert.equal(send.annotations.readOnlyHint, false)
  assert.equal(send.annotations.idempotentHint, false)
  for (const n of ['weixin_chat_search', 'weixin_history_read', 'weixin_session_observe', 'weixin_unread_list']) {
    assert.equal(byName.get(n)!.annotations.readOnlyHint, true, `${n} 应为只读`)
  }
})

test('tool schema 单一来源：manifestTools 的 inputSchema 由 toolDefs 推导（改 toolDefs 即改 digest）', () => {
  const tools = manifestTools()
  const probeProps = tools[0]!.inputSchema.properties as Record<string, { type?: string }>
  assert.equal(probeProps.verbose!.type, 'boolean')
})

test('toolDefs ↔ registry 一致性守卫：每个 toolDef 可取到 operation，未注册名 fail-loud', () => {
  // 意图：toolDefs 与 OPERATIONS 是两个手工列表，漂移时必须启动期就失败；
  // 若守卫失效，MCP 运行期会以 TypeError 裸错回 Host（无结构化 code）
  for (const name of TOOL_NAMES) {
    assert.equal(getOperationEntry(name).operation.name, name)
  }
  assert.throws(() => getOperationEntry('weixin_nonexistent'), /漂移/)
})

test('version --json 输出与 buildManifest/computeSchemaDigest 同源', () => {
  const cli = path.join(ROOT, 'dist', 'src', 'cli', 'index.js')
  const out = execFileSync(process.execPath, [cli, 'version', '--json'], { encoding: 'utf8' })
  const parsed = JSON.parse(out)
  assert.equal(parsed.schema_digest, computeSchemaDigest())
  assert.equal(parsed.provider_id, 'ai.aidwork.weixin')
  assert.deepEqual(parsed.tools.map((t: { name: string }) => t.name), [
    'weixin_probe',
    'weixin_chat_search',
    'weixin_message_send',
    'weixin_history_read',
    'weixin_session_observe',
    'weixin_unread_list',
  ])
})

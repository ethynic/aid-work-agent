/**
 * manifest digest 稳定性 + 字段完整性 + 静态 provider-manifest.json 同源 + version --json。
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
import { getOperationEntry, OPERATION_NAMES } from '../src/operations/registry.js'

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

test('manifest 字段完整且与静态 provider-manifest.json 同源', () => {
  const m = buildManifest()
  const staticFields = staticManifestFields()
  const staticFile = JSON.parse(readFileSync(path.join(ROOT, 'provider-manifest.json'), 'utf8'))

  assert.equal(m.provider_id, 'ai.aidwork.wecom')
  assert.equal(staticFile.provider_id, 'ai.aidwork.wecom')
  assert.deepEqual(
    { protocol: m.protocol, transport: m.transport, platforms: m.platforms, entrypoint: m.entrypoint, execution_target: m.execution_target, min_mcp_protocol_version: m.min_mcp_protocol_version },
    { protocol: staticFields.protocol, transport: staticFields.transport, platforms: staticFields.platforms, entrypoint: staticFields.entrypoint, execution_target: staticFields.execution_target, min_mcp_protocol_version: staticFields.min_mcp_protocol_version },
  )
  assert.deepEqual(m.platforms, ['win32-x64'])
  assert.deepEqual(m.entrypoint, ['aid-wecom.exe', 'mcp', '--stdio'])
  assert.equal(m.execution_target, 'local_required')

  const pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
  assert.equal(m.provider_version, pkg.version)
  assert.equal(m.schema_digest, computeSchemaDigest())

  // M1：wecom_probe + wecom_add_customer；M2：wecom_chat_search + wecom_message_send；
  // M3：wecom_unread_list + wecom_watch_poll（wecom_history_read 只走 CLI，不进 MCP）
  assert.deepEqual(TOOL_NAMES, ['wecom_probe', 'wecom_add_customer', 'wecom_chat_search', 'wecom_message_send', 'wecom_unread_list', 'wecom_watch_poll'])
  assert.equal(m.tools.length, 6)
  const probe = m.tools[0]!
  assert.equal(probe.name, 'wecom_probe')
  assert.ok(probe.title.length > 0 && probe.description.length > 0)
  assert.equal(probe.inputSchema.type, 'object')
  assert.equal(probe.annotations.readOnlyHint, true)

  // 注解语义：add_customer / message_send 是写动作（readOnly=false 且非幂等），search 只读幂等
  const byName = new Map(m.tools.map((t) => [t.name, t]))
  for (const n of ['wecom_add_customer', 'wecom_message_send']) {
    assert.equal(byName.get(n)!.annotations.readOnlyHint, false, `${n} 应为写动作`)
    assert.equal(byName.get(n)!.annotations.idempotentHint, false, `${n} 应非幂等`)
  }
  assert.equal(byName.get('wecom_chat_search')!.annotations.readOnlyHint, true)
  assert.equal(byName.get('wecom_chat_search')!.annotations.idempotentHint, true)
  // M3 读工具注解：unread_list 只读幂等；watch_poll 只读但非幂等（推进水位）
  assert.equal(byName.get('wecom_unread_list')!.annotations.readOnlyHint, true)
  assert.equal(byName.get('wecom_unread_list')!.annotations.idempotentHint, true)
  assert.equal(byName.get('wecom_watch_poll')!.annotations.readOnlyHint, true)
  assert.equal(byName.get('wecom_watch_poll')!.annotations.idempotentHint, false)
  // wecom_history_read 不进 MCP（长滚动抓取只适合 CLI）
  assert.equal(byName.has('wecom_history_read'), false)
})

test('tool schema 单一来源：manifestTools 的 inputSchema 由 toolDefs 推导（改 toolDefs 即改 digest）', () => {
  const tools = manifestTools()
  const probeProps = tools[0]!.inputSchema.properties as Record<string, { type?: string }>
  assert.equal(probeProps.verbose!.type, 'boolean')
  const addSchema = tools[1]!.inputSchema
  assert.deepEqual(addSchema.required, ['phone', 'confirm'])
  const sendSchema = tools[3]!.inputSchema
  assert.deepEqual(sendSchema.required, ['target_ref', 'text'])
})

test('toolDefs ↔ registry 一致性守卫：每个 toolDef 可取到 operation，未注册名 fail-loud', () => {
  // 意图：toolDefs 与 OPERATIONS 是两个手工列表，漂移时必须启动期就失败；
  // 若守卫失效，MCP 运行期会以 TypeError 裸错回 Host（无结构化 code）
  for (const name of TOOL_NAMES) {
    assert.equal(getOperationEntry(name).operation.name, name)
  }
  // registry ⊇ toolDefs：M3 起 registry 有 7 个 operation（wecom_history_read 仅 CLI），
  // toolDefs 6 个（MCP 不暴露 history_read）；每个 MCP tool 必须能在 registry 取到
  assert.equal(OPERATION_NAMES.length, 7)
  assert.ok(OPERATION_NAMES.includes('wecom_history_read'))
  for (const name of TOOL_NAMES) {
    assert.ok(OPERATION_NAMES.includes(name), `MCP tool「${name}」必须在 OPERATIONS 注册`)
  }
  assert.throws(() => getOperationEntry('wecom_nonexistent'), /漂移/)
})

test('version --json 输出与 buildManifest/computeSchemaDigest 同源', () => {
  const cli = path.join(ROOT, 'dist', 'src', 'cli', 'index.js')
  const out = execFileSync(process.execPath, [cli, 'version', '--json'], { encoding: 'utf8' })
  const parsed = JSON.parse(out)
  assert.equal(parsed.schema_digest, computeSchemaDigest())
  assert.equal(parsed.provider_id, 'ai.aidwork.wecom')
  assert.deepEqual(
    parsed.tools.map((t: { name: string }) => t.name),
    ['wecom_probe', 'wecom_add_customer', 'wecom_chat_search', 'wecom_message_send', 'wecom_unread_list', 'wecom_watch_poll'],
  )
})

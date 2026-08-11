/**
 * Provider manifest 构建与 schema digest 计算（实施规格 m02 §7 / 标准 §6）。
 *
 * digest 算法：toolDefs 的 name+description+inputSchema+annotations 按 key 排序
 * JSON.stringify（规范序列化）→ sha256。inputSchema 由 zodShape 用 zod v4 toJSONSchema
 * 推导（与 SDK list_tools 同源）。computeSchemaDigest/buildManifest 同时供
 * `version --json` 输出和测试断言使用。
 */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { z } from 'zod'
import { TOOL_DEFS } from './toolDefs.js'

export interface ManifestTool {
  name: string
  title: string
  description: string
  inputSchema: Record<string, unknown>
  annotations: Record<string, unknown>
}

export interface ProviderManifest {
  provider_id: string
  provider_version: string
  protocol: 'mcp'
  transport: 'stdio'
  platforms: string[]
  entrypoint: string[]
  tools: ManifestTool[]
  schema_digest: string
  execution_target: 'local_required'
  min_mcp_protocol_version: string
}

/** 规范序列化：对象 key 递归排序后 JSON.stringify（数组保持顺序），同一输入恒定输出 */
export function canonicalSerialize(value: unknown): string {
  return JSON.stringify(sortKeys(value))
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys)
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    return Object.fromEntries(entries.map(([k, v]) => [k, sortKeys(v)]))
  }
  return value
}

/** zodShape → JSON Schema（与 SDK 1.30.0 list_tools 相同的推导：zod v4 toJSONSchema draft-7 / io=input） */
export function jsonSchemaOf(def: (typeof TOOL_DEFS)[number]): Record<string, unknown> {
  return z.toJSONSchema(z.object(def.zodShape), { target: 'draft-7', io: 'input' }) as Record<string, unknown>
}

/** manifest 的 tools 数组（digest 来源） */
export function manifestTools(): ManifestTool[] {
  return TOOL_DEFS.map((def) => ({
    name: def.name,
    title: def.title,
    description: def.description,
    inputSchema: jsonSchemaOf(def),
    annotations: def.annotations as unknown as Record<string, unknown>,
  }))
}

/** schema digest：对 tools 数组规范序列化的 sha256 */
export function computeSchemaDigest(): string {
  const hash = createHash('sha256').update(canonicalSerialize(manifestTools()), 'utf8').digest('hex')
  return `sha256:${hash}`
}

/** 读取 package.json version（dist/src/mcp → 项目根） */
export function providerVersion(): string {
  const pkg = JSON.parse(readFileSync(new URL('../../../package.json', import.meta.url), 'utf8')) as { version: string }
  return pkg.version
}

export function buildManifest(): ProviderManifest {
  return {
    provider_id: 'ai.aidwork.boss-recruiting',
    provider_version: providerVersion(),
    protocol: 'mcp',
    transport: 'stdio',
    platforms: ['win32-x64'],
    entrypoint: ['boss-recruiting.exe', 'mcp', '--stdio'],
    tools: manifestTools(),
    schema_digest: computeSchemaDigest(),
    execution_target: 'local_required',
    min_mcp_protocol_version: '2024-11-05',
  }
}

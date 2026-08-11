/**
 * Provider manifest 构建与 schema digest 计算（上位规范 §6 / 设计 §4.1）。
 *
 * 身份字段（provider_id/entrypoint/platforms/execution_target 等）的单一来源是包根
 * provider-manifest.json（随签名包发布、运行时不可被云端覆盖）；本模块读取该文件并叠加
 * 运行时推导的 provider_version / tools / schema_digest。
 *
 * digest 算法：toolDefs 的 name+title+description+inputSchema+annotations 按 key 排序
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

/** 包根 provider-manifest.json 的静态身份字段（digest/tools/version 不入文件，避免漂移） */
interface StaticManifestFields {
  provider_id: string
  protocol: 'mcp'
  transport: 'stdio'
  platforms: string[]
  entrypoint: string[]
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

/** 读取静态身份字段（dist/src/mcp → 包根） */
export function staticManifestFields(): StaticManifestFields {
  return JSON.parse(readFileSync(new URL('../../../provider-manifest.json', import.meta.url), 'utf8')) as StaticManifestFields
}

/** 读取 package.json version（dist/src/mcp → 包根） */
export function providerVersion(): string {
  const pkg = JSON.parse(readFileSync(new URL('../../../package.json', import.meta.url), 'utf8')) as { version: string }
  return pkg.version
}

export function buildManifest(): ProviderManifest {
  const staticFields = staticManifestFields()
  return {
    ...staticFields,
    provider_version: providerVersion(),
    tools: manifestTools(),
    schema_digest: computeSchemaDigest(),
  }
}

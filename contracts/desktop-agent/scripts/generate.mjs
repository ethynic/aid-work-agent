import { readdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const schemaDir = path.join(root, 'schemas')
const pascal = (value) => value.replace(/(^|-)([a-z])/g, (_, _d, c) => c.toUpperCase())
const refName = (ref) => pascal(path.basename(ref, '.schema.json'))
function typeOf(node, py = false) {
  if (node.$ref) return refName(node.$ref)
  if (node.oneOf) return node.oneOf.map((entry) => typeOf(entry, py)).join(' | ')
  if (Object.hasOwn(node, 'const')) return py ? `Literal[${JSON.stringify(node.const)}]` : JSON.stringify(node.const)
  if (node.type === 'array') return py ? `list[${typeOf(node.items ?? {}, true)}]` : `Array<${typeOf(node.items ?? {})}>`
  if (node.type === 'string') return py ? 'str' : 'string'
  if (node.type === 'integer' || node.type === 'number') return py ? (node.type === 'integer' ? 'int' : 'float') : 'number'
  if (node.type === 'boolean') return py ? 'bool' : 'boolean'
  if (node.type === 'object' || Object.keys(node).length === 0) return py ? 'dict[str, object]' : 'Record<string, unknown>'
  throw new Error(`Unsupported schema node: ${JSON.stringify(node)}`)
}
function render(schema, py = false) {
  if (schema.oneOf) return py ? `${schema.title} = ${typeOf(schema, true)}` : `export type ${schema.title} = ${typeOf(schema)}`
  if (schema.type !== 'object' || schema.additionalProperties !== false) return ''
  const required = new Set(schema.required ?? [])
  const fields = Object.entries(schema.properties ?? {}).map(([name, node]) => py
    ? `    ${name}: ${required.has(name) ? typeOf(node, true) : `NotRequired[${typeOf(node, true)}]`}`
    : `  ${name}${required.has(name) ? '' : '?'}: ${typeOf(node)}`)
  return py ? `class ${schema.title}(TypedDict):\n${fields.join('\n') || '    pass'}` : `export interface ${schema.title} {\n${fields.join('\n')}\n}`
}
function validate(value, schema, byFile, at = '$') {
  if (schema.$ref) { const target = byFile.get(schema.$ref); if (!target) throw new Error(`Unknown ref ${schema.$ref}`); return validate(value, target, byFile, at) }
  if (schema.oneOf) { for (const branch of schema.oneOf) { try { validate(value, branch, byFile, at); return } catch {} } throw new Error(`${at} does not match oneOf`) }
  if (Object.hasOwn(schema, 'const') && value !== schema.const) throw new Error(`${at} has wrong constant`)
  if (schema.type === 'string' && (typeof value !== 'string' || (schema.minLength && value.length < schema.minLength))) throw new Error(`${at} must be a valid string`)
  if (schema.type === 'array') { if (!Array.isArray(value)) throw new Error(`${at} must be an array`); value.forEach((v, i) => validate(v, schema.items ?? {}, byFile, `${at}[${i}]`)) }
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${at} must be an object`)
    for (const key of schema.required ?? []) if (!Object.hasOwn(value, key)) throw new Error(`${at}.${key} is required`)
    if (schema.additionalProperties === false) for (const key of Object.keys(value)) if (!Object.hasOwn(schema.properties ?? {}, key)) throw new Error(`${at}.${key} is not allowed`)
    for (const [key, child] of Object.entries(schema.properties ?? {})) if (Object.hasOwn(value, key)) validate(value[key], child, byFile, `${at}.${key}`)
  }
}
const negotiate = (client, server) => client.filter((v) => server.includes(v)).sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))[0] ?? null
async function main() {
  const files = (await readdir(schemaDir)).filter((file) => file.endsWith('.schema.json')).sort()
  const schemas = await Promise.all(files.map(async (file) => JSON.parse(await readFile(path.join(schemaDir, file), 'utf8'))))
  const byFile = new Map(files.map((file, i) => [file, schemas[i]]))
  const version = byFile.get('agent-turn-next-response.schema.json')['x-protocol-version']
  for (const name of ['version-compatible.json', 'version-incompatible.json']) {
    const sample = JSON.parse(await readFile(path.join(root, 'examples', name), 'utf8'))
    if (negotiate(sample.client_supported, sample.server_supported) !== sample.selected) throw new Error(`${name} negotiation is invalid`)
  }
  validate(JSON.parse(await readFile(path.join(root, 'examples/agent-turn-next-response.json'), 'utf8')), byFile.get('agent-turn-next-response.schema.json'), byFile)
  const ts = `// Generated from schemas/*.schema.json. Do not edit.\nexport const DESKTOP_AGENT_PROTOCOL_VERSION = ${JSON.stringify(version)} as const\n\n${schemas.map((s) => render(s)).filter(Boolean).join('\n\n')}\n`
  const pythonSchemas = [...schemas.filter((s) => !s.oneOf), ...schemas.filter((s) => s.oneOf)]
  const py = `# Generated from schemas/*.schema.json. Do not edit.\nfrom __future__ import annotations\n\nfrom typing import Literal, NotRequired, TypedDict\n\nDESKTOP_AGENT_PROTOCOL_VERSION: Literal[${JSON.stringify(version)}] = ${JSON.stringify(version)}\n\n${pythonSchemas.map((s) => render(s, true)).filter(Boolean).join('\n\n')}\n`
  for (const [file, content] of [[path.join(root, 'generated/desktopAgent.ts'), ts], [path.join(root, 'generated/desktop_agent.py'), py]]) {
    if (process.argv.includes('--check')) { if ((await readFile(file, 'utf8').catch(() => '')) !== content) throw new Error(`${path.relative(root, file)} is stale`) }
    else await writeFile(file, content, 'utf8')
  }
  process.stdout.write(`Desktop Agent protocol ${version}: schemas, negotiation examples, TS and Python agree.\n`)
}
main().catch((error) => { console.error(error.message ?? error); process.exitCode = 1 })

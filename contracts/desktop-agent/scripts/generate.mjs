import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const contractRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const schemasDirectory = path.join(contractRoot, 'schemas')
const schemaFiles = [
  'agent-turn-correlation.schema.json',
  'agent-turn-payload-reference.schema.json',
  'agent-turn-envelope.schema.json',
]

const pascalCase = (value) => value.replace(/(^|-)([a-z])/g, (_, _dash, letter) => letter.toUpperCase())
const refName = (ref) => pascalCase(path.basename(ref, '.schema.json'))

function tsType(property) {
  if (Object.hasOwn(property, 'const')) return JSON.stringify(property.const)
  if (property.$ref) return refName(property.$ref)
  if (property.type === 'string') return 'string'
  if (property.type === 'object') return 'Record<string, unknown>'
  throw new Error(`Unsupported TypeScript schema node: ${JSON.stringify(property)}`)
}

function pythonType(property) {
  if (Object.hasOwn(property, 'const')) return `Literal[${JSON.stringify(property.const)}]`
  if (property.$ref) return refName(property.$ref)
  if (property.type === 'string') return 'str'
  if (property.type === 'object') return 'dict[str, object]'
  throw new Error(`Unsupported Python schema node: ${JSON.stringify(property)}`)
}

function renderInterface(schema, mapper, header, declaration) {
  if (schema.type !== 'object' || schema.additionalProperties !== false) {
    throw new Error(`${schema.title} must be a closed object schema`)
  }
  const required = new Set(schema.required ?? [])
  const fields = Object.entries(schema.properties ?? {}).map(([name, property]) => ({
    name,
    optional: !required.has(name),
    type: mapper(property),
  }))
  return declaration(schema.title, fields, header)
}

function renderTypeScript(schemas, version) {
  const blocks = schemas.map((schema) => renderInterface(schema, tsType, '', (name, fields) => [
    `export interface ${name} {`,
    ...fields.map((field) => `  ${field.name}${field.optional ? '?' : ''}: ${field.type}`),
    '}',
  ].join('\n')))
  return `// Generated from schemas/*.schema.json. Do not edit.\nexport const DESKTOP_AGENT_PROTOCOL_VERSION = ${JSON.stringify(version)} as const\n\n${blocks.join('\n\n')}\n`
}

function renderPython(schemas, version) {
  const blocks = schemas.map((schema) => renderInterface(schema, pythonType, '', (name, fields) => [
    `class ${name}(TypedDict):`,
    ...fields.map((field) => `    ${field.name}: ${field.type}`),
  ].join('\n')))
  return `# Generated from schemas/*.schema.json. Do not edit.\nfrom typing import Literal, TypedDict\n\nDESKTOP_AGENT_PROTOCOL_VERSION: Literal[${JSON.stringify(version)}] = ${JSON.stringify(version)}\n\n${blocks.join('\n\n')}\n`
}

function validateValue(value, schema, schemasByFile, location = '$') {
  if (schema.$ref) return validateValue(value, schemasByFile.get(schema.$ref), schemasByFile, location)
  if (Object.hasOwn(schema, 'const') && value !== schema.const) throw new Error(`${location} must equal ${JSON.stringify(schema.const)}`)
  if (schema.type === 'string' && (typeof value !== 'string' || (schema.minLength && value.length < schema.minLength))) throw new Error(`${location} must be a non-empty string`)
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${location} must be an object`)
    for (const required of schema.required ?? []) if (!Object.hasOwn(value, required)) throw new Error(`${location}.${required} is required`)
    if (schema.additionalProperties === false) {
      for (const key of Object.keys(value)) if (!Object.hasOwn(schema.properties ?? {}, key)) throw new Error(`${location}.${key} is not allowed`)
    }
    for (const [key, property] of Object.entries(schema.properties ?? {})) if (Object.hasOwn(value, key)) validateValue(value[key], property, schemasByFile, `${location}.${key}`)
  }
}

async function main() {
  const schemas = await Promise.all(schemaFiles.map(async (file) => JSON.parse(await readFile(path.join(schemasDirectory, file), 'utf8'))))
  const schemasByFile = new Map(schemaFiles.map((file, index) => [file, schemas[index]]))
  const envelope = schemasByFile.get('agent-turn-envelope.schema.json')
  const version = envelope['x-protocol-version']
  if (!/^\d+\.\d+$/.test(version) || envelope.properties.protocol_version.const !== version) throw new Error('Envelope protocol version must use matching MAJOR.MINOR values')
  const example = JSON.parse(await readFile(path.join(contractRoot, 'examples/agent-turn-envelope.json'), 'utf8'))
  validateValue(example, envelope, schemasByFile)
  const outputs = new Map([
    [path.join(contractRoot, 'generated/desktopAgent.ts'), renderTypeScript(schemas, version)],
    [path.join(contractRoot, 'generated/desktop_agent.py'), renderPython(schemas, version)],
  ])
  const check = process.argv.includes('--check')
  for (const [file, content] of outputs) {
    if (check) {
      const existing = await readFile(file, 'utf8').catch(() => '')
      if (existing !== content) throw new Error(`${path.relative(contractRoot, file)} is stale; run node scripts/generate.mjs`)
    } else await writeFile(file, content, 'utf8')
  }
  process.stdout.write(`Desktop Agent protocol ${version} schemas, example and generated types are consistent.\n`)
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
})

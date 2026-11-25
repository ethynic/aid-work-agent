import { createHash } from 'node:crypto'
import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { listNativeTools, nativePath, NATIVE_SHA256, openNative } from '../src/native.js'

const root = fileURLToPath(new URL('../', import.meta.url))
const digest = createHash('sha256').update(readFileSync(nativePath(root))).digest('hex')
if (digest !== NATIVE_SHA256) throw new Error('官方 agent-browser 执行器 SHA-256 不匹配')

const needed = new Set(['agent_browser_upload', 'agent_browser_batch'])
const core = await openNative(root, 'core')
let tools
try {
  tools = await listNativeTools(core.client)
} finally {
  await core.close()
}
const extended = await openNative(root, 'core,debug')
try {
  tools.push(...(await listNativeTools(extended.client)).filter(tool => needed.has(tool.name)))
} finally {
  await extended.close()
}
const names = new Set(tools.map(tool => tool.name))
if (names.has('agent_browser_chat') || names.size !== tools.length || [...needed].some(name => !names.has(name))) {
  throw new Error('原生 MCP 公开工具目录与插件接线约定不符')
}
const target = join(root, 'dist', 'src')
mkdirSync(join(target, 'cli'), { recursive: true })
cpSync(join(root, 'src', 'native.js'), join(target, 'native.js'))
cpSync(join(root, 'src', 'cli', 'index.js'), join(target, 'cli', 'index.js'))
writeFileSync(join(target, 'cli', 'native-tools.json'), JSON.stringify(tools, null, 2) + '\n')

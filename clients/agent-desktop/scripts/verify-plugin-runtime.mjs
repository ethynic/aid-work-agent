import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

export const WECOM_BUSINESS_TOOLS = ['wecom_read_session', 'wecom_read_sessions', 'wecom_message_send', 'wecom_session_directory']

// Read only the target's public resources. Do not import a plugin or modify its trust store.
export async function verifyPluginRuntime(resources, packagePath) {
  const runtime = existsSync(join(resources, 'runtime/trust-roots.json')) ? join(resources, 'runtime') : resolve(resources)
  const core = join(runtime, 'host/node_modules/@aid/local-tool-host-core')
  const version = JSON.parse(readFileSync(join(core, 'package.json'), 'utf8')).version
  const hostVersion = JSON.parse(readFileSync(join(runtime, 'host/package.json'), 'utf8')).version
  const roots = JSON.parse(readFileSync(join(runtime, 'trust-roots.json'), 'utf8')).roots
  const node = join(runtime, 'node/win-x64/node.exe')
  const facts = JSON.parse(execFileSync(node, ['-e', 'console.log(JSON.stringify({platform:process.platform,arch:process.arch,node_version:process.versions.node,node_abi:process.versions.modules}))'], { encoding: 'utf8', windowsHide: true }))
  const { verifyOfflinePackage } = await import(pathToFileURL(join(core, 'dist/plugins/package.js')).href)
  const { getProviderManifest } = await import(pathToFileURL(join(core, 'dist/legacy/providers.js')).href)
  const { RUNTIME_VERSION: runtimeVersion } = await import(pathToFileURL(join(core, 'dist/legacy/config.js')).href)
  const verified = await verifyOfflinePackage(readFileSync(packagePath), roots, { ...facts, runtime_version: runtimeVersion })
  if (verified.manifest.provider_id !== 'ai.aidwork.wecom') throw new Error('Expected a WeCom plugin')
  const allowed = getProviderManifest('wecom')?.tools ?? []
  const packaged = verified.manifest.tools.map(tool => typeof tool === 'string' ? tool : tool.name)
  const missing = WECOM_BUSINESS_TOOLS.filter(tool => !allowed.includes(tool) || !packaged.includes(tool))
  if (missing.length) throw new Error(`Target Runtime/plugin lacks business tools: ${missing.join(', ')}`)
  return { result: 'WECOM_TARGET_RUNTIME_PASS', resources: runtime, runtimeVersion, hostVersion, coreVersion: version, providerVersion: verified.manifest.provider_version, publisher: verified.envelope.publisher_key_id, tools: WECOM_BUSINESS_TOOLS }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try {
    const args = process.argv.slice(2)
    const option = name => args[args.indexOf(name) + 1]
    if (!args.includes('--resources') || !args.includes('--package')) throw new Error('--resources and --package are required')
    const report = await verifyPluginRuntime(option('--resources'), option('--package'))
    if (args.includes('--output')) writeFileSync(option('--output'), JSON.stringify(report, null, 2) + '\n')
    console.log(JSON.stringify(report))
  } catch (error) {
    console.error(`目标 Runtime 不能使用此 WeCom 包：${error.message}。请使用 release-batch.ps1 -Component RuntimeWecom -RuntimeVersion <新版本> -WecomVersion <新版本> -Signer <稳定签名者> 配套出包；不要修改客户机信任根。`)
    process.exitCode = 1
  }
}

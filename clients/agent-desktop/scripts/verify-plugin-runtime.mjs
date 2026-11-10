import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

export const WECOM_BUSINESS_TOOLS = ['wecom_read_session', 'wecom_read_sessions', 'wecom_message_send', 'wecom_session_directory']
const providerIds = { boss: 'ai.aidwork.boss-recruiting', wecom: 'ai.aidwork.wecom' }

// Read only the target's public resources. Do not import a plugin or modify its trust store.
export async function verifyPluginRuntime(resources, packagePath, provider = 'wecom') {
  if (!Object.hasOwn(providerIds, provider)) throw new Error('Expected boss or wecom as the target provider')
  const runtime = existsSync(join(resources, 'runtime/trust-roots.json')) ? join(resources, 'runtime') : resolve(resources)
  const core = join(runtime, 'host/node_modules/@aid/local-tool-host-core')
  const version = JSON.parse(readFileSync(join(core, 'package.json'), 'utf8')).version
  const hostVersion = JSON.parse(readFileSync(join(runtime, 'host/package.json'), 'utf8')).version
  const roots = JSON.parse(readFileSync(join(runtime, 'trust-roots.json'), 'utf8')).roots
  const node = join(runtime, 'node/win-x64/node.exe')
  const facts = JSON.parse(execFileSync(node, ['-e', 'console.log(JSON.stringify({platform:process.platform,arch:process.arch,node_version:process.versions.node,node_abi:process.versions.modules}))'], { encoding: 'utf8', windowsHide: true }))
  const { verifyOfflinePackage } = await import(pathToFileURL(join(core, 'dist/plugins/package.js')).href)
  const { RUNTIME_VERSION: runtimeVersion } = await import(pathToFileURL(join(core, 'dist/legacy/config.js')).href)
  const verified = await verifyOfflinePackage(readFileSync(packagePath), roots, { ...facts, runtime_version: runtimeVersion })
  if (verified.manifest.provider_id !== providerIds[provider]) throw new Error(`Expected a ${provider} plugin`)
  const report = { result: `${provider.toUpperCase()}_TARGET_RUNTIME_PASS`, resources: runtime, runtimeVersion, hostVersion, coreVersion: version, providerVersion: verified.manifest.provider_version, publisher: verified.envelope.publisher_key_id }
  if (provider === 'wecom') {
    const { getProviderManifest } = await import(pathToFileURL(join(core, 'dist/legacy/providers.js')).href)
    const allowed = getProviderManifest('wecom')?.tools ?? []
    const packaged = verified.manifest.tools.map(tool => typeof tool === 'string' ? tool : tool.name)
    const missing = WECOM_BUSINESS_TOOLS.filter(tool => !allowed.includes(tool) || !packaged.includes(tool))
    if (missing.length) throw new Error(`Target Runtime/plugin lacks business tools: ${missing.join(', ')}`)
    report.tools = WECOM_BUSINESS_TOOLS
  }
  return report
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const args = process.argv.slice(2)
  const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : undefined
  const provider = option('--provider') ?? 'wecom'
  try {
    if (!args.includes('--resources') || !args.includes('--package')) throw new Error('--resources and --package are required')
    const report = await verifyPluginRuntime(option('--resources'), option('--package'), provider)
    if (args.includes('--output')) writeFileSync(option('--output'), JSON.stringify(report, null, 2) + '\n')
    console.log(JSON.stringify(report))
  } catch (error) {
    console.error(`目标 Runtime 不能使用此 ${provider} 插件：${error.message}。配套出包见 docs/ops/runtime-boss-release-guide.md。`)
    process.exitCode = 1
  }
}

import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { probePlugin } from '../src/pluginProbe.js'
import type { RuntimeManifest } from '@aid/local-tool-host-core'

test('plugin version and readiness probes preserve an existing Windows home beyond MAX_PATH', { skip: process.platform !== 'win32' }, async t => {
  const owned = mkdtempSync(join(tmpdir(), 'runtime-probe-long-cwd-'))
  t.after(() => rmSync(owned, { recursive: true, force: true }))
  let root = owned
  while (root.length < 310) root = join(root, 'retained-connection-with-signed-plugin')
  mkdirSync(root, { recursive: true })
  const manifest: RuntimeManifest = { manifest_version: 1, provider_id: 'ai.aidwork.boss-recruiting', provider_version: '1.0.0',
    display_name: 'probe fixture', description: 'isolated signed-entry contract', protocol: { version: 1 }, transport: 'stdio', platforms: ['win32'],
    entrypoint: ['probe.cjs', 'mcp', '--stdio'], tools: [{ name: 'fixture' }], schema_digest: 'fixture', execution_target: 'local_required',
    distribution: { platform: 'win32', arch: 'x64', node: { version: '22.23.3', modules_abi: '127' }, required_files: [] } }
  writeFileSync(join(root, 'probe.cjs'), `if(process.argv[2]==='version'){console.log(${JSON.stringify(JSON.stringify(manifest))})}else{console.log(JSON.stringify({success:false,runtime_readiness:{ready:false,reason:'请登录业务应用'}}));process.exitCode=1}`)
  const node = process.env.AIDWORK_TEST_NODE ? resolve(process.env.AIDWORK_TEST_NODE) : process.execPath
  const result = await probePlugin(node, root, manifest, {})
  assert.equal(result.ready, false)
  assert.equal(result.reason, '请登录业务应用', 'A retained long home must reach the business readiness check instead of failing child-process pipes')
})

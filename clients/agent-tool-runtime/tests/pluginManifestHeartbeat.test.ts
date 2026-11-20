import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import type { SignedPluginManifest } from '@aid/local-tool-host-core/plugins/package'
import { ApiClient } from '../src/apiClient.js'
import { PollLoop } from '../src/pollLoop.js'
import { ProviderSet } from '../src/providerManager.js'

for (const supportsSync of [false, true]) {
  test(`manifest heartbeat ${supportsSync ? 'sends only the requested snapshot' : 'continues with an old server'}`, async () => {
    // These objects stand in for the PluginStore's verified snapshot. This test
    // drives the wire protocol and never opens a real provider process.
    const a = { envelope: { manifest_digest: `sha256:${'a'.repeat(64)}` }, manifest: { provider_id: 'ai.aidwork.a' } } as SignedPluginManifest
    const b = { envelope: { manifest_digest: `sha256:${'b'.repeat(64)}` }, manifest: { provider_id: 'ai.aidwork.b' } } as SignedPluginManifest
    const heartbeats: Array<{ capabilities: Record<string, unknown>; plugin_manifests?: Record<string, SignedPluginManifest> }> = []
    const claims = new Map<boolean, Record<string, unknown>>()
    let sentSerial = false
    let drainedSerial = false
    let finish!: () => void
    const complete = new Promise<void>(resolve => { finish = resolve })
    const finishIfReady = () => {
      if (heartbeats.length >= 3 && claims.has(false) && (!supportsSync || claims.has(true) && drainedSerial)) finish()
    }
    const server = createServer(async (request, response) => {
      const chunks = []
      for await (const chunk of request) chunks.push(chunk)
      const body = JSON.parse(Buffer.concat(chunks).toString())
      response.setHeader('Content-Type', 'application/json')
      if (request.url?.includes('/claim?')) {
        const parallel = request.url.includes('parallel_only=true')
        claims.set(parallel, body)
        await new Promise(resolve => setTimeout(resolve, 10))
        if (supportsSync && !parallel && !sentSerial) {
          sentSerial = true
          response.end(JSON.stringify({ success: true, invocation_id: '47397410-a21e-4c4a-85cd-3863cb21860f',
            tool_name: 'fixture_tool', provider: 'a', arguments: {}, claim_token: 'fixture-claim-token' }))
        } else {
          response.end(JSON.stringify({ success: true, invocation: null }))
        }
      } else if (request.url?.endsWith('/result')) {
        // Keep a failed serial fixture in flight so the real parallel lane starts.
        await new Promise(resolve => setTimeout(resolve, 100))
        response.end(JSON.stringify({ success: true }))
      } else {
        heartbeats.push(body)
        response.end(JSON.stringify({ success: true, selected: true, parallel_claim: supportsSync,
          ...(supportsSync && heartbeats.length === 1 ? { missing_plugin_manifests: ['b', 'not-installed'] } : {}) }))
      }
      finishIfReady()
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address()
    assert(address && typeof address === 'object')
    const dataDir = mkdtempSync(join(tmpdir(), 'manifest-heartbeat-'))
    const loop = new PollLoop({
      api: new ApiClient(`http://127.0.0.1:${address.port}`, 'fixture-device-token'),
      runnerDeps: { providers: new ProviderSet({}), desktopCheck: async () => true,
        desktopResourceKey: 'fixture-manifest-sync', runtimeDataDir: dataDir },
      parallelTools: { b: ['tool_b'] },
      onInvocationEnd: () => { drainedSerial = true; finishIfReady() },
      heartbeatIntervalMs: 15,
      capabilities: () => ({ declared_tools: { a: ['tool_a'], b: ['tool_b'] } }),
      pluginManifests: { a, b },
    })
    const processing = loop.run()
    try {
      await Promise.race([complete, once(AbortSignal.timeout(3000), 'abort').then(() => {
        throw new Error('heartbeat fixture did not converge')
      })])
      for (const request of heartbeats.slice(0, 3)) {
        assert.deepEqual(request.capabilities.plugin_manifest_digests, { a: a.envelope.manifest_digest, b: b.envelope.manifest_digest })
        assert.deepEqual(request.capabilities.declared_tools, { a: ['tool_a'], b: ['tool_b'] })
      }
      assert.equal(heartbeats[0]!.plugin_manifests, undefined)
      assert.deepEqual(heartbeats[1]!.plugin_manifests, supportsSync ? { b } : undefined)
      assert.equal(heartbeats[2]!.plugin_manifests, undefined)
      for (const claim of claims.values()) {
        assert.deepEqual(claim, { plugin_manifest_digests: { a: a.envelope.manifest_digest, b: b.envelope.manifest_digest } })
      }
    } finally {
      loop.shutdown()
      await processing
      server.close()
      rmSync(dataDir, { recursive: true, force: true })
    }
  })
}

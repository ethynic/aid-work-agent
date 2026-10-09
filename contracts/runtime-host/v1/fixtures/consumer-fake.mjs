// TEST ONLY: projection example; does not validate schema or authorize execution.
import assert from 'node:assert/strict'

export function createManagementConsumer() {
  const pending = new Map()
  let instanceId = null
  let snapshot = null
  let generation = 0
  let watermark = 0
  let pluginsSnapshot = null
  let latestListQuery = 0
  let appliedListQuery = 0
  return {
    expect(request) {
      assert.ok(!pending.has(request.request_id), 'request_id already pending')
      const listQuery = request.method === 'plugins.list' ? ++latestListQuery : null
      pending.set(request.request_id, { request: JSON.parse(JSON.stringify(request)), instanceId, listQuery })
    },
    receive(response, sourceGeneration = generation) {
      if (sourceGeneration !== generation) return { ignored: true }
      const entry = pending.get(response.request_id)
      assert.ok(entry, 'reply has no current request')
      if (entry.instanceId !== instanceId) {
        pending.delete(response.request_id)
        return { ignored: true }
      }
      const request = entry.request
      assert.equal(response.method, request.method, 'reply method differs')
      if (response.code !== 0 && request.method === 'plugins.list' && entry.listQuery !== latestListQuery) {
        pending.delete(response.request_id)
        return { ignored: true }
      }
      if (response.code !== 0) {
        pending.delete(response.request_id)
        return { code: response.code, error: response.error }
      }
      if (response.method === 'describe') {
        assert.equal(response.result.api_major, 1, 'unsupported management major')
        if (response.result.instance_id !== instanceId) {
          instanceId = response.result.instance_id
          snapshot = null
          pluginsSnapshot = null
          latestListQuery = 0
          appliedListQuery = 0
          watermark = response.result.revision
        } else {
          watermark = Math.max(watermark, response.result.revision)
        }
      } else if (response.method === 'getState') {
        assert.ok(instanceId, 'describe must precede snapshot')
        const state = response.result
        if (state.instance_id !== instanceId || state.revision < watermark) {
          pending.delete(response.request_id)
          return { ignored: true }
        }
        snapshot = state
        watermark = state.revision
      } else if (response.method === 'plugins.list') {
        assert.ok(instanceId, 'describe must precede plugin list')
        const list = response.result
        if (list.instance_id !== instanceId || list.revision < watermark) {
          pending.delete(response.request_id)
          return { ignored: true }
        }
        if (pluginsSnapshot && list.revision === pluginsSnapshot.revision && entry.listQuery < appliedListQuery) {
          pending.delete(response.request_id)
          return { ignored: true }
        }
        pluginsSnapshot = list
        appliedListQuery = entry.listQuery
        watermark = list.revision
      } else if (response.method === 'operations.get') {
        assert.equal(response.result.operation_id, request.params.operation_id)
      } else if (response.method !== 'plugins.list') {
        const operation = response.method === 'plugins.import' ? 'import' : response.method.replace('plugins.', '')
        assert.equal(response.result.operation, operation)
      }
      pending.delete(response.request_id)
      return { result: response.result }
    },
    needsRefresh(event, sourceGeneration = generation) {
      if (sourceGeneration !== generation || event.instance_id !== instanceId) return false
      watermark = Math.max(watermark, event.revision)
      return !snapshot || snapshot.revision < watermark
    },
    reconnect() {
      generation += 1
      pending.clear()
      instanceId = null
      snapshot = null
      pluginsSnapshot = null
      latestListQuery = 0
      appliedListQuery = 0
      watermark = 0
    },
    get snapshot() { return snapshot },
    get pluginsSnapshot() { return pluginsSnapshot },
    get needsPluginsRefresh() { return !pluginsSnapshot || pluginsSnapshot.revision < watermark },
    get generation() { return generation },
  }
}

export function consumeOutput(output, context) {
  const artifacts = output.artifacts ?? []
  for (const artifact of artifacts) {
    assert.equal(artifact.device_id, context.device_id, 'artifact belongs to another device')
    assert.equal(artifact.invocation_id, context.invocation_id, 'artifact belongs to another invocation')
  }
  return {
    code: output.code,
    error: output.error,
    effect: output.effect,
    text: output.text ?? '',
    complete: output.complete,
    needs_reconciliation: output.effect === 'unknown',
    artifacts,
  }
}

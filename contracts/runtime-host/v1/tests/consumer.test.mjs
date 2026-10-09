import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { createManagementConsumer, consumeOutput } from '../fixtures/consumer-fake.mjs'

const management = JSON.parse(await readFile(new URL('../fixtures/management.json', import.meta.url), 'utf8'))
const outputs = JSON.parse(await readFile(new URL('../fixtures/outputs.json', import.meta.url), 'utf8'))
const sample = name => management.find(c => c.name === name).value
const copy = value => JSON.parse(JSON.stringify(value))

function connected() {
  const consumer = createManagementConsumer()
  consumer.expect(sample('describe_request'))
  consumer.receive(sample('describe_response'))
  consumer.expect(sample('state_request'))
  consumer.receive(sample('state_response'))
  return consumer
}

test('Desktop can consume empty plugins, install failure, installed readiness and stop reconciliation', () => {
  const consumer = connected()
  for (const response of management.filter(c => c.valid && c.schema === 'management-response.schema.json')) {
    if (['describe_response', 'state_response'].includes(response.name)) continue
    const request = management.find(c => c.schema === 'management-request.schema.json' && c.value.request_id === response.value.request_id)
    assert.ok(request, `missing producer request for ${response.name}`)
    consumer.expect(request.value)
    const received = consumer.receive(response.value)
    if (response.name === 'empty_plugins_response') assert.deepEqual(received.result.plugins, [])
    if (response.name === 'dependency_missing') assert.equal(received.result.status, 'failed')
    if (response.name === 'installed_ready') { assert.equal(received.result.plugins[0].ready, true); assert.equal(Object.hasOwn(received.result.plugins[0], 'registration'), false) }
    if (response.name === 'stop_waiting_for_reconciliation') assert.equal(received.result.status, 'reconciling')
    if (response.name === 'unsupported_feature') { assert.equal(received.code, 3); assert.match(received.error, /不支持插件管理/) }
  }
})

test('late or mismatched replies cannot be applied to a different management request', () => {
  const consumer = connected()
  consumer.expect(sample('import_request'))
  const wrong = copy(sample('import_response'))
  wrong.method = 'stop'
  assert.throws(() => consumer.receive(wrong), /method differs/)
  wrong.method = 'plugins.import'
  wrong.result.operation = 'stop'
  assert.throws(() => consumer.receive(wrong))
  consumer.reconnect()
  assert.throws(() => consumer.receive(sample('import_response')), /no current request/)
})

test('zero code with an always-present empty error consumes success; unknown nonzero code remains readable', () => {
  const consumer = connected()
  consumer.expect(sample('empty_plugins_request'))
  assert.deepEqual(consumer.receive(sample('empty_plugins_response')), { result: sample('empty_plugins_response').result })
  consumer.expect(sample('unavailable_plugins_request'))
  const failure = { ...sample('unsupported_feature'), code: 999, error: '新版诊断信息' }
  assert.deepEqual(consumer.receive(failure), { code: 999, error: '新版诊断信息' })
})

test('notifications request a snapshot refresh; old instances and old revisions never overwrite current state', () => {
  const consumer = connected()
  assert.equal(consumer.needsRefresh(sample('state_notification')), true)
  assert.equal(consumer.needsRefresh({ ...sample('state_notification'), instance_id: 'old-host' }), false)
  consumer.expect({ request_id: 'refresh', method: 'getState', params: {} })
  consumer.receive({ request_id: 'refresh', method: 'getState', code: 0, error: '', result: sample('blocked_host') })
  assert.equal(consumer.snapshot.state, 'blocked')
  assert.equal(consumer.needsRefresh(sample('state_notification')), false)
  consumer.expect(sample('state_request'))
  assert.equal(consumer.receive(sample('state_response')).ignored, true)
  assert.equal(consumer.snapshot.state, 'blocked')
  consumer.expect({ request_id: 'old', method: 'getState', params: {} })
  assert.equal(consumer.receive({ request_id: 'old', method: 'getState', code: 0, error: '', result: { ...sample('blocked_host'), instance_id: 'old-host', revision: 99 } }).ignored, true)
})

test('an unsupported major prevents consuming the management connection', () => {
  const consumer = createManagementConsumer()
  consumer.expect(sample('describe_request'))
  const wrong = copy(sample('describe_response'))
  wrong.result.api_major = 2
  assert.throws(() => consumer.receive(wrong), /unsupported management major/)
})

test('describe advances the snapshot watermark without discarding a newer same-instance state', () => {
  const consumer = connected()
  consumer.expect({ request_id: 'describe-10', method: 'describe', params: {} })
  consumer.receive({ ...copy(sample('describe_response')), request_id: 'describe-10', result: { ...sample('describe_response').result, revision: 10 } })
  consumer.expect({ request_id: 'snapshot-9', method: 'getState', params: {} })
  assert.equal(consumer.receive({ request_id: 'snapshot-9', method: 'getState', code: 0, error: '', result: { ...sample('state_response').result, revision: 9 } }).ignored, true)
  assert.equal(consumer.snapshot.revision, 4)
  consumer.expect({ request_id: 'snapshot-10', method: 'getState', params: {} })
  consumer.receive({ request_id: 'snapshot-10', method: 'getState', code: 0, error: '', result: { ...sample('state_response').result, revision: 10 } })
  consumer.expect({ request_id: 'old-describe', method: 'describe', params: {} })
  consumer.receive({ ...copy(sample('describe_response')), request_id: 'old-describe' })
  consumer.expect({ request_id: 'snapshot-9-again', method: 'getState', params: {} })
  assert.equal(consumer.receive({ request_id: 'snapshot-9-again', method: 'getState', code: 0, error: '', result: { ...sample('state_response').result, revision: 9 } }).ignored, true)
  assert.equal(consumer.snapshot.revision, 10)
})

test('a new instance on the same connection clears the old snapshot and ignores old-instance in-flight replies', () => {
  const consumer = connected()
  consumer.expect({ request_id: 'old-query', method: 'getState', params: {} })
  consumer.expect({ request_id: 'old-description', method: 'describe', params: {} })
  consumer.expect({ request_id: 'new-description', method: 'describe', params: {} })
  consumer.receive({ ...copy(sample('describe_response')), request_id: 'new-description', result: { ...sample('describe_response').result, instance_id: 'host-new', revision: 0 } })
  assert.equal(consumer.snapshot, null)
  assert.equal(consumer.receive({ ...copy(sample('describe_response')), request_id: 'old-description' }).ignored, true)
  assert.equal(consumer.receive({ request_id: 'old-query', method: 'getState', code: 0, error: '', result: { ...sample('state_response').result, revision: 99 } }).ignored, true)
  consumer.expect({ request_id: 'new-query', method: 'getState', params: {} })
  consumer.receive({ request_id: 'new-query', method: 'getState', code: 0, error: '', result: sample('unpaired_host') })
  assert.equal(consumer.snapshot.instance_id, 'host-new')
  assert.equal(consumer.snapshot.revision, 0)
  assert.equal(consumer.needsRefresh(sample('state_notification')), false)
})

test('a notification received during snapshot retrieval keeps refresh pending until its watermark is reached', () => {
  const consumer = connected()
  consumer.expect({ request_id: 'in-flight', method: 'getState', params: {} })
  assert.equal(consumer.needsRefresh({ ...sample('state_notification'), revision: 12 }), true)
  assert.equal(consumer.receive({ request_id: 'in-flight', method: 'getState', code: 0, error: '', result: { ...sample('state_response').result, revision: 11 } }).ignored, true)
  assert.equal(consumer.needsRefresh({ ...sample('state_notification'), revision: 5 }), true)
  consumer.expect({ request_id: 'catch-up', method: 'getState', params: {} })
  consumer.receive({ request_id: 'catch-up', method: 'getState', code: 0, error: '', result: { ...sample('state_response').result, revision: 12 } })
  assert.equal(consumer.needsRefresh({ ...sample('state_notification'), revision: 12 }), false)
})

test('old connection callbacks cannot consume reused request IDs after reconnecting to the same Host', () => {
  const consumer = connected()
  const oldGeneration = consumer.generation
  consumer.reconnect()
  consumer.expect(sample('describe_request'))
  consumer.receive(sample('describe_response'))
  consumer.expect(sample('state_request'))
  const late = copy(sample('state_response'))
  late.result.revision = 99
  late.result.state = 'blocked'
  assert.equal(consumer.receive(late, oldGeneration).ignored, true)
  assert.equal(consumer.snapshot, null)
  consumer.receive(sample('state_response'))
  assert.equal(consumer.snapshot.state, 'running')
  assert.equal(consumer.needsRefresh(sample('state_notification'), oldGeneration), false)
})

test('remote artifacts preserve target ownership and unknown/truncation remain visible', () => {
  const context = { device_id: 'device-B', invocation_id: 'inv-1' }
  const image = outputs.find(c => c.name === 'screenshot_output').value
  assert.equal(consumeOutput(image, context).artifacts[0].name, 'screen.png')
  assert.throws(() => consumeOutput(image, { ...context, device_id: 'device-A' }), /another device/)
  assert.throws(() => consumeOutput(image, { ...context, invocation_id: 'inv-2' }), /another invocation/)
  const unknown = consumeOutput(outputs.find(c => c.name === 'unknown_business_effect').value, context)
  assert.equal(unknown.code, 100)
  assert.match(unknown.error, /待核对/)
  assert.equal(unknown.needs_reconciliation, true)
  assert.equal(Object.hasOwn(unknown, 'safe_to_retry'), false)
  assert.equal(consumeOutput(outputs.find(c => c.name === 'truncated_read_output').value, context).complete, false)
})

test('an error result keeps applied or partial effects visible instead of inviting replay', () => {
  const context = { device_id: 'device-B', invocation_id: 'inv-1' }
  const original = outputs.find(c => c.name === 'unknown_business_effect').value
  for (const effect of ['applied', 'partial']) {
    const received = consumeOutput({ ...original, effect, complete: false }, context)
    assert.equal(received.code, 100)
    assert.equal(received.error, original.error)
    assert.equal(received.effect, effect)
    assert.equal(received.complete, false)
    assert.equal(Object.hasOwn(received, 'safe_to_retry'), false)
  }
})

function listReply(requestId, revision, plugins = [], instanceId = 'host-B-1') {
  return { request_id: requestId, method: 'plugins.list', code: 0, error: '', result: { instance_id: instanceId, revision, plugins } }
}
function expectList(consumer, requestId) {
  consumer.expect({ request_id: requestId, method: 'plugins.list', params: {} })
}

test('plugin lists share the Host watermark and use real version text without guessing release IDs', () => {
  const consumer = connected()
  consumer.expect({ request_id: 'description-10', method: 'describe', params: {} })
  consumer.receive({ ...sample('describe_response'), request_id: 'description-10', result: { ...sample('describe_response').result, revision: 10 } })
  expectList(consumer, 'list-9')
  assert.equal(consumer.receive(listReply('list-9', 9)).ignored, true)
  assert.equal(consumer.pluginsSnapshot, null)
  expectList(consumer, 'list-10')
  const plugin = copy(sample('installed_ready').result.plugins[0])
  consumer.receive(listReply('list-10', 10, [plugin]))
  assert.equal(consumer.pluginsSnapshot.plugins[0].version, '0.3.0')
  assert.equal(consumer.needsPluginsRefresh, false)
  expectList(consumer, 'legacy-list')
  delete plugin.version
  consumer.receive(listReply('legacy-list', 10, [plugin]))
  assert.equal(Object.hasOwn(consumer.pluginsSnapshot.plugins[0], 'version'), false)
})

test('changing instance clears the plugin list and old-instance in-flight lists cannot restore it', () => {
  const consumer = connected()
  expectList(consumer, 'seed-list')
  consumer.receive(listReply('seed-list', 4))
  expectList(consumer, 'old-list')
  consumer.expect({ request_id: 'new-description', method: 'describe', params: {} })
  consumer.receive({ ...sample('describe_response'), request_id: 'new-description', result: { ...sample('describe_response').result, instance_id: 'host-new', revision: 0 } })
  assert.equal(consumer.pluginsSnapshot, null)
  assert.equal(consumer.receive(listReply('old-list', 99)).ignored, true)
  expectList(consumer, 'new-list')
  consumer.receive(listReply('new-list', 0, [], 'host-new'))
  assert.equal(consumer.pluginsSnapshot.instance_id, 'host-new')
  assert.equal(consumer.pluginsSnapshot.revision, 0)
})

test('same-watermark list queries use local ordering, and state/list races never lower the shared watermark', () => {
  const consumer = connected()
  expectList(consumer, 'list-first')
  expectList(consumer, 'list-second')
  consumer.receive(listReply('list-second', 4, []))
  assert.equal(consumer.receive(listReply('list-first', 4, sample('installed_ready').result.plugins)).ignored, true)
  assert.deepEqual(consumer.pluginsSnapshot.plugins, [])
  expectList(consumer, 'list-in-flight')
  consumer.expect({ request_id: 'state-12', method: 'getState', params: {} })
  consumer.receive({ ...sample('state_response'), request_id: 'state-12', result: { ...sample('state_response').result, revision: 12 } })
  assert.equal(consumer.receive(listReply('list-in-flight', 11)).ignored, true)
  assert.equal(consumer.needsPluginsRefresh, true)
  expectList(consumer, 'list-caught-up')
  consumer.receive(listReply('list-caught-up', 12))
  consumer.expect({ request_id: 'old-state', method: 'getState', params: {} })
  expectList(consumer, 'list-newer')
  consumer.receive(listReply('list-newer', 13))
  assert.equal(consumer.receive({ ...sample('state_response'), request_id: 'old-state', result: { ...sample('state_response').result, revision: 12 } }).ignored, true)
  assert.equal(consumer.pluginsSnapshot.revision, 13)
  assert.equal(consumer.needsRefresh({ ...sample('state_notification'), revision: 13 }), true)
})

test('an earlier list request with a higher revision advances the watermark despite query order', () => {
  const consumer = connected()
  expectList(consumer, 'sent-first')
  expectList(consumer, 'sent-second')
  consumer.receive(listReply('sent-second', 10))
  assert.equal(consumer.receive(listReply('sent-first', 11, sample('installed_ready').result.plugins)).ignored, undefined)
  assert.equal(consumer.pluginsSnapshot.revision, 11)
  assert.equal(consumer.needsPluginsRefresh, false)
  consumer.expect({ request_id: 'stale-state', method: 'getState', params: {} })
  assert.equal(consumer.receive({ ...sample('state_response'), request_id: 'stale-state', result: { ...sample('state_response').result, revision: 10 } }).ignored, true)
  // Sending the newer query does not imply it has already supplied a snapshot.
  expectList(consumer, 'pending-first')
  expectList(consumer, 'pending-second')
  consumer.receive(listReply('pending-first', 12))
  assert.equal(consumer.pluginsSnapshot.revision, 12)
  assert.equal(consumer.receive(listReply('pending-second', 11)).ignored, true)
  assert.equal(consumer.pluginsSnapshot.revision, 12)
})

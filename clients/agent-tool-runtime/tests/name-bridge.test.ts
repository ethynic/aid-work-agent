import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, createHmac, randomUUID } from 'node:crypto'
import { NameSessionBridge } from '../src/sessionTasks/nameBridge.js'
import type { ProviderSet } from '../src/providerManager.js'

test('name bridge freezes observed target and authenticated payload, rejects drift', async () => {
  const id = randomUUID(), key = 'ab'.repeat(32), text = '你好，测试中', frame = 'c'.repeat(64)
  let observedName: unknown, payloadCalls = 0
  const providers = { get: () => ({ callTool: async (_name: string, args: Record<string, unknown>) => {
    observedName = args.target_name
    return { success: true, data: { observation_id: randomUUID(), conversation_binding_id: id, binding_version: 2,
      account_identity_version: 0, coverage: 'complete_window', ordered_messages: [], frame } }
  } }) } as unknown as Pick<ProviderSet, 'get'>
  const bridge = new NameSessionBridge(providers, { invocationPayload: async () => { payloadCalls++; return Buffer.from(text) } }, key)
  const inv = { invocation_id: randomUUID(), tool_name: 'weixin_message_send_v2', claim_token: 'test',
    arguments: { receipt_mode: 'submission', receipt_context: 'weixin_name', request_id: randomUUID(), target_ref: id, target_version: 'iv-2', payload_hash: createHash('sha256').update(text).digest('hex'), text: 'untrusted' } }
  const permit = { permitId: 'permit', permitToken: 'private', isValid: () => true, msRemaining: () => 20_000 }
  const signal = new AbortController().signal
  await assert.rejects(bridge.prepare(inv, permit, signal))
  assert.equal(payloadCalls, 0)
  await bridge.observe({ taskId: randomUUID(), conversationBindingId: id, expectedBindingVersion: 2, expectedAccountIdentityVersion: 0, targetName: '测试联系人' }, { watermark: null })
  assert.equal(observedName, '测试联系人')
  for(const drift of [{receipt_mode:undefined},{receipt_mode:'verified'},{receipt_context:undefined},{receipt_context:'legacy'}]){
    await assert.rejects(bridge.prepare({...inv,arguments:{...inv.arguments,...drift}},permit,signal))
  }
  assert.equal(payloadCalls,0)
  const signed = await bridge.prepare(inv, permit, signal)
  const context = JSON.parse(signed.context as string)
  assert.equal(context.text, text)
  assert.equal(context.target_name, '测试联系人')
  assert.equal(context.expected_frame, frame)
  assert.equal('permit_token' in context, false)
  assert.equal(signed.signature, createHmac('sha256', Buffer.from(key, 'hex')).update(signed.context as string).digest('hex'))
  await assert.rejects(bridge.prepare({ ...inv, arguments: { ...inv.arguments, target_version: 'iv-3' } }, permit, signal))
  await assert.rejects(bridge.prepare({ ...inv, arguments: { ...inv.arguments, payload_hash: 'f'.repeat(64) } }, permit, signal))
  await assert.rejects(bridge.prepare(inv, { ...permit, isValid: () => false }, signal))
  await bridge.observe({ taskId: randomUUID(), conversationBindingId: id, expectedBindingVersion: 2, expectedAccountIdentityVersion: 0 }, { watermark: null })
  const legacy = await bridge.prepare(inv, permit, signal)
  assert.equal(legacy.permit_token, permit.permitToken)
  assert.equal('signature' in legacy, false)
})

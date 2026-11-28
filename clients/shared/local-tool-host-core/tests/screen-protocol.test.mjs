import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  CONNECT_TIMEOUT_MS, EVIDENCE_WAIT_MS, HOST_LOOKUP_TIMEOUT_MS, LEASE_TIMEOUT_MS, LOCK_CHECK_MS,
  MAX_PAYLOAD_BYTES, MAX_SIGNALS_PER_SESSION, PEER_PORT_WAIT_MS, POLL_INTERVAL_MS, POLL_JITTER_MS,
  RESOURCE_SAMPLE_MS, RUNTIME_END_REASONS, RUNTIME_SIGNAL_TYPES, SCREEN_NOTICE_STATES, SCREEN_PATHS,
  SCREEN_SESSION_PATH, SCREEN_SIGNAL_PATH, SCREEN_STATUS_STATES, SCREEN_STREAM_CAPABILITY, SCREEN_STREAM_VERSION,
  SIGNAL_MAX_ATTEMPTS, SIGNAL_RETRY_DELAY_MS, VERIFY_TIMEOUT_MS, WEB_SIGNAL_TYPES,
  parseHeartbeatScreenSession, serverSupportsScreenStream,
} from '../dist/screen/protocol.js'

// 画面协议里与服务器逐字对齐的常量（src/local_tools/screen_stream.py），以及心跳响应里可选字段的解析。
// 解析的唯一规则：字段缺失或类型不对一律按「没有这次通知」处理，不抛异常。

test('常量与服务端实现逐字一致', () => {
  assert.equal(SCREEN_STREAM_CAPABILITY, 'screen_stream_v1')
  assert.equal(SCREEN_STREAM_VERSION, 1)
  assert.equal(SCREEN_SESSION_PATH, '/api/local-tools/runtime/screen/session')
  assert.equal(SCREEN_SIGNAL_PATH, '/api/local-tools/runtime/screen/signal')
  assert.deepEqual([...SCREEN_NOTICE_STATES], ['pending', 'streaming', 'ended'])
  assert.deepEqual([...RUNTIME_SIGNAL_TYPES], ['prepared', 'offer', 'candidate', 'status', 'end'])
  assert.deepEqual([...WEB_SIGNAL_TYPES], ['start', 'answer', 'candidate', 'end'])
  assert.deepEqual([...SCREEN_STATUS_STATES], ['connected', 'streaming', 'locked', 'unlocked', 'degraded', 'restored'])
  assert.deepEqual([...SCREEN_PATHS], ['lan', 'p2p'])
  // 顺序也照服务器：Runtime 报别的原因会被拒绝
  assert.deepEqual([...RUNTIME_END_REASONS], [
    'runtime_stopped', 'runtime_rejected', 'same_machine', 'machine_check_unknown',
    'connect_failed', 'renewal_timeout', 'resource_busy',
  ])
  assert.equal(MAX_SIGNALS_PER_SESSION, 128)
  assert.equal(MAX_PAYLOAD_BYTES, 16 * 1024)
})

test('计时常量', () => {
  assert.equal(CONNECT_TIMEOUT_MS, 30_000)
  assert.equal(VERIFY_TIMEOUT_MS, 15_000)
  assert.equal(LEASE_TIMEOUT_MS, 30_000)
  assert.equal(POLL_INTERVAL_MS, 500)
  assert.ok(POLL_JITTER_MS > 0 && POLL_JITTER_MS <= 200, '轮询抖动是少量的')
  assert.equal(RESOURCE_SAMPLE_MS, 5_000)
  assert.equal(LOCK_CHECK_MS, 2_000)
  assert.equal(EVIDENCE_WAIT_MS, 1_500)
  assert.equal(PEER_PORT_WAIT_MS, 1_500)
  assert.equal(HOST_LOOKUP_TIMEOUT_MS, 2_000)
  assert.equal(SIGNAL_RETRY_DELAY_MS, 500)
  assert.equal(SIGNAL_MAX_ATTEMPTS, 20)
})

test('服务端能力标记：只有 version 为 1 的对象才算支持', () => {
  assert.equal(serverSupportsScreenStream({ screen_stream: { version: 1 } }), true)
  for (const response of [
    undefined, null, 'ok', 42, [],
    {}, { screen_stream: undefined }, { screen_stream: null }, { screen_stream: 'v1' },
    { screen_stream: {} }, { screen_stream: { version: 2 } }, { screen_stream: { version: '1' } },
  ]) assert.equal(serverSupportsScreenStream(response), false, JSON.stringify(response) ?? String(response))
})

test('心跳里的会话通知：三种状态都能读出', () => {
  for (const state of SCREEN_NOTICE_STATES) {
    assert.deepEqual(parseHeartbeatScreenSession({ screen_session: { id: 'abc', state } }), { id: 'abc', state })
  }
})

test('心跳里的会话通知：字段缺失或类型不对一律按没有处理，不抛异常', () => {
  const bad = [
    undefined, null, 'ok', 42,
    {},
    { screen_session: null }, { screen_session: 'abc' }, { screen_session: ['abc'] }, { screen_session: 7 },
    { screen_session: {} },
    { screen_session: { id: 'abc' } },
    { screen_session: { state: 'pending' } },
    // id 必须是字符串
    { screen_session: { id: 123, state: 'pending' } },
    { screen_session: { id: null, state: 'pending' } },
    { screen_session: { id: ['abc'], state: 'pending' } },
    // 心跳里不会出现的状态同样按没有处理
    { screen_session: { id: 'abc', state: 'connecting' } },
    { screen_session: { id: 'abc', state: 'prepared' } },
    { screen_session: { id: 'abc', state: 'ended!' } },
    { screen_session: { id: 'abc', state: null } },
    { screen_session: { id: 'abc' } },
  ]
  for (const response of bad) assert.equal(parseHeartbeatScreenSession(response), null, JSON.stringify(response) ?? String(response))
})

test('会话通知只取 id 和 state，其余字段不带出来', () => {
  const notice = parseHeartbeatScreenSession({ success: true, screen_session: { id: 'abc', state: 'streaming', extra: 1 } })
  assert.deepEqual(notice, { id: 'abc', state: 'streaming' })
  assert.deepEqual(Object.keys(notice).sort(), ['id', 'state'])
})

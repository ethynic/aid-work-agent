/**
 * session_observer_v1 契约测试（C0 冻结验证）。
 *
 * fake 重放语料覆盖设计 §6 / 计划 C0 §3.2 的场景族：基线、增量、相同文本×10
 * （对齐靠重复项序号不靠正文）、拆行重建、sender 不明降级 gap、引擎不可用、
 * 离线重启后重对齐、突发合批（观察层 3 条）、视口外 gap。
 * 语料是**重建后的观察结果**（OCR 拆行等原始噪声的重建属于实现，C2 用同语料回归）。
 * 真机证据形态未验证（C0 真机 BLOCKED）；本测试只冻结契约，不宣称真机通过。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SESSION_GAP_REASONS,
  SESSION_OBSERVER_CAPABILITY,
  SESSION_OBSERVER_ERROR_CODES,
  SESSION_UNAVAILABLE_REASONS,
  classifySessionObservation,
  sessionObserverRequestSchema,
  validateSessionObservation,
  type SessionObservation,
} from '../src/platform/sessionObserver.js'

const UUID = '123e4567-e89b-12d3-a456-426614174000'
const BINDING = 'conv-binding-0001'

type FixtureMsg = {
  sender: 'peer' | 'self' | 'system' | 'unknown'
  text: string
  local_message_id: string
  source_evidence_ref: string
}

function msg(sender: FixtureMsg['sender'], text: string, seq: number): FixtureMsg {
  return {
    sender,
    text,
    local_message_id: `m-${UUID.slice(0, 8)}-0000-4000-8000-${String(seq).padStart(12, '0')}`,
    source_evidence_ref: `evd:${BINDING}:${seq}`,
  }
}

/** 松散拼装：负例语料需要携带契约外非法值，不能被 SessionObservation 类型收窄 */
function obs(partial: Record<string, unknown>): unknown {
  return {
    observation_id: UUID,
    account_identity_version: 0,
    conversation_binding_id: BINDING,
    binding_version: 0,
    observed_at: '2026-09-12T08:00:00.000Z',
    ordered_messages: [],
    window_fingerprint: null,
    gap_reason: null,
    ...partial,
  }
}

/** 合法语料：基线（水位为空的首次观察，窗口里已有 2 条历史消息） */
const FIXTURE_BASELINE = obs({
  coverage: 'complete_window',
  ordered_messages: [msg('peer', '上次聊到报价', 1), msg('self', '好的我回头确认', 2)],
  window_fingerprint: 'fp_baseline01',
})

/** 合法语料：增量对齐（水位之后新增 1 条 peer 消息） */
const FIXTURE_INCREMENT = obs({
  coverage: 'complete_window',
  ordered_messages: [msg('peer', '上次聊到报价', 1), msg('self', '好的我回头确认', 2), msg('peer', '那周五可以吗', 3)],
  window_fingerprint: 'fp_increment01',
})

/** 合法语料：相同文本×10 → 10 个不同 local_message_id，不允许折叠成 1 条 */
const FIXTURE_SAME_TEXT_X10 = obs({
  coverage: 'complete_window',
  ordered_messages: Array.from({ length: 10 }, (_, i) => msg('peer', '收到', i + 1)),
  window_fingerprint: 'fp_sametext01',
})

/** 合法语料：长句被 OCR 拆两行 → 重建为同一条消息 */
const FIXTURE_SPLIT_LINES = obs({
  coverage: 'complete_window',
  ordered_messages: [msg('peer', '这件事我们需要再对齐一下细节才能给结论', 1)],
  window_fingerprint: 'fp_splitline01',
})

/** 合法语料：突发 3 条进入同一观察（Runtime 侧按静默窗合批） */
const FIXTURE_BURST = obs({
  coverage: 'complete_window',
  ordered_messages: [msg('peer', '在吗', 1), msg('peer', '有个事想问', 2), msg('peer', '周五有空吗', 3)],
  window_fingerprint: 'fp_burst_seq1',
})

/** 合法语料：sender 判定失败 → 整体 gap、不带消息、不猜测 */
const FIXTURE_UNKNOWN_SENDER = obs({ coverage: 'gap', gap_reason: 'sender_ambiguous' })

/** 合法语料：OCR 引擎初始化失败 → unavailable，不返回空列表冒充无人回复 */
const FIXTURE_ENGINE_DOWN = obs({ coverage: 'unavailable', gap_reason: 'engine_unavailable' })

/** 合法语料：视口外/滚动断层 → gap */
const FIXTURE_VIEWPORT_OUT = obs({ coverage: 'gap', gap_reason: 'viewport_out_of_range' })

const VALID_FIXTURES: Array<[string, unknown]> = [
  ['基线', FIXTURE_BASELINE],
  ['增量对齐', FIXTURE_INCREMENT],
  ['相同文本×10', FIXTURE_SAME_TEXT_X10],
  ['拆行重建', FIXTURE_SPLIT_LINES],
  ['突发合批', FIXTURE_BURST],
  ['sender 不明降级 gap', FIXTURE_UNKNOWN_SENDER],
  ['引擎不可用', FIXTURE_ENGINE_DOWN],
  ['视口外 gap', FIXTURE_VIEWPORT_OUT],
]

test('能力名冻结为 session_observer_v1', () => {
  assert.equal(SESSION_OBSERVER_CAPABILITY, 'session_observer_v1')
})

test('合法语料全部通过契约校验', () => {
  for (const [name, fixture] of VALID_FIXTURES) {
    const r = validateSessionObservation(fixture)
    assert.ok(r.ok, `${name}: ${r.ok ? '' : r.message}`)
  }
})

test('相同文本×10：10 条消息 10 个不同 ID，不允许正文去重', () => {
  const r = validateSessionObservation(FIXTURE_SAME_TEXT_X10)
  assert.ok(r.ok)
  const texts = r.value.ordered_messages.map((m) => m.text)
  assert.equal(new Set(texts).size, 1)
  const ids = r.value.ordered_messages.map((m) => m.local_message_id)
  assert.equal(new Set(ids).size, 10)
})

test('gap/unavailable 携带任何消息即拒绝（不返回部分猜测）', () => {
  const gapCase = obs({ coverage: 'gap', gap_reason: 'sender_ambiguous', ordered_messages: [msg('peer', '半猜的消息', 1)] })
  let r = validateSessionObservation(gapCase)
  assert.ok(!r.ok && r.code === 'MESSAGES_ON_NON_COMPLETE')
  // unavailable 同一不变量：引擎不可用时不得携带任何消息
  const unavailableCase = obs({
    coverage: 'unavailable',
    gap_reason: 'engine_unavailable',
    ordered_messages: [msg('peer', '引擎失败前的残影', 1)],
  })
  r = validateSessionObservation(unavailableCase)
  assert.ok(!r.ok && r.code === 'MESSAGES_ON_NON_COMPLETE')
})

test('complete_window 缺 fingerprint / 多余 gap_reason 即拒绝', () => {
  let r = validateSessionObservation(obs({ coverage: 'complete_window', window_fingerprint: null }))
  assert.ok(!r.ok && r.code === 'FINGERPRINT_MISMATCH')
  r = validateSessionObservation(obs({ coverage: 'complete_window', window_fingerprint: 'fp_negative01', gap_reason: 'sender_ambiguous' }))
  assert.ok(!r.ok && r.code === 'GAP_REASON_MISMATCH')
})

test('gap/unavailable 缺 gap_reason 或原因集不匹配即拒绝', () => {
  let r = validateSessionObservation(obs({ coverage: 'gap', gap_reason: null }))
  assert.ok(!r.ok && r.code === 'GAP_REASON_MISMATCH')
  // engine_unavailable 属于 unavailable 集，gap 不接受
  r = validateSessionObservation(obs({ coverage: 'gap', gap_reason: 'engine_unavailable' }))
  assert.ok(!r.ok && r.code === 'GAP_REASON_SET_MISMATCH')
  // sender_ambiguous 属于 gap 集，unavailable 不接受
  r = validateSessionObservation(obs({ coverage: 'unavailable', gap_reason: 'sender_ambiguous' }))
  assert.ok(!r.ok && r.code === 'GAP_REASON_SET_MISMATCH')
})

test('complete_window 出现 sender=unknown 即拒绝（必须整体降级 gap）', () => {
  const bad = obs({
    coverage: 'complete_window',
    window_fingerprint: 'fp_negative02',
    ordered_messages: [msg('peer', '正常消息', 1), { ...msg('peer', '身份不明消息', 2), sender: 'unknown' }],
  })
  const r = validateSessionObservation(bad)
  assert.ok(!r.ok && r.code === 'UNKNOWN_SENDER_IN_WINDOW')
})

test('同一观察内 local_message_id 重复即拒绝（对齐失败应报 gap）', () => {
  const dup = obs({
    coverage: 'complete_window',
    window_fingerprint: 'fp_negative03',
    ordered_messages: [msg('peer', '第一条', 1), msg('peer', '第二条', 1)],
  })
  const r = validateSessionObservation(dup)
  assert.ok(!r.ok && r.code === 'DUPLICATE_MESSAGE_ID')
})

test('schema 层非法：ID 格式、空文本、伪造原因值、非对象', () => {
  const cases: unknown[] = [
    obs({
      coverage: 'complete_window',
      window_fingerprint: 'fp_negative04',
      ordered_messages: [{ ...msg('peer', 'x', 1), local_message_id: 'not-a-uuid-id' }],
    }),
    obs({ coverage: 'complete_window', window_fingerprint: 'fp_negative05', ordered_messages: [{ ...msg('peer', '  ', 1) }] }),
    obs({ coverage: 'gap', gap_reason: 'made_up_reason' }),
    null,
    42,
  ]
  for (const c of cases) {
    const r = validateSessionObservation(c)
    assert.ok(!r.ok && r.code === 'SCHEMA_INVALID')
  }
})

test('消息原文保留：首尾空白不丢、纯空白仍拒绝（校验不变换文本）', () => {
  const withSpaces = obs({
    coverage: 'complete_window',
    window_fingerprint: 'fp_negatives06',
    ordered_messages: [msg('peer', '  keep spaces  ', 1)],
  })
  const r = validateSessionObservation(withSpaces)
  assert.ok(r.ok)
  const kept = r.ok ? r.value.ordered_messages[0] : undefined
  assert.ok(kept)
  assert.equal(kept.text, '  keep spaces  ') // 原样保留，不 trim
  // 纯空白（含全角）拒绝
  for (const bad of ['   ', '　', '\t\n ']) {
    const rejected = validateSessionObservation(
      obs({ coverage: 'complete_window', window_fingerprint: 'fp_negatives07', ordered_messages: [msg('peer', bad, 1)] }),
    )
    assert.ok(!rejected.ok && rejected.code === 'SCHEMA_INVALID', JSON.stringify(bad))
  }
})

test('空基线→首条入站→重启恢复：水位链不丢首条消息、不重复接纳', () => {
  const req = (watermark: unknown) =>
    sessionObserverRequestSchema.parse({ conversation_binding_id: BINDING, binding_version: 0, account_identity_version: 0, watermark })

  // 1) 首次观察：窗口为空，建立「空基线」（fingerprint 有值、无消息）
  const emptyBase = obs({ coverage: 'complete_window', window_fingerprint: 'fp_emptybase01' })
  let vr = validateSessionObservation(emptyBase)
  assert.ok(vr.ok)
  let out = classifySessionObservation(req(null), vr.value)
  assert.ok(out.kind === 'baseline')
  assert.equal(out.watermark.last_local_message_id, null)
  assert.equal(out.watermark.window_fingerprint, 'fp_emptybase01')

  // 2) 增量请求携带空基线水位（不是 watermark=null 重建）→ 首条入站按新消息接纳
  const firstInboundMsg = msg('peer', '在吗', 1)
  const firstInbound = obs({ coverage: 'complete_window', window_fingerprint: 'fp_firstmsg01', ordered_messages: [firstInboundMsg] })
  vr = validateSessionObservation(firstInbound)
  assert.ok(vr.ok)
  out = classifySessionObservation(req({ last_local_message_id: null, window_fingerprint: 'fp_emptybase01' }), vr.value)
  assert.ok(out.kind === 'new_messages')
  assert.equal(out.count, 1)
  assert.equal(out.watermark.last_local_message_id, firstInboundMsg.local_message_id)

  // 3) 重启恢复：按上轮水位重放同一窗口 → no_change，不重复接纳
  const replaySame = obs({ coverage: 'complete_window', window_fingerprint: 'fp_firstmsg01', ordered_messages: [] })
  vr = validateSessionObservation(replaySame)
  assert.ok(vr.ok)
  out = classifySessionObservation(
    req({ last_local_message_id: firstInboundMsg.local_message_id, window_fingerprint: 'fp_firstmsg01' }),
    vr.value,
  )
  assert.ok(out.kind === 'no_change')

  // 4) 空基线水位 schema：last=null 合法；fingerprint 缺失非法（负例不经 parse 型 req）
  const rawBase = { conversation_binding_id: BINDING, binding_version: 0, account_identity_version: 0 }
  assert.ok(sessionObserverRequestSchema.safeParse({ ...rawBase, watermark: { last_local_message_id: null, window_fingerprint: 'fp_emptybase01' } }).success)
  assert.ok(!sessionObserverRequestSchema.safeParse({ ...rawBase, watermark: { last_local_message_id: null } }).success)
})

test('分类器：身份漂移优先于 coverage（即使 complete_window 也不可信）', () => {
  const req = sessionObserverRequestSchema.parse({
    conversation_binding_id: BINDING,
    binding_version: 0,
    account_identity_version: 3,
    watermark: { last_local_message_id: null, window_fingerprint: 'fp_emptybase01' },
  })
  const drifted = obs({
    account_identity_version: 4,
    coverage: 'complete_window',
    window_fingerprint: 'fp_driftcase01',
    ordered_messages: [msg('peer', '看起来正常的新消息', 1)],
  })
  const vr = validateSessionObservation(drifted)
  assert.ok(vr.ok)
  const out = classifySessionObservation(req, vr.value)
  assert.ok(out.kind === 'identity_drift' && out.expected_version === 3 && out.observed_version === 4)
})

test('分类器：跨会话串扰与绑定版本漂移必须阻断（不得 new_messages/推进水位）', () => {
  const req = sessionObserverRequestSchema.parse({
    conversation_binding_id: BINDING,
    binding_version: 0,
    account_identity_version: 0,
    watermark: { last_local_message_id: null, window_fingerprint: 'fp_emptybase01' },
  })
  // 1) 请求会话 A、返回会话 B：binding_mismatch，即使窗口看起来正常
  const crossTalk = obs({
    conversation_binding_id: 'conv-binding-OTHER',
    coverage: 'complete_window',
    window_fingerprint: 'fp_crosstalk01',
    ordered_messages: [msg('peer', '别的会话的消息', 1)],
  })
  let vr = validateSessionObservation(crossTalk)
  assert.ok(vr.ok)
  let out = classifySessionObservation(req, vr.value)
  assert.ok(out.kind === 'binding_mismatch' && out.field === 'conversation_binding_id')
  if (out.kind === 'binding_mismatch') {
    assert.equal(out.expected, BINDING)
    assert.equal(out.observed, 'conv-binding-OTHER')
  }
  // 2) 绑定版本改变（重建绑定）：binding_mismatch
  const rebound = obs({
    binding_version: 1,
    coverage: 'complete_window',
    window_fingerprint: 'fp_rebound0001',
    ordered_messages: [msg('peer', '旧绑定窗口的消息', 1)],
  })
  vr = validateSessionObservation(rebound)
  assert.ok(vr.ok)
  out = classifySessionObservation(req, vr.value)
  assert.ok(out.kind === 'binding_mismatch' && out.field === 'binding_version')
  // 两种不匹配都不携带水位（调用方不得推进）
  assert.ok(!('watermark' in out))
})

test('观察请求 schema：水位 null=建基线；对象水位=增量；非法拒绝', () => {
  const base = { conversation_binding_id: BINDING, binding_version: 0, account_identity_version: 0 }
  assert.ok(sessionObserverRequestSchema.safeParse({ ...base, watermark: null }).success)
  assert.ok(
    sessionObserverRequestSchema.safeParse({
      ...base,
      watermark: { last_local_message_id: msg('peer', 'x', 1).local_message_id, window_fingerprint: 'fp_baseline01' },
    }).success,
  )
  assert.ok(!sessionObserverRequestSchema.safeParse({ ...base }).success) // watermark 必填
  assert.ok(!sessionObserverRequestSchema.safeParse({ ...base, watermark: {} }).success)
  assert.ok(!sessionObserverRequestSchema.safeParse({ ...base, binding_version: -1, watermark: null }).success)
})

test('gap/unavailable 原因枚举集互斥且非空', () => {
  assert.ok(SESSION_GAP_REASONS.length > 0)
  assert.ok(SESSION_UNAVAILABLE_REASONS.length > 0)
  const overlap = SESSION_GAP_REASONS.filter((r) => (SESSION_UNAVAILABLE_REASONS as readonly string[]).includes(r))
  assert.deepEqual(overlap, [])
})

/** 观察失败错误码子集已上移为产品契约（src/platform/sessionObserver.ts 导出），此处只验证其存在与非空 */
test('observer 失败错误码子集已冻结为产品契约常量', () => {
  assert.ok(SESSION_OBSERVER_ERROR_CODES.length > 0)
  assert.ok(SESSION_OBSERVER_ERROR_CODES.includes('UI_CHANGED'))
})

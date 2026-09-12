/**
 * session_observer_v1 有序重放链路测试（C0 评审修订 P2-2/P2-3 交付）。
 *
 * 逐场景走「请求→响应→预期处置」序列，校验三件事：
 * 1. 每步请求通过 schema、每步响应通过观察契约校验（或错误码在冻结子集内）；
 * 2. 水位链一致性：场景从 initialWatermark（或首步建基线）出发，下一步请求水位
 *    必须 == 上一步分类器产出的水位；**失败分支（error/gap/unavailable/漂移/串扰）
 *    原样保留进入时的水位**，不允许静默重置；
 * 3. 协议处置与语料预期一致（classifySessionObservation 的 kind/newCount）。
 *
 * 负例（防测试自身失明）：失败后故意更换请求水位，链路检查必须抛错——证明
 * 「失败保留水位」真的在被验证，而不是靠 prevWatermark=null 跳过检查。
 * expect.flags 是 Runtime 侧处置标签（C2 对账基准），协议层不判定，此处只确保被声明。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SESSION_OBSERVER_ERROR_CODES,
  classifySessionObservation,
  sessionObserverRequestSchema,
  validateSessionObservation,
  type SessionObserverWatermark,
} from '../src/platform/sessionObserver.js'
import { REPLAY_SCENARIOS, type ReplayScenario, type ReplayStep } from './fixtures/sessionObserverReplay.js'

interface ErrorLike {
  error_code?: unknown
}

function isErrorResponse(response: unknown): response is ErrorLike {
  return typeof response === 'object' && response !== null && 'error_code' in response
}

function runStep(step: ReplayStep, watermark: SessionObserverWatermark | null): SessionObserverWatermark | null {
  const parsedReq = sessionObserverRequestSchema.safeParse(step.request)
  assert.ok(parsedReq.success, `${step.name}: 请求 schema 非法（语料本身出错）`)

  if (!step.rebaseline && watermark !== null) {
    assert.deepEqual(
      parsedReq.data.watermark,
      watermark,
      `${step.name}: 请求水位与上一步分类器产出不一致`,
    )
  }

  if (step.expect.outcome === 'error') {
    assert.ok(isErrorResponse(step.response), `${step.name}: 预期 error 但响应不是 {error_code}`)
    const code = String((step.response as ErrorLike).error_code)
    assert.ok(
      (SESSION_OBSERVER_ERROR_CODES as readonly string[]).includes(code),
      `${step.name}: 错误码 ${code} 不在冻结子集内`,
    )
    assert.equal(code, step.expect.errorCode)
    return watermark // 错误不推进水位，原样保留（含非空初始水位）
  }

  assert.ok(!isErrorResponse(step.response), `${step.name}: 响应不应是 error 形态`)
  const vr = validateSessionObservation(step.response)
  assert.ok(vr.ok, `${step.name}: 响应未通过契约校验: ${vr.ok ? '' : vr.message}`)
  const out = classifySessionObservation(parsedReq.data, vr.value)
  assert.equal(out.kind, step.expect.outcome, `${step.name}: 处置不符`)
  if ('count' in out && step.expect.newCount !== undefined) {
    assert.equal(out.count, step.expect.newCount, `${step.name}: 新消息数不符`)
  }
  if (step.expect.flags !== undefined) {
    assert.ok(step.expect.flags.length > 0, `${step.name}: flags 声明不能为空数组`)
  }
  if (out.kind === 'baseline' || out.kind === 'new_messages' || out.kind === 'no_change') {
    return out.watermark
  }
  // gap/unavailable/identity_drift/binding_mismatch：阻断，原样保留进入时水位
  return watermark
}

function runScenario(scenario: ReplayScenario): void {
  let watermark: SessionObserverWatermark | null = scenario.initialWatermark ?? null
  for (const step of scenario.steps) {
    watermark = runStep(step, watermark)
  }
}

/** 场景首步带初始水位时，请求必须与之匹配（语料自检，防语料拼装错误） */
function checkInitialWatermarkConsistency(scenario: ReplayScenario): void {
  if (scenario.initialWatermark === undefined) return
  const first = scenario.steps[0]
  if (!first || first.rebaseline) return
  const parsed = sessionObserverRequestSchema.safeParse(first.request)
  assert.ok(parsed.success, `${scenario.name}: 首步请求 schema 非法`)
  assert.deepEqual(parsed.data.watermark, scenario.initialWatermark, `${scenario.name}: 首步请求与 initialWatermark 不一致`)
}

test('重放语料覆盖 8 个场景且总量守恒（防语料被静默删减）', () => {
  assert.ok(REPLAY_SCENARIOS.length >= 8)
  const totalSteps = REPLAY_SCENARIOS.reduce((n, s) => n + s.steps.length, 0)
  assert.ok(totalSteps >= 12, `语料步骤过少（${totalSteps}）`)
  const names = new Set(REPLAY_SCENARIOS.map((s) => s.name))
  for (const must of [
    '空基线→首条入站→重启恢复',
    '人工回复（用户接管）',
    '人工已读（角标不可信）',
    '当前打开会话（无未读角标深读）',
    '离线→重启断层→显式重建基线',
    '焦点抢占中断观察',
    '账号身份漂移',
    '跨会话观察串扰',
  ]) {
    assert.ok(names.has(must), `缺少场景：${must}`)
  }
  // 非建基线场景必须显式声明初始水位（否则失败保留逻辑无法被验证）
  const withInitial = REPLAY_SCENARIOS.filter((s) => s.initialWatermark !== undefined)
  assert.ok(withInitial.length >= 7, '声明 initialWatermark 的场景不足')
})

test('每个场景的请求/响应/水位链/处置全部一致', () => {
  for (const scenario of REPLAY_SCENARIOS) {
    checkInitialWatermarkConsistency(scenario)
    runScenario(scenario)
  }
})

test('语料内 local_message_id 全局唯一（跨场景不碰撞）', () => {
  const ids = new Set<string>()
  for (const scenario of REPLAY_SCENARIOS) {
    for (const step of scenario.steps) {
      if (isErrorResponse(step.response)) continue
      const messages = (step.response as { ordered_messages?: Array<{ local_message_id: string }> }).ordered_messages ?? []
      for (const m of messages) ids.add(m.local_message_id)
    }
  }
  const all = [...ids]
  assert.equal(new Set(all).size, all.length)
})

test('负例：error 失败后篡改请求水位，链路检查必须拒绝', () => {
  const focusScenario = REPLAY_SCENARIOS.find((s) => s.name === '焦点抢占中断观察')
  assert.ok(focusScenario && focusScenario.steps.length >= 1 && focusScenario.initialWatermark)
  const tampered: ReplayScenario = {
    name: '负例：error 后篡改水位',
    designRef: '测试专用',
    initialWatermark: focusScenario.initialWatermark,
    steps: [
      focusScenario.steps[0] as ReplayStep,
      {
        name: 'error 后请求水位指纹被改',
        request: {
          conversation_binding_id: 'conv-binding-replay',
          binding_version: 0,
          account_identity_version: 0,
          watermark: { last_local_message_id: null, window_fingerprint: 'fp_tampered01' },
        },
        response: focusScenario.steps[0]?.response,
        expect: { outcome: 'error', errorCode: 'FOREGROUND_LOST', note: '负例：上一步 error 后水位必须原样保留' },
      },
    ],
  }
  assert.throws(() => runScenario(tampered), /请求水位与上一步分类器产出不一致/)
})

test('负例：unavailable 失败后篡改请求水位，链路检查必须拒绝', () => {
  const offlineScenario = REPLAY_SCENARIOS.find((s) => s.name === '离线→重启断层→显式重建基线')
  assert.ok(offlineScenario && offlineScenario.steps.length >= 2 && offlineScenario.initialWatermark)
  const initial = offlineScenario.initialWatermark
  const tampered: ReplayScenario = {
    ...offlineScenario,
    steps: [
      offlineScenario.steps[0] as ReplayStep,
      {
        ...(offlineScenario.steps[1] as ReplayStep),
        request: {
          conversation_binding_id: 'conv-binding-replay',
          binding_version: 0,
          account_identity_version: 0,
          watermark: {
            last_local_message_id: initial.last_local_message_id === null ? 'm-999e4567-0000-4000-8000-000000000009' : null,
            window_fingerprint: initial.window_fingerprint,
          },
        },
      },
    ],
  }
  assert.throws(() => runScenario(tampered), /请求水位与上一步分类器产出不一致/)
})

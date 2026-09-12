/**
 * session_observer_v1 有序重放语料（C0 评审修订 P2-3 交付）。
 *
 * 每个场景是「请求（含水位）→ 响应（观察结果或操作错误码）→ 预期协议处置」的
 * 有序序列：真机真实性仍 BLOCKED（场景形态按设计 §6/§7/§5/§4/§8 构造 fake），
 * 但链路语义（水位推进、阻断、身份漂移、错误重试）现在即可冻结并回归。
 * C2 Runtime 调度器以本语料 + expect.flags 作为对账基准；C0 不实现调度。
 */

export type ReplayOutcomeExpect = 'baseline' | 'new_messages' | 'no_change' | 'gap' | 'unavailable' | 'identity_drift' | 'binding_mismatch' | 'error'

export interface ReplayStep {
  name: string
  /** 本步请求（必须通过 sessionObserverRequestSchema 校验） */
  request: unknown
  /** 本步响应：观察结果，或 { error_code } 形式的操作层失败（如焦点抢占） */
  response: unknown
  /** 显式重建基线（watermark=null）：链路检查豁免 */
  rebaseline?: boolean
  expect: {
    outcome: ReplayOutcomeExpect
    errorCode?: string
    newCount?: number
    /** Runtime 侧预期处置标签（C2 实现的对账基准，协议层不判定） */
    flags?: string[]
    note: string
  }
}

export interface ReplayScenario {
  name: string
  designRef: string
  /**
   * 场景进入前的既有水位（C0 评审修订 P2-2）：失败分支（error/gap/unavailable/
   * 漂移/串扰）必须原样保留它；省略 = 场景从零开始（首步建基线）。
   */
  initialWatermark?: { last_local_message_id: string | null; window_fingerprint: string }
  steps: ReplayStep[]
}

/** 每场景独立 UUID 前缀，保证语料内 local_message_id 全局唯一 */
function ids(uuid8: string) {
  return (seq: number) => `m-${uuid8}-0000-4000-8000-${String(seq).padStart(12, '0')}`
}

function buildMsg(
  id: string,
  sender: 'peer' | 'self' | 'system',
  text: string,
): { sender: string; text: string; local_message_id: string; source_evidence_ref: string } {
  return { sender, text, local_message_id: id, source_evidence_ref: `evd:${id}` }
}

function obs(
  uuid: string,
  partial: Record<string, unknown>,
): Record<string, unknown> {
  return {
    observation_id: `${uuid}-e89b-12d3-a456-426614174000`,
    account_identity_version: 0,
    conversation_binding_id: 'conv-binding-replay',
    binding_version: 0,
    observed_at: '2026-09-12T09:00:00.000Z',
    ordered_messages: [],
    window_fingerprint: null,
    gap_reason: null,
    ...partial,
  }
}

function req(watermark: unknown, accountIdentityVersion = 0): Record<string, unknown> {
  return {
    conversation_binding_id: 'conv-binding-replay',
    binding_version: 0,
    account_identity_version: accountIdentityVersion,
    watermark,
  }
}

const S1 = ids('111e4567')

export const REPLAY_SCENARIOS: ReplayScenario[] = [
  {
    name: '空基线→首条入站→重启恢复',
    designRef: '设计 §6（新启用只建基线）/§8（重启先回放再验证连续性）',
    steps: [
      {
        name: '首次观察窗口为空，建立空基线',
        request: req(null),
        response: obs('111e4567', { coverage: 'complete_window', window_fingerprint: 'fp_establish01' }),
        expect: { outcome: 'baseline', flags: ['baseline_established'], note: '空基线= {last:null, fingerprint}，不是「未建立」' },
      },
      {
        name: '携带空基线水位增量观察，首条入站按新消息接纳',
        request: req({ last_local_message_id: null, window_fingerprint: 'fp_establish01' }),
        response: obs('111e4567', {
          coverage: 'complete_window',
          window_fingerprint: 'fp_firstinb01',
          ordered_messages: [buildMsg(S1(1), 'peer', '在吗')],
        }),
        expect: { outcome: 'new_messages', newCount: 1, note: '首条入站不得被当历史跳过' },
      },
      {
        name: '重启后按同水位重放，不重复接纳',
        request: req({ last_local_message_id: S1(1), window_fingerprint: 'fp_firstinb01' }),
        response: obs('111e4567', {
          coverage: 'complete_window',
          window_fingerprint: 'fp_firstinb01',
          ordered_messages: [], // 增量语义：新于水位无新消息
        }),
        expect: { outcome: 'no_change', flags: ['no_double_accept'], note: '同窗口重放 no_change，水位锚点不变' },
      },
    ],
  },
  {
    name: '人工回复（用户接管）',
    designRef: '设计 §5（用户手动发消息→human_required）',
    initialWatermark: { last_local_message_id: S1(1), window_fingerprint: 'fp_firstinb01' },
    steps: [
      {
        name: '水位后新增 sender=self 且非本任务已记账发送',
        request: req({ last_local_message_id: S1(1), window_fingerprint: 'fp_firstinb01' }),
        response: obs('222e4567', {
          coverage: 'complete_window',
          window_fingerprint: 'fp_manualrep01',
          ordered_messages: [buildMsg(ids('222e4567')(1), 'self', '我看过需求了，稍后统一答复')],
        }),
        expect: {
          outcome: 'new_messages',
          newCount: 1,
          flags: ['manual_intervention'],
          note: 'Runtime 对照本地发送账本发现该 self 消息非本任务发送 → human_required、取消未开始发送；协议层只交付事实',
        },
      },
    ],
  },
  {
    name: '人工已读（角标不可信）',
    designRef: '设计 §6（角标仅唤醒信号，不推进水位）',
    initialWatermark: { last_local_message_id: ids('222e4567')(1), window_fingerprint: 'fp_manualrep01' },
    steps: [
      {
        name: '角标已被人工清除，窗口对齐仍发现新消息',
        request: req({ last_local_message_id: ids('222e4567')(1), window_fingerprint: 'fp_manualrep01' }),
        response: obs('333e4567', {
          coverage: 'complete_window',
          window_fingerprint: 'fp_manualrd01',
          ordered_messages: [
            buildMsg(ids('333e4567')(1), 'peer', '方案确认了'),
            buildMsg(ids('333e4567')(2), 'peer', '那就周五下午2点'),
          ],
        }),
        expect: {
          outcome: 'new_messages',
          newCount: 2,
          flags: ['badge_cleared_irrelevant'],
          note: '契约无未读数字段；人工已读不影响窗口对齐与接纳',
        },
      },
    ],
  },
  {
    name: '当前打开会话（无未读角标深读）',
    designRef: '设计 §6（当前已打开会话须有界复查）',
    initialWatermark: { last_local_message_id: ids('333e4567')(2), window_fingerprint: 'fp_manualrd01' },
    steps: [
      {
        name: '当前会话角标不存在，深读仍发现新消息',
        request: req({ last_local_message_id: ids('333e4567')(2), window_fingerprint: 'fp_manualrd01' }),
        response: obs('444e4567', {
          coverage: 'complete_window',
          window_fingerprint: 'fp_currentop01',
          ordered_messages: [buildMsg(ids('444e4567')(1), 'peer', '收到，辛苦')],
        }),
        expect: {
          outcome: 'new_messages',
          newCount: 1,
          flags: ['currently_open_deep_read'],
          note: '不依赖角标；当前会话按公平队列复查',
        },
      },
    ],
  },
  {
    name: '离线→重启断层→显式重建基线',
    designRef: '设计 §1（离线不新增副作用）/§6（断层→gap）/§8（恢复顺序）',
    initialWatermark: { last_local_message_id: ids('444e4567')(1), window_fingerprint: 'fp_currentop01' },
    steps: [
      {
        name: '离线期间窗口不可得',
        request: req({ last_local_message_id: ids('444e4567')(1), window_fingerprint: 'fp_currentop01' }),
        response: obs('555e4567', { coverage: 'unavailable', gap_reason: 'window_missing' }),
        expect: { outcome: 'unavailable', flags: ['offline'], note: '离线保存状态、不请求决策/发送；水位不推进' },
      },
      {
        name: '重启后同水位重试，滚动断层无法对齐',
        request: req({ last_local_message_id: ids('444e4567')(1), window_fingerprint: 'fp_currentop01' }),
        response: obs('555e4567', { coverage: 'gap', gap_reason: 'scroll_discontinuity' }),
        expect: { outcome: 'gap', flags: ['restart_misalign'], note: '断层阻断自动回复，不猜测' },
      },
      {
        name: '显式重建基线（rebaseline）',
        rebaseline: true,
        request: req(null),
        response: obs('555e4567', {
          coverage: 'complete_window',
          window_fingerprint: 'fp_rebase00001',
          ordered_messages: [
            buildMsg(ids('555e4567')(1), 'peer', '在吗'),
            buildMsg(ids('555e4567')(2), 'peer', '断档期间的积压消息'),
          ],
        }),
        expect: {
          outcome: 'baseline',
          flags: ['explicit_rebaseline', 'no_backlog_reply'],
          note: '基线语义：整窗口作为历史锚点（historical_count=2）；积压不自动回复，需员工确认范围',
        },
      },
    ],
  },
  {
    name: '焦点抢占中断观察',
    designRef: '设计 §7（抢焦点停在可判定边界）',
    initialWatermark: { last_local_message_id: null, window_fingerprint: 'fp_establish01' },
    steps: [
      {
        name: '观察中被用户抢焦点中断',
        request: req({ last_local_message_id: null, window_fingerprint: 'fp_establish01' }),
        response: { error_code: 'FOREGROUND_LOST' },
        expect: {
          outcome: 'error',
          errorCode: 'FOREGROUND_LOST',
          flags: ['watermark_not_advanced'],
          note: '操作层失败不推进水位；重试须重新观察，不复用部分结果',
        },
      },
    ],
  },
  {
    name: '账号身份漂移',
    designRef: '设计 §4/§13.3（身份漂移→blocked，重新核验）',
    initialWatermark: { last_local_message_id: null, window_fingerprint: 'fp_establish01' },
    steps: [
      {
        name: '观察到的账号身份版本与请求期望不符',
        request: req({ last_local_message_id: null, window_fingerprint: 'fp_establish01' }, 5),
        response: obs('666e4567', {
          account_identity_version: 6,
          coverage: 'complete_window',
          window_fingerprint: 'fp_identity01',
          ordered_messages: [buildMsg(ids('666e4567')(1), 'peer', '看起来正常的新消息')],
        }),
        expect: {
          outcome: 'identity_drift',
          flags: ['task_blocked', 'rebind_required'],
          note: '身份漂移优先于 coverage：窗口内容一律不可信，任务 blocked、绑定重新核验',
        },
      },
    ],
  },
  {
    name: '跨会话观察串扰',
    designRef: '设计 §6（观察必须绑定会话与版本；C0 评审 P2-1）',
    initialWatermark: { last_local_message_id: null, window_fingerprint: 'fp_establish01' },
    steps: [
      {
        name: '请求会话 A 返回会话 B 的观察',
        request: req({ last_local_message_id: null, window_fingerprint: 'fp_establish01' }),
        response: obs('777e4567', {
          conversation_binding_id: 'conv-binding-OTHER',
          coverage: 'complete_window',
          window_fingerprint: 'fp_crosstalk01',
          ordered_messages: [buildMsg(ids('777e4567')(1), 'peer', '别的会话的消息')],
        }),
        expect: {
          outcome: 'binding_mismatch',
          flags: ['task_blocked', 'protocol_violation'],
          note: '三项身份字段（会话/绑定版本/账号版本）任一不符即阻断，不推进水位；即使 coverage=complete_window 也不可信',
        },
      },
    ],
  },
]

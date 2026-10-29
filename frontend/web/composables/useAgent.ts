import { ref, computed, shallowReactive, type Ref } from 'vue'
import type { ChatMessage, InputHintState, ProgressMessage, VerboseMessage } from '@/types'
import type { AskForm, AskFormErrorItem, RunnerAnswerItem } from '@/api/runner'
import { SSEManager, uploadFile, type UploadedFile } from '@/api/agent'
import { notifyScheduledTasksChanged } from '@/api/scheduledTask'
import { getSession, getSessionMessages, updateFailedMessageUI, type FailedMessageUI } from '@/api/session'
import { RunnerClient, RunnerRequestError, runnerTerminal, runnerObservationComplete, type RunnerCapabilities, type RunnerControlIntent, type RunnerControlView, type RunnerSubmission, type RunnerView, type RunnerWait } from '@/api/runner'
import { historyMessages, mergeRunnerMessages, visibleFailedMessages } from '@/utils/runnerMessages'
import { clientCanonicalText } from '@/utils/askForm'
import { useToast } from 'vue-toastification'
import { useTenantAuth } from './useTenantAuth'
import { useCreditCheck } from './useCreditCheck'
import { useVideoGenParams } from './useVideoGenParams'

/**
 * 多会话后台流式架构
 *
 * 每个会话拥有独立的 SessionStreamState（独立 SSEManager + 消息缓冲），
 * 切换会话只是改变 sessionId（当前查看的会话），后台会话的 SSE 连接保持存活继续收流。
 * 导出的 messages/isProcessing 等是指向「当前查看会话状态」的 computed 代理，
 * 消费组件无需感知状态池的存在。
 *
 * 设计文档：docs/system/multi-session-background-streaming-design.md
 */
interface SessionStreamState {
  sseManager: SSEManager
  messages: Ref<ChatMessage[]>
  progressMessages: Ref<ProgressMessage[]>
  isProcessing: Ref<boolean>
  currentResponse: Ref<string>
  inputHintState: Ref<InputHintState>
  /** 本轮 live verbose 中间提示（Phase 2，设计 §8.2；每轮最多一条，按 eventId 覆盖） */
  liveVerbose: Ref<VerboseMessage | null>
  /** 后台完成且用户尚未查看 → 会话列表显示完成小点 */
  hasUnreadCompletion: Ref<boolean>
  /** 是否已从 DB 加载过历史消息（或已有实时消息），避免重复请求 */
  dbLoaded: boolean
  /** 用户主动取消标记（区分 abort 和正常完成） */
  cancelledByUser: boolean
  runnerClient: RunnerClient
  runnerViews: Map<string, RunnerView>
  retryingRunners: Set<string>
  deletingRunners: Set<string>
  failedMessageUI: FailedMessageUI
  pendingRunner?: RunnerSubmission
  pendingControl?: { runnerId: string; body: RunnerControlIntent }
  controlViews: Map<string, RunnerControlView>
  liveControlKeys: Set<string>
  controlIssue?: string
  submitTimer?: ReturnType<typeof setTimeout>
  runnerGeneration: number
  submitFailures: number
  submitSelecting: boolean
  runnerDiscoveryLoaded: boolean
  runnerDiscovery?: Promise<RunnerCapabilities | undefined>
  runnerIssue?: string
  runnerNotifiedIssue?: string
}

// 会话状态池。必须用 shallowReactive：reactive 会深层包装状态对象并 unwrap 内部 ref，
// 导致 state.messages.value 访问失效；shallowReactive 只追踪 Map 的增删，值保持原样。
const sessionStreams = shallowReactive(new Map<string, SessionStreamState>())
let runnerSubject = ''

/**
 * 提问卡片提交状态（WP4，设计 §5.7 卡片状态机的本端上下文）。
 * 键为 runnerId:waitId（runner_id 全局唯一）；卡片状态以「快照 + 本表」重建，
 * 本表仅承载在途提交/受理回执，不复制快照权威状态（§5.7 双端冲突以服务端为准）。
 */
export interface AskSubmissionState {
  status: 'submitting' | 'accepted' | 'error'
  /** control consumed（答案已应用）；仅 accepted 态可能出现 */
  applied?: boolean
  clientRequestId?: string
  controlId?: string
  errorCode?: string
  errorMessage?: string
  /** 422 逐题错误明细（question_id='form' 为表单级，设计 §5.7） */
  errors?: AskFormErrorItem[]
  /** 双端冲突（RUNNER_RESUME_PENDING）已被快照事实解除（评审 P2）：wait 仍在
   *  waiting 且无已受理在途 control，卡片恢复可修正重提；不覆盖原 errorCode 事实。 */
  conflictCleared?: boolean
}

const askSubmissions = shallowReactive(new Map<string, AskSubmissionState>())
const askSubmissionKey = (runnerId: string, waitId: string) => runnerId + ':' + waitId

export interface AskSubmitInput {
  runnerId: string
  /** 卡片绑定的原 wait（评审 P1）：提交与重试始终携带，不使用“当前最新 wait” */
  waitId: string
  targetExecutionId?: string
  form: AskForm
  answers?: RunnerAnswerItem[]
  skip?: boolean
}

export type AskSubmitOutcome = 'accepted' | 'validation' | 'stale' | 'resume-pending' | 'closed'
  | 'failed' | 'busy' | 'retrying' | 'context-lost'

// 当前查看的会话 ID
const sessionId = ref<string>(generateSessionId())

// 当前查看会话的错误（仅活动会话的错误才写入，避免后台会话错误污染当前视图）
const error = ref<string | null>(null)
// Only a newly observed, current-owner error produces a notification. Restoring
// a session's history/error state must not replay a toast from another view.
const runnerFeedback = ref<{ message: string; canPresent: () => boolean } | null>(null)

const LOCAL_RECOVERY_MESSAGES = {
  stopBusy: '原设备还有正在执行的任务，本次未停止。请等该任务结束后重试，仍可只读观察页面。',
  verification: '原本机操作结果尚未核对，不能重复执行。请保持原 Runtime 连接，使用“核对并继续”；也可结束等待后继续对话。补充消息和附件已保留，但尚未应用。',
  instruction: '原本机操作需要核对。请使用“核对并继续”或“结束等待，继续对话”；本次输入和附件已保留。',
  ending: '正在结束原任务，请稍候再发送。本次输入和附件已保留。',
  cancelBlocked: '原任务取消后仍有本机结果待核对，可选择“结束等待，继续对话”；本次输入和附件已保留。',
  upgrade: '当前服务端尚不支持结束等待，请联系管理员更新服务端。原任务、消息和附件已保留。',
  verifyUpgrade: '原设备 Runtime 不支持安全只读核验，请更新 Runtime 后重试。旧操作结果仍保留未知。',
  verifyActive: '原本机调用仍在排队或执行，暂不能只读核验。请等待原结果，或先选择结束等待；核验不会自动取消原操作。',
  verifyUnavailable: '本机只读核验暂不可用，请确认原 Runtime 连接后重试；原操作不会重新执行。',
  unavailable: '原操作暂不能结束等待。请保持原 Runtime 连接以回传结果，再核对原操作；补充消息和附件仍保留。',
} as const

function recoveryControlMessage(code?: string | null) {
  if (code === 'RECOVERY_TOOL_VERIFICATION_REQUIRED' || code === 'LOCAL_DEADLINE_VERIFICATION_REQUIRED')
    return LOCAL_RECOVERY_MESSAGES.verification
  return '任务暂时无法继续，补充消息和附件已保留，但尚未应用。'
}

// 当前附件列表（输入框草稿，跨会话共享，与现状一致）
const currentFiles = ref<UploadedFile[]>([])

function generateSessionId(): string {
  return 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9)
}

// ---- 聊天 runner 终态 → 任务列表信号（Phase 5 二轮评审第 2 项） ----
// 聊天中 create/manage_scheduled_task 工具的执行结果只出现在 runner 的
// tool_result 里，不识别关键词：任一聊天 runner 终态落定即可能已变更任务集合，
// 合并 5s bump scheduledTasksVersion（连续完成只触发一次侧栏刷新）。
let scheduledTasksNotifyTimer: ReturnType<typeof setTimeout> | undefined
function notifyChatRunnerTerminal() {
  if (scheduledTasksNotifyTimer !== undefined) return
  scheduledTasksNotifyTimer = setTimeout(() => {
    scheduledTasksNotifyTimer = undefined
    notifyScheduledTasksChanged()
  }, 5_000)
}

/**
 * 获取（或懒创建）指定会话的流式状态
 */
function getStreamState(sid: string): SessionStreamState {
  let state = sessionStreams.get(sid)
  if (!state) {
    state = {
      sseManager: new SSEManager(),
      messages: ref<ChatMessage[]>([]),
      progressMessages: ref<ProgressMessage[]>([]),
      isProcessing: ref(false),
      currentResponse: ref(''),
      inputHintState: ref<InputHintState>('idle'),
      liveVerbose: ref<VerboseMessage | null>(null),
      hasUnreadCompletion: ref(false),
      dbLoaded: false,
      cancelledByUser: false,
      runnerClient: new RunnerClient(),
      runnerViews: new Map(),
      retryingRunners: new Set(),
      deletingRunners: new Set(),
      failedMessageUI: {},
      // shallowReactive：control 回执（consumed/rejected）到达时 set() 必须触发
      // askControlView 的 computed 重算，历史卡片才能随事实更新（评审 P2）。
      controlViews: shallowReactive(new Map<string, RunnerControlView>()),
      liveControlKeys: new Set(),
      runnerGeneration: 0,
      submitFailures: 0,
      submitSelecting: false,
      runnerDiscoveryLoaded: false,
    }
    sessionStreams.set(sid, state)
  }
  return state
}

// ---- 指向当前查看会话状态的 computed 代理（保持原有导出 API 不变） ----

const messages = computed<ChatMessage[]>({
  get: () => getStreamState(sessionId.value).messages.value,
  set: (v) => { getStreamState(sessionId.value).messages.value = v }
})
const progressMessages = computed<ProgressMessage[]>({
  get: () => getStreamState(sessionId.value).progressMessages.value,
  set: (v) => { getStreamState(sessionId.value).progressMessages.value = v }
})
const isProcessing = computed<boolean>(() => getStreamState(sessionId.value).isProcessing.value)
const currentResponse = computed<string>(() => getStreamState(sessionId.value).currentResponse.value)
// 当前查看会话的 live verbose 中间提示（每会话独立，多会话互不串扰）
const liveVerbose = computed<VerboseMessage | null>(() => getStreamState(sessionId.value).liveVerbose.value)
const inputHintState = computed<InputHintState>({
  get: () => getStreamState(sessionId.value).inputHintState.value,
  set: (v) => { getStreamState(sessionId.value).inputHintState.value = v }
})

export function useAgent() {
  const localRecovery = computed(() => {
    const state = getStreamState(sessionId.value)
    void state.messages.value
    void state.isProcessing.value
    if (state.pendingControl) return undefined
    const rows = [...state.runnerViews.values()].sort((a,b) => b.queue_order-a.queue_order)
    return rows.find(row => (row.local_recovery || row.local_verification_available) && !row.resume_requested)
      || (rows[0]?.local_verification?.resource_stopped ? rows[0] : undefined)
  })
  const localVerificationMessage = computed(() => {
    const value = localRecovery.value?.local_verification
    if (!value) return ''
    if (value.resource_stopped && value.page_observed === false) return value.waiting_ended
      ? '旧执行已停稳，但原页面未核对；原等待已结束。请明确提出当前页的新意图，未知发送不会重发。'
      : '旧执行已停稳，但原页面未核对。请结束等待，再明确提出当前页的新意图，未知发送不会重发。'
    if (value.resource_stopped && value.waiting_ended) return '旧执行已停稳，已只读观察页面。原等待已结束，请明确提出新的页面意图；未知发送不会重发。'
    if (value.resource_stopped) return value.target_relation === 'original'
      ? '旧执行已停稳，已只读观察原页面。请结束等待，再明确提出新的页面操作；未知发送不会重发。'
      : '旧执行已停稳，已只读观察当前页面，但无法核对原目标。请结束等待，再明确提出当前页的新意图；未知发送不会重发。'
    if (value.state === 'queued' || value.state === 'running' || value.state === 'claimed') return '正在等待原 Runtime 处理核验请求，不会重新执行原操作。'
    const stopIssue = value.stop_error_code
    if (stopIssue === 'LOCAL_RECOVERY_BUSY') return '原设备还有正在执行的任务，本次未回收受管进程。请等该任务结束后重试；只读观察仍可使用。'
    if (stopIssue === 'LOCAL_RECOVERY_LEGACY_UNMANAGED') return '检测到未受管的旧 Runtime 实例。请先结束该旧实例，再停止旧执行并解锁设备；无需重启 Windows。'
    if (stopIssue === 'LOCAL_RECOVERY_PROCESS_CHECK_UNAVAILABLE') return '查询系统进程失败，未能确认旧执行已停止；请检查 Runtime 诊断后重试，仍可只读观察页面。'
    if (stopIssue === 'LOCAL_RECOVERY_OTHER_SESSION') return '旧执行仍属于另一个 Windows 登录会话。请退出该会话的旧 Runtime 并等待停止回执，或注销该登录会话后重试；无需重启 Windows。'
    if (stopIssue === 'LOCAL_RECOVERY_NO_EXECUTION_EVIDENCE') return '当前 Runtime 缺少原受管业务记录，不能据此解除历史设备占用；请连接原设备的原 Runtime 配置。只读观察仍可使用。'
    if (value.page_observed) {
      const result = value.page_summary?.result
      const detail = result === 'match' ? '当前页面事实与期望匹配' : result === 'mismatch' ? '当前页面事实与期望不匹配' : '原操作结果仍无法确认'
      return `已只读观察${value.target_relation === 'original' ? '原' : '当前'}页面；${detail}。旧执行是否停止仍未知，原调用不会自动重发。`
    }
    return '未取得停止及页面核验证据。请检查原 Runtime、已有 Chrome 和原标签；不会切换标签或重新执行原操作。'
  })
  const localRecoveryBusy = ref(false)
  async function resolveLocalRecovery(action: 'resume' | 'end' | 'verify' | 'stop') {
    const row = localRecovery.value
    if (!row || localRecoveryBusy.value || action === 'resume' && row.cancel_requested) return
    if (row.local_recovery_actions && !row.local_recovery_actions.includes(action)) return
    const sid = sessionId.value
    const state = getStreamState(sid)
    const generation = state.runnerGeneration
    const headers = getEffectiveAuthHeader()
    localRecoveryBusy.value = true
    try {
      if (action === 'verify' || action === 'stop') {
        const updated = await state.runnerClient.verifyLocal(row.runner_id, crypto.randomUUID(), headers, action === 'stop')
        if (!currentRunnerContext(state,sid,generation,headers)) return
        observeRunner(state,updated,headers)
      } else if (action === 'resume') {
        const body: RunnerControlIntent = { client_request_id:crypto.randomUUID(), action:'resume' }
        state.pendingControl = { runnerId:row.runner_id, body }
        state.isProcessing.value = true
        await submitControl(state, row.runner_id, body, headers)
      } else {
        const updated = await state.runnerClient.endWaiting(row.runner_id, headers)
        if (!currentRunnerContext(state,sid,generation,headers)) return
        state.controlIssue = undefined
        observeRunner(state,updated,headers)
      }
    } catch (issue) {
      if (currentRunnerContext(state,sid,generation,headers)) runnerError(sid,
        new Error(action === 'verify' || action === 'stop'
          ? issue instanceof RunnerRequestError && issue.code === 'LOCAL_RECOVERY_BUSY'
            ? LOCAL_RECOVERY_MESSAGES.stopBusy
            : issue instanceof RunnerRequestError && issue.code === 'LOCAL_VERIFICATION_ORIGINAL_ACTIVE'
            ? LOCAL_RECOVERY_MESSAGES.verifyActive
            : issue instanceof RunnerRequestError && (issue.status === 404 || issue.code === 'LOCAL_VERIFICATION_RUNTIME_UPGRADE_REQUIRED')
            ? LOCAL_RECOVERY_MESSAGES.verifyUpgrade : LOCAL_RECOVERY_MESSAGES.verifyUnavailable
          : issue instanceof RunnerRequestError && issue.status === 404
            ? LOCAL_RECOVERY_MESSAGES.upgrade : LOCAL_RECOVERY_MESSAGES.unavailable))
    } finally { localRecoveryBusy.value = false }
  }
  const canStop = computed(() => {
    const state = getStreamState(sessionId.value)
    return state.isProcessing.value || state.messages.value.some(message => {
      const row = message.runnerId ? state.runnerViews.get(message.runnerId) : undefined
      return !!row && !runnerTerminal(row.status)
    })
  })
  const isWaitingHuman = computed(() => {
    const state = getStreamState(sessionId.value)
    const currentMessages = messages.value
    // Reply remains available when the original tree is waiting on clarification.
    const parked = [...state.runnerViews.values()].sort((a,b) => a.queue_order-b.queue_order)
      .find(row => ['waiting','paused','interrupted'].includes(row.status))
    const leaf = clarificationLeaf(parked?.snapshot.waiting)
    if (leaf?.wait_id && leaf.target_execution_id && !parked?.resume_requested) return false
    return currentMessages.some(message => {
      const state = message.browserAssistance?.state
      return state === 'pending' || state === 'controlling' || state === 'resume_queued'
    })
  })
  // 根据路由获取认证头（租户前台自动带 X-Tenant-Id）
  function getEffectiveAuthHeader(): Record<string, string> {
    const { getAuthHeader } = useTenantAuth()
    return getAuthHeader()
  }

  function detachRunner(state: SessionStreamState) {
    state.runnerGeneration++
    state.runnerClient.detach()
    clearTimeout(state.submitTimer)
    state.pendingRunner = undefined
    state.pendingControl = undefined
    // 提问卡片的本端提交上下文随会话观察 detach 一并清理，不跨认证主体残留。
    for (const runnerId of state.runnerViews.keys()) {
      for (const key of Array.from(askSubmissions.keys())) {
        if (key.startsWith(runnerId + ':')) askSubmissions.delete(key)
      }
    }
  }

  function checkRunnerSubject(headers: Record<string, string>) {
    const subject = JSON.stringify(headers)
    if (runnerSubject && subject !== runnerSubject) {
      for (const state of sessionStreams.values()) { detachRunner(state); state.sseManager.disconnect() }
      sessionStreams.clear()
      error.value = null
      runnerFeedback.value = null
    }
    runnerSubject = subject
  }

  function currentRunnerContext(state: SessionStreamState, sid: string, generation: number, headers: Record<string, string>) {
    const subject = JSON.stringify(headers)
    return sessionStreams.get(sid) === state && state.runnerGeneration === generation
      && runnerSubject === subject && JSON.stringify(getEffectiveAuthHeader()) === subject
  }

  /** runner 错误文案归约：control 保留文案 / NO_CREDIT 积分提示 / 通用兜底 */
  function runnerIssueText(issue: Error): string {
    const safeMessages = new Set([
      ...Object.values(LOCAL_RECOVERY_MESSAGES),
      '消息操作未成功，请稍后重试。',
      '任务暂时无法继续，补充消息和附件已保留，但尚未应用。',
      '暂时无法确认原任务状态，本次消息和附件已保留。',
      '请先完成原任务的人工协助，本次输入已保留。',
      '原任务需要恢复核对，本次输入已保留。',
      '补充消息暂时无法确认接收，请稍后重试。',
    ])
    return safeMessages.has(issue.message) ? issue.message
      : issue instanceof RunnerRequestError && issue.code === 'NO_CREDIT'
        ? '积分不足，请充值后重试。'
        : '任务暂时无法继续，请稍后重试。'
  }

  function runnerError(sid: string, issue: Error, notify = true) {
    const state = sessionStreams.get(sid)
    const message = runnerIssueText(issue)
    if (state) state.runnerIssue = message
    const headers = getEffectiveAuthHeader()
    const generation = state?.runnerGeneration
    if (!state || sid !== sessionId.value || !currentRunnerContext(state,sid,generation!,headers)) return
    error.value = message
    if (!notify || state.runnerNotifiedIssue === message) return
    state.runnerNotifiedIssue = message
    runnerFeedback.value = { message,
      canPresent:() => sid === sessionId.value && currentRunnerContext(state,sid,generation!,headers) }
  }

  function applyRunner(state: SessionStreamState, row: RunnerView, notify = true) {
    const previous = state.runnerViews.get(row.runner_id)
    if (previous && previous.view_revision > row.view_revision) return
    if (state.pendingRunner?.client_request_id === row.client_request_id) {
      state.pendingRunner = undefined
      state.submitFailures = 0
      clearTimeout(state.submitTimer)
    }
    if (state.pendingControl?.runnerId === row.runner_id) {
      // 快照确认受理（POST 已落库但响应丢失的场景）：supplementalInputs 出现本端键。
      // 除停掉重试外，还必须把 control_id 绑回提交上下文——否则该 control 随后被拒时
      // observeControl 找不到关联条目，卡片会永远停在 submitting 无法修正重提（评审 P2）。
      const confirmed = row.snapshot.supplementalInputs?.find(
        item => item.client_request_id === state.pendingControl?.body.client_request_id)
      if (confirmed) {
        state.pendingControl = undefined
        state.submitFailures = 0
        clearTimeout(state.submitTimer)
        for (const [key, item] of Array.from(askSubmissions.entries())) {
          if (item.status === 'submitting' && item.clientRequestId === confirmed.client_request_id) {
            askSubmissions.set(key, { ...item, status: 'accepted', controlId: confirmed.control_id })
          }
        }
      }
    }
    if (!state.controlIssue && row.status !== 'failed' && row.session.session_id === sessionId.value && error.value === state.runnerIssue) error.value = null
    if (!state.controlIssue) state.runnerIssue = undefined
    state.runnerViews.set(row.runner_id, row)
    reconcileAskConflicts(state, row)
    projectRunnerMessages(state)
    const active = Array.from(state.runnerViews.values()).filter(item => !runnerTerminal(item.status))
    state.isProcessing.value = state.submitSelecting || !!state.pendingRunner || !!state.pendingControl
      || active.some(item => item.resume_requested || ['queued', 'running', 'finalizing'].includes(item.status))
    const ordered = [...state.runnerViews.values()].sort((a, b) => a.queue_order - b.queue_order)
    const latest = ordered[ordered.length - 1]
    if (latest) {
      const assistant = state.messages.value.find(message => message.runnerId === latest.runner_id && message.role === 'assistant')
      state.currentResponse.value = assistant?.content || ''
      state.progressMessages.value = assistant?.progressMessages || []
      state.inputHintState.value = state.isProcessing.value ? (assistant?.content ? 'responding' : 'thinking') : 'idle'
      const verbose = assistant?.verboseMessages || []
      state.liveVerbose.value = state.isProcessing.value && !assistant?.content ? verbose[verbose.length - 1] || null : null
    }
    if (previous && !runnerTerminal(previous.status) && runnerTerminal(row.status)) {
      // 终态落定：聊天工具可能已创建/管理定时任务（不解析 tool_result），
      // 合并 5s bump 任务列表信号（Phase 5 二轮评审第 2 项）
      notifyChatRunnerTerminal()
      if (row.session.session_id !== sessionId.value) {
        state.hasUnreadCompletion.value = true
      }
    }
    if (row.status === 'failed') runnerError(row.session.session_id,
      new Error(row.result?.error_code || 'RUNNER_EXECUTION_FAILED'), notify && previous?.status !== 'failed')
  }

  function projectRunnerMessages(state: SessionStreamState) {
    const rows = Array.from(state.runnerViews.values())
    state.messages.value = visibleFailedMessages(mergeRunnerMessages(state.messages.value, rows), rows, state.failedMessageUI)
    const generation = state.runnerGeneration
    const headers = getEffectiveAuthHeader()
    for (const message of state.messages.value) {
      if (!message.runnerFailure || !message.runnerId) continue
      message.runnerFailure.retrying = state.retryingRunners.has(message.runnerId)
        || rows.some(row => !runnerTerminal(row.status) && state.failedMessageUI.retry_sources?.[row.client_request_id] === message.runnerId)
      message.runnerFailure.deleting = state.deletingRunners.has(message.runnerId)
      message.runnerFailure.retry = () => void retryFailedRunner(state, message, generation, headers)
      message.runnerFailure.remove = () => void removeFailedRunner(state, message, generation, headers)
    }
  }

  /** 快照事实重算双端冲突（评审 P2：RUNNER_RESUME_PENDING 不再永久缓存禁用）。
   *  受理在途以事实判定：该 wait 的 supplementalInputs 条目在 control 被拒后仍残留
   *  （服务端只改 control 行），须结合 controlViews 的观察结果排除已拒 control；
   *  观察未到达前按在途保守处理（维持禁用）。wait 已消费时卡片随投影消失，
   *  「已应用」走 answered 卡片与 control 事实（§7.1），此处只解除仍有效的冲突。 */
  function reconcileAskConflicts(state: SessionStreamState, row: RunnerView) {
    if (runnerTerminal(row.status)) return
    const live = clarificationLeaf(row.snapshot.waiting)
    for (const [key, item] of Array.from(askSubmissions.entries())) {
      if (item.status !== 'error' || item.errorCode !== 'RUNNER_RESUME_PENDING' || item.conflictCleared) continue
      if (!key.startsWith(row.runner_id + ':') || live?.wait_id !== key.slice(row.runner_id.length + 1)) continue
      const waitId = key.slice(row.runner_id.length + 1)
      const pending = (row.snapshot.supplementalInputs || []).some(input => input.wait_id === waitId
        && state.controlViews.get(input.control_id)?.status !== 'rejected')
      if (pending) continue
      askSubmissions.set(key, { ...item, conflictCleared: true })
    }
  }

  function observeRunner(state: SessionStreamState, row: RunnerView, headers: Record<string, string>, restoring = false) {
    const generation = state.runnerGeneration
    if (!currentRunnerContext(state, row.session.session_id, generation, headers)) return
    applyRunner(state, row, !restoring)
    for (const input of row.snapshot.supplementalInputs || []) observeControl(state,row.runner_id,input.control_id,headers)
    if (!runnerObservationComplete(row)) state.runnerClient.observe(row.runner_id, headers,
      view => {
        if (currentRunnerContext(state, row.session.session_id, generation, headers)) {
          if (view.runner_id !== row.runner_id || view.session.session_id !== row.session.session_id) {
            runnerError(row.session.session_id, new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE', 502))
            return
          }
          applyRunner(state, view)
          for (const input of view.snapshot.supplementalInputs || []) observeControl(state,view.runner_id,input.control_id,headers)
        }
        else detachRunner(state)
      }, issue => {
        if (currentRunnerContext(state, row.session.session_id, generation, headers)) runnerError(row.session.session_id, issue)
        else detachRunner(state)
      })
  }

  async function refreshRunner(id: string, headers: Record<string, string>) {
    const sid = sessionId.value
    const state = getStreamState(sid)
    const generation = state.runnerGeneration
    if (!state.messages.value.some(message => message.runnerId === id)
        || !currentRunnerContext(state,sid,generation,headers)) return
    const row = await state.runnerClient.get(id,headers)
    if (row.runner_id !== id || row.session.session_id !== sid
        || !currentRunnerContext(state,sid,generation,headers)) return
    observeRunner(state,row,headers)
  }

  function observeControl(state: SessionStreamState, runnerId: string, controlId: string, headers: Record<string,string>) {
    const sid = state.runnerViews.get(runnerId)?.session.session_id
    const generation = state.runnerGeneration
    if (!sid || !currentRunnerContext(state,sid,generation,headers)) return
    const existing = state.controlViews.get(controlId)
    if (existing && ['consumed','rejected'].includes(existing.status)) return
    state.runnerClient.observeControl(runnerId,controlId,headers,control => {
      if (!currentRunnerContext(state,sid,generation,headers)) return
      const previous = state.controlViews.get(controlId)
      state.controlViews.set(controlId,control)
      // 提问卡片提交上下文跟随 control 事实推进（已应用/被拒），供历史只读
      // 卡片区分「已提交/已应用」（设计 §7.1）；不新增权威状态。
      for (const [key, item] of Array.from(askSubmissions.entries())) {
        if (item.controlId !== controlId) continue
        askSubmissions.set(key, control.status === 'rejected'
          ? { ...item, status: 'error', errorCode: control.error_code || 'CONTROL_REJECTED' }
          : { ...item, status: 'accepted', applied: control.status === 'consumed' })
      }
      if (control.status==='rejected') {
        state.controlIssue = recoveryControlMessage(control.error_code)
        runnerError(sid,new Error(state.controlIssue),
          (!!previous && previous.status !== 'rejected') || state.liveControlKeys.has(control.client_request_id))
      }
      if (['consumed','rejected'].includes(control.status)) state.liveControlKeys.delete(control.client_request_id)
    },issue => {
      if (currentRunnerContext(state,sid,generation,headers)) runnerError(sid,issue)
    })
  }

  /** 找到提交 body 对应的乐观 assistant 占位消息（clientRequestId 关联） */
  function optimisticAssistant(state: SessionStreamState, clientRequestId?: string) {
    if (!clientRequestId) return undefined
    return state.messages.value.find(item => item.role === 'assistant' && item.clientRequestId === clientRequestId)
  }

  /** 把乐观轮次的「🚀 正在发送请求...」进度行原地替换为错误/重试文案
   *  （消息级与会话级两条 progress 路径同步，替换后不残留"正在发送"） */
  function replaceSendProgress(state: SessionStreamState, message: ChatMessage, content: string, type: ProgressMessage['type']) {
    const replace = (list: ProgressMessage[] | undefined) => {
      if (!list) return
      const target = list.find(item => item.content === '🚀 正在发送请求...')
      if (target) { target.content = content; target.type = type }
    }
    replace(message.progressMessages)
    replace(state.progressMessages.value)
  }

  /** runner POST 失败的乐观轮次 UI：挂内存态 submitFailure（definitive=错误态+重试，
   *  retrying=自动重试次数+立即重试），纯内存不进序列化/持久化路径 */
  function markSubmitFailure(state: SessionStreamState, body: RunnerSubmission, issue: Error, definitive: boolean) {
    const message = optimisticAssistant(state, body.client_request_id)
    if (!message) return
    const generation = state.runnerGeneration
    message.submitFailure = {
      kind: definitive ? 'definitive' : 'retrying',
      message: runnerIssueText(issue),
      attempts: definitive ? 0 : state.submitFailures,
      retry: () => retryRunnerSubmit(state, body, generation),
      remove: definitive ? () => {
        const headers = getEffectiveAuthHeader()
        if (!currentRunnerContext(state, body.session_id, generation, headers) || sessionId.value !== body.session_id) return
        const input = state.messages.value.find(item => item.role === 'user' && item.clientRequestId === body.client_request_id)
        if (input?.submitFailure?.kind !== 'definitive') return
        state.messages.value = state.messages.value.filter(item => item.clientRequestId !== body.client_request_id)
      } : undefined,
    }
    const input = state.messages.value.find(item => item.role === 'user' && item.clientRequestId === body.client_request_id)
    if (input) input.submitFailure = message.submitFailure
    replaceSendProgress(state, message, definitive
      ? `❌ 发送失败：${message.submitFailure.message}`
      : `⏳ 发送不稳定，正在自动重试（第 ${state.submitFailures} 次）...`,
      definitive ? 'error' : 'progress')
  }

  /** 手动重试（幂等重放红线）：复用原 client_request_id 与原 body（含 video_params
   *  快照与附件）重放 POST /api/chat/runners，绝不 mint 新 key 或回退 legacy SSE。
   *  与 abortStreaming 的同 key 重放互斥：先取消自动重试定时器，恢复 pendingRunner
   *  占住并发 guard（abort 仍走既有 pendingRunner 解析路径），失败态与 🚀 行还原。 */
  function retryRunnerSubmit(state: SessionStreamState, body: RunnerSubmission, generation: number) {
    const headers = getEffectiveAuthHeader()
    if (!currentRunnerContext(state, body.session_id, generation, headers)) return
    if (!optimisticAssistant(state, body.client_request_id)?.submitFailure) return
    // 与 abortStreaming 的同 key 重放互斥：另一提交/控制操作占位（含 definitive
    // 失败后用户已发的新消息）时不得重试，否则会覆盖其 pendingRunner 键，
    // 使 abort 丢失对该键的同 key 重放保障。同 key 的在飞自动重试除外——
    // 幂等重放安全，view_revision 防重复视图。
    if (state.submitSelecting || state.pendingControl) return
    if (state.pendingRunner && state.pendingRunner.client_request_id !== body.client_request_id) return
    clearTimeout(state.submitTimer)
    state.pendingRunner = body
    state.submitSelecting = false
    state.isProcessing.value = true
    state.inputHintState.value = 'thinking'
    state.submitFailures = 0
    const message = optimisticAssistant(state, body.client_request_id)
    if (message) {
      message.submitFailure = undefined
      const input = state.messages.value.find(item => item.role === 'user' && item.clientRequestId === body.client_request_id)
      if (input) input.submitFailure = undefined
      replaceSendProgress(state, message, '🚀 正在发送请求...', 'progress')
    }
    void submitRunner(state, body, headers)
  }

  /** 明确执行失败的手动重试是新任务；未知接单仍由 submitRunner 同键重放。 */
  async function retryFailedRunner(state: SessionStreamState, message: ChatMessage,
    generation: number, headers: Record<string, string>) {
    const sid = sessionId.value
    const row = message.runnerId ? state.runnerViews.get(message.runnerId) : undefined
    const available = () => row?.status === 'failed' && row.session.session_id === sid && sessionId.value === sid
      && currentRunnerContext(state, sid, generation, headers)
      && state.messages.value.some(input => input.runnerId === row.runner_id && input.runnerFailure)
      && !state.isProcessing.value && !state.submitSelecting && !state.pendingRunner && !state.pendingControl
      && ![...state.runnerViews.values()].some(view => !runnerTerminal(view.status))
    if (!row || !available() || state.retryingRunners.has(row.runner_id) || state.deletingRunners.has(row.runner_id)
        || state.failedMessageUI.hidden_failed?.[row.runner_id]) return
    state.retryingRunners.add(row.runner_id)
    if (message.runnerFailure) message.runnerFailure.retrying = true
    try {
      const session = await getSession(sid)
      if (!available()) return
      if (session.session_id !== sid) throw new Error('RUNNER_SESSION_MISMATCH')
      const profile = row.profile_id || session.subagent_id
      await sendMessage(message.content, profile === 'main' ? null : profile,
        sid, session.instance_id, message.attachments || [], row.runner_id)
    } catch (issue) {
      if (currentRunnerContext(state, sid, generation, headers)) runnerError(sid, issue as Error)
    } finally {
      state.retryingRunners.delete(row.runner_id)
      for (const input of state.messages.value) {
        if (input.runnerId === row.runner_id && input.runnerFailure) input.runnerFailure.retrying = [...state.runnerViews.values()]
          .some(view => !runnerTerminal(view.status) && state.failedMessageUI.retry_sources?.[view.client_request_id] === row.runner_id)
      }
    }
  }

  async function removeFailedRunner(state: SessionStreamState, message: ChatMessage,
    generation: number, headers: Record<string, string>) {
    const sid = sessionId.value
    const id = message.runnerId
    if (!id || !currentRunnerContext(state, sid, generation, headers)
        || state.runnerViews.get(id)?.session.session_id !== sid || state.runnerViews.get(id)?.status !== 'failed'
        || !state.messages.value.some(input => input.runnerId === id && input.runnerFailure)
        || state.retryingRunners.has(id) || state.deletingRunners.has(id) || message.runnerFailure?.retrying) return
    state.deletingRunners.add(id)
    if (message.runnerFailure) message.runnerFailure.deleting = true
    try {
      await updateFailedMessageUI(sid, id, { action: 'hide' }, headers)
      if (!currentRunnerContext(state, sid, generation, headers)) return
      state.failedMessageUI.hidden_failed = { ...state.failedMessageUI.hidden_failed, [id]: true }
      projectRunnerMessages(state)
    } catch (issue) {
      if (currentRunnerContext(state, sid, generation, headers)) runnerError(sid, issue as Error)
    } finally {
      state.deletingRunners.delete(id)
      for (const input of state.messages.value) {
        if (input.runnerId === id && input.runnerFailure) input.runnerFailure.deleting = false
      }
    }
  }

  async function submitRunner(state: SessionStreamState, body: RunnerSubmission, headers: Record<string, string>): Promise<'definitive' | undefined> {
    const generation = state.runnerGeneration
    if (!currentRunnerContext(state, body.session_id, generation, headers)) return
    try {
      const row = await state.runnerClient.submit(body, headers)
      if (!currentRunnerContext(state, body.session_id, generation, headers)) return
      state.pendingRunner = undefined
      state.submitFailures = 0
      observeRunner(state, row, headers)
    } catch (issue) {
      if (!currentRunnerContext(state, body.session_id, generation, headers)) return
      runnerError(body.session_id, issue as Error)
      const definitive = issue instanceof RunnerRequestError && issue.status < 500
      if (definitive) {
        markSubmitFailure(state, body, issue as Error, true)
        state.pendingRunner = undefined
        state.isProcessing.value = false
        state.inputHintState.value = 'idle'
        return 'definitive'
      }
      // Acceptance may already be committed. Keep the exact body and transport;
      // retrying cannot mint a second runner or switch to the legacy loop.
      state.submitFailures++
      markSubmitFailure(state, body, issue as Error, false)
      state.submitTimer = setTimeout(() => void submitRunner(state, body, headers),
        Math.min(30000, 1000 * 2 ** Math.min(state.submitFailures, 5)))
    }
  }

  function clarificationLeaf(wait?: RunnerWait): RunnerWait | undefined {
    let remaining = 64
    while (wait && remaining-- > 0) {
      if (wait.kind === 'clarification') return wait
      wait = wait.child_wait
    }
    return undefined
  }

  async function submitControl(state: SessionStreamState, runnerId: string, body: RunnerControlIntent, headers: Record<string, string>) {
    const sid = state.runnerViews.get(runnerId)?.session.session_id
    const generation = state.runnerGeneration
    if (!sid || !currentRunnerContext(state,sid,generation,headers)) return
    // Keep original-key proof across a lost POST response and snapshot-driven
    // acceptance. A first rejected GET can still be this live user operation.
    state.liveControlKeys.add(body.client_request_id)
    try {
      const result = await state.runnerClient.control(runnerId,body,headers)
      if (!currentRunnerContext(state,sid,generation,headers)) return
      state.pendingControl = undefined
      state.submitFailures = 0
      observeRunner(state,result.runner,headers)
      state.controlViews.set(result.control.control_id,result.control)
      if (result.control.status==='rejected') {
        state.controlIssue = recoveryControlMessage(result.control.error_code)
        runnerError(sid,new Error(state.controlIssue))
        state.liveControlKeys.delete(body.client_request_id)
        return false
      }
      state.controlIssue = undefined
      if (result.control.status === 'consumed') state.liveControlKeys.delete(body.client_request_id)
      observeControl(state,runnerId,result.control.control_id,headers)
      return true
    } catch (issue) {
      if (!currentRunnerContext(state,sid,generation,headers)) return
      runnerError(sid,new Error('补充消息暂时无法确认接收，请稍后重试。'))
      if (issue instanceof RunnerRequestError && issue.status < 500) {
        state.liveControlKeys.delete(body.client_request_id)
        state.pendingControl = undefined
        state.isProcessing.value = false
        state.inputHintState.value = 'idle'
        return false
      } else {
        state.submitFailures++
        state.submitTimer = setTimeout(() => void submitControl(state,runnerId,body,headers),
          Math.min(30000,1000*2**Math.min(state.submitFailures,5)))
      }
    }
  }

  function now(): string {
    const d = new Date()
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(3, '0')}.${d.getMilliseconds().toString().padStart(3, '0')}`
  }

  // ---- 结构化提问提交（WP4，设计 §4.2/§5.7/§7.1/§8）----

  /** 组装提问卡片控制请求：结构化 answers/skip（answer 留空，canonical 服务端生成）；
   *  capabilities 未声明 ask_structured_answers 时降级为客户端 canonical 文本路径
   *  （灰度/回滚窗口专用，服务端权威校验在该窗口不生效，设计 §8，不做提交时探测）。 */
  function askControlBody(state: SessionStreamState, input: AskSubmitInput): RunnerControlIntent {
    const base = { client_request_id: generateSessionId(), action: 'reply' as const,
      wait_id: input.waitId, target_execution_id: input.targetExecutionId }
    if (!state.runnerClient.askStructuredSupported) {
      return { ...base, answer: clientCanonicalText(input.form, input.answers || [], input.skip === true) }
    }
    if (input.skip) return { ...base, skip: true }
    // 显式 []（全部未答）也是合法普通提交（设计 §5.4 判定表）
    return { ...base, answers: input.answers || [] }
  }

  /** 提问卡片的终局落定（受理前的同步结果，全部停掉在途 guard 与会话处理态） */
  function settleAskRejection(state: SessionStreamState, key: string, body: RunnerControlIntent,
    errorCode: string, errorMessage: string | undefined, errors?: AskFormErrorItem[]) {
    askSubmissions.set(key, { status: 'error', errorCode, errorMessage, errors,
      clientRequestId: body.client_request_id, controlId: askSubmissions.get(key)?.controlId })
    state.liveControlKeys.delete(body.client_request_id)
    state.pendingControl = undefined
    state.isProcessing.value = false
    state.inputHintState.value = 'idle'
  }

  async function postAskControl(state: SessionStreamState, key: string, runnerId: string,
      body: RunnerControlIntent, headers: Record<string, string>): Promise<AskSubmitOutcome> {
    const sid = state.runnerViews.get(runnerId)?.session.session_id
    const generation = state.runnerGeneration
    if (!sid || !currentRunnerContext(state, sid, generation, headers)) return 'context-lost'
    // 网络结果不明时保留原键证明（与 submitControl 同一机制）。
    state.liveControlKeys.add(body.client_request_id)
    try {
      const result = await state.runnerClient.control(runnerId, body, headers)
      if (!currentRunnerContext(state, sid, generation, headers)) return 'context-lost'
      state.pendingControl = undefined
      state.submitFailures = 0
      observeRunner(state, result.runner, headers)
      state.controlViews.set(result.control.control_id, result.control)
      if (result.control.status === 'rejected') {
        state.liveControlKeys.delete(body.client_request_id)
        askSubmissions.set(key, { status: 'error', errorCode: result.control.error_code || 'CONTROL_REJECTED',
          clientRequestId: body.client_request_id, controlId: result.control.control_id })
        state.isProcessing.value = false
        state.inputHintState.value = 'idle'
        return 'failed'
      }
      askSubmissions.set(key, { status: 'accepted', clientRequestId: body.client_request_id,
        controlId: result.control.control_id })
      state.controlIssue = undefined
      observeControl(state, runnerId, result.control.control_id, headers)
      return 'accepted'
    } catch (issue) {
      if (!currentRunnerContext(state, sid, generation, headers)) return 'context-lost'
      const error = issue as RunnerRequestError
      if (error instanceof RunnerRequestError && error.status === 422) {
        // 表单校验失败（设计 §5.7）：同步 422 + errors，未落库未消费 wait；
        // 卡片逐题高亮后直接修正重提即可（无专门键管理）。
        settleAskRejection(state, key, body, error.code, error.message, error.errors)
        return 'validation'
      }
      if (error instanceof RunnerRequestError && error.status === 409) {
        // 过期/冲突（CONTROL_WAIT_STALE、CONTROL_WAIT_CONSUMED、RUNNER_RESUME_PENDING、
        // CONTROL_EXECUTION_CLOSED 等）：提示并刷新快照，不改投最新问题（评审 P1）。
        settleAskRejection(state, key, body, error.code, error.message)
        // 尽力而为刷新：观察通道已在推送快照，遥测失败（含 detach 中止）不另起错误面。
        void refreshRunner(runnerId, headers).catch(() => {})
        if (error.code === 'RUNNER_RESUME_PENDING') return 'resume-pending'
        if (error.code === 'CONTROL_EXECUTION_CLOSED') return 'closed'
        return 'stale'
      }
      if (error instanceof RunnerRequestError && error.status < 500) {
        settleAskRejection(state, key, body, error.code, error.message)
        return 'failed'
      }
      // 网络结果不明：不降级为文本路径，沿用原意图与原键重试（幂等安全，设计 §5.7）。
      runnerError(sid, new Error('补充消息暂时无法确认接收，请稍后重试。'))
      state.submitFailures++
      state.submitTimer = setTimeout(() => void postAskControl(state, key, runnerId, body, headers),
        Math.min(30000, 1000 * 2 ** Math.min(state.submitFailures, 5)))
      return 'retrying'
    }
  }

  /** 提问卡片驱动的结构化提交入口：防重复复用 pendingControl/submitting 状态；
   *  受理后用户修改再提交由卡片发起新调用、自动换新 client_request_id
   *  （已受理后同键改答案会被服务端 IDEMPOTENCY_INPUT_MISMATCH 拒绝，设计 §5.7）。 */
  async function submitAskAnswers(input: AskSubmitInput): Promise<AskSubmitOutcome> {
    const headers = getEffectiveAuthHeader()
    const state = getStreamState(sessionId.value)
    const row = state.runnerViews.get(input.runnerId)
    if (!row || runnerTerminal(row.status)) return 'closed'
    const key = askSubmissionKey(input.runnerId, input.waitId)
    if (askSubmissions.get(key)?.status === 'submitting') return 'busy'
    if (state.submitSelecting || state.pendingRunner || state.pendingControl) return 'busy'
    const body = askControlBody(state, input)
    askSubmissions.set(key, { status: 'submitting', clientRequestId: body.client_request_id })
    state.pendingControl = { runnerId: input.runnerId, body }
    state.isProcessing.value = true
    state.inputHintState.value = 'thinking'
    return postAskControl(state, key, input.runnerId, body, headers)
  }

  /** 提问卡片状态机读取（响应式）：卡片按「快照绑定 + 本端上下文」渲染 §5.7 状态。 */
  function askSubmissionFor(runnerId: string, waitId: string) {
    return computed(() => askSubmissions.get(askSubmissionKey(runnerId, waitId)))
  }

  /** 历史只读已答卡片按 control_id 反查提交事实（区分已提交/已应用，§7.1）。 */
  function askSubmissionForControl(controlId: string) {
    return computed(() => {
      for (const item of askSubmissions.values()) {
        if (item.controlId === controlId) return item
      }
      return undefined
    })
  }

  /** 已查询到的 control 观察事实（复用 controlViews，不新增轮询）：本端无提交上下文
   *  （如刷新后 askSubmissions 为空）时，历史只读卡片按它区分已应用/被拒/仅提交（§7.1）。 */
  function askControlView(controlId: string) {
    return computed<RunnerControlView | undefined>(() =>
      getStreamState(sessionId.value).controlViews.get(controlId))
  }


  async function discoverSession(state: SessionStreamState, sid: string, headers: Record<string,string>) {
    const generation = state.runnerGeneration
    if (state.runnerDiscoveryLoaded) return
    if (state.runnerDiscovery) return state.runnerDiscovery
    const discovery = (async () => {
      const [result, capabilities] = await Promise.all([
        state.dbLoaded || state.messages.value.length ? Promise.resolve(null) : getSessionMessages(sid),
        state.runnerClient.capabilities(headers),
      ])
      if (!currentRunnerContext(state,sid,generation,headers)) return
      if (result && (!state.isProcessing.value || state.submitSelecting) && !state.messages.value.length) {
        state.messages.value = historyMessages(result.messages || [])
      }
      if (result) state.failedMessageUI = result.failed_message_ui || {}
      state.runnerClient.installCapabilities(capabilities)
      state.dbLoaded = true
      if (capabilities.observe_existing || state.messages.value.some(message => message.runnerId)) {
        let before: string | undefined
        do {
          const page = await state.runnerClient.list(sid,headers,before)
          if (!currentRunnerContext(state,sid,generation,headers)) return
          const rows = new Map([...page.runners,...page.active_runners].map(row => [row.runner_id,row]))
          for (const row of rows.values()) observeRunner(state,row,headers,true)
          before = page.has_more && page.next_cursor ? page.next_cursor : undefined
        } while (before)
      }
      state.runnerDiscoveryLoaded = true
      return capabilities
    })()
    state.runnerDiscovery = discovery
    try { return await discovery }
    finally { if (state.runnerDiscovery===discovery) state.runnerDiscovery = undefined }
  }

  /**
   * 切换会话：不再中断任何流式连接，仅改变当前查看的会话。
   * 目标会话若已有内存状态（进行中/已完成/已加载），computed 代理自动显示其实时内容；
   * 否则从数据库加载历史消息到该会话的状态中。
   */
  async function switchSession(newSessionId: string): Promise<void> {
    const headers = getEffectiveAuthHeader()
    checkRunnerSubject(headers)
    sessionId.value = newSessionId
    // 清除上一会话的错误提示，避免残留到目标会话视图（保持旧版 switchSession 行为）
    error.value = null

    const state = getStreamState(newSessionId)
    const generation = state.runnerGeneration
    // 用户查看了该会话，清除完成小点
    state.hasUnreadCompletion.value = false

    // 内存已有状态（进行中的实时消息或已加载的历史），无需请求 DB
    if (state.runnerDiscoveryLoaded && (state.dbLoaded || state.messages.value.length > 0)) {
      // A loaded message cache does not establish an active observer. Reattach
      // known durable work without creating a new execution or control action.
      for (const row of state.runnerViews.values()) observeRunner(state, row, headers,true)
      return
    }

    try {
      await discoverSession(state,newSessionId,headers)
    } catch (issue) {
      if (currentRunnerContext(state, newSessionId, generation, headers)) { runnerError(newSessionId, issue as Error); throw issue }
      return
    }
  }

  /**
   * 上传单个文件
   * 先插入无 file_id 的占位（ChatInput 显示"上传中"转圈），成功后替换为真实结果，失败则移除
   */
  async function uploadAttachment(file: File): Promise<UploadedFile> {
    const placeholder: UploadedFile = {
      file_id: '',
      name: file.name,
      size: file.size,
      mime_type: file.type,
      type: file.type.startsWith('image/') ? 'image' : 'file'
    }
    currentFiles.value.push(placeholder)
    try {
      const uploaded = await uploadFile(file, getEffectiveAuthHeader())
      const idx = currentFiles.value.indexOf(placeholder)
      if (idx !== -1) {
        currentFiles.value.splice(idx, 1, uploaded)
      }
      return uploaded
    } catch (error) {
      currentFiles.value = currentFiles.value.filter(f => f !== placeholder)
      throw error
    }
  }

  /**
   * 移除已上传的附件
   * 空 file_id 是上传中的占位（无法取消进行中的请求），忽略
   */
  function removeAttachment(file_id: string) {
    if (!file_id) return
    currentFiles.value = currentFiles.value.filter(f => f.file_id !== file_id)
  }

  /**
   * 清空所有附件
   */
  function clearAttachments() {
    currentFiles.value = []
  }

  async function sendMessage(content: string, subagent?: string | null, overrideSessionId?: string, instanceId?: string | null,
    retryFiles?: UploadedFile[], retrySource?: string) {
    if (!content.trim()) return
    const headers = getEffectiveAuthHeader()
    checkRunnerSubject(headers)
    const effectiveSessionId = overrideSessionId || sessionId.value
    const state = getStreamState(effectiveSessionId)
    const generation = state.runnerGeneration
    let ownsSelection = false
    const currentSendContext = () => {
      if (!currentRunnerContext(state, effectiveSessionId, generation, headers)) return false
      if (!retryFiles || sessionId.value === effectiveSessionId) return true
      // 手动重试尚未发出请求就离开会话：释放自己的选择占位，不能留下假忙态。
      if (ownsSelection) {
        ownsSelection = false
        state.submitSelecting = false
        state.isProcessing.value = !!state.pendingRunner || !!state.pendingControl
          || [...state.runnerViews.values()].some(view => view.resume_requested || ['queued', 'running', 'finalizing'].includes(view.status))
        state.inputHintState.value = state.isProcessing.value ? 'thinking' : 'idle'
      }
      return false
    }

    // 余额检查：余额 ≤ 0 阻断发送（仅在租户前台模式下生效）
    if (window.location.pathname.startsWith('/t/')) {
      const { checkCreditBeforeAction } = useCreditCheck()
      const creditCheck = await checkCreditBeforeAction('sendMessage')
      if (!currentSendContext()) return
      if (!creditCheck.allowed) {
        return
      }
    }

    // 优先使用外部传入的 sessionId（来自 DB 的真实会话 ID），避免与本地生成的 sessionId 产生竞态

    if (state.submitSelecting || state.pendingRunner || state.pendingControl) return
    // A new explicit Send starts a new notification round. Automatic retries
    // and repeated observations of that same operation never reset this gate.
    state.runnerNotifiedIssue = undefined
    let discovered: RunnerCapabilities | undefined
    if (!state.runnerDiscoveryLoaded) {
      const wasProcessing = state.isProcessing.value
      state.submitSelecting = true
      ownsSelection = true
      state.isProcessing.value = true
      try {
        discovered = await discoverSession(state,effectiveSessionId,headers)
        if (!currentSendContext()) return
      } catch (issue) {
        if (!currentSendContext()) return
        state.submitSelecting = false
        ownsSelection = false
        state.isProcessing.value = false
        runnerError(effectiveSessionId,new Error('暂时无法确认原任务状态，本次消息和附件已保留。'))
        return { restoreInput:true,canRestore:() => currentRunnerContext(state,effectiveSessionId,generation,headers) }
      }
      state.submitSelecting = false
      ownsSelection = false
      state.isProcessing.value = (wasProcessing && !state.runnerViews.size)
        || [...state.runnerViews.values()].some(row => row.resume_requested || ['queued','running','finalizing'].includes(row.status))
    }

    if (!currentSendContext() || (retryFiles && [...state.runnerViews.values()].some(view => !runnerTerminal(view.status)))) return

    // 按会话 guard：仅当目标会话正在处理时才拒绝，不阻塞其他会话发消息
    const parked = [...state.runnerViews.values()].sort((a,b) => a.queue_order-b.queue_order)
      .find(row => ['waiting','paused','interrupted'].includes(row.status))
    const leaf = clarificationLeaf(parked?.snapshot.waiting)
    const canReply = !!(leaf?.wait_id && leaf.target_execution_id)
    if (parked?.cancel_requested) {
      runnerError(effectiveSessionId,new Error(parked.local_recovery
        ? LOCAL_RECOVERY_MESSAGES.cancelBlocked : LOCAL_RECOVERY_MESSAGES.ending))
      return { restoreInput:true, canRestore:() => currentRunnerContext(state,effectiveSessionId,generation,headers) }
    }
    let resumeWait = parked?.snapshot.waiting
    let waitDepth = 64
    while (resumeWait?.kind === 'child_wait' && waitDepth-- > 0) resumeWait = resumeWait.child_wait
    const canResume = !!parked && ['paused','interrupted'].includes(parked.status)
      && !resumeWait
    if (parked && (canReply || canResume) && !parked.resume_requested) {
      if (parked.local_recovery && !canReply) {
        runnerError(effectiveSessionId,new Error(LOCAL_RECOVERY_MESSAGES.instruction))
        return { restoreInput:true, canRestore:() => currentRunnerContext(state,effectiveSessionId,generation,headers) }
      }
      if (state.submitSelecting || state.pendingRunner || state.pendingControl) return
      const files = currentFiles.value.filter(file => file.file_id)
      const body: RunnerControlIntent = JSON.parse(JSON.stringify({ client_request_id:generateSessionId(),
        action:canReply ? 'reply' : 'resume',
        wait_id:canReply ? leaf!.wait_id : undefined,
        target_execution_id:canReply ? leaf!.target_execution_id : undefined,answer:content,
        attachments:files.length ? files : undefined }))
      state.pendingControl = { runnerId:parked.runner_id,body }
      state.isProcessing.value = true
      state.inputHintState.value = 'thinking'
      currentFiles.value = []
      const accepted = await submitControl(state,parked.runner_id,body,headers)
      if (accepted===false && currentRunnerContext(state,effectiveSessionId,generation,headers)) {
        if (sessionId.value === effectiveSessionId && !currentFiles.value.length) currentFiles.value = files
        return { restoreInput:true, canRestore:() => currentRunnerContext(state,effectiveSessionId,generation,headers) }
      }
      return
    }
    if (parked) {
      const issue = Object.assign(new Error(parked.status === 'waiting'
        ? '请先完成原任务的人工协助，本次输入已保留。'
        : '原任务需要恢复核对，本次输入已保留。'), {
        code: parked.status === 'waiting' ? 'RUNNER_ASSISTANCE_PENDING' : 'RUNNER_RESUME_REQUIRED', status: 409,
      })
      runnerError(effectiveSessionId,issue)
      return { restoreInput: true, canRestore:() => currentRunnerContext(state,effectiveSessionId,generation,headers) }
    }
    if (state.isProcessing.value) return

    // Selection is a server capability, never a health-based fallback. Keep the
    // guard while checking so two sends cannot create concurrent optimistic turns.
    state.isProcessing.value = true
    state.submitSelecting = true
    ownsSelection = true
    let runnerTransport: boolean
    try {
      const capabilities = discovered || await state.runnerClient.capabilities(headers)
      if (!currentSendContext()) return
      state.runnerClient.installCapabilities(capabilities)
      runnerTransport = capabilities.web_enabled
    } catch (issue) {
      if (!currentSendContext()) return
      state.submitSelecting = false
      ownsSelection = false
      state.isProcessing.value = false
      runnerError(effectiveSessionId, issue as Error)
      return
    }
    const requestId = runnerTransport ? crypto.randomUUID() : undefined
    if (retryFiles && !runnerTransport) {
      state.submitSelecting = false
      ownsSelection = false
      state.isProcessing.value = false
      runnerError(effectiveSessionId, new Error('RUNNER_SERVICE_UNAVAILABLE'))
      return
    }

    if (retrySource && requestId) {
      try {
        await updateFailedMessageUI(effectiveSessionId, retrySource,
          { action: 'retry', client_request_id: requestId }, headers)
        if (!currentSendContext()) return
        state.failedMessageUI.retry_sources = { ...state.failedMessageUI.retry_sources, [requestId]: retrySource }
      } catch (issue) {
        if (!currentSendContext()) return
        state.submitSelecting = false
        ownsSelection = false
        state.isProcessing.value = false
        runnerError(effectiveSessionId, issue as Error)
        return
      }
    }

    state.dbLoaded = true

    // 构建用户消息内容（含附件信息）；防御性过滤上传中的占位（正常情况下发送按钮已禁用）
    const readyFiles = (retryFiles || currentFiles.value).filter(f => f.file_id)
    let userContent = content.trim()
    if (readyFiles.length > 0) {
      const fileNames = readyFiles.map(f => f.name).join(', ')
      userContent += `\n\n[附件: ${fileNames}]`
    }

    // 添加用户消息
    state.messages.value.push({
      role: 'user',
      clientRequestId: requestId,
      messageId: requestId ? 'request:' + requestId + ':user' : undefined,
      content: userContent,
      timestamp: Date.now(),
      attachments: readyFiles.length > 0 ? [...readyFiles] : undefined
    })

    // 重置状态
    state.isProcessing.value = true
    if (effectiveSessionId === sessionId.value) {
      error.value = null
    }
    state.currentResponse.value = ''
    state.progressMessages.value = []
    state.liveVerbose.value = null
    state.inputHintState.value = 'thinking'

    // 添加空的助手消息占位
    const assistantMessageId = requestId ? 'request:' + requestId + ':assistant' : 'local:' + generateSessionId() + ':assistant'
    const assistantMessage = {
      role: 'assistant' as const,
      clientRequestId: requestId,
      messageId: assistantMessageId,
      content: '',
      timestamp: Date.now(),
      progressMessages: [] as ProgressMessage[]  // 初始化空数组
    }
    state.messages.value.push(assistantMessage)

    // 添加初始进度消息（在 AI 消息创建之后）
    addProgress(state, '🚀 正在发送请求...', 'progress')

    // 附件数据已浅拷贝到用户消息与 SSE 请求参数（下方 connect 调用），
    // 在这里清空附件输入区，确保用户看到自己消息出现的同时附件框立即清空，
    // 避免 ChatContainer.handleSend 在某些 return 分支下漏清空导致附件残留。
    const filesToSend = readyFiles.length > 0 ? [...readyFiles] : undefined
    if (!retryFiles) currentFiles.value = []

    if (runnerTransport) {
      const body: RunnerSubmission = JSON.parse(JSON.stringify({
        client_request_id: requestId, message: content, session_id: effectiveSessionId,
        files: filesToSend, subagent, instance_id: instanceId,
        video_params: !subagent || subagent === 'video-agent' ? { ...useVideoGenParams().params.value } : null,
      }))
      state.pendingRunner = body
      state.submitSelecting = false
      ownsSelection = false
      const failed = await submitRunner(state, body, headers)
      // definitive 失败（403 NO_CREDIT 等 4xx）：本次输入回填草稿与附件，
      // 与 control/parked 分支同一 restoreInput disposition，由 ChatContainer.handleSend 消费
      if (failed === 'definitive' && currentRunnerContext(state, effectiveSessionId, generation, headers)) {
        if (!retryFiles && sessionId.value === effectiveSessionId && !currentFiles.value.length && filesToSend?.length) {
          currentFiles.value = filesToSend
        }
        return { restoreInput: true, canRestore: () => currentRunnerContext(state, effectiveSessionId, generation, headers) }
      }
      return
    }
    state.submitSelecting = false
    ownsSelection = false

    // 流结束时若是后台会话（用户正在查看其他会话），标记完成小点
    const markUnreadIfBackground = () => {
      if (effectiveSessionId !== sessionId.value) {
        state.hasUnreadCompletion.value = true
      }
    }

    try {
      await state.sseManager.connect(
        content,
        effectiveSessionId,
        filesToSend,
        getEffectiveAuthHeader(), // 传递认证头
        // onProgress - 工具执行进度，仅添加到执行详情
        (data) => {
          // 过滤冗余进度：工具开始/完成已有专门事件（tool_start/tool_result），
          // 后端额外推送的「正在执行...」「...执行完成」会重复刷屏，这里静默丢弃。
          // 失败预览（❌ 开头，常含堆栈/日志）只写 console，界面由工具结果的中性状态承接。
          const trimmed = data.trim()
          if (/^(🔧\s*正在执行|✅\s*.+执行完成$|❌)/.test(trimmed)) {
            if (trimmed.startsWith('❌')) console.warn('[Agent progress]', data)
            return
          }
          addProgress(state, data, 'progress')
          // 不再将进度追加到助手消息内容
        },
        // onResponse - AI响应内容
        (data) => {
          // response 开始：关闭 live verbose 占位（设计 §8.2，response 后隐藏）
          state.liveVerbose.value = null
          state.currentResponse.value += data
          const target = state.messages.value.find(message => message.messageId === assistantMessageId)
          if (target) target.content = state.currentResponse.value
          if (state.inputHintState.value !== 'responding') {
            state.inputHintState.value = 'responding'
          }
        },
        // onComplete
        () => {
          state.liveVerbose.value = null
          if (state.cancelledByUser) {
            addProgress(state, '⚠️ 已停止', 'error')
            state.cancelledByUser = false
          } else {
            addProgress(state, '✅ 任务完成', 'complete')
          }
          // 助手回复完成，把占位消息的 timestamp 更新为完成时刻，
          // 对齐后端 assistant 消息的 created_at（避免始终显示发送时刻）
          const target = state.messages.value.find(message => message.messageId === assistantMessageId)
          if (target) target.timestamp = Date.now()
          state.isProcessing.value = false
          state.inputHintState.value = 'idle'
          markUnreadIfBackground()
        },
        // onError
        (err) => {
          state.liveVerbose.value = null
          // 识别 SSE 403 NO_CREDIT 错误，显示友好 toast 提示
          const errWithCode = err as Error & { code?: string; status?: number }
          if (errWithCode.code === 'NO_CREDIT' || errWithCode.status === 403) {
            try {
              const toast = useToast()
              toast.error(errWithCode.message || '积分余额已耗尽，数字员工无法工作')
            } catch {
              // toast 不可用时降级到进度消息
            }
          }
          if (effectiveSessionId === sessionId.value) {
            error.value = err.message
          }
          addProgress(state, `❌ 错误: ${err.message}`, 'error')
          state.isProcessing.value = false
          state.inputHintState.value = 'idle'
          markUnreadIfBackground()
        },
        // onToolStart - 工具开始执行
        (toolName, toolArgs, displayName, toolCallId) => {
          const toolDisplayName = displayName || toolName
          addProgress(
            state,
            `🔧 需要调用工具【${toolDisplayName}】`,
            'tool_start',
            toolName,
            toolArgs,
            undefined,
            displayName,
            toolCallId,
          )
          state.inputHintState.value = 'working'
        },
        // onToolResult - 工具执行结果
        (toolName, result, success, displayName, toolCallId) => {
          const toolDisplayName = displayName || toolName
          if (success) {
            // 结构化消费者只依赖结果字段，不依赖生产该结果的工具名称。
            const batchFiles: Array<Record<string, any>> = Array.isArray(result?.files) ? result.files : []
            const singleFile = (!batchFiles.length && result?.file_id && result.visible !== false)
              ? [result] : []
            const deliverableFiles = [...batchFiles, ...singleFile]
              .filter(f => f?.file_id && f.visible !== false)
            if (deliverableFiles.length) {
              const lastMsg = state.messages.value[state.messages.value.length - 1]
              if (lastMsg && lastMsg.role === 'assistant') {
                if (!lastMsg.downloadableFiles) {
                  lastMsg.downloadableFiles = []
                }
                // 按 file_id 去重
                for (const f of deliverableFiles) {
                  if (lastMsg.downloadableFiles.some(x => x.file_id === f.file_id)) continue
                  lastMsg.downloadableFiles.push({
                    file_id: f.file_id,
                    file_name: f.download_file_name || f.file_name || '未命名文件',
                    file_size: f.file_size || 0,
                    download_url: f.download_url || `/api/files/${f.file_id}/download`,
                    mime_type: f.mime_type || '',
                  })
                }
              }
            }
            addProgress(
              state, `✅ 【${toolDisplayName}】执行完成`, 'tool_result',
              toolName, undefined, result, displayName, toolCallId, true,
            )
          } else {
            const errorMsg = result?.error || '未知错误'
            addProgress(
              state, `❌ ${toolDisplayName}失败: ${errorMsg}`, 'tool_result',
              toolName, undefined, result, displayName, toolCallId, false,
            )
          }
        },
        // onThinking - LLM思考中
        (data) => {
          addProgress(state, `🤔 ${data}`, 'thinking')
          state.inputHintState.value = 'thinking'
        },
        // onClarification - 子智能体需要用户补充信息（form 为 Phase 3 透传的
        // 结构化表单数据，随帧携带供消费方使用；作答方式不变）
        (subagentName, question, form?: AskForm) => {
          addProgress(state, `❓ ${subagentName}需要补充信息: ${question}`, 'tool_start',
            'clarification', undefined, { form })
        },
        // onImages - Agent 推送的图片资产（Phase 2 P2.5）
        (images, placement) => {
          if (!images || !images.length) return
          // 找到当前助手消息（最后一条）
          const lastMsg = state.messages.value[state.messages.value.length - 1]
          if (!lastMsg || lastMsg.role !== 'assistant') return
          if (!lastMsg.images) lastMsg.images = []
          // 按 file_id 去重合并（一次回复可能多次推送 images 事件）
          for (const img of images) {
            if (!img?.file_id) continue
            // 兜底 placement：后端理论上已 set，但前端容错
            const normalized = { ...img, placement: img.placement || placement || 'after_text' }
            if (!lastMsg.images.some(existing => existing.file_id === normalized.file_id)) {
              lastMsg.images.push(normalized)
            }
          }
        },
        // onBrowserHumanRequired - 结构化卡片独立于 LLM 文本渲染
        (event) => {
          state.liveVerbose.value = null
          const lastMsg = state.messages.value[state.messages.value.length - 1]
          if (lastMsg?.role === 'assistant') {
            lastMsg.browserAssistance = { ...event, state: 'pending' }
          }
          state.isProcessing.value = false
          state.inputHintState.value = 'idle'
          addProgress(state, '等待你在浏览器中完成操作', 'progress')
        },
        // onVerbose - 用户可见中间消息（Phase 2，设计 §8.2）
        // 后端每轮最多一条；这里按 eventId 覆盖去重（后来者覆盖）纯属防御，
        // 只写当前 session 状态，不进 progressMessages / debug 执行详情
        (message) => {
          if (!message?.eventId) return
          state.liveVerbose.value = message
        },
        subagent,
        instanceId,
        // 视频创作参数：仅 video-agent 子智能体使用，其他智能体忽略
        subagent === 'video-agent' ? { ...useVideoGenParams().params.value } : null
      )
    } catch (err) {
      state.liveVerbose.value = null
      if (effectiveSessionId === sessionId.value) {
        error.value = (err as Error).message
      }
      addProgress(state, `❌ 连接错误: ${(err as Error).message}`, 'error')
      state.isProcessing.value = false
      state.inputHintState.value = 'idle'
      markUnreadIfBackground()
    }
  }

  function addProgress(
    state: SessionStreamState,
    content: string,
    type: ProgressMessage['type'],
    toolName?: string,
    toolArgs?: object,
    result?: any,
    displayName?: string,
    toolCallId?: string,
    success?: boolean,
  ) {
    const newMsg: ProgressMessage = {
      type,
      content,
      timestamp: Date.now(),
      toolName,
      displayName,
      toolCallId,
      toolArgs,
      result,
      success,
    }
    state.progressMessages.value.push(newMsg)

    // 输出到前端 console，方便调试
    const ts = new Date().toLocaleTimeString()
    const label = `[Agent ${type}]`
    if (type === 'error') {
      console.error(`${ts} ${label}`, content, { toolName, toolArgs, result })
    } else if (type === 'tool_result' || type === 'tool_start') {
      console.info(`${ts} ${label}`, content, { toolName, toolArgs, result })
    } else {
      console.log(`${ts} ${label}`, content)
    }

    // 同时更新 AI 消息占位中的 progressMessages（用于 MessageItem 显示）
    const lastMsg = state.messages.value[state.messages.value.length - 1]
    if (lastMsg && lastMsg.role === 'assistant' && lastMsg.progressMessages) {
      lastMsg.progressMessages.push(newMsg)
    }
  }

  function clearMessages() {
    const state = getStreamState(sessionId.value)
    state.messages.value = []
    state.progressMessages.value = []
    state.currentResponse.value = ''
    error.value = null
  }

  function clearSession() {
    sessionId.value = generateSessionId()
    error.value = null
  }

  /**
   * 预先标记一个新建的空会话（跳过后续 DB 请求）
   */
  function precacheNewSession(sid: string) {
    console.log(`[${now()}] [precacheNewSession] precache empty session:`, sid)
    const state = getStreamState(sid)
    state.messages.value = []
    state.dbLoaded = true
  }

  /**
   * 清除会话状态缓存
   * - 传入 sid：仅清除该会话的内存状态（进行中的会话不清除），下次切回时从 DB 重新加载
   * - 不传 sid：清空全部会话状态并断开所有流式连接（用于登出/重新登录）
   */
  function clearSessionCache(sid?: string) {
    if (sid) {
      const state = sessionStreams.get(sid)
      if (state && !state.isProcessing.value) {
        detachRunner(state)
        sessionStreams.delete(sid)
      }
      return
    }
    for (const [, state] of sessionStreams) {
      state.sseManager.disconnect()
      detachRunner(state)
    }
    sessionStreams.clear()
    runnerSubject = ''
    error.value = null
    runnerFeedback.value = null
  }

  /**
   * 删除会话时同步清理其流式状态（断开连接并从状态池移除）
   */
  function removeStreamState(sid: string) {
    const state = sessionStreams.get(sid)
    if (state) {
      state.sseManager.disconnect()
      detachRunner(state)
      sessionStreams.delete(sid)
    }
  }

  /**
   * 中止指定会话（默认当前查看会话）正在进行的流式响应
   */
  async function abortStreaming(targetSid?: string) {
    const sid = targetSid || sessionId.value
    const state = sessionStreams.get(sid)
    const headers = getEffectiveAuthHeader()
    const generation = state?.runnerGeneration
    const current = () => !!state && currentRunnerContext(state, sid, generation!, headers)
    if (state && !current()) return
    if (state?.runnerViews.size) {
      const active = [...state.runnerViews.values()].filter(row => !runnerTerminal(row.status))
      if (active.length) {
        try {
          for (const row of active) {
            if (!current()) return
            const cancelled = await state.runnerClient.cancel(row.runner_id, headers)
            if (!current()) return
            applyRunner(state, cancelled)
          }
          // Observe committed cancellation. A successful request is not a terminal result.
        } catch (issue) { if (current()) runnerError(sid, issue as Error) }
        return
      }
    }
    if (state?.submitSelecting) {
      // No submit has started yet. Stop local selection and invalidate its
      // outstanding capability response; do not call the legacy cancel API.
      state.runnerGeneration++
      state.submitSelecting = false
      state.isProcessing.value = false
      state.inputHintState.value = 'idle'
      return
    }
    // The POST may already be accepted even if its response was lost. Resolve
    // that original idempotency key before issuing a control operation.
    if (state?.pendingRunner) {
      try {
        const row = await state.runnerClient.submit(state.pendingRunner, headers)
        if (!current()) return
        state.pendingRunner = undefined
        clearTimeout(state.submitTimer)
        observeRunner(state, row, headers)
        const cancelled = await state.runnerClient.cancel(row.runner_id, headers)
        if (!current()) return
        applyRunner(state, cancelled)
      } catch (issue) { if (current()) runnerError(sid, issue as Error) }
      return
    }
    console.log(`[${now()}] [abortStreaming] called, sid=`, sid, 'isProcessing=', state?.isProcessing.value)
    if (state?.isProcessing.value) {
      // 标记为用户主动取消，让 onComplete 显示取消状态
      state.cancelledByUser = true
      // 先通知后端取消（设置 Redis 标记，agent 在检查点正常收尾落账），再断开连接。
      // 顺序不能反：若先 disconnect，后端走 CancelledError 兜底路径，取消轮次的
      // 取消消息落库（cancelled 标记）会丢失，仅剩计费兜底。
      try {
        const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
        await fetch(`${apiBase}/chat/${encodeURIComponent(sid)}/cancel`, {
          method: 'POST',
          headers: getEffectiveAuthHeader()
        })
        console.log(`[${now()}] [abortStreaming] backend cancel request sent`)
      } catch (err) {
        console.warn('[abortStreaming] failed to notify backend:', err)
        // 即使后端通知失败，前端仍然中止
      }
      state.sseManager.disconnect()
      state.isProcessing.value = false
      state.inputHintState.value = 'idle'
    }
  }

  /**
   * 会话列表指示器：该会话是否正在流式生成（显示旋转 loading）
   */
  function isSessionRunning(sid: string): boolean {
    return sessionStreams.get(sid)?.isProcessing.value ?? false
  }

  /**
   * 会话列表指示器：该会话是否在后台完成且用户尚未查看（显示完成小点）
   */
  function hasSessionUnreadCompletion(sid: string): boolean {
    return sessionStreams.get(sid)?.hasUnreadCompletion.value ?? false
  }

  return {
    messages,
    progressMessages,
    isProcessing,
    canStop,
    refreshRunner,
    currentResponse,
    liveVerbose,
    error,
    runnerFeedback,
    localRecovery,
    localRecoveryBusy,
    localVerificationMessage,
    resolveLocalRecovery,
    sessionId,
    currentFiles,
    sendMessage,
    clearMessages,
    clearSession,
    switchSession,
    clearSessionCache,
    precacheNewSession,
    uploadAttachment,
    removeAttachment,
    clearAttachments,
    abortStreaming,
    removeStreamState,
    isSessionRunning,
    hasSessionUnreadCompletion,
    // 结构化提问卡片（WP4）：提交入口与 §5.7 状态机上下文
    submitAskAnswers,
    askSubmissionFor,
    askSubmissionForControl,
    askControlView,
    // "正在输入"提示
    inputHintState,
    isWaitingHuman
  }
}

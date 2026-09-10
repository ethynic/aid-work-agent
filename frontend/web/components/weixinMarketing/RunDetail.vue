<template>
  <!-- 运行详情：run 状态 + 逐条 delivery（safe_to_retry 证据门槛）/ resolve / retry / cancel -->
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
      <!-- 顶部工具栏 -->
      <div class="page-toolbar">
        <div class="page-toolbar-left flex-wrap">
          <BaseButton size="sm" intent="secondary" @click="goBack">← 返回</BaseButton>
          <template v-if="detail">
            <span class="text-sm font-medium text-default">运行 {{ shortId(runId) }}</span>
            <BaseBadge :intent="RUN_STATE_BADGE[detail.run.state]">
              {{ RUN_STATE_LABELS[detail.run.state] }}
              <template v-if="pollingActive">（{{ pollCountdownText }}）</template>
            </BaseBadge>
            <span class="text-xs text-muted">自动化 {{ shortId(detail.run.task_ref) }}</span>
            <span class="text-xs text-muted">创建 {{ formatDateTime(detail.run.created_at) }}</span>
            <span v-if="detail.run.finished_at" class="text-xs text-muted">
              完成 {{ formatDateTime(detail.run.finished_at) }}
            </span>
          </template>
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" intent="ghost" :disabled="loading" @click="loadDetail(true)">刷新</BaseButton>
          <BaseButton
            v-if="detail && !isRunTerminal(detail.run.state)"
            size="sm"
            intent="danger-ghost"
            :disabled="cancelling"
            @click="handleCancelRun"
          >{{ cancelling ? '请求取消中...' : '取消运行' }}</BaseButton>
        </div>
      </div>

      <!-- 加载 / 错误 / 空态 -->
      <div v-if="loading && !detail" class="page-content p-4 text-muted text-sm">加载中...</div>
      <div v-else-if="loadError && !detail" class="page-content p-4">
        <p class="text-sm text-danger-600 mb-2">{{ loadError }}</p>
        <BaseButton size="sm" intent="secondary" @click="loadDetail(true)">重试</BaseButton>
      </div>
      <div v-else-if="!detail" class="empty-state">
        <div class="empty-state-icon">◌</div>
        <p class="text-sm text-muted">未找到运行记录</p>
      </div>

      <template v-else>
        <div v-if="pollError" class="mb-2 text-xs text-warning-600">
          {{ pollError }}（退避至 {{ Math.round(pollDelayMs / 1000) }} 秒后重试）
        </div>

        <!-- 逐条 delivery -->
        <div class="table-scroll-wrapper flex-1">
          <BaseTable :columns="columns" :data="detail.deliveries" row-key="id">
            <template #position="{ row }">#{{ (row.position ?? 0) + 1 }}</template>
            <template #state="{ row }">
              <BaseBadge :intent="DELIVERY_STATE_BADGE[row.state as DeliveryState]">
                {{ DELIVERY_STATE_LABELS[row.state as DeliveryState] }}
              </BaseBadge>
            </template>
            <template #effect="{ row }">
              <span class="text-sm text-default">{{ row.effect || '-' }}</span>
            </template>
            <template #attempt="{ row }">
              <div class="text-xs space-y-0.5">
                <template v-if="row.latest_attempt">
                  <div class="text-default">第 {{ row.latest_attempt.attempt_no ?? '?' }} 次 · {{ row.latest_attempt.effect || '-' }} / {{ row.latest_attempt.phase || '-' }}</div>
                  <div v-if="row.latest_attempt.safe_to_retry === true" class="text-success-600">机器判定可安全重试</div>
                  <div v-else class="text-muted">不可安全重试（需人工证据）</div>
                  <div v-if="row.latest_attempt.evidence_ref" class="text-muted" :title="row.latest_attempt.evidence_ref">
                    证据：{{ shortId(row.latest_attempt.evidence_ref) }}
                  </div>
                </template>
                <template v-else>
                  <span class="text-muted">尚无 attempt</span>
                </template>
              </div>
            </template>
            <template #finished_at="{ row }">{{ formatDateTime(row.finished_at) }}</template>
            <template #actions="{ row }">
              <div class="flex items-center justify-center gap-1 whitespace-nowrap">
                <BaseButton
                  size="sm"
                  intent="ghost"
                  :disabled="!canResolve(row as DeliveryDetail)"
                  :title="!canResolve(row as DeliveryDetail) ? '仅终态投递可记录人工结论' : ''"
                  @click="openResolve(row as DeliveryDetail)"
                >结论</BaseButton>
                <BaseButton
                  size="sm"
                  intent="ghost"
                  :disabled="!canRetry(row as DeliveryDetail) || retryingIds.has(row.id)"
                  :title="retryTitle(row as DeliveryDetail)"
                  @click="openRetry(row as DeliveryDetail)"
                >{{ retryingIds.has(row.id) ? '重试中...' : '重试' }}</BaseButton>
              </div>
            </template>
            <template #empty>该运行暂无投递条目</template>
          </BaseTable>
        </div>
      </template>
    </div>

    <!-- 人工结论弹框 -->
    <BaseModal v-model="showResolve" title="投递人工结论" size="md">
      <div v-if="resolveTarget" class="space-y-4">
        <p class="text-sm text-muted">
          对第 {{ (resolveTarget.position ?? 0) + 1 }} 条投递记录人工结论；结论独立审计留痕，不覆盖机器判定。
        </p>
        <div>
          <label class="form-label">人工判定 <span class="form-required">*</span></label>
          <BaseSelect v-model="resolveForm.verdict">
            <option value="delivered">已送达（delivered）</option>
            <option value="not_delivered">未送达（not_delivered）</option>
            <option value="uncertain">无法确定（uncertain）</option>
          </BaseSelect>
        </div>
        <div class="flex items-start gap-2">
          <input
            id="wxm-resolve-decision"
            v-model="resolveForm.withDecision"
            type="checkbox"
            class="mt-1 rounded border-gray-300 text-primary-600 focus:ring-primary-500/20"
          />
          <label for="wxm-resolve-decision" class="text-sm text-default">
            确认该次结果未知条目「实际未发送」并授权重试（confirmed_not_sent）
            <span class="block text-xs text-muted">勾选后必须填写证据说明；这是未知效果条目重试的前置条件</span>
          </label>
        </div>
        <div>
          <label class="form-label">
            证据说明 <span v-if="resolveForm.withDecision" class="form-required">*</span>
          </label>
          <textarea
            v-model="resolveForm.note"
            rows="3"
            maxlength="2000"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            placeholder="例如：已人工查看设备客户端会话，确认该条消息未出现在群中"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="resolving" @click="showResolve = false">取消</BaseButton>
        <BaseButton :disabled="!canSubmitResolve || resolving" @click="handleResolve">
          {{ resolving ? '提交中...' : '提交结论' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- 重试确认弹框 -->
    <BaseModal v-model="showRetry" title="确认人工重试" size="sm">
      <div v-if="retryTarget" class="space-y-3">
        <p class="text-sm text-default">
          将为第 {{ (retryTarget.position ?? 0) + 1 }} 条投递创建新 attempt 重新发送。该操作有真实发送副作用，请确认。
        </p>
        <p v-if="!isMachineSafeToRetry(retryTarget)" class="text-sm text-warning-600">
          该条目机器未判定可安全重试：需先在「结论」中记录
          <span class="font-medium">确认未发送并授权重试（confirmed_not_sent）</span>，否则服务端将拒绝（409）。
        </p>
        <p v-else class="text-sm text-success-600">机器已判定可安全重试（未开始/未产生效果）。</p>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="retrying" @click="showRetry = false">取消</BaseButton>
        <BaseButton intent="danger" :disabled="retrying" @click="handleRetry">
          {{ retrying ? '提交中...' : '确认重试' }}
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
/**
 * RunDetail：运行详情（R54②/微信计划 §8）。
 * - 轮询：非终态每 2 秒刷新；网络错误指数退避（2s→4s→…封顶 30s），成功后复位；
 *   进入终态立即停止；卸载/切路由 AbortController 取消在途请求并停止定时器；
 * - resolve：人工结论 + confirmed_not_sent 决定（需 note）；
 * - retry：confirm 确认弹框；409 RETRY_EVIDENCE_REQUIRED 文案化引导先 resolve；
 * - cancel：非终态请求取消（202，已提交条目照实回收）。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import {
  WX_ERROR_CODES,
  WeixinApiError,
  cancelRun,
  getRunDetail,
  isRetryEvidenceRequired,
  isRunTerminal,
  resolveDelivery,
  retryDelivery,
  type DeliveryDetail,
  type DeliveryState,
  type RunDetailData,
} from '@/api/weixinMarketing'
import {
  DELIVERY_STATE_BADGE,
  DELIVERY_STATE_LABELS,
  RUN_STATE_BADGE,
  RUN_STATE_LABELS,
  formatDateTime,
} from './weixinDisplay'

const POLL_BASE_MS = 2000
const POLL_MAX_MS = 30000

const route = useRoute()
const router = useRouter()
const toast = useToast()

const runId = computed(() => String(route.params.runId || ''))

const detail = ref<RunDetailData | null>(null)
const loading = ref(false)
const loadError = ref('')

// ---------- 轮询（2s → 终态停止；错误退避；卸载 AbortController） ----------
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollAbort: AbortController | null = null
let loadAbort: AbortController | null = null
const pollDelayMs = ref(POLL_BASE_MS)
const pollError = ref('')

const pollingActive = computed(
  () => !!detail.value && !isRunTerminal(detail.value.run.state) && !loadError.value,
)

const pollCountdownText = computed(() => `${Math.round(pollDelayMs.value / 1000)}s 刷新`)

function stopPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  pollAbort?.abort()
  pollAbort = null
}

function scheduleNextPoll(delayMs: number) {
  stopPolling()
  pollTimer = setTimeout(async () => {
    await pollOnce()
  }, delayMs)
}

async function pollOnce() {
  if (!detail.value || isRunTerminal(detail.value.run.state)) return
  // 本轮独享 controller：卸载/停止后 ctl.signal.aborted 置位，
  // catch/schedule 前检查，杜绝「卸载后在途请求回落又重排定时器」的复活（V-P1-2）
  const ctl = new AbortController()
  pollAbort = ctl
  try {
    const fresh = await getRunDetail(runId.value, ctl.signal)
    if (ctl.signal.aborted) return
    detail.value = fresh
    pollError.value = ''
    pollDelayMs.value = POLL_BASE_MS
    if (isRunTerminal(fresh.run.state)) {
      stopPolling()
      return
    }
  } catch (e) {
    if (ctl.signal.aborted) return
    if (e instanceof WeixinApiError && e.code === WX_ERROR_CODES.NETWORK_ERROR) {
      pollError.value = '网络异常，自动刷新暂停'
      pollDelayMs.value = Math.min(pollDelayMs.value * 2, POLL_MAX_MS)
    } else if (e instanceof WeixinApiError && e.status === 404) {
      pollError.value = '运行记录不存在或已被清理'
      stopPolling()
      return
    } else {
      pollError.value = e instanceof WeixinApiError ? e.message : '刷新失败'
      pollDelayMs.value = Math.min(pollDelayMs.value * 2, POLL_MAX_MS)
    }
  }
  if (!ctl.signal.aborted) {
    scheduleNextPoll(pollDelayMs.value)
  }
}

async function loadDetail(showLoading = false) {
  if (!runId.value) return
  if (showLoading) loading.value = true
  stopPolling()
  loadAbort?.abort()
  loadAbort = new AbortController()
  const signal = loadAbort.signal
  try {
    const data = await getRunDetail(runId.value, signal)
    if (signal.aborted) return
    detail.value = data
    loadError.value = ''
    pollError.value = ''
    pollDelayMs.value = POLL_BASE_MS
    if (!isRunTerminal(data.run.state)) {
      scheduleNextPoll(POLL_BASE_MS)
    }
  } catch (e) {
    if (signal.aborted) return
    loadError.value = e instanceof WeixinApiError ? e.message : '加载运行详情失败'
    detail.value = null
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

// ---------- 表格 ----------
const columns = [
  { key: 'position', label: '条目', width: '70px' },
  { key: 'state', label: '状态', width: '100px' },
  { key: 'effect', label: '效果', width: '90px' },
  { key: 'attempt', label: '最近 attempt / 证据' },
  { key: 'finished_at', label: '完成时间', width: '140px' },
  { key: 'actions', label: '操作', width: '120px', tdAlign: 'center' as const },
]

const RETRYABLE_STATES = ['failed', 'unknown', 'expired']
const RESOLVABLE_STATES = ['succeeded', 'failed', 'unknown', 'expired', 'skipped']

function canResolve(delivery: DeliveryDetail): boolean {
  return RESOLVABLE_STATES.includes(delivery.state)
}

function canRetry(delivery: DeliveryDetail): boolean {
  return RETRYABLE_STATES.includes(delivery.state)
}

/** safe_to_retry 仅 true 表示机器判定可安全重试（None/False 均不可） */
function isMachineSafeToRetry(delivery: DeliveryDetail): boolean {
  return delivery.latest_attempt?.safe_to_retry === true
}

function retryTitle(delivery: DeliveryDetail): string {
  if (!canRetry(delivery)) return `状态 ${delivery.state} 不可重试`
  if (isMachineSafeToRetry(delivery)) return '机器判定可安全重试'
  return '需先记录人工结论（确认未发送并授权重试）'
}

function shortId(value: string): string {
  return value ? `${value.slice(0, 8)}…` : '-'
}

function goBack() {
  router.back()
}

// ---------- 取消运行 ----------
const cancelling = ref(false)

async function handleCancelRun() {
  if (!detail.value || cancelling.value) return
  cancelling.value = true
  try {
    const res = await cancelRun(runId.value)
    toast.success(res.changed ? '已请求取消：未提交条目将停止' : `当前状态 ${res.state}，无可取消条目`)
    await loadDetail(true)
  } catch (e) {
    toast.error(e instanceof WeixinApiError ? e.message : '取消请求失败')
  } finally {
    cancelling.value = false
  }
}

// ---------- resolve ----------
const showResolve = ref(false)
const resolving = ref(false)
const resolveTarget = ref<DeliveryDetail | null>(null)
const resolveForm = ref({ verdict: 'uncertain', withDecision: false, note: '' })

const canSubmitResolve = computed(() => {
  if (!resolveForm.value.verdict) return false
  if (resolveForm.value.withDecision && !resolveForm.value.note.trim()) return false
  return true
})

function openResolve(delivery: DeliveryDetail) {
  resolveTarget.value = delivery
  resolveForm.value = { verdict: 'uncertain', withDecision: false, note: '' }
  showResolve.value = true
}

async function handleResolve() {
  if (!resolveTarget.value || !canSubmitResolve.value || resolving.value) return
  resolving.value = true
  try {
    await resolveDelivery(resolveTarget.value.id, {
      verdict: resolveForm.value.verdict as 'delivered' | 'not_delivered' | 'uncertain',
      decision: resolveForm.value.withDecision ? 'confirmed_not_sent' : null,
      note: resolveForm.value.note.trim() || null,
    })
    toast.success('人工结论已记录（独立审计留痕）')
    showResolve.value = false
    await loadDetail(true)
  } catch (e) {
    toast.error(e instanceof WeixinApiError ? e.message : '提交结论失败')
  } finally {
    resolving.value = false
  }
}

// ---------- retry ----------
const showRetry = ref(false)
const retrying = ref(false)
const retryTarget = ref<DeliveryDetail | null>(null)
const retryingIds = ref(new Set<string>())

function openRetry(delivery: DeliveryDetail) {
  retryTarget.value = delivery
  showRetry.value = true
}

async function handleRetry() {
  if (!retryTarget.value || retrying.value) return
  retrying.value = true
  const targetId = retryTarget.value.id
  retryingIds.value = new Set([...retryingIds.value, targetId])
  try {
    const res = await retryDelivery(targetId)
    toast.success(`已创建新 attempt（第 ${res.attempt_no} 次），正在重新发送`)
    showRetry.value = false
    await loadDetail(true)
  } catch (e) {
    if (isRetryEvidenceRequired(e)) {
      toast.warning('服务端要求先补人工证据：请在「结论」中勾选「确认未发送并授权重试」并填写说明后重试')
    } else {
      toast.error(e instanceof WeixinApiError ? e.message : '重试失败')
    }
  } finally {
    retrying.value = false
    const next = new Set(retryingIds.value)
    next.delete(targetId)
    retryingIds.value = next
  }
}

// ---------- 生命周期 ----------
watch(
  runId,
  (id) => {
    if (id) loadDetail(true)
  },
  { immediate: true },
)

onUnmounted(() => {
  stopPolling()
  loadAbort?.abort()
  loadAbort = null
})
</script>

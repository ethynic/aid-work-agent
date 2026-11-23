<template>
  <div class="space-y-4">
    <!-- 运行告警列表 -->
    <div>
      <div class="flex flex-wrap items-center justify-between gap-3 mb-2">
        <h2 class="text-sm font-semibold text-default">运行告警</h2>
        <div class="flex flex-wrap items-center gap-2">
          <span v-if="runnerFilter" class="text-sm text-muted break-all">Runner：{{ runnerFilter }}</span>
          <BaseButton v-if="runnerFilter" intent="ghost" @click="clearRunnerFilter">清除筛选</BaseButton>
          <BaseButton :intent="state === 'current' ? 'primary' : 'secondary'" @click="selectState('current')">当前</BaseButton>
          <BaseButton :intent="state === 'history' ? 'primary' : 'secondary'" @click="selectState('history')">历史</BaseButton>
          <BaseButton intent="secondary" :disabled="loading" @click="loadAlerts">刷新</BaseButton>
        </div>
      </div>

      <p v-if="listError" role="alert" class="text-sm text-danger-700 mb-3">{{ listError }}</p>
      <div v-if="loading && alerts.length === 0" class="text-sm text-muted py-4 text-center bg-white rounded-lg border border-default">
        加载中…
      </div>
      <div v-else-if="alerts.length === 0" class="text-sm text-muted py-4 text-center bg-white rounded-lg border border-default">
        暂无{{ state === 'current' ? '运行中' : '历史' }}告警
      </div>
      <template v-else>
        <div class="table-scroll-wrapper">
          <BaseTable :columns="alertColumns" :data="alerts" row-key="alert_id" table-fixed>
            <template #runner_id="{ row }"><span class="font-mono text-xs break-all">{{ row.runner_id }}</span></template>
            <template #tenant_id="{ row }"><span class="break-all">{{ row.tenant_id || '未归属租户' }}</span></template>
            <template #profile_id="{ row }">{{ row.profile_id || 'main' }}</template>
            <template #elapsed_seconds="{ row }">{{ formatDuration(row.elapsed_seconds) }}</template>
            <template #alerted_at="{ row }">{{ formatTime(row.alerted_at) }}</template>
            <template #ended_at="{ row }">
              <BaseBadge :intent="row.ended_at ? 'neutral' : 'warning'">{{ row.ended_at ? '已结束' : '运行中' }}</BaseBadge>
            </template>
            <template #actions="{ row }">
              <BaseButton intent="ghost" @click="openDetail(row.alert_id)">详情</BaseButton>
            </template>
          </BaseTable>
        </div>
        <div class="mt-3 flex justify-center">
          <BasePagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="loadAlerts" />
        </div>
      </template>
    </div>

    <!-- 告警配置（平台管理员运维入口，默认折叠） -->
    <div v-if="showConfig" class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <BaseCard title="执行时长阈值">
        <p class="text-sm text-muted mb-4">平台默认 {{ defaultMinutes }} 分钟；租户与数字员工覆盖阈值在下一次扫描时生效。</p>
        <p v-if="thresholdError" role="alert" class="text-sm text-danger-700 mb-3">{{ thresholdError }}</p>
        <form class="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end mb-4" @submit.prevent="saveThreshold">
          <label class="form-field sm:col-span-2">
            <span class="form-label">租户 ID</span>
            <BaseInput v-model="thresholdTenant" required placeholder="租户 ID" />
          </label>
          <label class="form-field">
            <span class="form-label">数字员工 ID</span>
            <BaseInput v-model="thresholdProfile" required placeholder="profile_id" />
          </label>
          <label class="form-field">
            <span class="form-label">分钟</span>
            <BaseInput v-model="thresholdMinutes" type="number" min="1" step="1" required />
          </label>
          <BaseButton class="sm:col-span-4 justify-self-start" :disabled="savingThreshold">
            {{ savingThreshold ? '保存中…' : '保存覆盖阈值' }}
          </BaseButton>
        </form>
        <div class="table-scroll-wrapper">
          <BaseTable :columns="thresholdColumns" :data="thresholdRows" row-key="key" table-fixed>
            <template #tenant_id="{ row }"><span class="break-all">{{ row.tenant_id }}</span></template>
            <template #profile_id="{ row }"><span class="break-all">{{ row.profile_id }}</span></template>
            <template #actions="{ row }"><BaseButton intent="danger-ghost" :disabled="savingThreshold" @click="removeThreshold(row as RunnerAlertThreshold)">恢复默认</BaseButton></template>
            <template #empty>{{ loadingSettings ? '加载中…' : '暂无覆盖阈值' }}</template>
          </BaseTable>
        </div>
      </BaseCard>

      <BaseCard title="平台飞书目标">
        <p class="text-sm text-muted mb-4">仅用于 Runner 长时运行告警；群消息不包含原问题、用户或模型上下文。</p>
        <p v-if="targetError" role="alert" class="text-sm text-danger-700 mb-3">{{ targetError }}</p>
        <div class="flex flex-wrap items-center gap-2 mb-4">
          <BaseBadge :intent="target ? 'success' : 'neutral'">{{ target ? '已配置' : '未配置' }}</BaseBadge>
          <span v-if="target" class="text-sm text-muted break-all">{{ target.configuration_display || '已配置飞书 Webhook' }}</span>
          <BaseButton v-if="target" intent="danger-ghost" :disabled="savingTarget" @click="showRevokeConfirm = true">撤销</BaseButton>
        </div>
        <label class="form-field block mb-3">
          <span class="form-label">{{ target ? '替换 Webhook 地址' : '飞书群机器人 Webhook 地址' }}</span>
          <BaseInput v-model="webhookUrl" type="url" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…" />
        </label>
        <BaseButton :disabled="savingTarget || !webhookUrl.trim()" @click="saveTarget">
          {{ savingTarget ? '保存中…' : target ? '替换目标' : '配置目标' }}
        </BaseButton>
      </BaseCard>
    </div>
    <div v-else class="flex justify-end">
      <BaseButton size="sm" intent="ghost" @click="openConfig">告警配置…</BaseButton>
    </div>

    <BaseModal v-model="showDetail" title="Runner 告警详情" size="xl" scrollable accessible mode="view" :show-fullscreen="false">
      <p v-if="detailError" role="alert" class="text-sm text-danger-700">{{ detailError }}</p>
      <p v-else-if="detailLoading" class="text-sm text-muted">加载中…</p>
      <div v-else-if="detail" class="space-y-5 text-sm">
        <dl class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div><dt class="text-muted">Runner ID</dt><dd class="font-mono break-all">{{ detail.alert.runner_id }}</dd></div>
          <div><dt class="text-muted">租户 / 数字员工</dt><dd class="break-all">{{ detail.alert.tenant_id || '未归属租户' }} / {{ detail.alert.profile_id || 'main' }}</dd></div>
          <div><dt class="text-muted">本次领取</dt><dd>{{ formatTime(detail.alert.started_at) }}</dd></div>
          <div><dt class="text-muted">触发告警</dt><dd>{{ formatTime(detail.alert.alerted_at) }}</dd></div>
          <div><dt class="text-muted">告警结束</dt><dd>{{ formatTime(detail.alert.ended_at) }}</dd></div>
          <div><dt class="text-muted">最近模型事实保存时间</dt><dd>{{ formatTime(detail.runner.model_saved_at) }}</dd></div>
        </dl>
        <section><h3 class="font-medium text-default mb-2">受理时原问题（原始输入）</h3><p class="whitespace-pre-wrap break-words">{{ detail.runner.request_text || '暂无已保存内容' }}</p></section>
        <p v-if="detail.runner.model_unavailable_reason === 'CURRENT_ATTEMPT_CHANGED'" class="text-warning-700">当前快照已更新，该次上下文不可用。</p>
        <p v-if="detail.runner.context_redacted" class="text-xs text-muted">以下是脱敏摘录：敏感字段已遮盖，普通长字符串仅保留前 200 字符。</p>
        <section><h3 class="font-medium text-default mb-2">最近模型返回（脱敏摘录）</h3><pre class="whitespace-pre-wrap break-words bg-surface-hover p-3 rounded">{{ detail.runner.latest_model_response || '暂无已保存内容' }}</pre></section>
        <section><h3 class="font-medium text-default mb-2">模型上下文（脱敏摘录）</h3><pre class="whitespace-pre-wrap break-words bg-surface-hover p-3 rounded">{{ displayContext(detail.runner.latest_model_context) }}</pre></section>
        <p class="text-xs text-muted">以上是最近已保存的事实；缺失内容不会推断为当前执行步骤。</p>
      </div>
    </BaseModal>

    <BaseModal v-model="showRevokeConfirm" title="撤销平台飞书目标" size="sm" accessible :show-fullscreen="false">
      <p class="text-sm text-default">撤销后，新告警不会发往这个飞书群。已生成的投递按服务端状态保留。</p>
      <template #footer>
        <BaseButton intent="secondary" @click="showRevokeConfirm = false">取消</BaseButton>
        <BaseButton intent="danger" :disabled="savingTarget" @click="revokeTarget">确认撤销</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
/**
 * 系统健康页内嵌的 Runner 运行告警区段（2026-11-23 自独立 Runner 告警页合并）。
 * 职责：告警列表（当前/历史）+ 详情弹窗 + 运维配置（阈值覆盖 / 平台飞书目标，默认折叠）。
 * /portal/runner-alerts 旧路由保留为重定向（飞书告警详情链接依赖该路径），
 * 携带 runner_id query 进入时自动打开对应告警详情。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import { formatDateTimeWithSeconds } from '@/utils/date'
import { runnerAlertsAPI, type RunnerAlert, type RunnerAlertDetail, type RunnerAlertTarget, type RunnerAlertThreshold } from '@/api/runnerAlerts'

const route = useRoute()
const router = useRouter()
const toast = useToast()

const state = ref<'current' | 'history'>('current')
const page = ref(1)
const pageSize = ref(20)
const alerts = ref<RunnerAlert[]>([])
const total = ref(0)
const loading = ref(false)
const listError = ref('')
const runnerFilter = computed(() => typeof route.query.runner_id === 'string' ? route.query.runner_id : '')

const alertColumns: TableColumn[] = [
  { key: 'runner_id', label: 'Runner ID', width: '235px' },
  { key: 'tenant_id', label: '租户', width: '150px' },
  { key: 'profile_id', label: '数字员工', width: '145px' },
  { key: 'elapsed_seconds', label: '执行时长', width: '100px' },
  { key: 'alerted_at', label: '告警时间', width: '170px' },
  { key: 'ended_at', label: '状态', width: '100px' },
  { key: 'actions', label: '操作', width: '85px' },
]
const thresholdColumns: TableColumn[] = [
  { key: 'tenant_id', label: '租户 ID', width: '165px' },
  { key: 'profile_id', label: '数字员工 ID', width: '165px' },
  { key: 'minutes', label: '分钟', width: '75px' },
  { key: 'actions', label: '操作', width: '95px' },
]

function formatTime(value: string | null | undefined) { return value ? formatDateTimeWithSeconds(value) : '暂无记录' }
function formatDuration(seconds: number) { return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` }
function displayContext(value: unknown) { return value == null ? '暂无已保存内容' : typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : '请求失败，请稍后重试' }

async function loadAlerts() {
  loading.value = true
  listError.value = ''
  try {
    const result = await runnerAlertsAPI.list({ state: runnerFilter.value ? 'all' : state.value, page: page.value, page_size: pageSize.value, runner_id: runnerFilter.value || undefined })
    alerts.value = result.items
    total.value = result.total
    if (runnerFilter.value && page.value === 1 && result.items.length) await openDetail(result.items[0].alert_id)
  } catch (error) {
    listError.value = errorMessage(error)
    alerts.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function selectState(next: 'current' | 'history') { state.value = next; page.value = 1; void loadAlerts() }
function clearRunnerFilter() { void router.replace({ query: { ...route.query, runner_id: undefined } }) }
watch(runnerFilter, () => { page.value = 1; void loadAlerts() })

const showDetail = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detail = ref<RunnerAlertDetail | null>(null)
async function openDetail(alertId: string) {
  showDetail.value = true
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  try { detail.value = await runnerAlertsAPI.detail(alertId) }
  catch (error) { detailError.value = errorMessage(error) }
  finally { detailLoading.value = false }
}

// ==================== 告警配置（默认折叠） ====================

const showConfig = ref(false)
const loadingSettings = ref(false)
const defaultMinutes = ref(30)
const thresholds = ref<RunnerAlertThreshold[]>([])
const thresholdRows = computed(() => thresholds.value.map(item => ({ ...item, key: `${item.tenant_id}:${item.profile_id}` })))
const thresholdError = ref('')
const thresholdTenant = ref('')
const thresholdProfile = ref('')
const thresholdMinutes = ref('30')
const savingThreshold = ref(false)

function openConfig() {
  showConfig.value = true
  if (!thresholds.value.length) void loadThresholds()
  if (target.value === null && !targetError.value) void loadTarget()
}

async function loadThresholds() {
  loadingSettings.value = true
  thresholdError.value = ''
  try {
    const result = await runnerAlertsAPI.thresholds()
    defaultMinutes.value = result.default_minutes
    thresholds.value = result.overrides
  } catch (error) { thresholdError.value = errorMessage(error) }
  finally { loadingSettings.value = false }
}
async function saveThreshold() {
  const minutes = Number(thresholdMinutes.value)
  if (!thresholdTenant.value.trim() || !thresholdProfile.value.trim() || !Number.isInteger(minutes) || minutes < 1) {
    thresholdError.value = '请填写租户 ID、数字员工 ID 和正整数分钟数。'
    return
  }
  savingThreshold.value = true
  thresholdError.value = ''
  try {
    await runnerAlertsAPI.saveThreshold({ tenant_id: thresholdTenant.value.trim(), profile_id: thresholdProfile.value.trim(), minutes })
    toast.success('阈值已保存')
    await loadThresholds()
  } catch (error) { thresholdError.value = errorMessage(error) }
  finally { savingThreshold.value = false }
}
async function removeThreshold(item: RunnerAlertThreshold) {
  savingThreshold.value = true
  thresholdError.value = ''
  try {
    await runnerAlertsAPI.deleteThreshold(item.tenant_id, item.profile_id)
    toast.success('已恢复平台默认阈值')
    await loadThresholds()
  } catch (error) { thresholdError.value = errorMessage(error) }
  finally { savingThreshold.value = false }
}

const target = ref<RunnerAlertTarget | null>(null)
const targetError = ref('')
const webhookUrl = ref('')
const savingTarget = ref(false)
const showRevokeConfirm = ref(false)
async function loadTarget() {
  targetError.value = ''
  try { target.value = (await runnerAlertsAPI.target()).target }
  catch (error) { targetError.value = errorMessage(error) }
}
async function saveTarget() {
  if (!webhookUrl.value.trim()) return
  savingTarget.value = true
  targetError.value = ''
  try {
    target.value = (await runnerAlertsAPI.saveTarget(webhookUrl.value.trim(), target.value?.binding_version)).target
    webhookUrl.value = ''
    toast.success('平台飞书目标已保存')
  } catch (error) { targetError.value = errorMessage(error) }
  finally { savingTarget.value = false }
}
async function revokeTarget() {
  if (!target.value) return
  savingTarget.value = true
  targetError.value = ''
  try {
    await runnerAlertsAPI.deleteTarget(target.value.binding_version)
    target.value = null
    showRevokeConfirm.value = false
    toast.success('平台飞书目标已撤销')
  } catch (error) { targetError.value = errorMessage(error); showRevokeConfirm.value = false }
  finally { savingTarget.value = false }
}

onMounted(() => { void loadAlerts() })
</script>

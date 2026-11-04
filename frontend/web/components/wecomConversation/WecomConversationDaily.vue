<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="企微日报"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="goCenter(); closeMenu()">
          会话记录
        </button>
        <button class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="reload(); closeMenu()">
          刷新
        </button>
      </template>
    </AppHeader>

    <main class="flex-1 overflow-y-auto">
      <div class="px-4 py-2.5 border-b border-default bg-surface flex items-center gap-3 flex-wrap sticky top-0 z-10">
        <input v-model="businessDate" type="date" class="px-2 py-1.5 bg-surface border border-default rounded-lg text-sm text-default" @change="reload" />
        <BaseBadge intent="neutral">{{ businessDate }}</BaseBadge>
        <select
          v-model="selectedPlanId"
          class="px-2 py-1.5 bg-surface border border-default rounded-lg text-sm text-default max-w-56"
          @change="reload"
        >
          <option value="">全部会话（仅查看）</option>
          <option v-for="plan in plans" :key="plan.task_id" :value="plan.task_id">
            {{ plan.name }}（{{ plan.conversation_count }} 个会话）
          </option>
        </select>
        <div class="flex-1" />
        <BaseButton
          size="sm" intent="secondary" :disabled="!canGenerate || generating"
          :title="canGenerate ? '' : '先选择一个日报计划；没有计划时请在代理会话中建立'"
          @click="generate(false)"
        >生成/重算该日</BaseButton>
        <BaseButton size="sm" :disabled="!canGenerate || generating" @click="generate(true)">总结今天（截至现在）</BaseButton>
      </div>

      <div v-if="!plans.length && !loadingPlans" class="px-4 py-2 text-xs text-muted border-b border-default bg-surface">
        当前没有日报计划；生成按钮需要在代理会话中建立日报计划后使用（页面查看不受影响）。
      </div>
      <div v-if="runnerStatus" class="px-4 py-2 text-xs text-muted border-b border-default bg-surface">
        生成任务状态：{{ runnerStatus }}（沿原任务系统，可稍后刷新查看）
      </div>
      <div v-if="generateError" class="px-4 py-2 text-xs text-danger border-b border-default">{{ generateError }}</div>
      <div v-if="lastGeneration && lastGeneration.failed.length" class="px-4 py-2 text-xs text-warning-700 border-b border-default bg-surface">
        本次执行（{{ lastGeneration.status || '?' }}）完成 {{ lastGeneration.completed.length }} 个会话、
        失败 {{ lastGeneration.failed.length }} 个（{{ lastGeneration.failed.map(f => f.name).join('、') }}）；
        失败会话保留旧报告，可重算。
      </div>
      <div v-else-if="lastGeneration && lastGeneration.status === 'failed' && !lastGeneration.completed.length" class="px-4 py-2 text-xs text-danger border-b border-default bg-surface">
        本次执行全部失败；当前页面展示的是历史成功报告（读取截至时间见各会话）。
      </div>

      <div class="max-w-4xl mx-auto p-4 space-y-4">
        <p v-if="loading && !reports.length" class="text-sm text-muted text-center mt-8">加载中…</p>
        <p v-else-if="!reports.length" class="text-sm text-muted text-center mt-8">
          暂无记录会话；智能体成功回复或建立日报计划后自动出现。
        </p>

        <section
          v-for="entry in visibleReports"
          :key="entry.conversation_id"
          class="border border-default rounded-xl bg-surface overflow-hidden"
          :data-conversation="entry.conversation_id"
        >
          <header class="px-4 py-3 border-b border-default flex items-center gap-2 flex-wrap bg-surface-hover/40">
            <h3 class="text-sm font-medium text-default m-0">{{ entry.name || `会话 #${entry.conversation_id}` }}</h3>
            <BaseBadge v-if="entry.summary && failedConversationIds.has(entry.conversation_id)" intent="warning">本次未完成，当前展示旧报告</BaseBadge>
            <BaseBadge v-else-if="entry.summary && completedConversationIds.has(entry.conversation_id)" intent="success">本次已生成</BaseBadge>
            <BaseBadge v-else-if="entry.summary" intent="success">已生成</BaseBadge>
            <BaseBadge v-else intent="warning">该日无报告</BaseBadge>
            <span class="text-xs text-muted">{{ coverageText(entry) }}</span>
          </header>

          <div v-if="!entry.summary" class="px-4 py-6 text-sm text-muted text-center">
            该会话此日期暂无报告（可能尚未生成，或本次生成失败；已生成的历史日期可切换日期查看）。
          </div>
          <div v-else class="px-4 py-3 space-y-3 text-sm text-default">
            <div>
              <h4 class="text-xs font-medium text-muted mb-1">当日概览</h4>
              <p class="whitespace-pre-wrap m-0">{{ entry.summary.overview || '—' }}</p>
            </div>
            <div v-if="entry.summary.categories?.length">
              <h4 class="text-xs font-medium text-muted mb-1">咨询与问题分类</h4>
              <ul class="m-0 pl-4 space-y-1">
                <li v-for="(item, index) in entry.summary.categories" :key="index">
                  {{ item.topic }}<template v-if="item.count"> ×{{ item.count }}</template>：{{ item.detail }}
                </li>
              </ul>
            </div>
            <div v-if="entry.summary.handled?.length">
              <h4 class="text-xs font-medium text-muted mb-1">已处理事项</h4>
              <ul class="m-0 pl-4 space-y-1">
                <li v-for="(item, index) in entry.summary.handled" :key="index">
                  {{ item.topic }}：{{ item.detail }} <span class="text-xs text-muted">
                  证据：<button
                    v-for="msgId in evidenceIds(item)" :key="msgId"
                    class="underline decoration-dotted hover:text-primary-700 mr-0.5"
                    :title="`在会话记录中定位消息 ${msgId}`"
                    @click="jumpToMessage(entry.conversation_id, msgId)"
                  >{{ msgId }}</button>
                </span>
                </li>
              </ul>
            </div>
            <div v-if="entry.summary.pending_human?.length">
              <h4 class="text-xs font-medium text-muted mb-1">待人工跟进</h4>
              <ul class="m-0 pl-4 space-y-1">
                <li v-for="(item, index) in entry.summary.pending_human" :key="index">
                  {{ item.topic }}：{{ item.detail }} <span class="text-xs text-muted">
                  证据：<button
                    v-for="msgId in evidenceIds(item)" :key="msgId"
                    class="underline decoration-dotted hover:text-primary-700 mr-0.5"
                    :title="`在会话记录中定位消息 ${msgId}`"
                    @click="jumpToMessage(entry.conversation_id, msgId)"
                  >{{ msgId }}</button>
                </span>
                </li>
              </ul>
            </div>
            <div v-if="entry.summary.noteworthy?.length">
              <h4 class="text-xs font-medium text-muted mb-1">值得关注</h4>
              <ul class="m-0 pl-4 space-y-1">
                <li v-for="(item, index) in entry.summary.noteworthy" :key="index">
                  {{ item.topic }}：{{ item.detail }} <span class="text-xs text-muted">
                  证据：<button
                    v-for="msgId in evidenceIds(item)" :key="msgId"
                    class="underline decoration-dotted hover:text-primary-700 mr-0.5"
                    :title="`在会话记录中定位消息 ${msgId}`"
                    @click="jumpToMessage(entry.conversation_id, msgId)"
                  >{{ msgId }}</button>
                </span>
                </li>
              </ul>
            </div>
            <p v-if="entry.summary.notes" class="text-xs text-muted m-0">说明：{{ entry.summary.notes }}</p>
          </div>
        </section>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import {
  dailyReports, generateDailyReport, getRunner, listDailyPlans,
  type DailyGenerationFact, type DailyPlan, type DailyReportEntry, type SummaryItem,
} from '@/api/wecomConversation'

const route = useRoute()
const router = useRouter()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : null)

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
function handleLogout() {
  tenantLogout()
}
function goCenter() {
  router.push({ name: 'tenant-wecom-conversations' })
}

/** 业务时区本地日期（评审 P2-17）：不用 UTC toISOString 造成 00:00–08:00 差一天。 */
function localDate(tz?: string): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: tz || 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(new Date())
  } catch {
    return new Date().toLocaleDateString('en-CA')
  }
}

const plans = ref<DailyPlan[]>([])
const loadingPlans = ref(false)
const selectedPlanId = ref(typeof route.query.task_id === 'string' ? route.query.task_id : '')
const selectedPlan = computed(() => plans.value.find(plan => plan.task_id === selectedPlanId.value) ?? null)
const canGenerate = computed(() => selectedPlan.value !== null)

const businessDate = ref(typeof route.query.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(route.query.date)
  ? route.query.date : localDate())
const reports = ref<DailyReportEntry[]>([])
const lastGeneration = ref<DailyGenerationFact | null>(null)
/** 本次逐会话结果（评审三 P1-4）：按稳定 conversation_id 匹配，不按名称猜。 */
const failedConversationIds = computed(() =>
  new Set((lastGeneration.value?.failed ?? []).map(item => item.conversation_id)))
const completedConversationIds = computed(() =>
  new Set((lastGeneration.value?.completed ?? []).map(item => item.conversation_id)))
const loading = ref(false)
const generating = ref(false)
const generateError = ref('')
const runnerStatus = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

/** 异步响应绑定发起时日期/计划（评审 P2-18）。 */
let requestSeq = 0

const visibleReports = computed(() => {
  if (!selectedPlan.value) return reports.value
  const wanted = new Set(
    reports.value.filter(entry => entry.daily_plan_task_id === selectedPlan.value!.task_id)
      .map(entry => entry.conversation_id))
  return reports.value.filter(entry => wanted.has(entry.conversation_id))
})

function coverageText(entry: DailyReportEntry): string {
  const coverage = (entry.coverage || {}) as { day_count?: number; background_count?: number }
  const parts: string[] = []
  if (typeof coverage.day_count === 'number') parts.push(`当日 ${coverage.day_count} 条`)
  if (typeof coverage.background_count === 'number' && coverage.background_count > 0) {
    parts.push(`背景 ${coverage.background_count} 条`)
  }
  if (entry.snapshot_at) parts.push(`读取截至 ${new Date(entry.snapshot_at).toLocaleString()}`)
  if (entry.generated_at) parts.push(`生成于 ${new Date(entry.generated_at).toLocaleString()}`)
  return parts.join(' · ')
}

/** 证据定位（评审 P2-19）：最近 1000 条范围内跳转会话记录页定位，超出范围由该页如实提示。 */
function evidenceIds(item: SummaryItem): number[] {
  return (item.evidence ?? []).slice(0, 8)
}

function jumpToMessage(conversationId: number, msgId: number) {
  router.push({ name: 'tenant-wecom-conversations',
    query: { conversation: String(conversationId), msg: String(msgId) } })
}

async function loadPlans() {
  loadingPlans.value = true
  try {
    const value = await listDailyPlans()
    plans.value = value.plans
    if (!selectedPlanId.value && plans.value.length) {
      selectedPlanId.value = plans.value[0]!.task_id
    }
  } finally {
    loadingPlans.value = false
  }
}

async function reload() {
  const seq = ++requestSeq
  const date = businessDate.value
  loading.value = true
  try {
    // 按所选计划取本次执行事实（评审三 P1-4）：task_id 范围过滤 + last_generation 消费。
    const value = await dailyReports(date,
      selectedPlanId.value ? { taskId: selectedPlanId.value } : {})
    if (seq !== requestSeq || businessDate.value !== date) return  // 评审 P2-18
    reports.value = value.reports
    lastGeneration.value = value.last_generation ?? null
  } catch (error) {
    if (seq === requestSeq) generateError.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

/** 生成/重算（评审 P1-5）：携带明确计划目标与该计划来源/时区，不做默认兜底。 */
async function generate(todayNow: boolean) {
  const plan = selectedPlan.value
  if (!plan) {
    generateError.value = '请先选择一个日报计划；没有计划时请在代理会话中建立。'
    return
  }
  generating.value = true
  generateError.value = ''
  runnerStatus.value = ''
  try {
    const date = todayNow ? localDate(plan.config.timezone) : businessDate.value
    const submitted = await generateDailyReport({
      business_date: date,
      plan_task_id: plan.task_id,
      timezone: plan.config.timezone,
      client_request_id: `wecom-daily-${plan.task_id}-${date}-${Date.now()}`,
    })
    if (submitted.runner_id) {
      runnerStatus.value = submitted.status || 'queued'
      pollRunner(submitted.runner_id)
    } else {
      runnerStatus.value = '已受理（复用现有任务）'
    }
  } catch (error) {
    generateError.value = error instanceof Error ? error.message : String(error)
  } finally {
    generating.value = false
  }
}

function pollRunner(runnerId: string) {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    const runner = await getRunner(runnerId)
    if (runner?.status) runnerStatus.value = runner.status
    if (runner && ['completed', 'failed', 'cancelled'].includes(runner.status || '')) {
      if (pollTimer) clearInterval(pollTimer)
      pollTimer = null
      await reload()
    }
  }, 5000)
}

onMounted(async () => {
  await loadPlans()
  await reload()
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

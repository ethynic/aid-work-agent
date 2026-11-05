<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader title="企业微信会话每日总结" :is-logged-in="effectiveIsLoggedIn" :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar" @logout="tenantLogout">
      <template #menu-items="{ closeMenu }">
        <button class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="goCenter(); closeMenu()">会话记录</button>
        <button class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="reload(); closeMenu()">刷新</button>
      </template>
    </AppHeader>

    <main class="flex-1 overflow-y-auto p-4 md:p-6">
      <div class="max-w-6xl mx-auto space-y-4">
        <div>
          <p class="text-xs text-muted mb-1">工作台 / 每日总结 / 企业微信会话</p>
          <h1 class="text-xl font-semibold text-default m-0">企业微信会话每日总结</h1>
        </div>
        <div class="bg-surface border border-default rounded-xl overflow-hidden">
          <div class="px-4 py-3 border-b border-default flex items-center gap-2 flex-wrap">
            <span class="text-xs font-medium text-muted mr-2">总结日期</span>
            <BaseButton :intent="dateMode === 'today' ? 'primary' : 'secondary'" @click="chooseDate(today)">今日</BaseButton>
            <BaseButton :intent="dateMode === 'yesterday' ? 'primary' : 'secondary'" @click="chooseDate(yesterday)">昨日</BaseButton>
            <label class="text-xs text-muted flex items-center gap-2">
              选择日期
              <BaseInput v-model="dateInput" type="date" :max="today" class="w-40" aria-label="选择总结日期" @change="chooseDate(dateInput)" />
            </label>
          </div>

          <div class="px-4 py-4 border-b border-default flex items-center gap-3 flex-wrap">
            <div class="flex-1 min-w-48">
              <h2 class="text-base font-semibold text-default m-0">{{ dateTitle }}</h2>
              <p class="text-xs text-muted m-0 mt-1">{{ latestExecution ? `${linkedTaskId ? '通知对应计划最近执行' : '最近执行'}：${executionStatus(latestExecution.status)} · ${formatTime(latestExecution.accepted_at)}` : '当前日期暂无执行记录' }}</p>
            </div>
            <BaseButton v-if="isToday" intent="secondary" :disabled="!planLoaded || !conversationsLoaded" @click="openSchedule">定时执行配置</BaseButton>
            <BaseButton v-if="isToday" data-testid="generate-daily" :disabled="!canGenerate" @click="generate">
              {{ generating ? '正在提交…' : '更新对话总结' }}
            </BaseButton>
          </div>

          <p v-if="pageError" role="alert" class="mx-4 mt-3 text-sm text-danger-700">{{ pageError }}</p>
          <p v-if="scheduleError && !scheduleOpen" role="alert" class="mx-4 mt-3 text-sm text-danger-700">定时配置读取失败：{{ scheduleError }}</p>
          <p v-if="planFeedback" role="status" class="mx-4 mt-3 text-sm text-muted">{{ planFeedback }}</p>
          <p v-if="!conversationsLoaded || loadingReports" class="mx-4 mt-3 text-sm text-muted">正在加载会话和总结…</p>
          <p v-if="isToday && conversationsLoaded && conversations.length && !selectedIds.size" class="mx-4 mt-3 text-xs text-warning-700">请至少选择一个会话后更新。</p>
          <p v-if="generateError" role="alert" class="mx-4 mt-3 text-sm text-danger-700">{{ generateError }}</p>

          <section class="px-4 py-5 border-b border-default" aria-label="本次总结的会话">
            <div class="flex items-center justify-between gap-3 mb-3">
              <h3 class="text-base font-semibold text-default m-0">本次总结的会话</h3>
              <BaseButton v-if="isToday" intent="secondary" @click="openAdd">＋ 添加会话</BaseButton>
            </div>
            <div v-if="conversationsLoaded && !conversations.length" class="text-sm text-muted py-3">
              暂无企业微信会话。点击“添加会话”选择会话，或让智能体接手监控后自动加入。
            </div>
            <div v-else class="flex flex-wrap gap-2">
              <label v-if="isToday && conversationsLoaded" class="inline-flex items-center gap-2 rounded-lg border border-default px-3 py-2 text-xs text-default cursor-pointer">
                <input type="checkbox" :checked="allSelected" @change="toggleAll" />全部会话（{{ conversations.length }}）
              </label>
              <label v-for="conversation in conversations" :key="conversation.id"
                class="inline-flex items-center gap-2 rounded-lg border border-default px-3 py-2 text-xs text-default"
                :class="isToday ? 'cursor-pointer' : ''">
                <input v-if="isToday" type="checkbox" :checked="selectedIds.has(conversation.id)" @change="toggleConversation(conversation.id)" />
                {{ conversation.name || `会话 #${conversation.id}` }}
              </label>
            </div>
          </section>

          <section class="px-4 py-5" aria-label="总结内容">
            <div class="flex items-center justify-between gap-3 mb-3">
              <h3 class="text-lg font-semibold text-default m-0">总结内容</h3>
              <BaseButton intent="ghost" @click="toggleExecutions">{{ showExecutions ? '收起执行记录' : '查看执行记录' }}</BaseButton>
            </div>
            <div v-if="conversationsLoaded && !conversations.length" class="text-sm text-muted py-6 text-center">添加会话后可在这里查看每日总结。</div>
            <div v-else class="space-y-3">
              <article v-for="entry in reports" :key="entry.conversation_id" class="rounded-xl border border-default overflow-hidden bg-surface">
                <header class="px-5 py-4 bg-gray-50 border-b border-default flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <h4 class="text-xl font-semibold text-default m-0 break-words">{{ entry.name || `会话 #${entry.conversation_id}` }}</h4>
                    <p class="text-xs text-muted m-0 mt-1">{{ coverageText(entry) }}</p>
                  </div>
                  <BaseBadge class="shrink-0 whitespace-nowrap" :intent="badgeIntent(targetFor(entry.conversation_id)?.status, !!entry.summary)">{{ targetLabel(targetFor(entry.conversation_id)?.status, !!entry.summary) }}</BaseBadge>
                </header>
                <div v-if="isUnsuccessfulTarget(targetFor(entry.conversation_id)?.status)"
                  class="mx-5 mt-4 rounded-lg border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-700">
                  <p class="m-0">本次执行：{{ targetReason(targetFor(entry.conversation_id), displayExecution).label }}
                    <span v-if="targetReason(targetFor(entry.conversation_id), displayExecution).code"> · 错误码 {{ targetReason(targetFor(entry.conversation_id), displayExecution).code }}</span>
                    <span v-if="entry.summary && entry.source_runner_id !== displayExecution?.runner_id">；下方为此前保存的成功报告。</span>
                  </p>
                  <details class="mt-1 text-xs text-muted"><summary class="cursor-pointer">查看执行时间和诊断编号</summary>
                    <p class="m-0 mt-1 break-all">阶段 {{ targetFor(entry.conversation_id)?.phase }} · {{ formatTime(targetFor(entry.conversation_id)?.finished_at || targetFor(entry.conversation_id)?.started_at) }} · 执行 {{ displayExecution?.runner_id }}</p>
                  </details>
                </div>
                <p v-if="!entry.summary" class="text-sm text-muted m-0 px-5 py-6">该日期暂无已保存报告。</p>
                <div v-else class="px-5 py-5 md:px-7 text-default">
                  <section class="md:grid md:grid-cols-[7rem_minmax(0,1fr)] gap-5 pb-5">
                    <h5 class="text-sm font-semibold text-default m-0 mb-2 md:mb-0">当日概览</h5>
                    <ol v-if="Array.isArray(entry.summary.overview)" class="m-0 pl-6 list-decimal space-y-2 text-[15px] leading-7">
                      <li v-for="(item, index) in entry.summary.overview" :key="index">{{ item }}</li>
                    </ol>
                    <p v-else class="text-[15px] leading-7 whitespace-pre-wrap m-0">{{ entry.summary.overview || '—' }}</p>
                  </section>
                  <section v-for="section in summarySections(entry.summary)" :key="section.key"
                    class="md:grid md:grid-cols-[7rem_minmax(0,1fr)] gap-5 border-t border-default py-5 last:pb-0">
                    <h5 class="text-sm font-semibold text-default m-0 mb-2 md:mb-0">{{ section.title }}</h5>
                    <component :is="section.key === 'pending_human' ? 'ol' : 'ul'" class="m-0 space-y-2"
                      :class="section.key === 'pending_human' ? 'pl-6 list-decimal' : 'pl-5'">
                      <li v-for="(item, index) in section.items" :key="index" class="text-[15px] leading-7"
                        :class="section.key === 'anomalies' ? 'list-none -ml-5 pl-3 border-l-[3px] border-warning-500' : ''">
                        <template v-if="item.topic">{{ item.topic }}<template v-if="item.count"> ×{{ item.count }}</template>：</template>{{ item.detail }}
                        <span v-if="item.evidence?.length" class="text-xs text-muted ml-2">证据
                          <button v-for="msgId in evidenceIds(item)" :key="msgId" class="underline hover:text-primary-700 mr-1"
                            :title="`在会话记录中定位消息 ${msgId}`" @click="jumpToMessage(entry.conversation_id, msgId)">{{ msgId }}</button>
                        </span>
                      </li>
                    </component>
                  </section>
                  <p v-if="entry.summary.notes" class="text-xs text-muted m-0 pt-5">说明：{{ entry.summary.notes }}</p>
                  <details v-if="entry.source_runner_id" class="text-xs text-muted mt-4"><summary class="cursor-pointer">报告信息</summary>
                    <p class="m-0 mt-1 break-all">已保存报告 · 执行 {{ entry.source_runner_id }} · 生成于 {{ formatTime(entry.generated_at) }}</p>
                  </details>
                </div>
              </article>
            </div>
          </section>
        </div>

        <section v-if="showExecutions" class="bg-surface border border-default rounded-xl p-4" aria-label="执行记录">
          <h3 class="text-sm font-semibold text-default mt-0">执行记录</h3>
          <p v-if="executionError" role="alert" class="text-sm text-danger-700">{{ executionError }}</p>
          <p v-if="loadingExecutions" class="text-sm text-muted">正在加载执行记录…</p>
          <p v-if="!loadingExecutions && !executions.length" class="text-sm text-muted">该日期暂无执行记录。</p>
          <div v-for="execution in executions" :key="execution.runner_id" class="border-t border-default py-3 text-sm">
            <div class="flex items-center justify-between gap-2 flex-wrap">
              <div><strong>{{ executionStatus(execution.status) }}</strong> · {{ formatTime(execution.accepted_at) }} · {{ execution.target_count }} 个会话
                <span class="text-xs text-muted">{{ execution.trigger === 'manual' ? '手动' : '定时' }}</span></div>
              <BaseButton intent="ghost" @click="openExecution(execution.runner_id)">查看详情</BaseButton>
            </div>
            <div v-if="expandedRunnerId === execution.runner_id" class="mt-2 text-xs text-muted space-y-2">
              <p v-if="detailError" role="alert" class="text-danger-700">{{ detailError }}</p>
              <p v-else-if="!executionDetail">正在加载详情…</p>
              <div v-for="target in executionDetail?.targets || []" :key="target.conversation_id" class="flex items-start justify-between gap-2 border-t border-default pt-2">
                <span>{{ target.name || `会话 #${target.conversation_id}` }} · {{ targetLabel(target.status, false) }} · {{ targetReason(target, executionDetail).label }}
                  <template v-if="targetReason(target, executionDetail).code"> · 错误码 {{ targetReason(target, executionDetail).code }}</template>
                  · 阶段 {{ target.phase }}</span>
                <BaseButton intent="ghost" @click="copyDiagnostic(target.diagnostic)">复制诊断信息</BaseButton>
              </div>
            </div>
          </div>
          <BaseButton v-if="executionCursor" intent="secondary" :disabled="loadingExecutions" @click="loadExecutions(true)">加载更多</BaseButton>
        </section>
      </div>
    </main>

    <BaseModal v-model="addOpen" title="添加企业微信会话" size="lg" accessible :show-fullscreen="false">
      <div class="space-y-3">
        <p class="text-xs text-muted m-0">{{ directoryIdentity || '正在连接当前选定设备与企微账号…' }}</p>
        <div class="flex gap-2"><BaseInput v-model="directoryQuery" placeholder="搜索会话名称" maxlength="100" aria-label="搜索会话名称" @keyup.enter="searchDirectory" />
          <BaseButton intent="secondary" @click="searchDirectory">搜索</BaseButton></div>
        <p v-if="directoryError" role="alert" class="text-sm text-danger-700">{{ directoryError }}</p>
        <p v-if="loadingDirectory" class="text-sm text-muted">正在读取本地目录…</p>
        <div class="max-h-72 overflow-y-auto rounded-lg border border-default divide-y divide-default">
          <div v-for="item in directoryItems" :key="item.session" class="px-3 py-2 text-sm flex items-start gap-2">
            <input type="checkbox" class="mt-1" :checked="directorySelected.has(item.session)" :disabled="item.added"
              :aria-label="`选择${item.name || shortSession(item.session)}`" @change="toggleDirectory(item.session)" />
            <div class="flex-1 min-w-0"><div class="flex items-center gap-2"><span class="truncate text-default">{{ item.name || '未命名会话' }}</span>
              <span class="text-xs text-muted">{{ item.added ? '已添加' : kindLabel(item.kind) }}</span></div>
              <span class="text-xs text-muted">{{ shortSession(item.session) }}<template v-if="item.kind !== 'group'"> · {{ item.peer_platform }}</template></span>
              <BaseButton v-if="item.same_name && !item.added" intent="ghost" @click="preview(item.session)">查看最近 3 条</BaseButton>
              <p v-if="previews[item.session]" class="text-xs text-muted m-0 whitespace-pre-wrap">{{ previews[item.session] }}</p>
            </div>
          </div>
          <p v-if="!loadingDirectory && !directoryItems.length" class="text-sm text-muted px-3 py-5 m-0">当前目录没有匹配会话，空会话也可添加。</p>
        </div>
        <BaseButton v-if="directoryCursor" intent="secondary" :disabled="loadingDirectory" @click="loadDirectory(true)">加载更多</BaseButton>
      </div>
      <template #footer>
        <div class="flex items-center justify-between gap-2 w-full"><span class="text-xs text-muted">已选择 {{ directorySelected.size }} 个会话</span>
          <div class="flex gap-2"><BaseButton intent="secondary" @click="addOpen = false">取消</BaseButton>
            <BaseButton :disabled="!directorySelected.size || adding" @click="confirmAdd">{{ adding ? '添加中…' : '添加会话' }}</BaseButton></div></div>
      </template>
    </BaseModal>

    <BaseModal v-model="scheduleOpen" title="定时执行配置" accessible :show-fullscreen="false" :close-on-overlay="true"
      class="!items-stretch !justify-end" content-class="!max-w-md !max-h-screen !h-full !rounded-none">
          <div class="space-y-5 text-sm">
            <p class="text-xs text-muted m-0">一份配置管理当前企业微信账号的自动总结。</p>
            <p v-if="scheduleError" role="alert" class="text-danger-700">{{ scheduleError }}</p>
            <p v-if="planLoading || !conversationsLoaded" class="text-muted">正在读取配置和会话…</p>
            <template v-else>
              <div v-if="plan" class="flex items-center justify-between"><span>自动总结：{{ plan.status === 'active' ? '已开启' : '已暂停' }}</span>
                <BaseButton intent="secondary" :disabled="planBusy" @click="togglePlan">{{ plan.status === 'active' ? '暂停' : '恢复' }}</BaseButton></div>
              <div><label class="block text-xs font-medium text-muted mb-1">总结日期</label>
                <BaseSelect v-model="planDay"><option value="yesterday">前一天</option><option value="today">今天</option></BaseSelect></div>
              <div v-if="planDay === 'yesterday'"><label class="block text-xs font-medium text-muted mb-1">每天执行时间（北京时间）</label>
                <BaseInput v-model="planTime" type="time" aria-label="每天执行时间" /></div>
              <div v-else><label class="block text-xs font-medium text-muted mb-1">每隔多少小时</label>
                <BaseSelect v-model="planInterval"><option v-for="hour in 24" :key="hour" :value="String(hour)">{{ hour }} 小时</option></BaseSelect></div>
              <div class="border-t border-default pt-4"><p class="font-medium text-default mt-0 mb-2">自动总结的会话</p>
                <label class="flex gap-2 mb-2"><input v-model="planScope" type="radio" value="all" />动态全部会话</label>
                <label class="flex gap-2 mb-2"><input v-model="planScope" type="radio" value="selected" />自选会话</label>
                <div v-if="planScope === 'selected'" class="max-h-52 overflow-y-auto border border-default rounded-lg p-2 space-y-2">
                  <label v-for="item in conversations" :key="item.id" class="flex gap-2 text-xs"><input type="checkbox" :checked="planSelected.has(item.id)" @change="togglePlanConversation(item.id)" />{{ item.name || `会话 #${item.id}` }}</label>
                  <p v-if="!conversations.length" class="text-xs text-muted m-0">请先添加会话。</p>
                </div>
              </div>
              <p class="text-xs text-muted">下次预计执行：{{ plan?.status === 'paused' ? '恢复后重新计算' : plan?.next_due_at ? formatDue(plan.next_due_at) : '保存配置后显示' }}</p>
            </template>
          </div>
      <template #footer><BaseButton intent="secondary" @click="scheduleOpen = false">取消</BaseButton>
        <BaseButton :disabled="planBusy || planLoading || !conversationsLoaded" @click="savePlan">{{ planBusy ? '保存中…' : '保存配置' }}</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import {
  addConversations, dailyReports, generateDailyReport, getDailyExecution, getDailyPlan,
  listDailyExecutions, listDirectory, pauseDailyPlan, previewDirectory,
  resumeDailyPlan, saveDailyPlan, type DailyExecution, type DailyExecutionTarget,
  type DailyPlan, type DailyReportEntry, type DirectoryConversation, type SummaryItem,
} from '@/api/wecomConversation'

const route = useRoute()
const router = useRouter()
const { admin, isLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const effectiveIsLoggedIn = computed(() => isLoggedIn.value)
const effectiveUser = computed(() => admin.value ? { user_id: admin.value.user_id, username: admin.value.username, phone: admin.value.phone } : null)
function handleToggleSidebar() { toggleSidebarFn?.() }
function goCenter() { router.push({ name: 'tenant-wecom-conversations' }) }

function shanghaiDate(offset = 0): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' })
    .format(new Date(Date.now() + offset * 86400000))
}
const today = ref(shanghaiDate())
const yesterday = ref(shanghaiDate(-1))
const initialDate = typeof route.query.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(route.query.date)
  && route.query.date <= today.value ? route.query.date : today.value
const businessDate = ref(initialDate)
const dateInput = ref(initialDate)
const linkedTaskId = ref(typeof route.query.task_id === 'string' ? route.query.task_id : '')
const dateMode = computed(() => businessDate.value === today.value ? 'today' : businessDate.value === yesterday.value ? 'yesterday' : 'other')
const isToday = computed(() => dateMode.value === 'today')
const dateTitle = computed(() => `${isToday.value ? '今天' : dateMode.value === 'yesterday' ? '昨天' : '所选日期'} · ${businessDate.value}`)

const conversations = ref<{ id: number; name: string | null; daily_plan_task_id: string | null }[]>([])
const conversationsLoaded = ref(false)
const selectedIds = ref(new Set<number>())
const reports = ref<DailyReportEntry[]>([])
const loadingReports = ref(false)
const reportError = ref('')
const pageError = computed(() => reportError.value)
const generateError = ref('')
const generating = ref(false)
const latestExecution = ref<DailyExecution | null>(null)
const activeExecution = ref<DailyExecution | null>(null)
const activeRunnerId = ref<string | null>(null)
const displayExecution = computed(() => activeExecution.value || latestExecution.value)
const allSelected = computed(() => conversations.value.length > 0 && selectedIds.value.size === conversations.value.length)
const canGenerate = computed(() => isToday.value && conversationsLoaded.value && !loadingReports.value
  && !pageError.value && selectedIds.value.size > 0 && !generating.value)
let reportSeq = 0
let pollTimer: ReturnType<typeof setInterval> | null = null
let dateTimer: ReturnType<typeof setInterval> | null = null
let dateSelectionSeq = 0

function chooseDate(date: string, taskId = '') {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date > today.value) return
  dateSelectionSeq++
  businessDate.value = date
  dateInput.value = date
  linkedTaskId.value = taskId
  reports.value = []
  latestExecution.value = null
  activeRunnerId.value = null
  activeExecution.value = null
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
  reloadReports()
  if (showExecutions.value) loadExecutions(false)
}
watch(() => [route.query.date, route.query.task_id], ([date, taskId]) => {
  if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date) || date > today.value) {
    if (businessDate.value !== today.value || linkedTaskId.value) chooseDate(today.value)
    return
  }
  const linked = typeof taskId === 'string' ? taskId : ''
  if (date !== businessDate.value || linked !== linkedTaskId.value) chooseDate(date, linked)
})
function toggleConversation(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  selectedIds.value = next
}
function toggleAll() { selectedIds.value = allSelected.value ? new Set() : new Set(conversations.value.map(item => item.id)) }

async function reloadReports() {
  const seq = ++reportSeq
  const date = businessDate.value
  loadingReports.value = true
  reportError.value = ''
  try {
    const rows: DailyReportEntry[] = []
    let cursor: number | undefined
    let generation: DailyExecution | null = null
    while (true) {
      const page = await dailyReports(date, { cursor, limit: 200, taskId: linkedTaskId.value || undefined })
      if (seq !== reportSeq || businessDate.value !== date) return
      rows.push(...page.reports)
      if (cursor === undefined) generation = page.last_generation
      if (!page.has_more) break
      if (page.next_cursor == null || page.next_cursor === cursor) throw new Error('报告分页游标无效，请刷新重试')
      cursor = page.next_cursor
    }
    const oldIds = new Set(conversations.value.map(item => item.id))
    const previouslyLoaded = conversationsLoaded.value
    conversations.value = rows.map(item => ({ id: item.conversation_id, name: item.name,
      daily_plan_task_id: item.daily_plan_task_id }))
    selectedIds.value = new Set(rows.filter(item => !previouslyLoaded || !oldIds.has(item.conversation_id)
      || selectedIds.value.has(item.conversation_id)).map(item => item.conversation_id))
    conversationsLoaded.value = true
    reports.value = rows
    latestExecution.value = generation
    if (isToday.value && generation && !['completed', 'failed', 'cancelled'].includes(generation.status) && !activeRunnerId.value) {
      activeRunnerId.value = generation.runner_id
      activeExecution.value = generation
      startActivePolling(generation.runner_id)
    }
  } catch (error) {
    if (seq === reportSeq && businessDate.value === date) reportError.value = errorText(error)
  } finally {
    if (seq === reportSeq) loadingReports.value = false
  }
}
function reload() { void reloadReports(); void loadPlan() }

function startActivePolling(runnerId: string) {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(() => { void refreshActiveRunner(runnerId) }, 5000)
}

async function generate() {
  if (!canGenerate.value) return
  const submittedDate = businessDate.value
  const selectionSeq = dateSelectionSeq
  generating.value = true
  generateError.value = ''
  try {
    const submitted = await generateDailyReport({ conversation_ids: [...selectedIds.value],
      client_request_id: `wecom-daily-${Date.now()}-${Math.random().toString(36).slice(2)}` })
    if (selectionSeq !== dateSelectionSeq || businessDate.value !== submittedDate || !isToday.value) return
    if (!submitted.runner_id) throw new Error('提交成功但未取得执行 ID，请刷新执行记录核对')
    activeRunnerId.value = submitted.runner_id
    activeExecution.value = null
    if (await refreshActiveRunner(submitted.runner_id)) return
    if (selectionSeq !== dateSelectionSeq || businessDate.value !== submittedDate || !isToday.value) return
    startActivePolling(submitted.runner_id)
  } catch (error) { if (selectionSeq === dateSelectionSeq && businessDate.value === submittedDate && isToday.value) generateError.value = errorText(error) }
  finally { generating.value = false }
}
async function refreshActiveRunner(runnerId: string): Promise<boolean> {
  try {
    const value = await getDailyExecution(runnerId)
    if (activeRunnerId.value !== runnerId || !isToday.value) return false
    activeExecution.value = value.execution
    if (['completed', 'failed', 'cancelled'].includes(value.execution.status)) {
      if (pollTimer) clearInterval(pollTimer)
      pollTimer = null
      await reloadReports()
      if (showExecutions.value) await loadExecutions(false)
      if (activeRunnerId.value !== runnerId || !isToday.value) return true
      activeRunnerId.value = null
      activeExecution.value = null
      const next = latestExecution.value
      if (isToday.value && next && next.runner_id !== runnerId && !['completed', 'failed', 'cancelled'].includes(next.status)) {
        activeRunnerId.value = next.runner_id
        activeExecution.value = next
        startActivePolling(next.runner_id)
      }
      return true
    } else {
      await reloadReports()
      return false
    }
  } catch (error) { if (activeRunnerId.value === runnerId) generateError.value = errorText(error); return false }
}

function targetFor(id: number): DailyExecutionTarget | undefined { return displayExecution.value?.targets?.find(item => item.conversation_id === id) }
function targetReason(target?: DailyExecutionTarget, execution?: DailyExecution | null): { label: string; code: string | null } {
  const inheritGlobal = target?.status === 'not_executed'
    || (target?.status === 'interrupted' && execution?.status === 'failed' && !target.error_code)
  const rawCode = target?.error_code || (inheritGlobal ? execution?.error_code : null)
  const code = rawCode && /^[A-Za-z][A-Za-z0-9_]{0,127}$/.test(rawCode) ? rawCode : null
  if (target?.reason_label) return { label: target.reason_label, code }
  if (target?.status === 'completed' || target?.status === 'completed_reconciled') return { label: '报告已保存。', code }
  if (target?.status === 'running') return { label: '正在处理该会话。', code }
  if (target?.status === 'not_started') return { label: '等待处理该会话。', code }
  if (target?.status === 'interrupted' && execution?.status === 'cancelled')
    return { label: '本次执行已取消，当前会话未完成。', code }
  if (target?.status === 'not_executed' && execution?.status === 'cancelled')
    return { label: '本次执行已取消，该会话未执行。', code }
  if (target?.status === 'superseded') return { label: '已有更新报告，本次未覆盖。', code }
  if (target?.status === 'result_unverified') return { label: '报告保存结果待核对。', code }
  const known: Record<string, string> = {
    WECOM_DAY_MODE_UNAVAILABLE: '日期读取结果缺少日期窗口，请核对本地读取能力后重试。',
    WECOM_DAY_READ_INCOMPLETE: '该日会话记录分页未读完，请重试。',
    WECOM_DAY_SNAPSHOT_MISSING: '该日会话记录快照不完整，请重试。',
    WECOM_DAY_READ_FAILED: '该日会话记录读取失败，请稍后重试。',
    WECOM_SUMMARY_INVALID: '总结结果格式不完整，请重试。',
    WECOM_SUMMARY_TRUNCATED: '总结结果被截断，请重试。',
    WECOM_SUMMARY_INPUT_OVERSIZE: '该日内容超出单次总结范围，请稍后核对。',
    DEVICE_UNAVAILABLE: '受理设备当前不可用，请确认设备在线及授权。',
    DEVICE_MISMATCH: '会话来源设备已变化，请确认后重试。',
    WECOM_TASK_DEVICE_CHANGED: '受理设备已变化，本次执行已停止。',
    LOCAL_DEVICE_OFFLINE: '受理设备已离线，请确认后重试。',
    LOCAL_DEVICE_UNSUPPORTED: '受理设备不支持日期读取，请升级后重试。',
    WECOM_SOURCE_MISMATCH: '企微账号或会话来源已变化，本次执行已停止。',
    PLAN_SOURCE_MISMATCH: '会话与计划绑定的企微来源不符，请核对后重试。',
    IDENTITY_UNCONFIRMED: '企微账号或会话身份未能确认，请核对后重试。',
    NOT_LOGGED_IN: '企微账号未登录，请登录后重试。',
    WECOM_NOT_FOUND: '受理的企微账号当前不可用，请核对后重试。',
    CURSOR_UNSUPPORTED: '会话读取来源或游标已变化，请重试。',
    SNAPSHOT_UNSTABLE: '会话记录快照不稳定，请稍后重试。',
  }
  if (target?.status === 'interrupted') {
    const sourceCodes = ['WECOM_SOURCE_MISMATCH', 'PLAN_SOURCE_MISMATCH', 'IDENTITY_UNCONFIRMED',
      'DEVICE_UNAVAILABLE', 'DEVICE_MISMATCH', 'WECOM_TASK_DEVICE_CHANGED', 'LOCAL_DEVICE_OFFLINE',
      'LOCAL_DEVICE_UNSUPPORTED', 'NOT_LOGGED_IN', 'WECOM_NOT_FOUND', 'CURSOR_UNSUPPORTED',
      'SNAPSHOT_UNSTABLE']
    if (execution?.status === 'failed' && code && sourceCodes.includes(code))
      return { label: known[code], code }
    return { label: '执行中断，当前会话未完成。', code }
  }
  if (code && known[code]) return { label: known[code], code }
  if (code?.startsWith('WECOM_DAY_')) return { label: '该日会话记录读取未完成，请稍后重试。', code }
  if (code?.startsWith('WECOM_SUMMARY_')) return { label: '总结生成失败，请稍后重试。', code }
  if (target?.status === 'not_executed') return { label: '本次执行在处理该会话前结束。', code }
  if (target?.status !== 'failed') return { label: '本次执行尚未完成。', code }
  const phases: Record<string, string> = {
    reading: '会话记录读取失败，请稍后重试。',
    dispatching: '会话读取未能启动，请稍后重试。',
    analyzing: '总结生成失败，请稍后重试。',
    saving: '报告保存或状态确认失败，请稍后核对。',
  }
  return { label: phases[target?.phase || ''] || '本次执行未完成，请在执行记录中核对。', code }
}
function isUnsuccessfulTarget(status?: string): boolean {
  return ['failed', 'superseded', 'result_unverified', 'interrupted', 'not_executed'].includes(status || '')
}
function targetLabel(status?: string, hasReport = false): string {
  const labels: Record<string, string> = { not_started: '等待执行', running: '执行中', completed: '本次已生成',
    completed_reconciled: '本次已生成', failed: '本次失败', superseded: '已有更新报告',
    result_unverified: '结果待核对', interrupted: '执行中断', not_executed: '未执行' }
  return status ? labels[status] || status : hasReport ? '已生成' : '该日无报告'
}
function badgeIntent(status?: string, hasReport = false): 'success' | 'warning' | 'neutral' {
  if (status === 'completed' || status === 'completed_reconciled' || (!status && hasReport)) return 'success'
  if (status === 'failed' || status === 'result_unverified' || status === 'interrupted') return 'warning'
  return 'neutral'
}
function executionStatus(status: string): string { return ({ queued: '排队中', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消', paused: '已暂停' } as Record<string, string>)[status] || status }
function formatTime(value?: string | null): string { return value ? new Date(value).toLocaleString('zh-CN') : '—' }
function formatClock(value?: string | null): string { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) : '—' }
function formatReportTime(value: string): string {
  const time = new Date(value)
  const localDate = `${time.getFullYear()}-${String(time.getMonth() + 1).padStart(2, '0')}-${String(time.getDate()).padStart(2, '0')}`
  return localDate === businessDate.value ? formatClock(value) : formatTime(value)
}
function formatDue(value: string): string { return `${value.replace('T', ' ').slice(0, 16)}（北京时间）` }
function coverageText(entry: DailyReportEntry): string {
  const coverage = (entry.coverage || {}) as { day_count?: number }
  const parts: string[] = []
  if (typeof coverage.day_count === 'number') parts.push(`当日 ${coverage.day_count} 条消息`)
  if (entry.generated_at) parts.push(`生成于 ${formatReportTime(entry.generated_at)}`)
  if (entry.snapshot_at) parts.push(`读取截至 ${formatReportTime(entry.snapshot_at)}`)
  return parts.join(' · ')
}
function evidenceIds(item: SummaryItem) { return (item.evidence || []).slice(0, 8) }
function summarySections(summary: DailyReportEntry['summary']) {
  if (Array.isArray(summary?.anomalies)) return [
    { key: 'pending_human', title: '待跟进', items: summary?.pending_human || [] },
    { key: 'anomalies', title: '异常', items: summary.anomalies },
  ].filter(section => section.items.length)
  // 历史报告保留原字段，避免只读日期的既有内容在新版页面消失。
  return [
    { key: 'categories', title: '咨询与问题分类', items: summary?.categories || [] },
    { key: 'handled', title: '已处理事项', items: summary?.handled || [] },
    { key: 'pending_human', title: '待人工跟进', items: summary?.pending_human || [] },
    { key: 'noteworthy', title: '值得关注', items: summary?.noteworthy || [] },
  ].filter(section => section.items.length)
}
function jumpToMessage(conversationId: number, msgId: number) {
  router.push({ name: 'tenant-wecom-conversations', query: { conversation: String(conversationId), msg: String(msgId) } })
}
function errorText(error: unknown): string { return error instanceof Error ? error.message : String(error) }

const addOpen = ref(false)
const directoryQuery = ref('')
const appliedDirectoryQuery = ref('')
const directoryItems = ref<DirectoryConversation[]>([])
const directorySelected = ref(new Set<string>())
const directoryCursor = ref<string | null>(null)
const directoryIdentity = ref('')
const directorySource = ref('')
const directoryError = ref('')
const loadingDirectory = ref(false)
const adding = ref(false)
const previews = ref<Record<string, string>>({})
let directorySeq = 0
function openAdd() {
  addOpen.value = true
  directoryQuery.value = ''
  appliedDirectoryQuery.value = ''
  directorySelected.value = new Set()
  directoryItems.value = []
  directoryCursor.value = null
  directoryIdentity.value = ''
  directorySource.value = ''
  previews.value = {}
  void loadDirectory(false)
}
function searchDirectory() { void loadDirectory(false) }
async function loadDirectory(more: boolean) {
  if (more && !directoryCursor.value) return
  const seq = ++directorySeq
  const cursor = more ? directoryCursor.value || undefined : undefined
  if (!more) {
    appliedDirectoryQuery.value = directoryQuery.value.trim()
    directoryItems.value = []
    directoryCursor.value = null
    directorySelected.value = new Set()
  }
  loadingDirectory.value = true
  directoryError.value = ''
  try {
    const page = await listDirectory({ query: appliedDirectoryQuery.value, cursor, limit: 30 })
    if (seq !== directorySeq || !addOpen.value) return
    const source = `${page.device_id}|${page.self_user_id}|${page.source_generation}`
    if (more && directorySource.value !== source) {
      directorySelected.value = new Set()
      directoryItems.value = []
      directoryCursor.value = null
      throw new Error('设备或企微账号已变化，请重新搜索')
    }
    directorySource.value = source
    directoryIdentity.value = `当前设备 ${shortSession(page.device_id)} · 账号 ${page.self_user_id}`
    directoryItems.value = more ? [...directoryItems.value, ...page.conversations] : page.conversations
    directoryCursor.value = page.next_cursor
  } catch (error) { if (seq === directorySeq) directoryError.value = errorText(error) }
  finally { if (seq === directorySeq) loadingDirectory.value = false }
}
function toggleDirectory(session: string) { const next = new Set(directorySelected.value); if (next.has(session)) next.delete(session); else next.add(session); directorySelected.value = next }
function shortSession(session: string): string { return session.length > 16 ? `${session.slice(0, 8)}…${session.slice(-6)}` : session }
function kindLabel(kind: string) { return kind === 'group' ? '群聊' : kind === 'single' ? '单聊' : kind }
async function preview(session: string) {
  try {
    const value = await previewDirectory(session)
    if (`${value.device_id}|${value.self_user_id}|${value.source_generation}` !== directorySource.value) {
      throw new Error('设备或企微账号已变化，请重新搜索')
    }
    previews.value = { ...previews.value, [session]: value.messages.length
      ? value.messages.map(item => `${item.sender_name || '成员'}：${item.text || '（非文本消息）'}`).join('\n') : '暂无消息，可按会话类型和稳定 ID 区分。' }
  } catch (error) { directoryError.value = errorText(error) }
}
async function confirmAdd() {
  if (!directorySelected.value.size || adding.value) return
  adding.value = true
  directoryError.value = ''
  try {
    await addConversations([...directorySelected.value], { expected_device_id: directorySource.value.split('|')[0],
      expected_source_generation: directorySource.value.split('|')[2] })
    addOpen.value = false
    await reloadReports()
  } catch (error) { directoryError.value = errorText(error) }
  finally { adding.value = false }
}

const plan = ref<DailyPlan | null>(null)
const planLoading = ref(false)
const planLoaded = ref(false)
const planBusy = ref(false)
const planFeedback = ref('')
const scheduleOpen = ref(false)
const scheduleError = ref('')
const planDay = ref<'today' | 'yesterday'>('yesterday')
const planScope = ref<'all' | 'selected'>('all')
const planTime = ref('02:00')
const planInterval = ref('1')
const planSelected = ref(new Set<number>())
async function loadPlan(): Promise<boolean> {
  planLoading.value = true
  planLoaded.value = false
  scheduleError.value = ''
  try { plan.value = (await getDailyPlan()).plan; planLoaded.value = true; return true }
  catch (error) { scheduleError.value = errorText(error); return false }
  finally { planLoading.value = false }
}
function openSchedule(event: MouseEvent) {
  if (event.currentTarget instanceof HTMLElement) event.currentTarget.focus()
  scheduleOpen.value = true
  scheduleError.value = ''
  const current = plan.value
  planDay.value = current?.config.target_day || 'yesterday'
  planScope.value = current?.config.scope_mode || 'all'
  const parts = current?.cron_expression?.split(' ') || []
  planTime.value = parts.length >= 2 ? `${String(parts[1]).padStart(2, '0')}:${String(parts[0]).padStart(2, '0')}` : '02:00'
  planInterval.value = String(current?.interval_seconds ? current.interval_seconds / 3600 : 1)
  planSelected.value = new Set(conversations.value.filter(item => item.daily_plan_task_id === current?.task_id).map(item => item.id))
}
function togglePlanConversation(id: number) { const next = new Set(planSelected.value); if (next.has(id)) next.delete(id); else next.add(id); planSelected.value = next }
async function savePlan() {
  if (planBusy.value || !conversationsLoaded.value) return
  if (planScope.value === 'selected' && !planSelected.value.size) { scheduleError.value = '请至少选择一个自动总结会话'; return }
  if (planDay.value === 'yesterday' && !/^([01]\d|2[0-3]):[0-5]\d$/.test(planTime.value)) {
    scheduleError.value = '请选择有效的北京时间执行时刻'; return
  }
  if (planDay.value === 'today' && (!Number.isInteger(Number(planInterval.value)) ||
      Number(planInterval.value) < 1 || Number(planInterval.value) > 24)) {
    scheduleError.value = '间隔须为 1–24 小时'; return
  }
  planBusy.value = true
  scheduleError.value = ''
  try {
    const [hour, minute] = planTime.value.split(':').map(Number)
    const body = { target_day: planDay.value, scope_mode: planScope.value,
      conversation_ids: planScope.value === 'selected' ? [...planSelected.value] : [],
      ...(planDay.value === 'yesterday' ? { hour, minute } : { interval_hours: Number(planInterval.value) }) }
    plan.value = (await saveDailyPlan(body, !!plan.value)).plan
    planFeedback.value = plan.value.status === 'paused'
      ? '配置已保存，计划仍暂停；如需自动执行，请在配置中点击恢复。' : '定时配置已保存。'
    scheduleOpen.value = false
    await reloadReports()
  } catch (error) { scheduleError.value = errorText(error) }
  finally { planBusy.value = false }
}
async function togglePlan() {
  if (!plan.value || planBusy.value) return
  planBusy.value = true
  scheduleError.value = ''
  try {
    const pausing = plan.value.status === 'active'
    if (pausing) {
      await pauseDailyPlan()
      plan.value = { ...plan.value, status: 'paused', next_due_at: null }
    } else {
      plan.value = (await resumeDailyPlan()).plan
    }
    const verified = await loadPlan()
    planFeedback.value = verified ? (pausing ? '定时执行已暂停。' : '定时执行已恢复。')
      : '操作已受理，配置重查失败，请刷新核对。'
  } catch (error) { scheduleError.value = errorText(error) }
  finally { planBusy.value = false }
}

const showExecutions = ref(false)
const executions = ref<DailyExecution[]>([])
const executionCursor = ref<string | null>(null)
const loadingExecutions = ref(false)
const executionError = ref('')
const expandedRunnerId = ref<string | null>(null)
const executionDetail = ref<DailyExecution | null>(null)
const detailError = ref('')
let executionSeq = 0
let detailSeq = 0
function toggleExecutions() { showExecutions.value = !showExecutions.value; if (showExecutions.value) void loadExecutions(false) }
async function loadExecutions(more: boolean) {
  const seq = ++executionSeq
  const date = businessDate.value
  loadingExecutions.value = true
  executionError.value = ''
  if (!more) { executions.value = []; executionCursor.value = null; expandedRunnerId.value = null }
  try {
    const page = await listDailyExecutions(date, more ? executionCursor.value || undefined : undefined)
    if (seq !== executionSeq || date !== businessDate.value) return
    executions.value = more ? [...executions.value, ...page.executions] : page.executions
    executionCursor.value = page.next_cursor
  } catch (error) { if (seq === executionSeq) executionError.value = errorText(error) }
  finally { if (seq === executionSeq) loadingExecutions.value = false }
}
async function openExecution(runnerId: string) {
  if (expandedRunnerId.value === runnerId) { expandedRunnerId.value = null; return }
  const seq = ++detailSeq
  expandedRunnerId.value = runnerId
  executionDetail.value = null
  detailError.value = ''
  try { const value = await getDailyExecution(runnerId); if (seq === detailSeq && expandedRunnerId.value === runnerId) executionDetail.value = value.execution }
  catch (error) { if (seq === detailSeq) detailError.value = errorText(error) }
}
async function copyDiagnostic(value: string) {
  try { await navigator.clipboard.writeText(value) }
  catch { detailError.value = '复制失败，请检查浏览器剪贴板权限' }
}

onMounted(() => {
  reload()
  dateTimer = setInterval(() => { today.value = shanghaiDate(); yesterday.value = shanghaiDate(-1) }, 60000)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (dateTimer) clearInterval(dateTimer)
  reportSeq++; directorySeq++; executionSeq++; detailSeq++
})
</script>

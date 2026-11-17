<template>
  <div :class="embedded ? 'h-full' : 'h-screen'" class="flex flex-col min-w-0 bg-canvas">
    <AppHeader title="定时消息发送" :is-logged-in="isLoggedIn" :user="user" @toggle-sidebar="toggleSidebar?.()" @logout="logout" />
    <main class="flex-1 min-h-0 overflow-y-auto p-3 sm:p-6">
      <div class="max-w-6xl mx-auto space-y-4">
        <section class="min-w-0 bg-surface border border-default rounded-xl overflow-hidden">
          <div class="flex flex-wrap justify-between items-center gap-3 border-b border-default p-3 sm:px-4">
            <nav class="flex flex-wrap gap-1" aria-label="定时消息发送">
              <BaseButton :intent="view === 'plans' ? 'secondary' : 'ghost'" :aria-current="view === 'plans' ? 'page' : undefined"
                @click="selectView('plans')">发送配置</BaseButton>
              <BaseButton :intent="view === 'history' ? 'secondary' : 'ghost'" :aria-current="view === 'history' ? 'page' : undefined"
                @click="selectView('history')">执行记录</BaseButton>
            </nav>
            <div class="flex flex-wrap items-center gap-2">
              <BaseButton intent="ghost" :disabled="loading" @click="load">刷新</BaseButton>
              <BaseButton v-if="view === 'plans'" @click="openForm(null)">新建配置</BaseButton>
            </div>
          </div>
          <p v-if="error" role="alert" class="mx-4 my-3 text-sm text-danger-700">{{ error }}</p>
          <div v-if="view === 'history' && historyPlan" class="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-default">
            <span class="text-sm text-muted break-all">当前配置：{{ historyPlan.name }}</span>
            <BaseButton intent="ghost" @click="selectView('history')">显示全部记录</BaseButton>
          </div>

          <div v-if="view === 'plans'" class="table-scroll-wrapper overflow-x-auto" tabindex="0" aria-label="发送配置列表">
            <BaseTable :columns="planColumns" :data="plans" row-key="task_id">
              <template #empty>{{ loading ? '正在加载配置…' : '暂无发送配置' }}</template>
              <template #name="{ row }">
                <span class="block max-w-64 truncate font-medium text-default" :title="row.name">{{ row.name }}</span>
                <span class="block max-w-64 truncate text-xs text-muted" :title="targetSummary(row as ScheduledSendPlan)">{{ targetSummary(row as ScheduledSendPlan) }}</span>
              </template>
              <template #binding="{ row }">
                <span class="block max-w-44 truncate" :title="row.device_name || row.device_id">{{ row.device_name || row.device_id.slice(0, 8) }}</span>
                <span class="block max-w-44 truncate text-xs text-muted" :title="row.account">{{ row.account }}</span>
              </template>
              <template #rule="{ row }"><span class="whitespace-nowrap">{{ scheduleLabel(row as ScheduledSendPlan) }}</span></template>
              <template #next_due_at="{ row }"><span class="whitespace-nowrap">{{ formatTime(row.next_due_at) }}</span></template>
              <template #status="{ row }">
                <BaseBadge :intent="planStates[row.status].intent">{{ planStates[row.status].label }}</BaseBadge>
              </template>
              <template #actions="{ row }">
                <div class="flex flex-wrap items-center gap-1 min-w-40">
                  <BaseButton v-if="row.status === 'paused'" intent="ghost" :disabled="!!updating" @click="changeState(row as ScheduledSendPlan, 'enable')">启用</BaseButton>
                  <BaseButton v-else intent="ghost" :disabled="!!updating" @click="changeState(row as ScheduledSendPlan, 'pause')">暂停</BaseButton>
                  <BaseButton intent="ghost" :disabled="row.status !== 'paused' || !!updating"
                    :title="row.status !== 'paused' ? '请先暂停后编辑' : undefined" @click="openForm(row as ScheduledSendPlan)">编辑</BaseButton>
                  <BaseButton intent="ghost" @click="selectView('history', row as ScheduledSendPlan)">记录</BaseButton>
                  <BaseButton intent="danger-ghost" :disabled="!!updating" @click="askDelete(row as ScheduledSendPlan)">删除</BaseButton>
                </div>
              </template>
            </BaseTable>
          </div>
          <div v-else class="table-scroll-wrapper overflow-x-auto" tabindex="0" aria-label="定时消息执行记录">
            <BaseTable :columns="historyColumns" :data="executions" row-key="id">
              <template #empty>{{ loading ? '正在加载执行记录…' : '暂无执行记录' }}</template>
              <template #plan_name="{ row }"><span class="block max-w-48 truncate">{{ row.plan_name }}</span></template>
              <template #session_name="{ row }"><span class="block max-w-48 truncate">{{ row.session_name || '未命名会话' }}</span></template>
              <template #due_at="{ row }"><span class="whitespace-nowrap">{{ formatTime(row.due_at) }}</span></template>
              <template #started_at="{ row }"><span class="whitespace-nowrap">{{ formatTime(row.started_at) }}</span></template>
              <template #status="{ row }">
                <div class="space-y-1">
                  <BaseBadge :intent="executionStates[row.status].intent">{{ executionStates[row.status].label }}</BaseBadge>
                  <p v-if="row.reason" class="m-0 text-xs text-muted">{{ row.reason }}</p>
                </div>
              </template>
              <template #diagnostic_id="{ row }">
                <div v-if="row.diagnostic_id" class="space-y-1">
                  <code class="block text-xs text-muted break-all">{{ row.diagnostic_id }}</code>
                  <BaseButton intent="ghost" @click="copyDiagnostic(row.diagnostic_id)">复制诊断编号</BaseButton>
                </div>
                <span v-else class="text-muted">—</span>
              </template>
            </BaseTable>
          </div>
          <div class="flex flex-wrap justify-between items-center gap-2 border-t border-default p-3 sm:px-4">
            <span class="text-xs text-muted">时间均为北京时间</span>
            <div v-if="offset || hasMore" class="flex items-center gap-2">
              <BaseButton intent="secondary" :disabled="!offset || loading" @click="move(-20)">上一页</BaseButton>
              <BaseButton intent="secondary" :disabled="!hasMore || loading" @click="move(20)">下一页</BaseButton>
            </div>
          </div>
        </section>
      </div>
    </main>

    <WecomScheduledSendForm v-if="formOpen" :plan="editing" @close="formOpen = false" @saved="onSaved" />
    <BaseModal :model-value="!!deleting" title="删除定时消息配置" size="sm" accessible :show-fullscreen="false"
      @update:model-value="open => { if (!open && !updating) deleting = null }">
      <p class="m-0 text-sm break-words">确定删除「{{ deleting?.name }}」？未开始的发送会取消，已有执行记录仍可查看。</p>
      <p v-if="deleteError" role="alert" class="text-sm text-danger-700">{{ deleteError }}</p>
      <template #footer>
        <BaseButton intent="secondary" :disabled="!!updating" @click="deleting = null">取消</BaseButton>
        <BaseButton intent="danger" :disabled="!!updating" @click="confirmDelete">确认删除</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import WecomScheduledSendForm from './WecomScheduledSendForm.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { deleteScheduledSend, listScheduledSendExecutions, listScheduledSends, setScheduledSendState,
  type ScheduledSendExecution, type ScheduledSendPlan } from '@/api/wecomConversation'

defineProps<{ embedded?: boolean }>()
const { admin, isLoggedIn, logout } = useTenantAuth()
const user = computed(() => admin.value ? { user_id: admin.value.user_id, username: admin.value.username, phone: admin.value.phone } : null)
const toggleSidebar = inject<() => void>('toggleSidebar')
const route = useRoute()
const toast = useToast()

type View = 'plans' | 'history'
type Badge = { label: string; intent: 'success' | 'neutral' | 'warning' | 'danger' | 'info' }
const planStates: Record<string, Badge> = {
  paused: { label: '已暂停', intent: 'neutral' }, active: { label: '已启用', intent: 'success' },
  completed: { label: '已完成', intent: 'neutral' }, expired: { label: '已过期', intent: 'warning' },
}
const executionStates: Record<string, Badge> = {
  queued: { label: '待发送', intent: 'neutral' }, dispatched: { label: '等待设备授权', intent: 'info' },
  running: { label: '发送中', intent: 'info' }, succeeded: { label: '已发送', intent: 'success' },
  failed: { label: '发送失败', intent: 'danger' }, expired: { label: '已过期', intent: 'warning' },
  cancelled: { label: '已取消', intent: 'neutral' }, unknown: { label: '发送结果未确认', intent: 'warning' },
}
const planColumns = [
  { key: 'name', label: '配置名称' }, { key: 'binding', label: '发送设备 / 企微账号' },
  { key: 'rule', label: '发送规则' }, { key: 'next_due_at', label: '下次计划时间' },
  { key: 'status', label: '状态' }, { key: 'actions', label: '操作' },
]
const historyColumns = [
  { key: 'plan_name', label: '配置名称' }, { key: 'session_name', label: '会话' },
  { key: 'due_at', label: '计划发送时间' }, { key: 'started_at', label: '实际开始时间' },
  { key: 'status', label: '结果', minWidth: '8rem' }, { key: 'diagnostic_id', label: '诊断编号', minWidth: '10rem' },
]

const view = ref<View>('plans')
const plans = ref<ScheduledSendPlan[]>([])
const executions = ref<ScheduledSendExecution[]>([])
const historyPlan = ref<ScheduledSendPlan | null>(null)
const offset = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const error = ref('')
const editing = ref<ScheduledSendPlan | null>(null)
const formOpen = ref(false)
const deleting = ref<ScheduledSendPlan | null>(null)
const deleteError = ref('')
const updating = ref('')
let loadRequest = 0

function message(e: unknown) { return e instanceof Error ? e.message : String(e) }
const formatter = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
function formatTime(value?: string | null) {
  if (!value) return '—'
  const iso = value.replace(' ', 'T')
  const date = new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(iso) ? iso : `${iso}+08:00`)
  return Number.isNaN(date.valueOf()) ? '—' : formatter.format(date)
}
function scheduleLabel(plan: ScheduledSendPlan) {
  if (plan.schedule_type === 'once') return `一次 · ${formatTime(plan.time_config.run_at)}`
  const { hour, minute, weekdays } = plan.time_config
  const clock = [hour, minute].map(value => String(value ?? 0).padStart(2, '0')).join(':')
  if (plan.schedule_type === 'daily') return `每天 ${clock}`
  const labels: Record<string, string> = { mon: '一', tue: '二', wed: '三', thu: '四', fri: '五', sat: '六', sun: '日' }
  return `每周${(weekdays || []).map(day => labels[day]).join('、')} ${clock}`
}
function targetSummary(plan: ScheduledSendPlan) {
  const names = plan.sessions.slice(0, 2).map(session => plan.session_names?.[session] || '未命名会话')
  return `${names.join('、')}${plan.sessions.length > 2 ? ` 等 ${plan.sessions.length} 个会话` : ''}`
}

async function load() {
  const request = ++loadRequest
  error.value = ''
  loading.value = true
  try {
    if (view.value === 'history') {
      const data = await listScheduledSendExecutions(offset.value, historyPlan.value?.task_id)
      if (request !== loadRequest) return
      executions.value = data.executions
      hasMore.value = data.has_more
    } else {
      const data = await listScheduledSends(offset.value)
      if (request !== loadRequest) return
      plans.value = data.plans
      hasMore.value = data.has_more
    }
  } catch (e) {
    if (request === loadRequest) error.value = message(e)
  } finally {
    if (request === loadRequest) loading.value = false
  }
}

function selectView(value: View, plan: ScheduledSendPlan | null = null) {
  view.value = value
  historyPlan.value = plan
  offset.value = 0
  hasMore.value = false
  executions.value = []
  void load()
}
function move(delta: number) {
  offset.value += delta
  void load()
}
function openForm(plan: ScheduledSendPlan | null) {
  editing.value = plan
  formOpen.value = true
}
function onSaved() {
  toast.success(editing.value ? '已保存修改' : '已保存配置，请手动启用')
  formOpen.value = false
  offset.value = 0
  void load()
}

function askDelete(plan: ScheduledSendPlan) {
  deleteError.value = ''
  deleting.value = plan
}

async function changeState(plan: ScheduledSendPlan, action: 'enable' | 'pause') {
  if (updating.value) return
  updating.value = plan.task_id
  error.value = ''
  try {
    const data = await setScheduledSendState(plan.task_id, action)
    plans.value = plans.value.map(item => item.task_id === plan.task_id ? data.plan : item)
    toast.success(action === 'enable' ? '配置已启用' : '配置已暂停')
  } catch (e) {
    error.value = message(e)
  } finally {
    updating.value = ''
  }
}

async function confirmDelete() {
  if (updating.value || !deleting.value) return
  updating.value = deleting.value.task_id
  deleteError.value = ''
  try {
    await deleteScheduledSend(updating.value)
    deleting.value = null
    toast.success('配置已删除')
    if (plans.value.length === 1) offset.value = Math.max(0, offset.value - 20)
    await load()
  } catch (e) {
    deleteError.value = message(e)
  } finally {
    updating.value = ''
  }
}

async function copyDiagnostic(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    toast.success('诊断编号已复制')
  } catch {
    toast.error('复制失败，可选中编号手动复制')
  }
}

onMounted(load)
watch(() => route.params.tenant_id, () => {
  formOpen.value = false
  deleting.value = null
  plans.value = []
  selectView('plans')
})
</script>

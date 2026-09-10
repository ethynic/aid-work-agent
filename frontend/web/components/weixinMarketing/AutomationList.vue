<template>
  <!-- 自动化列表：keyword/status/trigger_type 过滤 + 分页 + 5s 可见时轮询 + 操作入口 -->
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
      <div class="page-toolbar">
        <div class="page-toolbar-left flex-wrap">
          <BaseInput
            v-model="keywordInput"
            placeholder="搜索任务名称"
            size="sm"
            class="w-64"
            :disabled="loading"
            @keyup.enter="applyFilters"
          />
          <BaseSelect v-model="statusFilter" size="sm" class="w-32" @update:model-value="applyFilters">
            <option value="">全部状态</option>
            <option v-for="(label, value) in AUTOMATION_STATUS_LABELS" :key="value" :value="value">{{ label }}</option>
          </BaseSelect>
          <BaseSelect v-model="triggerFilter" size="sm" class="w-36" @update:model-value="applyFilters">
            <option value="">全部触发类型</option>
            <option value="once">一次性</option>
            <option value="interval">间隔循环</option>
            <option value="calendar">日历（cron）</option>
            <option value="event">事件</option>
          </BaseSelect>
          <BaseButton size="sm" intent="secondary" :disabled="loading" @click="applyFilters">搜索</BaseButton>
          <span v-if="pollDegraded" class="text-xs text-warning-600" title="自动刷新遇到网络错误，正在按 15 秒间隔重试">
            自动刷新暂不可用
          </span>
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" intent="secondary" @click="showBindingsModal = true">群绑定</BaseButton>
          <BaseButton size="sm" intent="secondary" @click="openRunsModal">运行记录</BaseButton>
          <BaseButton size="sm" @click="openCreate">新增自动化</BaseButton>
        </div>
      </div>

      <div v-if="error && !items.length" class="page-content p-4">
        <p class="text-sm text-danger-600 mb-2">{{ error }}</p>
        <BaseButton size="sm" intent="secondary" @click="load()">重试</BaseButton>
      </div>

      <div v-else class="table-scroll-wrapper flex-1">
        <BaseTable :columns="columns" :data="items" row-key="id">
          <template #seq="{ index }">{{ seqNumber(index) }}</template>
          <template #name="{ row }">
            <span class="text-sm font-medium text-default" :title="row.name">{{ row.name }}</span>
          </template>
          <template #status="{ row }">
            <BaseBadge :intent="AUTOMATION_STATUS_BADGE[row.status as AutomationStatus]">
              {{ AUTOMATION_STATUS_LABELS[row.status as AutomationStatus] }}
            </BaseBadge>
          </template>
          <template #updated_at="{ row }">{{ formatDateTime(row.updated_at) }}</template>
          <template #actions="{ row }">
            <div class="flex items-center justify-center gap-1 whitespace-nowrap">
              <BaseButton size="sm" intent="ghost" @click="openEdit(row as AutomationRow)">编辑</BaseButton>
              <BaseButton
                v-if="row.status === 'active'"
                size="sm"
                intent="ghost"
                :disabled="actionPending(row.id)"
                @click="handlePause(row as AutomationRow)"
              >{{ actionPending(row.id) ? '...' : '暂停' }}</BaseButton>
              <BaseButton
                v-else-if="row.status === 'paused'"
                size="sm"
                intent="ghost"
                :disabled="actionPending(row.id)"
                @click="handleResume(row as AutomationRow)"
              >{{ actionPending(row.id) ? '...' : '恢复' }}</BaseButton>
              <BaseButton
                v-if="row.status !== 'archived'"
                size="sm"
                intent="ghost"
                :disabled="actionPending(row.id)"
                title="手动触发一次执行（202，异步）"
                @click="handleManualRun(row as AutomationRow)"
              >运行</BaseButton>
              <BaseButton
                v-if="row.status !== 'archived'"
                size="sm"
                intent="danger-ghost"
                :disabled="actionPending(row.id)"
                @click="handleArchive(row as AutomationRow)"
              >归档</BaseButton>
            </div>
          </template>
          <template v-if="loading && !items.length" #empty>加载中...</template>
          <template v-else-if="!items.length" #empty>
            {{ error ? '加载失败' : '暂无自动化任务，点击右上角「新增自动化」创建第一个任务' }}
          </template>
        </BaseTable>
      </div>

      <BasePagination
        v-if="!error || items.length"
        :total="total"
        :current-page="currentPage"
        :page-size="pageSize"
        @change="handlePageChange"
      />
    </div>

    <!-- 编辑器 -->
    <AutomationEditor
      v-model="showEditor"
      :automation-id="editingId"
      @saved="load()"
      @created="id => (editingId = id)"
    />

    <!-- 群绑定管理 -->
    <GroupBindingPicker v-model="showBindingsModal" mode="manage" />

    <!-- 危险/真实副作用操作确认（V-P2-2） -->
    <BaseModal
      :model-value="!!confirmState"
      :title="confirmState?.title || '确认操作'"
      size="sm"
      @update:model-value="v => { if (!v) confirmState = null }"
    >
      <p class="text-sm text-default">{{ confirmState?.message }}</p>
      <template #footer>
        <BaseButton intent="secondary" @click="confirmState = null">取消</BaseButton>
        <BaseButton :intent="confirmState?.intent || 'primary'" @click="runConfirmedAction">确认</BaseButton>
      </template>
    </BaseModal>

    <!-- 运行记录 -->
    <BaseModal v-model="showRunsModal" title="运行记录" size="lg">
      <div class="space-y-3">
        <div class="flex items-center gap-2 flex-wrap">
          <BaseSelect v-model="runsStateFilter" size="sm" class="w-36" @update:model-value="loadRuns">
            <option value="">全部状态</option>
            <option v-for="(label, value) in RUN_STATE_LABELS" :key="value" :value="value">{{ label }}</option>
          </BaseSelect>
          <BaseButton size="sm" intent="ghost" :disabled="loadingRuns" @click="loadRuns">刷新</BaseButton>
        </div>
        <div v-if="runsError" class="text-sm text-danger-600">{{ runsError }}</div>
        <div v-else-if="loadingRuns && !runs.length" class="text-sm text-muted py-3">加载中...</div>
        <div v-else-if="!runs.length" class="empty-state">
          <div class="empty-state-icon">◌</div>
          <p class="text-sm text-muted">暂无运行记录</p>
        </div>
        <div v-else class="table-scroll-wrapper" style="max-height: 50vh">
          <BaseTable :columns="runColumns" :data="runs" row-key="id">
            <template #state="{ row }">
              <BaseBadge :intent="RUN_STATE_BADGE[row.state as RunState]">{{ RUN_STATE_LABELS[row.state as RunState] }}</BaseBadge>
            </template>
            <template #task_ref="{ row }">{{ shortId(row.task_ref) }}</template>
            <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
            <template #finished_at="{ row }">{{ formatDateTime(row.finished_at) }}</template>
            <template #actions="{ row }">
              <div class="text-center">
                <BaseButton size="sm" intent="ghost" @click="goRunDetail(row as RunListItem)">详情</BaseButton>
              </div>
            </template>
            <template #empty>暂无运行记录</template>
          </BaseTable>
        </div>
        <BasePagination
          :total="runsTotal"
          :current-page="runsPage"
          :page-size="RUNS_PAGE_SIZE"
          @change="page => { runsPage = page; loadRuns() }"
        />
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
/**
 * AutomationList：微信自动化列表（R54②/R55）。
 * - 过滤：keyword（名称 ILIKE）/status/trigger_type，服务端分页（page/page_size）；
 * - 轮询：每 5 秒静默刷新，仅 document 可见时执行（visibilitychange 感知，
 *   不可见期间跳过本次 tick）；网络错误降级为 15 秒间隔并在工具栏提示；
 *   刷新只替换列表行数据，不影响编辑器弹框内的草稿；
 * - 操作：编辑（409 冲突在编辑器内处理）/ 暂停/恢复/归档（版本 CAS）/
 *   手动运行（202，成功后可直接跳转运行详情）。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import AutomationEditor from './AutomationEditor.vue'
import GroupBindingPicker from './GroupBindingPicker.vue'
import {
  WeixinApiError,
  archiveAutomation,
  isVersionConflict,
  listAutomations,
  listRuns,
  pauseAutomation,
  resumeAutomation,
  runAutomation,
  type AutomationRow,
  type AutomationStatus,
  type RunListItem,
  type RunState,
} from '@/api/weixinMarketing'
import {
  AUTOMATION_STATUS_BADGE,
  AUTOMATION_STATUS_LABELS,
  RUN_STATE_BADGE,
  RUN_STATE_LABELS,
  formatDateTime,
} from './weixinDisplay'

const LIST_POLL_MS = 5000

const route = useRoute()
const router = useRouter()
const toast = useToast()

// ============== 列表 ==============

const items = ref<AutomationRow[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref('')

// 过滤（applied* 为已提交给后端的过滤条件；输入态单独保存避免轮询用未提交条件）
const keywordInput = ref('')
const statusFilter = ref<AutomationStatus | ''>('')
const triggerFilter = ref<'once' | 'interval' | 'calendar' | 'event' | ''>('')
const appliedKeyword = ref('')
const appliedStatus = ref<AutomationStatus | ''>('')
const appliedTrigger = ref<'once' | 'interval' | 'calendar' | 'event' | ''>('')

const actionIds = ref(new Set<string>())
function actionPending(id: string): boolean {
  return actionIds.value.has(id)
}
function markAction(id: string, on: boolean) {
  const next = new Set(actionIds.value)
  if (on) next.add(id)
  else next.delete(id)
  actionIds.value = next
}

function seqNumber(index: number): number {
  return (currentPage.value - 1) * pageSize.value + index + 1
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const res = await listAutomations({
      keyword: appliedKeyword.value || undefined,
      status: appliedStatus.value || undefined,
      trigger_type: appliedTrigger.value || undefined,
      page: currentPage.value,
      page_size: pageSize.value,
    })
    items.value = res.items
    total.value = res.total
    error.value = ''
    pollDegraded.value = false
  } catch (e) {
    if (!silent || !items.value.length) {
      error.value = e instanceof WeixinApiError ? e.message : '加载自动化列表失败'
    }
    // 静默轮询失败且已有数据：保留现状并降级提示
    if (silent && items.value.length) pollDegraded.value = true
  } finally {
    if (!silent) loading.value = false
  }
}

function applyFilters() {
  appliedKeyword.value = keywordInput.value.trim()
  appliedStatus.value = statusFilter.value
  appliedTrigger.value = triggerFilter.value
  currentPage.value = 1
  void load()
}

function handlePageChange(page: number, size: number) {
  currentPage.value = page
  pageSize.value = size
  void load()
}

// ============== 5s 轮询（仅页面可见时；连续失败降级 15s） ==============

let pollTimer: ReturnType<typeof setInterval> | null = null
const pollDegraded = ref(false)
let degradedTickCount = 0

function tick() {
  // 仅文档可见时轮询（R55）；不可见时跳过本次 tick，切回可见时立即补一次
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  void load(true)
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    if (pollDegraded.value) {
      // 降级间隔：每 3 个 tick（约 15 秒）尝试一次，成功后自动恢复 5 秒
      degradedTickCount = (degradedTickCount + 1) % 3
      if (degradedTickCount !== 0) return
    }
    tick()
  }, LIST_POLL_MS)
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') tick()
}

// ============== 行操作 ==============

async function withRowAction(id: string, action: () => Promise<void>) {
  if (actionPending(id)) return
  markAction(id, true)
  try {
    await action()
  } finally {
    markAction(id, false)
  }
}

function handlePause(row: AutomationRow) {
  void withRowAction(row.id, async () => {
    try {
      await pauseAutomation(row.id, row.version)
      toast.success('已暂停（未开始的工作将被撤销）')
      await load(true)
    } catch (e) {
      handleActionError(e, '暂停失败')
    }
  })
}

function handleResume(row: AutomationRow) {
  void withRowAction(row.id, async () => {
    try {
      await resumeAutomation(row.id, row.version)
      toast.success('已恢复运行')
      await load(true)
    } catch (e) {
      handleActionError(e, '恢复失败')
    }
  })
}

function handleArchive(row: AutomationRow) {
  askConfirm(
    '确认归档',
    `归档后任务不可再编辑或运行，确认归档「${row.name}」？`,
    'danger',
    () => withRowAction(row.id, async () => {
      try {
        await archiveAutomation(row.id, row.version)
        toast.success('已归档')
        await load(true)
      } catch (e) {
        handleActionError(e, '归档失败')
      }
    }),
  )
}

function handleManualRun(row: AutomationRow) {
  askConfirm(
    '确认手动运行',
    `手动触发一次「${row.name}」？将按已发布内容真实发送。`,
    'primary',
    () => withRowAction(row.id, async () => {
      try {
        const res = await runAutomation(row.id)
        toast.success(res.created ? '已触发执行' : '相同幂等键的触发已存在（未重复执行）')
        if (res.run_id) {
          goRunId(res.run_id)
        } else {
          await load(true)
        }
      } catch (e) {
        handleActionError(e, '触发失败')
      }
    }),
  )
}

// ============== 危险/真实副作用操作确认（V-P2-2：BaseModal 替代 window.confirm） ==============

const confirmState = ref<{
  title: string
  message: string
  intent: 'primary' | 'danger'
  action: () => void
} | null>(null)

function askConfirm(title: string, message: string, intent: 'primary' | 'danger', action: () => void) {
  confirmState.value = { title, message, intent, action }
}

async function runConfirmedAction() {
  const current = confirmState.value
  confirmState.value = null
  if (current) await current.action()
}

function handleActionError(e: unknown, fallback: string) {
  if (isVersionConflict(e)) {
    toast.warning('版本冲突：配置已被并发修改，列表已刷新，请重试')
    void load(true)
  } else {
    toast.error(e instanceof WeixinApiError ? e.message : fallback)
  }
}

// ============== 编辑器 ==============

const showEditor = ref(false)
const editingId = ref<string | null>(null)

function openCreate() {
  editingId.value = null
  showEditor.value = true
}

function openEdit(row: AutomationRow) {
  editingId.value = row.id
  showEditor.value = true
}

// ============== 群绑定 ==============

const showBindingsModal = ref(false)

// ============== 运行记录 ==============

const RUNS_PAGE_SIZE = 20
const showRunsModal = ref(false)
const runs = ref<RunListItem[]>([])
const runsTotal = ref(0)
const runsPage = ref(1)
const runsStateFilter = ref<RunState | ''>('')
const loadingRuns = ref(false)
const runsError = ref('')

function openRunsModal() {
  showRunsModal.value = true
  void loadRuns()
}

async function loadRuns() {
  loadingRuns.value = true
  runsError.value = ''
  try {
    const res = await listRuns({
      state: runsStateFilter.value || undefined,
      page: runsPage.value,
      page_size: RUNS_PAGE_SIZE,
    })
    runs.value = res.items
    runsTotal.value = res.total
  } catch (e) {
    runsError.value = e instanceof WeixinApiError ? e.message : '加载运行记录失败'
  } finally {
    loadingRuns.value = false
  }
}

function goRunDetail(row: RunListItem) {
  goRunId(row.id)
}

function goRunId(runId: string) {
  showRunsModal.value = false
  const match = route.path.match(/^(.*\/weixin-marketing)/)
  const base = match ? match[1] : '/weixin-marketing'
  router.push(`${base}/runs/${encodeURIComponent(runId)}`)
}

function shortId(value: string): string {
  return value ? `${value.slice(0, 8)}…` : '-'
}

// ============== 列定义 / 生命周期 ==============

const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'name', label: '任务名称' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'version', label: '版本', width: '70px' },
  { key: 'updated_at', label: '最近更新', width: '150px' },
  { key: 'actions', label: '操作', width: '200px', tdAlign: 'center' as const },
]

const runColumns = [
  { key: 'state', label: '状态', width: '100px' },
  { key: 'task_ref', label: '自动化', width: '110px' },
  { key: 'created_at', label: '创建时间', width: '150px' },
  { key: 'finished_at', label: '完成时间', width: '150px' },
  { key: 'actions', label: '操作', width: '70px', tdAlign: 'center' as const },
]

onMounted(() => {
  void load()
  startPolling()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div class="page-container bg-canvas">
    <AppHeader
      title="工作成果"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button
          v-if="isAdmin"
          class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover"
          @click="confirmRunReview(); closeMenu()"
        >
          手动复盘昨天
        </button>
        <button
          class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover"
          @click="refresh(); closeMenu()"
        >
          刷新
        </button>
      </template>
    </AppHeader>

    <div class="page-content p-6">
      <!-- 概览统计卡片 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4 flex-shrink-0">
        <div
          v-for="stat in statsCards"
          :key="stat.key"
          class="bg-surface rounded-xl border border-default p-4"
        >
          <p class="text-xs font-medium text-muted uppercase tracking-wider">{{ stat.label }}</p>
          <p class="text-2xl font-bold mt-1 tabular-nums">{{ stat.value }}</p>
          <p v-if="stat.sub" class="text-xs text-muted mt-1">{{ stat.sub }}</p>
        </div>
      </div>

      <!-- 筛选区 -->
      <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4 flex-shrink-0">
        <BaseSelect v-model="filters.outcome_type" size="sm" class="w-32">
          <option value="">全部类型</option>
          <option v-for="(label, key) in outcomeTypeLabels" :key="key" :value="key">{{ label }}</option>
        </BaseSelect>
        <BaseSelect v-model="filters.source" size="sm" class="w-36">
          <option value="">全部来源</option>
          <option v-for="(label, key) in sourceLabels" :key="key" :value="key">{{ label }}</option>
        </BaseSelect>
        <BaseSelect v-if="isAdmin" v-model="filters.subagent_id" size="sm" class="w-40">
          <option value="">全部智能体</option>
          <option value="main">主智能体</option>
          <option v-for="sa in subagentOptions" :key="sa" :value="sa">{{ sa }}</option>
        </BaseSelect>
        <BaseInput
          v-model="filters.keyword"
          placeholder="搜索摘要..."
          size="sm"
          class="w-56"
          @keyup.enter="handleSearch(filters.keyword)"
        />
        <input
          v-model="filters.start_date"
          type="date"
          class="px-3 py-2 bg-surface border border-default rounded-lg text-default text-sm focus:outline-none focus:border-primary-400"
        />
        <input
          v-model="filters.end_date"
          type="date"
          class="px-3 py-2 bg-surface border border-default rounded-lg text-default text-sm focus:outline-none focus:border-primary-400"
        />
        <div class="flex-1" />
        <BaseButton size="sm" @click="handleSearch(filters.keyword)">查询</BaseButton>
        <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
      </div>

      <!-- 列表表格 -->
      <div class="table-scroll-wrapper flex-1">
        <BaseTable :columns="columns" :data="outcomes" row-key="outcome_id">
          <template #index="{ index }">{{ seqNumber(index) }}</template>
          <template #summary="{ row }">
            <span class="block max-w-[640px] truncate text-left text-default" :title="row.summary">{{ row.summary }}</span>
          </template>
          <template #file="{ row }">
            <a
              v-if="row.file_id"
              :href="`/api/files/${row.file_id}/download`"
              class="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 whitespace-nowrap"
              :title="row.file_name || '下载文件'"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 4v12m0 0l-4-4m4 4l4-4" />
              </svg>
              下载
            </a>
            <span v-else class="text-xs text-muted">-</span>
          </template>
          <template #outcome_type="{ row }">
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="outcomeTypeBadgeClass(row.outcome_type)">
              {{ outcomeTypeLabels[row.outcome_type as OutcomeType] || row.outcome_type }}
            </span>
          </template>
          <template #source="{ row }">
            <span class="text-xs text-muted">{{ sourceLabels[row.source as OutcomeSource] || row.source }}</span>
          </template>
          <template #subagent_id="{ row }">
            <span class="text-xs text-muted">{{ row.subagent_id || '主智能体' }}</span>
          </template>
          <template #channel="{ row }">
            <span class="text-xs text-muted">{{ channelLabels[row.channel as string] || row.channel || '-' }}</span>
          </template>
          <template #confidence="{ row }">
            <span v-if="row.review_confidence !== null && row.review_confidence !== undefined" class="text-xs tabular-nums text-muted">
              {{ (row.review_confidence * 100).toFixed(0) }}%
            </span>
            <span v-else class="text-xs text-muted">-</span>
          </template>
          <template #created_at="{ row }">{{ formatDate(row.created_at) }}</template>
          <template #actions="{ row }">
            <div class="flex items-center justify-end gap-1">
              <BaseButton intent="ghost" size="sm" @click="openDetail(row)">详情</BaseButton>
              <BaseButton v-if="isAdmin" intent="danger-ghost" size="sm" @click="confirmDelete(row)">删除</BaseButton>
            </div>
          </template>
          <template #empty>
            <div v-if="loading" class="flex items-center justify-center gap-2 py-8">
              <svg class="w-5 h-5 animate-spin text-primary-500" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              <span class="text-muted">加载中...</span>
            </div>
            <span v-else class="text-muted py-8 inline-block">暂无工作成果数据</span>
          </template>
        </BaseTable>
      </div>

      <BasePagination
        :total="total"
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        @change="loadOutcomes"
      />
    </div>

    <!-- 详情 Modal -->
    <BaseModal v-model="showDetail" title="工作成果详情" size="lg">
      <template v-if="detail">
        <div class="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span class="text-muted">成果 ID：</span>
            <span class="font-mono text-xs text-default">{{ detail.outcome_id }}</span>
          </div>
          <div>
            <span class="text-muted">类型：</span>
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="outcomeTypeBadgeClass(detail.outcome_type)">
              {{ outcomeTypeLabels[detail.outcome_type as OutcomeType] || detail.outcome_type }}
            </span>
          </div>
          <div>
            <span class="text-muted">来源：</span>
            <span class="text-default">{{ sourceLabels[detail.source as OutcomeSource] || detail.source }}</span>
          </div>
          <div>
            <span class="text-muted">智能体：</span>
            <span class="text-default">{{ detail.subagent_id || '主智能体' }}</span>
          </div>
          <div>
            <span class="text-muted">渠道：</span>
            <span class="text-default">{{ channelLabels[detail.channel as string] || detail.channel || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">创建时间：</span>
            <span class="text-default tabular-nums">{{ formatDate(detail.created_at) }}</span>
          </div>
          <div v-if="detail.file_name">
            <span class="text-muted">文件名：</span>
            <span class="text-default">{{ detail.file_name }}</span>
          </div>
          <div v-if="detail.file_id">
            <span class="text-muted">文件 ID：</span>
            <span class="font-mono text-xs text-default">{{ detail.file_id }}</span>
          </div>
          <div v-if="detail.review_batch_id">
            <span class="text-muted">复盘批次：</span>
            <span class="font-mono text-xs text-default">{{ detail.review_batch_id }}</span>
          </div>
          <div v-if="detail.review_confidence !== null && detail.review_confidence !== undefined">
            <span class="text-muted">置信度：</span>
            <span class="text-default tabular-nums">{{ (detail.review_confidence * 100).toFixed(0) }}%</span>
          </div>
          <div>
            <span class="text-muted">会话 ID：</span>
            <span class="font-mono text-xs text-muted">{{ detail.session_id }}</span>
          </div>
          <div v-if="detail.chat_record_id">
            <span class="text-muted">对话记录 ID：</span>
            <span class="text-default tabular-nums">{{ detail.chat_record_id }}</span>
          </div>
        </div>

        <div class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-2 uppercase tracking-wider">摘要</h3>
          <p class="text-sm text-default bg-canvas rounded-lg p-3 border border-default">{{ detail.summary }}</p>
        </div>

        <div v-if="detail.metadata && Object.keys(detail.metadata).length > 0" class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-2 uppercase tracking-wider">元数据</h3>
          <pre class="text-xs text-muted bg-canvas rounded-lg p-3 border border-default overflow-x-auto">{{ JSON.stringify(detail.metadata, null, 2) }}</pre>
        </div>

        <div v-if="detail.file_id" class="mt-4">
          <a
            :href="`/api/files/${detail.file_id}/download`"
            class="inline-flex items-center gap-2 px-3 py-2 text-sm bg-primary-50 text-primary-700 rounded-lg hover:bg-primary-100 transition-colors"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1M12 4v12m0 0l-4-4m4 4l4-4" />
            </svg>
            下载文件
          </a>
        </div>
      </template>
      <template #footer>
        <BaseButton intent="secondary" @click="showDetail = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { usePageContext } from '@/composables/usePageContext'
import { useTenantAuth } from '@/composables/useTenantAuth'
import {
  listOutcomes,
  getStats,
  deleteOutcome,
  runReviewManually,
  type WorkOutcome,
  type OutcomeType,
  type OutcomeSource,
  type OutcomeStats,
} from '@/api/workOutcomes'
import { formatShortDateTime as formatDate } from '@/utils/date'

const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone,
    role: tenantAdmin.value.role,
  } : null
})
const isAdmin = computed(() => {
  const u = effectiveUser.value
  return !!u && (u.role === 'tenant_admin' || u.role === 'platform_admin')
})

const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
function handleLogout() {
  tenantLogout()
}

// ============== 类型映射 ==============

const outcomeTypeLabels: Record<OutcomeType, string> = {
  file: '文件交付',
  action: '业务操作',
  decision: '决策建议',
  other: '其他',
}

const sourceLabels: Record<OutcomeSource, string> = {
  cp_realtime: '实时登记',
  scheduled_review: '定时复盘',
  manual: '手动',
}

const channelLabels: Record<string, string> = {
  web: 'Web',
  wecom: '企业微信',
  dingtalk: '钉钉',
  feishu: '飞书',
  wecom_kf: '微信客服',
  wecom_personal_rpa: '企微个人RPA',
}

function outcomeTypeBadgeClass(type: string): string {
  const map: Record<string, string> = {
    file: 'bg-success-100 text-success-700',
    action: 'bg-info-100 text-info-700',
    decision: 'bg-warning-100 text-warning-700',
    other: 'bg-surface-hover text-muted',
  }
  return map[type] || 'bg-surface-hover text-muted'
}

// ============== 列表与分页 ==============

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'summary', label: '成果摘要', minWidth: '240px', tdAlign: 'left' as const },
  { key: 'file', label: '文件', width: '90px' },
  { key: 'outcome_type', label: '类型', width: '100px' },
  { key: 'source', label: '来源', width: '100px' },
  { key: 'subagent_id', label: '智能体', width: '120px' },
  { key: 'channel', label: '渠道', width: '100px' },
  { key: 'confidence', label: '置信度', width: '80px' },
  { key: 'created_at', label: '创建时间', width: '160px' },
  { key: 'actions', label: '操作', width: '140px' },
]

const outcomes = ref<WorkOutcome[]>([])
const total = ref(0)
const stats = ref<OutcomeStats | null>(null)
const detail = ref<WorkOutcome | null>(null)
const showDetail = ref(false)
const filters = ref({
  outcome_type: '',
  source: '',
  subagent_id: '',
  keyword: '',
  start_date: '',
  end_date: '',
})

const { currentPage, pageSize, loading, seqNumber, handleSearch, refresh } =
  usePageContext(async () => { await loadOutcomes() })

// 子智能体选项（从统计中提取）
const subagentOptions = computed(() => {
  if (!stats.value) return []
  return Object.keys(stats.value.by_subagent).filter(k => k !== 'main')
})

// 概览卡片
const statsCards = computed(() => {
  const s = stats.value
  if (!s) {
    return [
      { key: 'total', label: '总成果', value: '-', sub: '' },
      { key: 'file', label: '文件交付', value: '-', sub: '' },
      { key: 'action', label: '业务操作', value: '-', sub: '' },
      { key: 'review', label: '复盘产出', value: '-', sub: '' },
    ]
  }
  return [
    { key: 'total', label: '总成果', value: s.total, sub: '' },
    { key: 'file', label: '文件交付', value: s.by_type.file || 0, sub: '' },
    { key: 'action', label: '业务操作', value: s.by_type.action || 0, sub: '' },
    {
      key: 'review',
      label: '复盘产出',
      value: s.by_source.scheduled_review || 0,
      sub: s.review_stats.last_batch_id ? `最近批次: ${s.review_stats.last_batch_id.slice(0, 16)}...` : '',
    },
  ]
})

async function loadOutcomes() {
  try {
    const params: Record<string, unknown> = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    if (filters.value.outcome_type) params.outcome_type = filters.value.outcome_type
    if (filters.value.source) params.source = filters.value.source
    if (filters.value.subagent_id) {
      // 'main' 是 UI 占位值，表示"主智能体"（subagent_id IS NULL）。
      // 后端识别 __NULL__ 特殊值并转换为 IS NULL 查询（workOutcomes.ts 透传）。
      params.subagent_id = filters.value.subagent_id === 'main' ? '__NULL__' : filters.value.subagent_id
    }
    if (filters.value.keyword) params.keyword = filters.value.keyword
    if (filters.value.start_date) params.start_date = filters.value.start_date
    if (filters.value.end_date) params.end_date = filters.value.end_date

    const res = await listOutcomes(params as any)
    outcomes.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    console.error('加载工作成果列表失败:', e)
    outcomes.value = []
    total.value = 0
  }
  // 同步加载统计
  await loadStats()
}

async function loadStats() {
  try {
    const params: Record<string, unknown> = {}
    if (filters.value.start_date) params.start_date = filters.value.start_date
    if (filters.value.end_date) params.end_date = filters.value.end_date
    const res = await getStats(params)
    stats.value = res.data
  } catch (e: any) {
    console.error('加载工作成果统计失败:', e)
    stats.value = null
  }
}

function resetFilters() {
  filters.value = {
    outcome_type: '',
    source: '',
    subagent_id: '',
    keyword: '',
    start_date: '',
    end_date: '',
  }
  handleSearch('')
}

function openDetail(row: WorkOutcome | Record<string, any>) {
  detail.value = row as WorkOutcome
  showDetail.value = true
}

async function confirmDelete(row: WorkOutcome | Record<string, any>) {
  const r = row as WorkOutcome
  if (!confirm(`确定删除工作成果「${r.summary.slice(0, 40)}...」？`)) return
  try {
    await deleteOutcome(r.outcome_id)
    await refresh()
  } catch (e: any) {
    alert(`删除失败: ${e.message}`)
  }
}

async function confirmRunReview() {
  if (!confirm('手动触发复盘昨天的工作成果？此操作会消耗少量 LLM 积分。')) return
  try {
    const res = await runReviewManually()
    alert(`复盘任务已触发，批次 ID: ${res.data.review_batch_id}\n点击刷新查看新成果。`)
    await refresh()
  } catch (e: any) {
    alert(`触发复盘失败: ${e.message}`)
  }
}

onMounted(() => {
  loadOutcomes()
})
</script>

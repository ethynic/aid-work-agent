<template>
  <div class="page-container">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">投诉记录</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">投诉全生命周期管理</p>
      </div>
    </div>

    <!-- Stats Cards -->
    <div class="grid grid-cols-4 gap-4 mb-4">
      <div
        v-for="stat in statsCards"
        :key="stat.key"
        class="relative overflow-hidden rounded-xl border border-default bg-surface p-4 group hover:border-primary-300 transition-colors"
      >
        <div class="flex items-start justify-between">
          <div>
            <p class="text-xs font-medium text-muted uppercase tracking-wider">{{ stat.label }}</p>
            <p class="text-2xl font-bold mt-1 tabular-nums">{{ stat.value }}</p>
          </div>
          <div class="w-9 h-9 rounded-lg flex items-center justify-center" :class="stat.iconBg">
            <svg class="w-4.5 h-4.5" :class="stat.iconColor" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" :d="stat.iconPath" />
            </svg>
          </div>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.status" size="sm" class="w-28">
        <option value="">全部状态</option>
        <option v-for="(label, key) in statusLabels" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.category" size="sm" class="w-28">
        <option value="">全部分类</option>
        <option v-for="(label, key) in categoryLabels" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.urgency" size="sm" class="w-32">
        <option value="">全部紧急程度</option>
        <option v-for="(label, key) in urgencyLabels" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="filters.keyword" placeholder="搜索投诉内容、投诉ID..." size="sm" class="w-56" @keyup.enter="handleSearch(filters.keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(filters.keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="complaints" row-key="complaint_id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #complaint_id="{ row }">
          <span class="font-mono text-xs text-muted">{{ row.complaint_id }}</span>
        </template>
        <template #category="{ row }">
          <div>
            <div class="font-medium text-default">{{ categoryLabels[row.category as keyof typeof categoryLabels] || row.category }}</div>
            <div v-if="row.sub_category" class="text-xs text-muted mt-0.5">{{ row.sub_category }}</div>
          </div>
        </template>
        <template #urgency="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="urgencyBadgeClass(row.urgency)">
            {{ urgencyLabels[row.urgency as keyof typeof urgencyLabels] || row.urgency }}
          </span>
        </template>
        <template #status="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="statusBadgeClass(row.status)">
            {{ statusLabels[row.status as keyof typeof statusLabels] || row.status }}
          </span>
        </template>
        <template #customer_emotion="{ row }">
          <span v-if="row.customer_emotion" class="text-xs" :class="emotionClass(row.customer_emotion)">
            {{ emotionLabels[row.customer_emotion as keyof typeof emotionLabels] || row.customer_emotion }}
          </span>
          <span v-else class="text-muted">-</span>
        </template>
        <template #description="{ row }">
          <span class="block max-w-[200px] truncate text-muted">{{ row.description_short || row.description }}</span>
        </template>
        <template #interaction_count="{ row }">
          <span class="tabular-nums text-muted">{{ row.interaction_count || 0 }}</span>
        </template>
        <template #created_at="{ row }">{{ formatDate(row.created_at) }}</template>
        <template #actions="{ row }">
          <div class="flex items-center justify-end gap-1">
            <BaseButton intent="ghost" size="sm" @click="openDetail(row)">详情</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            加载中...
          </div>
          <span v-else>暂无投诉数据</span>
        </template>
      </BaseTable>
    </div>

    <BasePagination
      v-if="total > 0"
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      @change="loadComplaints"
    />

    <!-- Detail Side Panel -->
    <Teleport to="body">
      <div v-if="detail" class="fixed inset-0 z-50 flex justify-end">
        <div class="absolute inset-0 bg-black/30 backdrop-blur-sm" @click="detail = null" />
        <div class="relative w-full max-w-lg bg-surface border-l border-default shadow-xl overflow-y-auto">
          <div class="sticky top-0 z-10 bg-surface border-b border-default px-6 py-4 flex items-center justify-between">
            <h2 class="text-lg font-semibold">投诉详情</h2>
            <button @click="detail = null" class="p-1.5 rounded-md text-muted hover:text-default hover:bg-surface-hover transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-6">
            <!-- Basic Info -->
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">基本信息</h3>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div><span class="text-muted">投诉ID：</span><span class="font-mono text-xs text-default">{{ detail.complaint_id }}</span></div>
                <div><span class="text-muted">分类：</span><span class="text-default">{{ categoryLabels[detail.category as keyof typeof categoryLabels] || detail.category }}</span></div>
                <div v-if="detail.sub_category"><span class="text-muted">子分类：</span><span class="text-default">{{ detail.sub_category }}</span></div>
                <div><span class="text-muted">状态：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="statusBadgeClass(detail.status)">{{ statusLabels[detail.status as keyof typeof statusLabels] || detail.status }}</span>
                </div>
                <div><span class="text-muted">紧急程度：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="urgencyBadgeClass(detail.urgency)">{{ urgencyLabels[detail.urgency as keyof typeof urgencyLabels] || detail.urgency }}</span>
                </div>
                <div v-if="detail.customer_emotion"><span class="text-muted">客户情绪：</span><span :class="emotionClass(detail.customer_emotion)">{{ emotionLabels[detail.customer_emotion as keyof typeof emotionLabels] || detail.customer_emotion }}</span></div>
                <div v-if="detail.order_id"><span class="text-muted">订单ID：</span><span class="text-default">{{ detail.order_id }}</span></div>
                <div v-if="detail.escalated_to"><span class="text-muted">升级至：</span><span class="text-default">{{ detail.escalated_to }}</span></div>
                <div><span class="text-muted">创建时间：</span><span class="text-default tabular-nums">{{ formatDate(detail.created_at) }}</span></div>
                <div v-if="detail.resolved_at"><span class="text-muted">解决时间：</span><span class="text-default tabular-nums">{{ formatDate(detail.resolved_at) }}</span></div>
              </div>
            </div>

            <!-- Description -->
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">投诉内容</h3>
              <p class="text-sm text-muted bg-canvas rounded-lg p-3 border border-default">{{ detail.description }}</p>
            </div>

            <!-- Resolution -->
            <div v-if="detail.resolution">
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">处理结果</h3>
              <p class="text-sm text-muted bg-success-50 rounded-lg p-3 border border-success-200">{{ detail.resolution }}</p>
            </div>

            <!-- Interactions -->
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">交互记录 ({{ detail.interactions?.length || 0 }})</h3>
              <div v-if="!detail.interactions?.length" class="text-sm text-muted py-2">暂无交互记录</div>
              <div v-else class="space-y-3">
                <div
                  v-for="interaction in detail.interactions"
                  :key="interaction.id"
                  class="p-3 rounded-lg border border-default bg-canvas"
                >
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-xs font-medium" :class="senderTypeClass(interaction.sender_type)">{{ senderTypeLabels[interaction.sender_type] || interaction.sender_type }}</span>
                    <span class="text-xs text-muted tabular-nums">{{ formatDate(interaction.created_at) }}</span>
                  </div>
                  <p class="text-sm text-muted">{{ interaction.content }}</p>
                </div>
              </div>
            </div>

            <!-- Followups -->
            <div v-if="detail.followups?.length">
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">跟进任务</h3>
              <div class="space-y-2">
                <div
                  v-for="followup in detail.followups"
                  :key="followup.id"
                  class="p-3 rounded-lg border border-default bg-canvas"
                >
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-sm text-default">{{ followup.action }}</span>
                    <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="followupStatusClass(followup.status)">{{ followupStatusLabel(followup.status) }}</span>
                  </div>
                  <div v-if="followup.assigned_to" class="text-xs text-muted">负责人: {{ followup.assigned_to }}</div>
                  <div v-if="followup.due_date" class="text-xs text-muted">截止: {{ formatDate(followup.due_date) }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { complaintAPI, type Complaint } from '@/api/complaint'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'

const statusLabels: Record<string, string> = {
  open: '待处理', classifying: '分类中', in_progress: '处理中',
  resolved: '已解决', closed: '已关闭', escalated: '已升级',
}
const categoryLabels: Record<string, string> = {
  '产品质量': '产品质量', '服务态度': '服务态度', '物流配送': '物流配送',
  '虚假宣传': '虚假宣传', '售后服务': '售后服务', '价格争议': '价格争议',
  '隐私安全': '隐私安全', '其他': '其他',
}
const urgencyLabels: Record<string, string> = {
  normal: '一般', high: '紧急', urgent: '非常紧急', critical: '危急',
}
const emotionLabels: Record<string, string> = {
  neutral: '平静', dissatisfied: '不满', angry: '愤怒', furious: '极度愤怒',
}
const senderTypeLabels: Record<string, string> = {
  customer: '客户', agent: 'AI助手', system: '系统', supervisor: '主管',
}

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'complaint_id', label: '投诉ID' },
  { key: 'category', label: '分类' },
  { key: 'urgency', label: '紧急程度' },
  { key: 'status', label: '状态' },
  { key: 'customer_emotion', label: '情绪' },
  { key: 'description', label: '投诉内容' },
  { key: 'interaction_count', label: '交互数' },
  { key: 'created_at', label: '创建时间' },
  { key: 'actions', label: '操作', width: '80px' },
]

const complaints = ref<Complaint[]>([])
const total = ref(0)
const detail = ref<Complaint | null>(null)
const filters = ref({ status: '', category: '', urgency: '', keyword: '' })

const { currentPage, pageSize, loading, seqNumber, handleSearch } =
  usePageContext(async () => { await loadComplaints() })

const statsCards = computed(() => [
  { key: 'total', label: '总投诉', value: total.value, iconBg: 'bg-surface-hover', iconColor: 'text-default', iconPath: 'M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z' },
  { key: 'open', label: '待处理', value: complaints.value.filter(c => c.status === 'open').length, iconBg: 'bg-info-100', iconColor: 'text-info-600', iconPath: 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z' },
  { key: 'escalated', label: '已升级', value: complaints.value.filter(c => c.status === 'escalated').length, iconBg: 'bg-danger-100', iconColor: 'text-danger-600', iconPath: 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z' },
  { key: 'resolved', label: '已解决', value: complaints.value.filter(c => c.status === 'resolved' || c.status === 'closed').length, iconBg: 'bg-success-100', iconColor: 'text-success-600', iconPath: 'M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
])

function statusBadgeClass(status: string) {
  const map: Record<string, string> = {
    open: 'bg-info-100 text-info-700',
    classifying: 'bg-surface-hover text-default',
    in_progress: 'bg-warning-100 text-warning-700',
    resolved: 'bg-success-100 text-success-700',
    closed: 'bg-surface-hover text-muted',
    escalated: 'bg-danger-100 text-danger-700',
  }
  return map[status] || 'bg-surface-hover text-default'
}

function urgencyBadgeClass(urgency: string) {
  const map: Record<string, string> = {
    normal: 'bg-surface-hover text-default',
    high: 'bg-warning-100 text-warning-700',
    urgent: 'bg-warning-100 text-warning-700',
    critical: 'bg-danger-100 text-danger-700',
  }
  return map[urgency] || 'bg-surface-hover text-default'
}

function emotionClass(emotion: string) {
  const map: Record<string, string> = {
    neutral: 'text-muted', dissatisfied: 'text-warning-600',
    angry: 'text-danger-600', furious: 'text-danger-700 font-medium',
  }
  return map[emotion] || 'text-muted'
}

function senderTypeClass(type: string) {
  const map: Record<string, string> = {
    customer: 'text-info-600', agent: 'text-success-600',
    system: 'text-muted', supervisor: 'text-primary-600',
  }
  return map[type] || 'text-muted'
}

function followupStatusClass(status: string) {
  const map: Record<string, string> = {
    pending: 'bg-warning-100 text-warning-700',
    in_progress: 'bg-info-100 text-info-700',
    done: 'bg-success-100 text-success-700',
    skipped: 'bg-surface-hover text-muted',
  }
  return map[status] || 'bg-surface-hover text-muted'
}

function followupStatusLabel(status: string) {
  const map: Record<string, string> = { pending: '待处理', in_progress: '进行中', done: '已完成', skipped: '已跳过' }
  return map[status] || status
}

function formatDate(dateStr?: string | null) {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '-' }
}

async function loadComplaints() {
  try {
    const params: Record<string, any> = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    if (filters.value.status) params.status = filters.value.status
    if (filters.value.category) params.category = filters.value.category
    if (filters.value.urgency) params.urgency = filters.value.urgency
    if (filters.value.keyword) params.keyword = filters.value.keyword
    const data = await complaintAPI.listComplaints(params)
    complaints.value = data.items
    total.value = data.total
  } catch (e: any) {
    console.error('加载投诉列表失败:', e)
  }
}

function resetFilters() {
  filters.value = { status: '', category: '', urgency: '', keyword: '' }
  handleSearch('')
}

async function openDetail(row: any) {
  try {
    detail.value = await complaintAPI.getComplaint(row.complaint_id)
  } catch (e: any) {
    alert(e.message || '加载详情失败')
  }
}

onMounted(() => { loadComplaints() })
</script>

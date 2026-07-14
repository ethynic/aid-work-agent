<template>
  <div class="page-container">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">售后工单</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">售后工单全生命周期管理</p>
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
      <BaseSelect v-model="filters.priority" size="sm" class="w-28">
        <option value="">全部优先级</option>
        <option v-for="(label, key) in priorityLabels" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="filters.keyword" placeholder="搜索工单内容、工单ID..." size="sm" class="w-56" @keyup.enter="handleSearch(filters.keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(filters.keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="tickets" row-key="ticket_id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #ticket_id="{ row }">
          <span class="font-mono text-xs text-muted">{{ row.ticket_id }}</span>
        </template>
        <template #category="{ row }">
          <span>{{ categoryLabels[row.category as keyof typeof categoryLabels] || row.category }}</span>
        </template>
        <template #priority="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="priorityBadgeClass(row.priority)">
            {{ priorityLabels[row.priority as keyof typeof priorityLabels] || row.priority }}
          </span>
        </template>
        <template #status="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="statusBadgeClass(row.status)">
            {{ statusLabels[row.status as keyof typeof statusLabels] || row.status }}
          </span>
        </template>
        <template #description="{ row }">
          <span class="block max-w-[200px] truncate text-muted">{{ row.description_short || row.description }}</span>
        </template>
        <template #message_count="{ row }">
          <span class="tabular-nums text-muted">{{ row.message_count || 0 }}</span>
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
          <span v-else>暂无工单数据</span>
        </template>
      </BaseTable>
    </div>

    <BasePagination
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      @change="loadTickets"
    />

    <!-- Detail Modal -->
    <BaseModal v-model="showDetail" title="工单详情" size="lg" mode="view">
      <template v-if="detail">
        <!-- Basic Info -->
        <div>
          <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">基本信息</h3>
          <div class="grid grid-cols-2 gap-3 text-sm">
            <div><span class="text-muted">工单ID：</span><span class="font-mono text-xs text-default">{{ detail.ticket_id }}</span></div>
            <div><span class="text-muted">分类：</span><span class="text-default">{{ categoryLabels[detail.category as keyof typeof categoryLabels] || detail.category }}</span></div>
            <div><span class="text-muted">状态：</span>
              <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="statusBadgeClass(detail.status)">{{ statusLabels[detail.status as keyof typeof statusLabels] || detail.status }}</span>
            </div>
            <div><span class="text-muted">优先级：</span>
              <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="priorityBadgeClass(detail.priority)">{{ priorityLabels[detail.priority as keyof typeof priorityLabels] || detail.priority }}</span>
            </div>
            <div v-if="detail.order_id"><span class="text-muted">订单ID：</span><span class="text-default">{{ detail.order_id }}</span></div>
            <div><span class="text-muted">创建时间：</span><span class="text-default tabular-nums">{{ formatDate(detail.created_at) }}</span></div>
            <div v-if="detail.updated_at"><span class="text-muted">更新时间：</span><span class="text-default tabular-nums">{{ formatDate(detail.updated_at) }}</span></div>
          </div>
        </div>

        <!-- Description -->
        <div class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">问题描述</h3>
          <p class="text-sm text-muted bg-canvas rounded-lg p-3 border border-default">{{ detail.description }}</p>
        </div>

        <!-- Resolution -->
        <div v-if="detail.resolution" class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">处理结果</h3>
          <p class="text-sm text-muted bg-success-50 rounded-lg p-3 border border-success-200">{{ detail.resolution }}</p>
        </div>

        <!-- Messages -->
        <div class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">消息记录 ({{ detail.messages?.length || 0 }})</h3>
          <div v-if="!detail.messages?.length" class="text-sm text-muted py-2">暂无消息记录</div>
          <div v-else class="space-y-3">
            <div
              v-for="msg in detail.messages"
              :key="msg.id"
              class="p-3 rounded-lg border border-default bg-canvas"
            >
              <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-medium" :class="senderTypeClass(msg.sender_type)">{{ senderTypeLabels[msg.sender_type] || msg.sender_type }}</span>
                <span class="text-xs text-muted tabular-nums">{{ formatDate(msg.created_at) }}</span>
              </div>
              <p class="text-sm text-muted">{{ msg.content }}</p>
            </div>
          </div>
        </div>
      </template>
      <template #footer>
        <BaseButton intent="secondary" @click="showDetail = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { afterSalesAPI, type Ticket } from '@/api/afterSales'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { usePageContext } from '@/composables/usePageContext'
import { formatShortDateTime as formatDate } from '@/utils/date'

const statusLabels: Record<string, string> = {
  open: '待处理', in_progress: '处理中',
  resolved: '已解决', closed: '已关闭',
}
const categoryLabels: Record<string, string> = {
  order_issue: '订单问题', return: '退货', exchange: '换货',
  repair: '维修', usage: '使用问题', other: '其他',
}
const priorityLabels: Record<string, string> = {
  low: '低', normal: '普通', high: '高', urgent: '紧急',
}
const senderTypeLabels: Record<string, string> = {
  customer: '客户', agent: 'AI助手', system: '系统',
}

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'ticket_id', label: '工单ID' },
  { key: 'category', label: '分类' },
  { key: 'priority', label: '优先级' },
  { key: 'status', label: '状态' },
  { key: 'description', label: '问题描述' },
  { key: 'message_count', label: '消息数' },
  { key: 'created_at', label: '创建时间' },
  { key: 'actions', label: '操作', width: '80px' },
]

const tickets = ref<Ticket[]>([])
const total = ref(0)
const detail = ref<Ticket | null>(null)
const showDetail = ref(false)
const filters = ref({ status: '', category: '', priority: '', keyword: '' })

const { currentPage, pageSize, loading, seqNumber, handleSearch } =
  usePageContext(async () => { await loadTickets() })

const statsCards = computed(() => [
  { key: 'total', label: '总工单', value: total.value, iconBg: 'bg-surface-hover', iconColor: 'text-default', iconPath: 'M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15a2.25 2.25 0 012.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z' },
  { key: 'open', label: '待处理', value: tickets.value.filter(t => t.status === 'open').length, iconBg: 'bg-info-100', iconColor: 'text-info-600', iconPath: 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z' },
  { key: 'in_progress', label: '处理中', value: tickets.value.filter(t => t.status === 'in_progress').length, iconBg: 'bg-warning-100', iconColor: 'text-warning-600', iconPath: 'M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182' },
  { key: 'resolved', label: '已解决', value: tickets.value.filter(t => t.status === 'resolved' || t.status === 'closed').length, iconBg: 'bg-success-100', iconColor: 'text-success-600', iconPath: 'M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
])

function statusBadgeClass(status: string) {
  const map: Record<string, string> = {
    open: 'bg-info-100 text-info-700',
    in_progress: 'bg-warning-100 text-warning-700',
    resolved: 'bg-success-100 text-success-700',
    closed: 'bg-surface-hover text-muted',
  }
  return map[status] || 'bg-surface-hover text-default'
}

function priorityBadgeClass(priority: string) {
  const map: Record<string, string> = {
    low: 'bg-surface-hover text-muted',
    normal: 'bg-surface-hover text-default',
    high: 'bg-warning-100 text-warning-700',
    urgent: 'bg-danger-100 text-danger-700',
  }
  return map[priority] || 'bg-surface-hover text-default'
}

function senderTypeClass(type: string) {
  const map: Record<string, string> = {
    customer: 'text-info-600', agent: 'text-success-600',
    system: 'text-muted',
  }
  return map[type] || 'text-muted'
}

async function loadTickets() {
  try {
    const params: Record<string, any> = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    if (filters.value.status) params.status = filters.value.status
    if (filters.value.category) params.category = filters.value.category
    if (filters.value.priority) params.priority = filters.value.priority
    if (filters.value.keyword) params.keyword = filters.value.keyword
    const data = await afterSalesAPI.listTickets(params)
    tickets.value = data.items
    total.value = data.total
  } catch (e: any) {
    console.error('加载工单列表失败:', e)
  }
}

function resetFilters() {
  filters.value = { status: '', category: '', priority: '', keyword: '' }
  handleSearch('')
}

async function openDetail(row: any) {
  try {
    detail.value = await afterSalesAPI.getTicket(row.ticket_id)
    showDetail.value = true
  } catch (e: any) {
    alert(e.message || '加载详情失败')
  }
}

onMounted(() => { loadTickets() })
</script>

<template>
  <div class="page-container">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">退换货记录</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">退换货申请与处理记录</p>
      </div>
    </div>

    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.status" size="sm" class="w-28">
        <option value="">全部状态</option>
        <option v-for="(label, key) in returnStatusLabels" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.return_type" size="sm" class="w-28">
        <option value="">全部类型</option>
        <option value="return">退货</option>
        <option value="exchange">换货</option>
      </BaseSelect>
      <BaseInput v-model="filters.keyword" placeholder="搜索订单号、原因..." size="sm" class="w-56" @keyup.enter="handleSearch(filters.keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(filters.keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="records" row-key="return_id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #return_id="{ row }">
          <span class="font-mono text-xs text-muted">{{ row.return_id }}</span>
        </template>
        <template #order_id="{ row }">
          <span class="font-mono text-xs">{{ row.order_id }}</span>
        </template>
        <template #type="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="typeBadgeClass(row.type)">
            {{ row.type === 'return' ? '退货' : '换货' }}
          </span>
        </template>
        <template #reason="{ row }">
          <span class="block max-w-[200px] truncate text-muted">{{ row.reason }}</span>
        </template>
        <template #status="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="statusBadgeClass(row.status)">
            {{ returnStatusLabels[row.status as keyof typeof returnStatusLabels] || row.status }}
          </span>
        </template>
        <template #refund_amount="{ row }">
          <span v-if="row.refund_amount" class="tabular-nums">¥{{ Number(row.refund_amount).toFixed(2) }}</span>
          <span v-else class="text-muted">-</span>
        </template>
        <template #created_at="{ row }">{{ formatDate(row.created_at) }}</template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            加载中...
          </div>
          <span v-else>暂无退换货记录</span>
        </template>
      </BaseTable>
    </div>

    <BasePagination
      v-if="total > 0"
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      @change="loadReturns"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { afterSalesAPI, type ReturnRecord } from '@/api/afterSales'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'

const returnStatusLabels: Record<string, string> = {
  pending: '待处理', approved: '已批准', processing: '处理中',
  completed: '已完成', rejected: '已拒绝',
}

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'return_id', label: '记录ID' },
  { key: 'order_id', label: '订单号' },
  { key: 'type', label: '类型' },
  { key: 'reason', label: '原因' },
  { key: 'status', label: '状态' },
  { key: 'refund_amount', label: '退款金额' },
  { key: 'created_at', label: '创建时间' },
]

const records = ref<ReturnRecord[]>([])
const total = ref(0)
const filters = ref({ status: '', return_type: '', keyword: '' })

const { currentPage, pageSize, loading, seqNumber, handleSearch } =
  usePageContext(async () => { await loadReturns() })

function typeBadgeClass(type: string) {
  return type === 'return'
    ? 'bg-warning-100 text-warning-700'
    : 'bg-info-100 text-info-700'
}

function statusBadgeClass(status: string) {
  const map: Record<string, string> = {
    pending: 'bg-info-100 text-info-700',
    approved: 'bg-success-100 text-success-700',
    processing: 'bg-warning-100 text-warning-700',
    completed: 'bg-success-100 text-success-700',
    rejected: 'bg-danger-100 text-danger-700',
  }
  return map[status] || 'bg-surface-hover text-default'
}

function formatDate(dateStr?: string | null) {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '-' }
}

async function loadReturns() {
  try {
    const params: Record<string, any> = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    if (filters.value.status) params.status = filters.value.status
    if (filters.value.return_type) params.return_type = filters.value.return_type
    if (filters.value.keyword) params.keyword = filters.value.keyword
    const data = await afterSalesAPI.listReturns(params)
    records.value = data.items
    total.value = data.total
  } catch (e: any) {
    console.error('加载退换货记录失败:', e)
  }
}

function resetFilters() {
  filters.value = { status: '', return_type: '', keyword: '' }
  handleSearch('')
}

onMounted(() => { loadReturns() })
</script>

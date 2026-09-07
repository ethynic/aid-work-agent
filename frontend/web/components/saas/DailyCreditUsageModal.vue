<template>
  <BaseModal
    v-model="visible"
    :title="`每日积分用量 - ${tenantName}（${month}）`"
    size="xl"
    mode="view"
    :scrollable="false"
  >
    <div v-if="loading" class="text-center py-8 text-muted">加载中...</div>
    <template v-else>
      <div class="flex flex-col h-[calc(90vh-6rem)]">
        <!-- 余额 + 用量汇总卡片（与租户前台积分用量页一致） -->
        <div v-if="balance || usageSummary" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">积分余额</div>
            <div class="text-xl font-bold text-primary-600 mt-1">{{ formatCredit(balance?.credit_balance) }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">日均消耗</div>
            <div class="text-xl font-bold text-default mt-1">{{ formatCredit(balance?.daily_avg_cost) }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">预估可用天数</div>
            <div class="text-xl font-bold text-default mt-1">{{ formatEstimatedDays(balance?.estimated_days_left) }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">查询期总消耗积分</div>
            <div class="text-xl font-bold text-danger-600 mt-1">{{ formatCredit(usageSummary?.total_credit_cost) }}</div>
          </div>
        </div>

        <!-- 按日聚合明细表格 -->
        <div class="flex-1 min-h-0 mb-4 table-scroll-wrapper">
          <BaseTable :columns="columns" :data="data" row-key="date">
            <template #index="{ index }">{{ (currentPage - 1) * pageSize + index + 1 }}</template>
            <template #date="{ row }">{{ row.date || '-' }}</template>
            <template #credit_cost="{ row }">
              <a
                class="text-danger-600 font-medium underline-offset-2 hover:underline cursor-pointer"
                @click="$emit('view-detail', row.date)"
              >{{ formatCredit(row.credit_cost) }}</a>
            </template>
            <template #session_count="{ row }">{{ row.session_count }}</template>
            <template #message_count="{ row }">{{ row.message_count }}</template>
            <template #client_call_count="{ row }">
              <span :title="row.client_credit_cost != null ? `客户端消耗 ${formatCredit(row.client_credit_cost)} 积分` : ''">
                {{ row.client_call_count || 0 }}
              </span>
            </template>
            <template #empty>该月暂无用量数据</template>
          </BaseTable>
        </div>

        <div class="flex-shrink-0 flex items-center justify-center">
          <BasePagination
            :total="total"
            v-model:currentPage="currentPage"
            v-model:pageSize="pageSize"
            :size-options="[10, 20, 50, 100]"
            @change="loadData"
          />
        </div>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { formatCredit } from '@/utils/formatCredit'
import {
  getTenantBalance,
  getTenantUsage,
  type BalanceInfo,
  type UsageItem,
  type UsageSummary
} from '@/api/billing'

const props = defineProps<{
  modelValue: boolean
  /** 目标租户 ID（平台管理员代管理，注入 X-Tenant-Id） */
  tenantId: string
  tenantName: string
  /** 月份，格式 YYYY-MM（与管理后台报表页所选月份一致） */
  month: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'view-detail', date: string): void
}>()

const toast = useToast()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

const loading = ref(false)
const balance = ref<BalanceInfo | null>(null)
const data = ref<UsageItem[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const usageSummary = ref<UsageSummary | null>(null)

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'date', label: '日期', width: '140px' },
  { key: 'credit_cost', label: '消耗积分', width: '120px' },
  { key: 'session_count', label: '会话数', width: '100px' },
  { key: 'message_count', label: '消息数', width: '100px' },
  { key: 'client_call_count', label: '客户端调用', width: '100px' },
]

// 月份的起止日期（date_to 取该月最后一天）
function monthRange(month: string): { dateFrom: string; dateTo: string } {
  const [year, mon] = month.split('-').map(Number)
  const lastDay = new Date(year, mon, 0).getDate()
  return { dateFrom: `${month}-01`, dateTo: `${month}-${String(lastDay).padStart(2, '0')}` }
}

function formatEstimatedDays(days: number | null | undefined): string {
  if (days === null || days === undefined) return '-'
  if (days < 0) return '暂无数据'
  if (days > 365) return '365+'
  return `${days}`
}

async function loadData() {
  if (!props.tenantId || !props.month) return
  loading.value = true
  try {
    const { dateFrom, dateTo } = monthRange(props.month)
    const [balanceRes, usageRes] = await Promise.all([
      getTenantBalance(props.tenantId),
      getTenantUsage({
        date_from: dateFrom,
        date_to: dateTo,
        page: currentPage.value,
        page_size: pageSize.value
      }, props.tenantId)
    ])

    if (balanceRes.success && balanceRes.balance) {
      balance.value = balanceRes.balance
    } else {
      balance.value = null
      if (!balanceRes.success) {
        toast.error(balanceRes.message || '获取积分余额失败')
      }
    }

    if (usageRes.success) {
      data.value = usageRes.items || []
      total.value = usageRes.total || 0
      usageSummary.value = usageRes.summary || null
    } else {
      toast.error(usageRes.message || '加载每日积分用量失败')
      data.value = []
      total.value = 0
      usageSummary.value = null
    }
  } catch (error: any) {
    console.error('加载每日积分用量失败:', error)
    toast.error(error.message || '加载数据失败')
    balance.value = null
    data.value = []
    total.value = 0
    usageSummary.value = null
  } finally {
    loading.value = false
  }
}

// 打开弹框时从第一页加载
watch(() => props.modelValue, (open) => {
  if (open) {
    currentPage.value = 1
    loadData()
  }
})
</script>

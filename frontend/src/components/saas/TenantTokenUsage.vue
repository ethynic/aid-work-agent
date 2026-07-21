<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      title="积分用量"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <div class="flex-1 min-h-0 overflow-y-auto p-6">
      <!-- 顶部工具栏 -->
      <div class="page-toolbar">
        <div class="page-toolbar-left">
          <div class="flex items-center gap-2">
            <label class="text-sm text-default">开始日期:</label>
            <input type="date" v-model="dateFrom" @change="handleFilterChange"
              class="px-3 py-1.5 border border-default rounded-lg text-sm bg-surface focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-200">
            <span class="text-muted text-sm">至</span>
            <input type="date" v-model="dateTo" @change="handleFilterChange"
              class="px-3 py-1.5 border border-default rounded-lg text-sm bg-surface focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-200">
          </div>
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" @click="loadData(1)">刷新</BaseButton>
        </div>
      </div>

      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

      <template v-else>
        <!-- 余额 + 用量汇总卡片 -->
        <div v-if="balance || usageSummary" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">积分余额</div>
            <div class="text-xl font-bold text-primary-600 mt-1">{{ balance?.credit_balance ?? '-' }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">近7天日均消耗</div>
            <div class="text-xl font-bold text-default mt-1">{{ balance?.daily_avg_cost_7d ?? 0 }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">预估可用天数</div>
            <div class="text-xl font-bold text-default mt-1">{{ formatEstimatedDays(balance?.estimated_days_left) }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">查询期总消耗积分</div>
            <div class="text-xl font-bold text-danger-600 mt-1">{{ usageSummary?.total_credit_cost ?? 0 }}</div>
          </div>
        </div>

        <!-- 按日聚合明细表格 -->
        <div class="mb-4">
          <h3 class="text-sm font-medium text-default mb-3">每日用量明细</h3>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="columns" :data="data" row-key="date">
              <template #index="{ index }">{{ seqNumber(index) }}</template>
              <template #date="{ row }">{{ row.date || '-' }}</template>
              <template #credit_cost="{ row }">
                <span class="text-danger-600 font-medium">{{ row.credit_cost }}</span>
              </template>
              <template #session_count="{ row }">{{ row.session_count }}</template>
              <template #message_count="{ row }">{{ row.message_count }}</template>
              <template #empty>暂无数据</template>
            </BaseTable>
          </div>
        </div>

        <!-- 分页控件 -->
        <div class="flex items-center justify-center">
          <BasePagination
            :total="total"
            v-model:currentPage="currentPage"
            v-model:pageSize="pageSize"
            :size-options="[10, 20, 50, 100]"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { getTenantBalance, getTenantUsage, type BalanceInfo, type UsageItem, type UsageSummary } from '@/api/billing'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

// 统一的用户信息
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 从 PortalLayout 注入侧边栏状态
const toggleSidebarFn = inject<() => void>('toggleSidebar')

function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  }
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${route.params.tenant_id}/login`)
}

const loading = ref(true)
const dateFrom = ref('')
const dateTo = ref('')
const balance = ref<BalanceInfo | null>(null)
const data = ref<UsageItem[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
// 查询期全量汇总（后端返回，跨页稳定）
const usageSummary = ref<UsageSummary | null>(null)

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'date', label: '日期', width: '140px' },
  { key: 'credit_cost', label: '消耗积分', width: '120px' },
  { key: 'session_count', label: '会话数', width: '100px' },
  { key: 'message_count', label: '消息数', width: '100px' },
]

function seqNumber(index: number): number {
  return (currentPage.value - 1) * pageSize.value + index + 1
}

function formatEstimatedDays(days: number | null | undefined): string {
  if (days === null || days === undefined) return '-'
  if (days < 0) return '暂无数据'
  return `${days} 天`
}

async function loadData(page: number = 1) {
  loading.value = true
  try {
    // 并行拉取余额 + 用量明细
    const [balanceRes, usageRes] = await Promise.all([
      getTenantBalance(),
      getTenantUsage({
        date_from: dateFrom.value || undefined,
        date_to: dateTo.value || undefined,
        page,
        page_size: pageSize.value
      })
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
      toast.error(usageRes.message || '加载用量明细失败')
      data.value = []
      total.value = 0
      usageSummary.value = null
    }
  } catch (error: any) {
    console.error('加载积分用量明细失败:', error)
    toast.error(error.message || '加载数据失败')
    balance.value = null
    data.value = []
    total.value = 0
    usageSummary.value = null
  } finally {
    loading.value = false
  }
}

function handleFilterChange() {
  currentPage.value = 1
  loadData(1)
}

watch(currentPage, (newPage) => loadData(newPage))

onMounted(() => loadData(1))
</script>

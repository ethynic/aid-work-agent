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
        <div class="mb-4">
          <h3 class="text-sm font-medium text-default mb-3">每日用量明细</h3>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="columns" :data="data" row-key="date">
              <template #index="{ index }">{{ seqNumber(index) }}</template>
              <template #date="{ row }">{{ row.date || '-' }}</template>
              <template #credit_cost="{ row }">
                <span v-if="!canViewDetail" class="text-danger-600 font-medium">{{ formatCredit(row.credit_cost) }}</span>
                <a v-else
                   class="text-danger-600 font-medium underline-offset-2 hover:underline cursor-pointer"
                   @click="openDetailModal(row)">
                  {{ formatCredit(row.credit_cost) }}
                </a>
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

    <!-- 每日用量明细弹窗（平台管理员 + 租户管理员可见入口） -->
    <BaseModal
      v-model="showDetailModal"
      :title="`对话用量明细 - ${detailDate}`"
      size="xl"
      mode="view"
      :scrollable="false"
    >
      <div v-if="detailLoading" class="text-center py-8 text-muted">加载中...</div>
      <template v-else>
        <div class="flex flex-col h-[calc(90vh-6rem)]">
          <div class="flex-1 min-h-0 mb-4 table-scroll-wrapper">
            <BaseTable :columns="detailColumns" :data="detailData" row-key="record_id">
              <template #index="{ index }">
                {{ (detailCurrentPage - 1) * detailPageSize + index + 1 }}
              </template>
              <template #session_title="{ row }">
                <span :title="row.session_title">{{ row.session_title }}</span>
              </template>
              <template #user_message="{ row }">
                <span :title="row.user_message">{{ truncateText(row.user_message) }}</span>
              </template>
              <template #assistant_message="{ row }">
                <span :title="row.assistant_message">{{ truncateText(row.assistant_message) }}</span>
              </template>
              <template #bd_non_cached_input="{ row }">{{ formatBreakdown(row, 'non_cached_input') }}</template>
              <template #bd_cached_input="{ row }">{{ formatBreakdown(row, 'cached_input') }}</template>
              <template #bd_output="{ row }">{{ formatBreakdown(row, 'output') }}</template>
              <template #bd_video="{ row }">{{ formatBreakdown(row, 'video') }}</template>
              <template #bd_asr="{ row }">{{ formatBreakdown(row, 'asr') }}</template>
              <template #bd_embedding="{ row }">{{ formatBreakdown(row, 'embedding') }}</template>
              <template #credit_cost="{ row }">
                <span class="text-danger-600 font-medium">{{ formatCredit(row.credit_cost) }}</span>
              </template>
              <template #empty>该日暂无明细数据</template>
            </BaseTable>
          </div>
          <div class="flex-shrink-0 flex items-center justify-center">
            <BasePagination
              :total="detailTotal"
              v-model:currentPage="detailCurrentPage"
              v-model:pageSize="detailPageSize"
              :size-options="[20, 50, 100]"
              @change="handleDetailPageChange"
            />
          </div>
        </div>
      </template>
    </BaseModal>
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
import BaseModal from '@/components/ui/BaseModal.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { formatCredit } from '@/utils/formatCredit'
import {
  getTenantBalance,
  getTenantUsage,
  getDailyUsageDetail,
  type BalanceInfo,
  type UsageItem,
  type UsageSummary,
  type DailyUsageDetailItem,
  type BreakdownItem
} from '@/api/billing'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 平台管理员判断
const isPlatformAdmin = computed(() => tenantAdmin.value?.role === 'platform_admin')
// 租户管理员判断
const isTenantAdmin = computed(() => tenantAdmin.value?.role === 'tenant_admin')
// 「消耗积分」下钻链接与弹窗入口：平台管理员 + 租户管理员可见
const canViewDetail = computed(() => isPlatformAdmin.value || isTenantAdmin.value)

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

// ============== 每日用量明细弹窗（平台管理员 + 租户管理员） ==============

const showDetailModal = ref(false)
const detailLoading = ref(false)
const detailData = ref<DailyUsageDetailItem[]>([])
const detailTotal = ref(0)
const detailCurrentPage = ref(1)
const detailPageSize = ref(20)
const detailDate = ref('')

// usage_breakdown 6 分项列（仅平台管理员）：dataKey 对应 breakdown_items 的分项 key
const bdDetailCols = [
  { key: 'bd_non_cached_input', dataKey: 'non_cached_input', label: '未命中缓存输入', width: '220px' },
  { key: 'bd_cached_input', dataKey: 'cached_input', label: '命中缓存输入', width: '220px' },
  { key: 'bd_output', dataKey: 'output', label: '输出', width: '190px' },
  { key: 'bd_video', dataKey: 'video', label: '视频模型', width: '180px' },
  { key: 'bd_asr', dataKey: 'asr', label: 'ASR', width: '170px' },
  { key: 'bd_embedding', dataKey: 'embedding', label: '向量模型', width: '220px' },
]

// 弹窗列定义：usage_breakdown 6 分项（未命中缓存输入/命中缓存输入/输出/视频模型/ASR/向量模型）仅平台管理员可见
const detailColumns = computed(() => {
  const cols: Array<{ key: string; label: string; width: string; tooltip?: (row: Record<string, any>) => string | undefined }> = [
    { key: 'index', label: '序号', width: '60px' },
    { key: 'created_at', label: '创建时间', width: '160px' },
    { key: 'session_title', label: '会话标题', width: '180px' },
    { key: 'user_message', label: '用户消息', width: '220px' },
    { key: 'assistant_message', label: '智能体回复', width: '220px' },
    { key: 'user_display', label: '用户', width: '200px' },
    { key: 'source_type', label: '来源', width: '120px' },
  ]
  if (isPlatformAdmin.value) {
    bdDetailCols.forEach((c) => cols.push({
      key: c.key,
      label: c.label,
      width: c.width,
      // 公式较长可能在列宽内截断，悬停显示完整对账公式
      tooltip: (row: Record<string, any>) => formatBreakdown(row, c.dataKey),
    }))
  }
  cols.push({ key: 'credit_cost', label: '消耗积分', width: '100px' })
  return cols
})

function truncateText(text: string | null | undefined, maxLen: number = 30): string {
  if (!text) return '-'
  if (text.length <= maxLen) return text
  return text.slice(0, maxLen) + '...'
}

// 数值格式化：整数/小数均加千分位，小数保留 6 位去尾零
function fmtNum(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '-'
  const num = typeof v === 'string' ? parseFloat(v) : Number(v)
  if (isNaN(num)) return '-'
  const trimmed = num.toFixed(6).replace(/\.?0+$/, '')
  const [intPart, decPart] = trimmed.split('.')
  const intFormatted = Number(intPart).toLocaleString('en-US')
  return decPart !== undefined ? `${intFormatted}.${decPart}` : intFormatted
}

// 渲染 usage_breakdown 分项：
// - 新数据（含单价/系数/分项积分）：{数量} * {单价} * {系数} / 1M = {积分}（每百万类）/ {数量} * {单价} * {系数} = {积分}
// - 老数据（8-14 前无单价/系数/分项积分）：降级只显示数量（token数/秒数/次数）
function formatBreakdown(row: Record<string, any>, key: string): string {
  const items: BreakdownItem[] = row.breakdown_items || []
  const item = items.find(i => i.key === key)
  if (!item) return '-'
  const { qty, unit_price, usage_factor, credit } = item
  if (qty === null || qty === undefined) return '-'
  // 老数据：仅数量可追溯，单价/系数/分项积分缺失时只显示数量
  if (unit_price === null || unit_price === undefined || usage_factor === null || usage_factor === undefined || credit === null || credit === undefined) {
    return fmtNum(qty)
  }
  const base = `${fmtNum(qty)} * ${fmtNum(unit_price)} * ${fmtNum(usage_factor)}`
  return item.is_per_million ? `${base} / 1M = ${fmtNum(credit)}` : `${base} = ${fmtNum(credit)}`
}

function openDetailModal(row: UsageItem | Record<string, any>) {
  // 二次保护：仅平台管理员 / 租户管理员可打开弹窗（链接本身也仅这两类角色渲染）
  if (!canViewDetail.value) return
  detailDate.value = row.date
  detailCurrentPage.value = 1
  showDetailModal.value = true
  loadDetailData()
}

async function loadDetailData() {
  if (!detailDate.value) return
  detailLoading.value = true
  try {
    const res = await getDailyUsageDetail({
      date: detailDate.value,
      page: detailCurrentPage.value,
      page_size: detailPageSize.value,
    })
    if (res.success) {
      detailData.value = res.items || []
      detailTotal.value = res.total || 0
    } else {
      toast.error(res.message || '加载对话用量明细失败')
      detailData.value = []
      detailTotal.value = 0
    }
  } catch (error: any) {
    console.error('加载对话用量明细失败:', error)
    toast.error(error.message || '加载对话用量明细失败')
    detailData.value = []
    detailTotal.value = 0
  } finally {
    detailLoading.value = false
  }
}

function handleDetailPageChange(page: number) {
  detailCurrentPage.value = page
  loadDetailData()
}

onMounted(() => loadData(1))
</script>

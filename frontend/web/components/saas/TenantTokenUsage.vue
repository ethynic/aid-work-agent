<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      :title="pageTitle"
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
        <!-- 私有云：7 分项 token 汇总卡片（无积分概念） -->
        <div v-if="privateCloud && usageSummary" class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-4 mb-6">
          <div v-for="c in tokenQtyCols" :key="c.key" class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">{{ c.label }}</div>
            <div class="text-xl font-bold text-default mt-1">{{ fmtNum(usageSummary.total_token_qty?.[c.key]) }}</div>
          </div>
        </div>
        <!-- 公有云：余额 + 用量汇总卡片 -->
        <div v-else-if="balance || usageSummary" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
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
            <BaseTable :columns="columns" :data="data" row-key="date" table-fixed>
              <template #index="{ index }">{{ seqNumber(index) }}</template>
              <template #date="{ row }">
                <!-- 私有云：积分列不存在，明细下钻入口移到日期列 -->
                <a v-if="privateCloud && canViewDetail"
                   class="text-primary-600 font-medium underline-offset-2 hover:underline cursor-pointer"
                   @click="openDetailModal(row)">{{ row.date || '-' }}</a>
                <span v-else>{{ row.date || '-' }}</span>
              </template>
              <template #tq_non_cached_input="{ row }">{{ fmtNum(row.token_qty?.non_cached_input) }}</template>
              <template #tq_cached_input="{ row }">{{ fmtNum(row.token_qty?.cached_input) }}</template>
              <template #tq_cache_creation_input="{ row }">{{ fmtNum(row.token_qty?.cache_creation_input) }}</template>
              <template #tq_output="{ row }">{{ fmtNum(row.token_qty?.output) }}</template>
              <template #tq_video="{ row }">{{ fmtNum(row.token_qty?.video) }}</template>
              <template #tq_asr="{ row }">{{ fmtNum(row.token_qty?.asr) }}</template>
              <template #tq_embedding="{ row }">{{ fmtNum(row.token_qty?.embedding) }}</template>
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
              <template #client_call_count="{ row }">
                <span :title="row.client_credit_cost != null ? `客户端消耗 ${formatCredit(row.client_credit_cost)} 积分` : ''">
                  {{ row.client_call_count || 0 }}
                </span>
              </template>
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
      :title="`${privateCloud ? '用量明细' : '积分用量明细'} - ${detailDate}`"
      size="xl"
      mode="view"
      :scrollable="false"
    >
      <div v-if="detailLoading" class="text-center py-8 text-muted">加载中...</div>
      <template v-else>
        <div class="flex flex-col h-[calc(90vh-6rem)]">
          <div class="flex-1 min-h-0 mb-4 table-scroll-wrapper">
            <BaseTable :columns="detailColumns" :data="detailData" row-key="record_id" table-fixed>
              <template #index="{ index }">
                {{ (detailCurrentPage - 1) * detailPageSize + index + 1 }}
              </template>
              <template #bd_non_cached_input="{ row }">{{ formatBreakdownQty(row, 'non_cached_input') }}</template>
              <template #bd_cached_input="{ row }">{{ formatBreakdownQty(row, 'cached_input') }}</template>
              <template #bd_cache_creation_input="{ row }">{{ formatBreakdownQty(row, 'cache_creation_input') }}</template>
              <template #bd_output="{ row }">{{ formatBreakdownQty(row, 'output') }}</template>
              <template #bd_video="{ row }">{{ formatBreakdownQty(row, 'video') }}</template>
              <template #bd_asr="{ row }">{{ formatBreakdownQty(row, 'asr') }}</template>
              <template #bd_embedding="{ row }">{{ formatBreakdownQty(row, 'embedding') }}</template>
              <template #usage_type="{ row }">
                <span v-if="row.usage_type === 'client'"
                      class="text-xs px-1.5 py-0.5 rounded bg-primary-50 text-primary-600 border border-primary-200">客户端</span>
                <span v-else class="text-xs text-muted">对话</span>
              </template>
              <template #session_title="{ row }">
                <!-- client 行会话列改显来源（BOSS 工具/协会采集等 stage 标签） -->
                <span v-if="row.usage_type === 'client'" class="text-xs text-default" :title="row.source_type">{{ row.source_type }}</span>
                <span v-else :title="row.session_title">{{ row.session_title }}</span>
              </template>
              <template #channel_label="{ row }">
                <!-- client 行来源已在会话列展示，渠道列避免重复 -->
                <span v-if="row.usage_type === 'client'">-</span>
                <span v-else :title="row.channel_label || ''">{{ row.channel_label || '-' }}</span>
              </template>
              <template #user_message="{ row }">
                <!-- client 行消息列改显命令名 -->
                <span v-if="row.usage_type === 'client'" class="text-default font-medium" :title="row.command">{{ row.command || '-' }}</span>
                <span v-else :title="row.user_message">{{ truncateText(row.user_message) }}</span>
              </template>
              <template #assistant_message="{ row }">
                <!-- client 行回复列改显参数摘要（JSON，悬停看全量） -->
                <span v-if="row.usage_type === 'client'" class="text-xs text-muted" :title="row.arguments">{{ truncateText(row.arguments, 40) }}</span>
                <span v-else :title="row.assistant_message">{{ truncateText(row.assistant_message) }}</span>
              </template>
              <template #model="{ row }">
                <span :title="row.model">{{ row.model || '-' }}</span>
              </template>
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
  type DailyUsageDetailItem
} from '@/api/billing'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout, privateCloud } = useTenantAuth()

// 平台管理员判断
const isPlatformAdmin = computed(() => tenantAdmin.value?.role === 'platform_admin')
// 租户管理员判断
const isTenantAdmin = computed(() => tenantAdmin.value?.role === 'tenant_admin')
// 「消耗积分」下钻链接与弹窗入口：平台管理员 + 租户管理员可见
const canViewDetail = computed(() => isPlatformAdmin.value || isTenantAdmin.value)

// 页面标题：私有云不出现积分概念
const pageTitle = computed(() => privateCloud.value ? 'Token 用量' : '积分用量')

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

// 7 分项定义（key 对应后端 token_qty / breakdown_items 的分项 key）
const tokenQtyCols = [
  { key: 'non_cached_input', label: '未命中缓存输入' },
  { key: 'cached_input', label: '命中缓存输入' },
  { key: 'cache_creation_input', label: '缓存创建输入' },
  { key: 'output', label: '输出' },
  { key: 'video', label: '视频模型(秒)' },
  { key: 'asr', label: 'ASR(次)' },
  { key: 'embedding', label: '向量模型' },
] as const

const columns = computed(() => {
  const base: Array<{ key: string; label: string; width: string }> = [
    { key: 'index', label: '序号', width: '60px' },
    { key: 'date', label: '日期', width: '140px' },
  ]
  if (privateCloud.value) {
    // 私有云：消耗积分列换成 7 分项 token 数量
    tokenQtyCols.forEach((c) => base.push({ key: `tq_${c.key}`, label: c.label, width: '130px' }))
  } else {
    base.push({ key: 'credit_cost', label: '消耗积分', width: '120px' })
  }
  base.push(
    { key: 'session_count', label: '会话数', width: '100px' },
    { key: 'message_count', label: '消息数', width: '100px' },
    { key: 'client_call_count', label: '客户端调用', width: '100px' },
  )
  return base
})

function seqNumber(index: number): number {
  return (currentPage.value - 1) * pageSize.value + index + 1
}

function formatEstimatedDays(days: number | null | undefined): string {
  if (days === null || days === undefined) return '-'
  if (days < 0) return '暂无数据'
  // >365 天无意义封顶显示 365+
  if (days > 365) return '365+'
  return `${days}`
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

// 弹窗列定义：类型列区分智能体对话与客户端调用（P3 双表口径）。
// 公有云仅积分列；私有云把积分列换成 7 分项数量列（后端已剥离单价/系数/积分，仅返回数量）
const detailColumns = computed(() => {
  const cols: Array<{ key: string; label: string; width: string }> = [
    { key: 'index', label: '序号', width: '60px' },
    { key: 'usage_type', label: '类型', width: '80px' },
    { key: 'created_at', label: '创建时间', width: '160px' },
    { key: 'session_title', label: '会话/来源', width: '180px' },
    { key: 'user_message', label: '消息/命令', width: '220px' },
    { key: 'assistant_message', label: '回复/参数', width: '220px' },
    { key: 'user_display', label: '用户', width: '200px' },
    { key: 'channel_label', label: '渠道会话', width: '150px' },
  ]
  if (privateCloud.value) {
    tokenQtyCols.forEach((c) => cols.push({ key: `bd_${c.key}`, label: c.label, width: '130px' }))
  } else {
    cols.push({ key: 'credit_cost', label: '消耗积分', width: '100px' })
  }
  return cols
})

// 数值格式化：千分位 + 小数去尾零（私有云 token/秒/次数量展示）
function fmtNum(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '-'
  const num = typeof v === 'string' ? parseFloat(v) : Number(v)
  if (isNaN(num)) return '-'
  const trimmed = num.toFixed(6).replace(/\.?0+$/, '')
  const [intPart, decPart] = trimmed.split('.')
  const intFormatted = Number(intPart).toLocaleString('en-US')
  return decPart !== undefined ? `${intFormatted}.${decPart}` : intFormatted
}

// 私有云明细弹窗：usage_breakdown 分项仅展示数量（后端已剥离单价/系数/积分）
function formatBreakdownQty(row: Record<string, any>, key: string): string {
  const item = (row.breakdown_items || []).find((i: any) => i.key === key)
  if (!item) return '-'
  return fmtNum(item.qty)
}

function truncateText(text: string | null | undefined, maxLen: number = 30): string {
  if (!text) return '-'
  if (text.length <= maxLen) return text
  return text.slice(0, maxLen) + '...'
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
      toast.error(res.message || '加载积分用量明细失败')
      detailData.value = []
      detailTotal.value = 0
    }
  } catch (error: any) {
    console.error('加载积分用量明细失败:', error)
    toast.error(error.message || '加载积分用量明细失败')
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

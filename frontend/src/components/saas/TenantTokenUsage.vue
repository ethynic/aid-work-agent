<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      title="站点Token用量"
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
            <label class="text-sm text-default">选择月份:</label>
            <input type="month" v-model="selectedMonth" @change="handleMonthChange"
              class="px-3 py-1.5 border border-default rounded-lg text-sm bg-surface focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-200">
          </div>
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" @click="loadData(1)">刷新</BaseButton>
        </div>
      </div>

      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

      <template v-else>
        <!-- 汇总卡片 -->
        <div v-if="summary" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">总对话次数</div>
            <div class="text-xl font-bold text-default mt-1">{{ summary.total_conversations }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">输入Token总数 (百万)</div>
            <div class="text-xl font-bold text-info-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_input_tokens) }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">输出Token总数 (百万)</div>
            <div class="text-xl font-bold text-success-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_output_tokens) }}</div>
          </div>
          <div class="bg-surface rounded-lg p-4 border border-default">
            <div class="text-xs text-muted">平均每对话Token数</div>
            <div class="text-xl font-bold text-default mt-1">
              {{ summary.total_conversations > 0 ? formatTokensToMillionsThreeDecimals((summary.total_input_tokens + summary.total_output_tokens) / summary.total_conversations) : '0.000' }}
            </div>
          </div>
        </div>

        <!-- 对话明细表格 -->
        <div class="mb-4">
          <h3 class="text-sm font-medium text-default mb-3">对话明细 ({{ selectedMonth }})</h3>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="columns" :data="data" row-key="record_id">
              <template #index="{ index }">{{ seqNumber(index) }}</template>
              <template #username="{ row }">{{ row.username || row.user_id || '-' }}</template>
              <template #user_message="{ row }">
                <span class="truncate" :title="row.user_message">{{ formatMessagePreview(row.user_message) }}</span>
              </template>
              <template #input_tokens="{ row }">
                <span class="text-info-600">{{ formatTokensToMillionsThreeDecimals(row.input_tokens) }}</span>
              </template>
              <template #output_tokens="{ row }">
                <span class="text-success-600">{{ formatTokensToMillionsThreeDecimals(row.output_tokens) }}</span>
              </template>
              <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
              <template #empty>暂无数据</template>
            </BaseTable>
          </div>
        </div>

        <!-- 分页控件 -->
        <div v-if="total > 0" class="flex items-center justify-center">
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
import { getTenantTokenDetails } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { formatTokensToMillionsThreeDecimals, formatMessagePreview } from '@/utils/formatTokens'

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
const selectedMonth = ref(getDefaultMonth())
const summary = ref<any>(null)
const data = ref<any[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'username', label: '用户' },
  { key: 'user_message', label: '对话内容' },
  { key: 'input_tokens', label: '输入Token (M)' },
  { key: 'output_tokens', label: '输出Token (M)' },
  { key: 'created_at', label: '时间' },
]

function seqNumber(index: number): number {
  return (currentPage.value - 1) * pageSize.value + index + 1
}

function getDefaultMonth(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  return `${year}-${month}`
}

function formatDateTime(datetime: string): string {
  if (!datetime) return ''
  const date = new Date(datetime)
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function loadData(page: number = 1) {
  loading.value = true
  try {
    const response = await getTenantTokenDetails(selectedMonth.value, page, pageSize.value)
    if (response.success) {
      summary.value = response.summary
      data.value = response.data
      total.value = response.pagination?.total_count || 0
    } else {
      toast.error(response.message || '加载数据失败')
      summary.value = null
      data.value = []
      total.value = 0
    }
  } catch (error: any) {
    console.error('加载租户Token消耗明细失败:', error)
    toast.error(error.message || '加载数据失败')
    summary.value = null
    data.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handleMonthChange() {
  currentPage.value = 1
  loadData(1)
}

function handlePageSizeChange() {
  currentPage.value = 1
  loadData(1)
}

watch(currentPage, (newPage) => loadData(newPage))

onMounted(() => loadData(1))
</script>
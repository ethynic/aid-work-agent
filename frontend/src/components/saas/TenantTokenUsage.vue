<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="站点Token用量"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button
          @click="goToChat(); closeMenu()"
          class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          返回对话
        </button>
      </template>
    </AppHeader>

    <div class="flex-1 overflow-y-auto p-6">
      <div class="flex items-center gap-3 mb-6">
        <div class="flex items-center gap-2">
          <label class="text-sm text-default">选择月份:</label>
          <input type="month" v-model="selectedMonth" @change="loadData(1)"
            class="px-3 py-1.5 border border-hover rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500">
        </div>
        <button @click="loadData(1)" :disabled="loading"
          class="px-3 py-1.5 bg-primary-500 text-white rounded-lg text-sm hover:bg-primary-700 transition-colors disabled:opacity-50">
          刷新
        </button>
      </div>

    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <template v-else>
      <!-- 汇总卡片 -->
      <div v-if="summary" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">总对话次数</div>
          <div class="text-xl font-bold text-default mt-1">{{ summary.total_conversations }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">输入Token总数 (百万)</div>
          <div class="text-xl font-bold text-info-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_input_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">输出Token总数 (百万)</div>
          <div class="text-xl font-bold text-success-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_output_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">平均每对话Token数</div>
          <div class="text-xl font-bold text-default mt-1">
            {{ summary.total_conversations > 0 ? formatTokensToMillionsThreeDecimals((summary.total_input_tokens + summary.total_output_tokens) / summary.total_conversations) : '0.000' }}
          </div>
        </div>
      </div>

      <!-- 对话明细表格 -->
      <div class="bg-white rounded-xl shadow-sm border border-default overflow-hidden mb-6">
        <div class="px-5 py-3 border-b border-default">
          <h3 class="text-sm font-medium text-default">对话明细 ({{ selectedMonth }})</h3>
        </div>
        <div v-if="data.length > 0">
          <table class="w-full">
            <thead class="bg-canvas">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">序号</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">用户名</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">用户消息（前10字）</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">输入Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">输出Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">创建时间</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-default">
              <tr v-for="(item, index) in data" :key="item.record_id" class="hover:bg-surface-hover">
                <td class="px-4 py-2 text-sm text-default">{{ getRowNumber(index) }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ item.username || item.user_id || '-' }}</td>
                <td class="px-4 py-2 text-sm text-default" :title="item.user_message">{{ formatMessagePreview(item.user_message) }}</td>
                <td class="px-4 py-2 text-sm text-info-600">{{ formatTokensToMillionsThreeDecimals(item.input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-success-600">{{ formatTokensToMillionsThreeDecimals(item.output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ formatDateTime(item.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-center py-8 text-muted">暂无数据</div>
      </div>

      <!-- 分页控件 -->
      <div v-if="pagination && pagination.total_pages > 1" class="flex items-center justify-between">
        <div class="text-sm text-muted">
          共 {{ pagination.total_count }} 条记录，第 {{ pagination.page }} 页 / 共 {{ pagination.total_pages }} 页
        </div>
        <div class="flex items-center gap-1">
          <button @click="loadData(1)" :disabled="pagination.page === 1"
            class="px-3 py-1.5 text-sm border border-hover rounded-lg hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed">
            首页
          </button>
          <button @click="loadData(pagination.page - 1)" :disabled="pagination.page === 1"
            class="px-3 py-1.5 text-sm border border-hover rounded-lg hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed">
            上一页
          </button>
          <span class="px-3 py-1.5 text-sm text-default">第 {{ pagination.page }} 页</span>
          <button @click="loadData(pagination.page + 1)" :disabled="pagination.page >= pagination.total_pages"
            class="px-3 py-1.5 text-sm border border-hover rounded-lg hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed">
            下一页
          </button>
          <button @click="loadData(pagination.total_pages)" :disabled="pagination.page === pagination.total_pages"
            class="px-3 py-1.5 text-sm border border-hover rounded-lg hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed">
            末页
          </button>
        </div>
      </div>
    </template>
  </div>
</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
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

function goToChat() {
  router.push(`/t/${route.params.tenant_id}/chat`)
}

const loading = ref(true)
const selectedMonth = ref(getDefaultMonth())
const summary = ref<any>(null)
const data = ref<any[]>([])
const pagination = ref<any>(null)

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
    const response = await getTenantTokenDetails(selectedMonth.value, page, 100)
    if (response.success) {
      summary.value = response.summary
      data.value = response.data
      pagination.value = response.pagination
    } else {
      toast.error(response.message || '加载数据失败')
      summary.value = null
      data.value = []
      pagination.value = null
    }
  } catch (error: any) {
    console.error('加载租户Token消耗明细失败:', error)
    toast.error(error.message || '加载数据失败')
    summary.value = null
    data.value = []
    pagination.value = null
  } finally {
    loading.value = false
  }
}

function getRowNumber(index: number): number {
  if (!pagination.value) return index + 1
  const pageSize = 100 // 与 loadData 中调用 API 的 pageSize 一致
  return (pagination.value.page - 1) * pageSize + index + 1
}

onMounted(() => loadData(1))
</script>
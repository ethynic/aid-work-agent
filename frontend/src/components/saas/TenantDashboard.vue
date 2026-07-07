<template>
  <div class="p-6 h-full overflow-y-auto">
    <h1 class="text-2xl font-bold text-default mb-6">仪表盘</h1>

    <!-- 加载状态 -->
    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <!-- 统计卡片 -->
    <div v-else class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <!-- 租户数量 -->
      <button
        type="button"
        class="bg-surface rounded-xl shadow-sm p-6 border border-default hover:border-primary-400 hover:shadow-md transition-all text-left cursor-pointer flex flex-col"
        @click="goTo('/portal/tenants')"
      >
        <div class="flex items-center justify-between mb-3">
          <span class="text-sm text-muted">租户数量</span>
          <svg class="w-5 h-5 text-primary-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 21h18M5 21V7l7-4 7 4v14M9 9h.01M9 12h.01M9 15h.01M9 18h.01M15 9h.01M15 12h.01M15 15h.01M15 18h.01" />
          </svg>
        </div>
        <div class="text-3xl font-bold text-default">{{ stats.tenant_count }}</div>
        <div class="text-xs text-muted mt-2">正常租户总数 · 点击查看详情</div>
      </button>

      <!-- 本月Token用量 -->
      <button
        type="button"
        class="bg-surface rounded-xl shadow-sm p-6 border border-default hover:border-primary-400 hover:shadow-md transition-all text-left cursor-pointer flex flex-col"
        @click="goTo('/portal/token-usage')"
      >
        <div class="flex items-center justify-between mb-3">
          <span class="text-sm text-muted">本月Token用量</span>
          <svg class="w-5 h-5 text-primary-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 21h18M6 17V9M11 17V5M16 17v-4M21 17v-7" />
          </svg>
        </div>
        <div class="text-3xl font-bold text-default">{{ formatTokens(stats.monthly_token_usage) }}</div>
        <div class="text-xs text-muted mt-2">{{ stats.month }} · 点击查看详情</div>
      </button>

      <!-- 今日对话数量 -->
      <button
        type="button"
        class="bg-surface rounded-xl shadow-sm p-6 border border-default hover:border-primary-400 hover:shadow-md transition-all text-left cursor-pointer flex flex-col"
        @click="goTo('/portal/monitoring')"
      >
        <div class="flex items-center justify-between mb-3">
          <span class="text-sm text-muted">今日对话数量</span>
          <svg class="w-5 h-5 text-primary-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
          </svg>
        </div>
        <div class="text-3xl font-bold text-default">{{ stats.today_conversation_count }}</div>
        <div class="text-xs text-muted mt-2">全平台今日对话总数 · 点击查看详情</div>
      </button>
    </div>

    <!-- 错误信息 -->
    <div v-if="error" class="mt-4 p-3 bg-danger-50 border border-danger-200 rounded-lg text-danger-600 text-sm">
      {{ error }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getDashboardStats, type DashboardStats } from '@/api/adminReports'

const router = useRouter()
const loading = ref(true)
const error = ref('')

const stats = ref<DashboardStats>({
  success: false,
  tenant_count: 0,
  monthly_token_usage: 0,
  today_conversation_count: 0,
  month: ''
})

function formatTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

function goTo(path: string) {
  router.push(path)
}

async function loadData() {
  // 未登录时不调用需要认证的 API
  const token = localStorage.getItem('portal_token')
  if (!token) {
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await getDashboardStats()
    if (res.success) {
      stats.value = res
    } else {
      error.value = res.message || '加载失败'
    }
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => loadData())
</script>

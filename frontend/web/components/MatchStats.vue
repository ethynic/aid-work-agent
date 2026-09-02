<template>
  <div class="page-container">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">匹配统计</h2>
      </div>
      <div class="page-toolbar-right">
        <BaseButton size="sm" :disabled="loading" @click="loadData">
          {{ loading ? '加载中...' : '刷新' }}
        </BaseButton>
      </div>
    </div>

    <!-- 错误提示 -->
    <div v-if="error" class="mb-4 p-4 bg-danger-50 border border-danger-200 rounded-lg text-danger-600 text-sm">
      <p class="m-0">{{ error }}</p>
      <p v-if="debug" class="mt-2 text-xs text-danger-700">{{ debug }}</p>
    </div>

    <!-- 加载状态 -->
    <div v-else-if="loading && !stats" class="text-center py-12 text-muted">
      <svg class="w-8 h-8 animate-spin mx-auto mb-3" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
      加载统计数据中...
    </div>

    <template v-else-if="stats">
      <!-- 关键指标卡片 -->
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
        <div class="rounded-xl border border-default bg-surface p-4 text-center">
          <div class="text-3xl font-bold text-default tabular-nums">{{ stats.total_customers }}</div>
          <div class="text-sm text-muted mt-1">匹配客户总数</div>
        </div>
        <div class="rounded-xl border border-default bg-surface p-4 text-center">
          <div class="text-3xl font-bold text-primary-600 tabular-nums">{{ stats.total_emails }}</div>
          <div class="text-sm text-muted mt-1">邮件发送总数</div>
        </div>
        <div class="rounded-xl border border-default bg-surface p-4 text-center">
          <div class="text-3xl font-bold text-success-600 tabular-nums">{{ stats.success_emails }}</div>
          <div class="text-sm text-muted mt-1">成功发送</div>
        </div>
        <div class="rounded-xl border border-default bg-surface p-4 text-center">
          <div class="text-3xl font-bold text-danger-600 tabular-nums">{{ stats.failed_emails }}</div>
          <div class="text-sm text-muted mt-1">发送失败</div>
        </div>
        <div class="rounded-xl border border-default bg-surface p-4 text-center">
          <div class="text-3xl font-bold text-info-600 tabular-nums">{{ stats.recent_customers }}</div>
          <div class="text-sm text-muted mt-1">本周新增客户</div>
        </div>
        <div class="rounded-xl border border-default bg-surface p-4 text-center">
          <div class="text-3xl font-bold text-warning-600 tabular-nums">{{ stats.recent_emails }}</div>
          <div class="text-sm text-muted mt-1">本周发送邮件</div>
        </div>
      </div>

      <!-- 成功率 + 国家分布 -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <!-- 成功率 -->
        <div class="rounded-xl border border-default bg-surface p-5">
          <h3 class="text-sm font-semibold text-default m-0 mb-4">邮件发送成功率</h3>
          <div class="flex items-baseline gap-2 mb-3">
            <span class="text-4xl font-bold text-success-600 tabular-nums">{{ successRate }}%</span>
            <span class="text-sm text-muted">({{ stats.success_emails }}/{{ stats.total_emails }})</span>
          </div>
          <div class="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
            <div class="h-full bg-success-500 transition-all" :style="{ width: `${successRate}%` }"></div>
          </div>
          <div class="flex justify-between text-xs text-muted mt-2">
            <span>失败 {{ stats.failed_emails }}</span>
            <span>总计 {{ stats.total_emails }}</span>
          </div>
        </div>

        <!-- 国家分布 Top 10 -->
        <div class="lg:col-span-2 rounded-xl border border-default bg-surface p-5">
          <h3 class="text-sm font-semibold text-default m-0 mb-4">客户国家分布 Top {{ stats.country_distribution.length || 0 }}</h3>
          <div v-if="stats.country_distribution.length > 0" class="space-y-2.5">
            <div v-for="item in stats.country_distribution" :key="item.country" class="flex items-center gap-3">
              <div class="w-24 text-sm text-default truncate" :title="item.country || '-'">{{ item.country || '未知' }}</div>
              <div class="flex-1 h-6 bg-gray-100 rounded overflow-hidden">
                <div class="h-full bg-primary-500 transition-all flex items-center justify-end pr-2" :style="{ width: countryBarWidth(item.count) }">
                  <span class="text-xs text-white font-medium tabular-nums">{{ item.count }}</span>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="text-center py-8 text-muted text-sm">暂无客户分布数据</div>
        </div>
      </div>
    </template>

    <!-- 空状态 -->
    <div v-else class="text-center py-12 text-muted">
      <p class="text-lg mb-2">暂无统计数据</p>
      <p class="text-sm">外贸智能体匹配客户并发送邮件后，统计数据会显示在此处</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getStats, type CustomerStats } from '@/api/customer'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const { admin: tenantAdmin } = useTenantAuth()

const loading = ref(false)
const error = ref('')
const debug = ref('')
const stats = ref<CustomerStats | null>(null)

const userId = computed(() => {
  const queryUserId = route.query.user_id as string
  if (queryUserId) return queryUserId
  return tenantAdmin.value?.user_id || ''
})

const successRate = computed(() => {
  if (!stats.value || stats.value.total_emails === 0) return 0
  return Math.round((stats.value.success_emails / stats.value.total_emails) * 100)
})

function countryBarWidth(count: number): string {
  if (!stats.value || stats.value.country_distribution.length === 0) return '0%'
  const max = Math.max(...stats.value.country_distribution.map(c => c.count))
  if (max === 0) return '0%'
  const pct = Math.round((count / max) * 100)
  // 保证最小宽度让数字可见
  return `${Math.max(pct, 15)}%`
}

async function loadData() {
  if (!userId.value) {
    error.value = '缺少用户ID参数，请从外贸智能体页面跳转'
    return
  }

  loading.value = true
  error.value = ''
  debug.value = ''

  try {
    const res = await getStats(userId.value)
    if (res.success && res.data) {
      stats.value = res.data
    } else {
      error.value = res.error || '获取统计数据失败'
      debug.value = res.debug || ''
    }
  } catch (e: any) {
    error.value = '加载统计数据失败'
    debug.value = e.message || ''
  } finally {
    loading.value = false
  }
}

onMounted(() => { loadData() })
</script>

<template>
  <div class="min-h-full bg-surface text-default transition-colors duration-200">
    <!-- Header -->
    <div class="px-6 pt-6 pb-4">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-xl font-bold tracking-tight" style="font-family: 'Noto Sans SC', 'DM Sans', sans-serif;">
            投诉统计
          </h1>
          <p class="text-sm text-muted mt-0.5">投诉数据分析与趋势</p>
        </div>
        <div class="flex items-center gap-2">
          <select
            v-model="period"
            @change="loadStats"
            class="text-sm rounded-lg border border-default bg-canvas text-default px-3 py-1.5 outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="7d">近7天</option>
            <option value="30d">近30天</option>
            <option value="90d">近90天</option>
            <option value="180d">近半年</option>
          </select>
        </div>
      </div>

      <!-- Overview Stats -->
      <div class="grid grid-cols-4 gap-4 mb-6">
        <div class="rounded-xl border border-default bg-canvas p-4">
          <p class="text-xs font-medium text-muted uppercase tracking-wider">投诉总量</p>
          <p class="text-2xl font-bold mt-1 tabular-nums">{{ stats?.total || 0 }}</p>
        </div>
        <div class="rounded-xl border border-default bg-canvas p-4">
          <p class="text-xs font-medium text-muted uppercase tracking-wider">待处理</p>
          <p class="text-2xl font-bold mt-1 tabular-nums text-amber-600 dark:text-amber-400">{{ (stats?.status_distribution?.open || 0) + (stats?.status_distribution?.in_progress || 0) }}</p>
        </div>
        <div class="rounded-xl border border-default bg-canvas p-4">
          <p class="text-xs font-medium text-muted uppercase tracking-wider">已升级</p>
          <p class="text-2xl font-bold mt-1 tabular-nums text-danger-600 dark:text-red-400">{{ stats?.status_distribution?.escalated || 0 }}</p>
        </div>
        <div class="rounded-xl border border-default bg-canvas p-4">
          <p class="text-xs font-medium text-muted uppercase tracking-wider">平均处理时长</p>
          <p class="text-2xl font-bold mt-1 tabular-nums">{{ stats?.avg_resolution_hours || 0 }}<span class="text-sm font-normal text-muted ml-1">小时</span></p>
        </div>
      </div>
    </div>

    <!-- Charts Row -->
    <div class="px-6 pb-6 grid grid-cols-2 gap-6">
      <!-- Status Distribution -->
      <div class="rounded-xl border border-default bg-canvas p-5">
        <h3 class="text-sm font-semibold mb-4">状态分布</h3>
        <div v-if="!stats" class="text-center py-8 text-muted">加载中...</div>
        <div v-else class="space-y-3">
          <div v-for="item in statusBars" :key="item.key" class="flex items-center gap-3">
            <span class="text-xs text-muted w-16 text-right">{{ item.label }}</span>
            <div class="flex-1 h-6 rounded bg-surface border border-default overflow-hidden">
              <div
                class="h-full rounded transition-all duration-500"
                :class="item.barClass"
                :style="{ width: item.percentage + '%' }"
              />
            </div>
            <span class="text-xs tabular-nums w-12 text-right text-muted">{{ item.count }}</span>
            <span class="text-xs tabular-nums w-10 text-right text-muted">{{ item.percentage }}%</span>
          </div>
        </div>
      </div>

      <!-- Category Distribution -->
      <div class="rounded-xl border border-default bg-canvas p-5">
        <h3 class="text-sm font-semibold mb-4">分类分布</h3>
        <div v-if="!stats" class="text-center py-8 text-muted">加载中...</div>
        <div v-else class="space-y-3">
          <div v-for="item in categoryBars" :key="item.name" class="flex items-center gap-3">
            <span class="text-xs text-muted w-16 text-right truncate" :title="item.name">{{ item.name }}</span>
            <div class="flex-1 h-6 rounded bg-surface border border-default overflow-hidden">
              <div
                class="h-full rounded bg-primary-600 transition-all duration-500"
                :style="{ width: item.percentage + '%' }"
              />
            </div>
            <span class="text-xs tabular-nums w-12 text-right text-muted">{{ item.count }}</span>
            <span class="text-xs tabular-nums w-10 text-right text-muted">{{ item.percentage }}%</span>
          </div>
        </div>
      </div>

      <!-- Urgency Distribution -->
      <div class="rounded-xl border border-default bg-canvas p-5">
        <h3 class="text-sm font-semibold mb-4">紧急程度分布</h3>
        <div v-if="!stats" class="text-center py-8 text-muted">加载中...</div>
        <div v-else class="space-y-3">
          <div v-for="item in urgencyBars" :key="item.key" class="flex items-center gap-3">
            <span class="text-xs text-muted w-16 text-right">{{ item.label }}</span>
            <div class="flex-1 h-6 rounded bg-surface border border-default overflow-hidden">
              <div
                class="h-full rounded transition-all duration-500"
                :class="item.barClass"
                :style="{ width: item.percentage + '%' }"
              />
            </div>
            <span class="text-xs tabular-nums w-12 text-right text-muted">{{ item.count }}</span>
            <span class="text-xs tabular-nums w-10 text-right text-muted">{{ item.percentage }}%</span>
          </div>
        </div>
      </div>

      <!-- Daily Trend -->
      <div class="rounded-xl border border-default bg-canvas p-5">
        <h3 class="text-sm font-semibold mb-4">每日投诉趋势</h3>
        <div v-if="!stats?.daily_trend?.length" class="text-center py-8 text-muted">暂无趋势数据</div>
        <div v-else class="flex items-end gap-1 h-40">
          <div
            v-for="(day, i) in stats.daily_trend"
            :key="i"
            class="flex-1 flex flex-col items-center justify-end h-full"
          >
            <div
              class="w-full rounded-t bg-primary-600 transition-all duration-300 hover:brightness-110 min-h-[2px]"
              :style="{ height: trendBarHeight(day.count) + '%' }"
              :title="`${day.date}: ${day.count} 条`"
            />
          </div>
        </div>
        <div class="flex justify-between mt-2 text-xs text-muted">
          <span>{{ stats?.daily_trend?.[0]?.date?.slice(5) || '' }}</span>
          <span>{{ stats?.daily_trend?.[(stats?.daily_trend?.length ?? 1) - 1]?.date?.slice(5) || '' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { complaintAPI, type ComplaintStats } from '@/api/complaint'

const period = ref('30d')
const stats = ref<ComplaintStats | null>(null)

const statusLabels: Record<string, string> = {
  open: '待处理', in_progress: '处理中', resolved: '已解决', closed: '已关闭', escalated: '已升级',
}
const urgencyLabels: Record<string, string> = {
  normal: '一般', high: '紧急', urgent: '非常紧急', critical: '危急',
}

const total = computed(() => stats.value?.total || 0)

const statusBars = computed(() => {
  const dist = stats.value?.status_distribution || {}
  const t = total.value || 1
  const order = ['open', 'in_progress', 'resolved', 'closed', 'escalated']
  const barClasses: Record<string, string> = {
    open: 'bg-info-500', in_progress: 'bg-amber-500', resolved: 'bg-emerald-500', closed: 'bg-gray-400', escalated: 'bg-danger-500',
  }
  return order
    .filter(k => dist[k])
    .map(k => ({
      key: k,
      label: statusLabels[k] || k,
      count: dist[k],
      percentage: Math.round((dist[k] / t) * 100),
      barClass: barClasses[k] || 'bg-gray-400',
    }))
})

const categoryBars = computed(() => {
  const dist = stats.value?.category_distribution || []
  const t = total.value || 1
  return dist.map(item => ({
    ...item,
    percentage: Math.round((item.count / t) * 100),
  }))
})

const urgencyBars = computed(() => {
  const dist = stats.value?.urgency_distribution || {}
  const t = total.value || 1
  const order = ['normal', 'high', 'urgent', 'critical']
  const barClasses: Record<string, string> = {
    normal: 'bg-gray-400', high: 'bg-amber-500', urgent: 'bg-orange-500', critical: 'bg-red-600',
  }
  return order
    .filter(k => dist[k])
    .map(k => ({
      key: k,
      label: urgencyLabels[k] || k,
      count: dist[k],
      percentage: Math.round((dist[k] / t) * 100),
      barClass: barClasses[k] || 'bg-gray-400',
    }))
})

const maxTrend = computed(() => {
  const trend = stats.value?.daily_trend || []
  return Math.max(...trend.map(d => d.count), 1)
})

function trendBarHeight(count: number) {
  return Math.max((count / maxTrend.value) * 100, 1)
}

async function loadStats() {
  try {
    stats.value = await complaintAPI.getStats(period.value)
  } catch (e: any) {
    console.error('加载投诉统计失败:', e)
  }
}

onMounted(() => {
  loadStats()
})
</script>

<style scoped>
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f8fafc;
  --bg-hover: #f1f5f9;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-tertiary: #94a3b8;
  --border-primary: #e2e8f0;
  --accent-primary: #2563eb;
}
:root.dark, .dark {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-hover: #334155;
  --text-primary: #f1f5f9;
  --text-secondary: #cbd5e1;
  --text-tertiary: #64748b;
  --border-primary: #334155;
  --accent-primary: #3b82f6;
}
</style>

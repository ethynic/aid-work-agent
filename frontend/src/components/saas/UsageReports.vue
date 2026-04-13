<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-slate-800">用量报告</h1>
      <div class="flex items-center gap-2">
        <button v-for="d in [7, 30, 90]" :key="d"
          @click="days = d; loadData()"
          class="px-3 py-1.5 rounded-lg text-sm transition-colors"
          :class="days === d ? 'bg-cyan-500 text-white' : 'bg-slate-200 text-slate-600 hover:bg-slate-300'">
          {{ d }}天
        </button>
        <button @click="handleExport"
          class="px-3 py-1.5 bg-slate-200 text-slate-600 hover:bg-slate-300 rounded-lg text-sm transition-colors">
          导出
        </button>
      </div>
    </div>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <template v-else>
      <!-- 摘要卡片 -->
      <div v-if="summary" class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">总 Token 消耗</div>
          <div class="text-xl font-bold text-slate-800 mt-1">{{ formatNum(summary.total_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">总会话数</div>
          <div class="text-xl font-bold text-slate-800 mt-1">{{ formatNum(summary.total_sessions) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">活跃用户</div>
          <div class="text-xl font-bold text-slate-800 mt-1">{{ summary.active_users }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">平均 Token / 会话</div>
          <div class="text-xl font-bold text-slate-800 mt-1">{{ Math.round(summary.avg_tokens_per_session) }}</div>
        </div>
      </div>

      <!-- Token 趋势柱状图 -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 mb-6">
        <h3 class="text-sm font-medium text-slate-700 mb-4">Token 消耗趋势</h3>
        <div v-if="trend.length > 0" class="flex items-end gap-1 h-40">
          <div v-for="(item, i) in trend" :key="i" class="flex-1 flex flex-col items-center justify-end h-full">
            <div
              class="w-full bg-cyan-400 rounded-t transition-all min-h-[2px]"
              :style="{ height: (item.tokens / maxTokens * 100) + '%' }"
              :title="`${item.date}: ${item.tokens}`"
            ></div>
            <span v-if="trend.length <= 30 || i % Math.ceil(trend.length / 15) === 0"
              class="text-[10px] text-slate-400 mt-1 rotate-45 origin-left whitespace-nowrap">
              {{ item.date.slice(5) }}
            </span>
          </div>
        </div>
        <div v-else class="text-center py-8 text-slate-400">暂无趋势数据</div>
      </div>

      <!-- 用户用量明细 -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div class="px-5 py-3 border-b border-slate-200">
          <h3 class="text-sm font-medium text-slate-700">用户用量明细</h3>
        </div>
        <div v-if="userUsage.length > 0">
          <table class="w-full">
            <thead class="bg-slate-50">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">用户</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">Token 消耗</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">会话数</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">平均 Token / 会话</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="u in userUsage" :key="u.user_id" class="hover:bg-slate-50">
                <td class="px-4 py-2 text-sm text-slate-800">{{ u.username || u.user_id }}</td>
                <td class="px-4 py-2 text-sm text-slate-600">{{ formatNum(u.total_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-slate-600">{{ u.total_sessions }}</td>
                <td class="px-4 py-2 text-sm text-slate-600">{{ Math.round(u.avg_tokens_per_session) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-center py-8 text-slate-400">暂无用户用量数据</div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { getUsageSummary, getTokenTrend, getUserUsage, exportReport } from '@/api/saasTenant'

const loading = ref(true)
const days = ref(30)
const summary = ref<any>(null)
const trend = ref<{ date: string; tokens: number }[]>([])
const userUsage = ref<any[]>([])

const maxTokens = computed(() => {
  if (trend.value.length === 0) return 1
  return Math.max(...trend.value.map(t => t.tokens), 1)
})

function formatNum(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

async function loadData() {
  loading.value = true
  try {
    const [summaryRes, trendRes, usageRes] = await Promise.allSettled([
      getUsageSummary(days.value <= 7 ? 'week' : days.value <= 30 ? 'month' : 'month'),
      getTokenTrend(days.value),
      getUserUsage(days.value)
    ])
    if (summaryRes.status === 'fulfilled') summary.value = summaryRes.value.summary
    if (trendRes.status === 'fulfilled') trend.value = trendRes.value.trend || []
    if (usageRes.status === 'fulfilled') userUsage.value = usageRes.value.users || []
  } catch (e) {
    console.error('加载报告失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleExport() {
  try {
    const data = await exportReport(days.value)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `usage-report-${days.value}d.json`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    alert(e.message || '导出失败')
  }
}

onMounted(() => loadData())
</script>

<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-default">用量报告</h1>
      <div class="flex items-center gap-2">
        <button v-for="d in [7, 30, 90]" :key="d"
          @click="days = d; loadData()"
          class="px-3 py-1.5 rounded-lg text-sm transition-colors"
          :class="days === d ? 'bg-primary-500 text-white' : 'bg-surface-hover text-default hover:bg-default'">
          {{ d }}天
        </button>
        <button @click="handleExport"
          class="px-3 py-1.5 bg-surface-hover text-default hover:bg-default rounded-lg text-sm transition-colors">
          导出
        </button>
      </div>
    </div>

    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <template v-else>
      <!-- 摘要卡片 -->
      <div v-if="summary" class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 mb-6">
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">总 Token</div>
          <div class="text-xl font-bold text-default mt-1">{{ formatNum(summary.total_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">Input Tokens</div>
          <div class="text-xl font-bold text-info-600 mt-1">{{ formatNum(summary.input_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">Output Tokens</div>
          <div class="text-xl font-bold text-success-600 mt-1">{{ formatNum(summary.output_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">缓存命中</div>
          <div class="text-xl font-bold text-amber-600 mt-1">{{ formatNum(summary.cached_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">总会话数</div>
          <div class="text-xl font-bold text-default mt-1">{{ formatNum(summary.total_sessions) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">活跃用户</div>
          <div class="text-xl font-bold text-default mt-1">{{ summary.active_users }}</div>
        </div>
      </div>

      <!-- Token 趋势柱状图（含 input/output/cached 分项） -->
      <div class="bg-white rounded-xl shadow-sm border border-default p-5 mb-6">
        <h3 class="text-sm font-medium text-default mb-4">Token 消耗趋势（Input / Output / Cached）</h3>
        <div v-if="trend.length > 0" class="flex items-end gap-1 h-48">
          <div v-for="(item, i) in trend" :key="i" class="flex-1 flex flex-col items-center justify-end h-full gap-0">
            <div class="w-full flex flex-col" style="min-height: 2px;">
              <div class="w-full bg-success-400 rounded-t" :style="{ height: barHeight(item.output_tokens) }"></div>
              <div class="w-full bg-info-400" :style="{ height: barHeight(item.input_tokens) }"></div>
              <div class="w-full bg-amber-300 rounded-b" :style="{ height: barHeight(item.cached_tokens) }"></div>
            </div>
            <span v-if="trend.length <= 30 || i % Math.ceil(trend.length / 15) === 0"
              class="text-[10px] text-muted mt-1 rotate-45 origin-left whitespace-nowrap">
              {{ item.date.slice(5) }}
            </span>
          </div>
        </div>
        <div v-else class="text-center py-8 text-muted">暂无趋势数据</div>
        <div class="flex items-center gap-4 mt-3 text-xs text-muted">
          <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 bg-info-400 rounded"></span> Input</span>
          <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 bg-success-400 rounded"></span> Output</span>
          <span class="flex items-center gap-1"><span class="inline-block w-3 h-3 bg-amber-300 rounded"></span> Cached</span>
        </div>
      </div>

      <!-- 按模型分组 -->
      <div v-if="modelUsage.length > 0" class="bg-white rounded-xl shadow-sm border border-default overflow-hidden mb-6">
        <div class="px-5 py-3 border-b border-default">
          <h3 class="text-sm font-medium text-default">按模型统计</h3>
        </div>
        <table class="w-full">
          <thead class="bg-canvas">
            <tr>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">模型</th>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">提供商</th>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">对话数</th>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">Input</th>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">Output</th>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">Cached</th>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">总 Token</th>
              <th class="px-4 py-2 text-left text-xs font-medium text-muted">平均耗时</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-default">
            <tr v-for="m in modelUsage" :key="m.model" class="hover:bg-surface-hover">
              <td class="px-4 py-2 text-sm text-default">{{ m.model }}</td>
              <td class="px-4 py-2 text-sm text-default">{{ m.provider }}</td>
              <td class="px-4 py-2 text-sm text-default">{{ m.conversation_count }}</td>
              <td class="px-4 py-2 text-sm text-info-600">{{ formatNum(m.input_tokens) }}</td>
              <td class="px-4 py-2 text-sm text-success-600">{{ formatNum(m.output_tokens) }}</td>
              <td class="px-4 py-2 text-sm text-amber-600">{{ formatNum(m.cached_tokens) }}</td>
              <td class="px-4 py-2 text-sm font-medium text-default">{{ formatNum(m.total_tokens) }}</td>
              <td class="px-4 py-2 text-sm text-default">{{ m.conversation_count ? Math.round(m.total_duration_ms / m.conversation_count) : 0 }}ms</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 用户用量明细 -->
      <div class="bg-white rounded-xl shadow-sm border border-default overflow-hidden">
        <div class="px-5 py-3 border-b border-default">
          <h3 class="text-sm font-medium text-default">用户用量明细</h3>
        </div>
        <div v-if="userUsage.length > 0">
          <table class="w-full">
            <thead class="bg-canvas">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">用户</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">Input</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">Output</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">Cached</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">总 Token</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">会话数</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">Token / 会话</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-default">
              <tr v-for="u in userUsage" :key="u.user_id" class="hover:bg-surface-hover">
                <td class="px-4 py-2 text-sm text-default">{{ u.username || u.user_id }}</td>
                <td class="px-4 py-2 text-sm text-info-600">{{ formatNum(u.input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-success-600">{{ formatNum(u.output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-amber-600">{{ formatNum(u.cached_tokens) }}</td>
                <td class="px-4 py-2 text-sm font-medium text-default">{{ formatNum(u.total_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ u.total_sessions }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ u.avg_tokens_per_session }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-center py-8 text-muted">暂无用户用量数据</div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { getUsageSummary, getTokenDetail, getModelUsage, getUserUsage, exportReport } from '@/api/saasTenant'

const toast = useToast()

const loading = ref(true)
const days = ref(30)
const summary = ref<any>(null)
const trend = ref<{ date: string; tokens: number; input_tokens: number; output_tokens: number; cached_tokens: number }[]>([])
const userUsage = ref<any[]>([])
const modelUsage = ref<any[]>([])

const maxTokens = computed(() => {
  if (trend.value.length === 0) return 1
  return Math.max(...trend.value.map(t => t.tokens), 1)
})

function formatNum(n: number): string {
  if (!n) return '0'
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

function barHeight(value: number): string {
  if (maxTokens.value === 0) return '0%'
  return Math.max((value / maxTokens.value) * 100, 0.5) + '%'
}

async function loadData() {
  loading.value = true
  try {
    const period = days.value <= 7 ? 'week' : 'month'
    const [summaryRes, trendRes, modelRes, usageRes] = await Promise.allSettled([
      getUsageSummary(period),
      getTokenDetail(days.value),
      getModelUsage(days.value),
      getUserUsage(days.value)
    ])
    if (summaryRes.status === 'fulfilled') summary.value = summaryRes.value.summary
    if (trendRes.status === 'fulfilled') trend.value = trendRes.value.trend || []
    if (modelRes.status === 'fulfilled') modelUsage.value = modelRes.value.models || []
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
    toast.error(e.message || '导出失败')
  }
}

onMounted(() => loadData())
</script>

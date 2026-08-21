<template>
  <div class="h-full flex flex-col bg-canvas">
    <div class="flex items-center gap-3 px-6 py-4 bg-white border-b border-default">
      <button @click="goBack" class="px-2 py-1 text-sm text-primary-600 hover:text-primary-700">
        &larr; 返回列表
      </button>
      <h1 class="text-lg font-bold text-default">会话追踪</h1>
      <span class="text-sm text-muted font-mono">{{ sessionId }}</span>
    </div>

    <div class="flex-1 overflow-auto p-6 space-y-3">
      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>
      <template v-else>
        <div class="flex items-center justify-between gap-4 text-sm text-muted px-1">
          <div>共 <span class="font-semibold text-default">{{ effectiveTraceCount }}</span> 条消息</div>
          <button v-if="intermediateCount > 0" type="button" @click="showIntermediate = !showIntermediate"
            class="px-2.5 py-1 rounded border border-default bg-white text-xs text-default hover:bg-surface-hover transition-colors">
            {{ showIntermediate ? '隐藏处理过程' : `显示处理过程（${intermediateCount}）` }}
          </button>
        </div>
        <div v-if="visibleTraces.length === 0" class="text-center py-12 text-muted">该会话暂无追踪数据</div>
        <div v-for="(trace, idx) in visibleTraces" :key="trace.trace_id"
          :class="['rounded-xl shadow-sm border border-default p-4 hover:shadow-md transition-shadow',
                   trace.is_intermediate ? 'bg-gray-50 border-dashed' : (idx % 2 === 0 ? 'bg-white' : 'bg-primary-50')]">
          <div class="flex items-start justify-between gap-4">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-2">
                <span class="text-xs text-muted font-medium">Trace {{ idx + 1 }}</span>
                <span :class="statusClass(trace.status)" class="px-2 py-0.5 rounded text-xs font-medium">
                  {{ statusLabel(trace.status) }}
                </span>
                <span v-if="trace.is_intermediate" class="px-2 py-0.5 rounded text-xs font-medium bg-gray-200 text-default">
                  {{ trace.termination_reason === 'message_merged' ? '已被后续消息合并' : '中间处理过程' }}
                </span>
                <span v-if="trace.recall_type"
                  class="px-2 py-0.5 rounded text-xs font-medium bg-danger-100 text-danger-700">
                  {{ trace.recall_type === 'partial' ? '[用户部分撤回]' : '[用户撤回]' }}
                </span>
                <span v-if="trace.tags.length > 0" class="flex gap-1">
                  <span v-for="tag in trace.tags" :key="tag"
                    class="px-1.5 py-0.5 bg-warning-100 text-warning-700 rounded text-xs">{{ tag }}</span>
                </span>
              </div>
              <div class="mb-1">
                <span class="text-xs text-muted">用户：</span>
                <span :class="['text-sm text-default', trace.recall_type === 'full' ? 'line-through text-muted' : '']">{{ trace.input || '(空)' }}</span>
              </div>
              <div class="mb-2">
                <span class="text-xs text-muted">回复：</span>
                <span class="text-sm text-default">{{ trace.output ? (trace.output.length > 200 ? trace.output.substring(0, 200) + '...' : trace.output) : '(空)' }}</span>
              </div>
              <div class="flex items-center gap-4 text-xs text-muted">
                <span>{{ formatDuration(trace.duration_ms) }}</span>
                <span>{{ formatTokens(trace.total_tokens) }} tokens</span>
                <span>{{ trace.tool_calls_count }} 工具调用</span>
                <span>{{ trace.agent_iterations }} 迭代</span>
                <span>{{ formatDateTime(trace.created_at) }}</span>
              </div>
            </div>
            <button @click="goToDetail(trace.trace_id)"
              class="px-3 py-1.5 text-sm text-primary-600 bg-primary-50 rounded-lg hover:bg-primary-100 transition-colors whitespace-nowrap">
              查看详情 &rarr;
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getSessionTraces, type TraceSummary } from '@/api/monitor'
import { formatDateTimeWithSeconds as formatDateTime } from '@/utils/date'
import { formatTokensAuto as formatTokens } from '@/utils/formatTokens'

const router = useRouter()
const route = useRoute()
const sessionId = route.params.session_id as string

const traces = ref<TraceSummary[]>([])
const loading = ref(false)
const showIntermediate = ref(false)
const intermediateCount = computed(() => traces.value.filter(trace => trace.is_intermediate).length)
const visibleTraces = computed(() => {
  const filtered = showIntermediate.value
    ? traces.value
    : traces.value.filter(trace => !trace.is_intermediate)
  // 显式按时间降序（最新在上），不依赖后端返回顺序
  return [...filtered].sort((a, b) =>
    (b.created_at || '').localeCompare(a.created_at || ''),
  )
})
const effectiveTraceCount = computed(() => traces.value.filter(trace =>
  trace.display_state === 'message' || trace.is_persisted_message,
).length)

function goBack() {
  router.push('/portal/monitoring')
}

function goToDetail(traceId: string) {
  router.push(`/portal/monitoring/trace/${traceId}`)
}

function statusClass(status: string): string {
  if (status === 'completed') return 'bg-success-100 text-success-700'
  if (status === 'failed') return 'bg-danger-100 text-danger-700'
  if (status === 'cancelled') return 'bg-gray-200 text-default'
  return 'bg-info-100 text-info-700'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = { completed: '已完成', failed: '失败', cancelled: '已取消', running: '运行中' }
  return map[status] || status
}

function formatDuration(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}

async function loadData() {
  loading.value = true
  try {
    const res = await getSessionTraces(sessionId, true)
    if (res.success) {
      traces.value = res.traces
    }
  } catch (e) {
    console.error('Failed to load traces:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

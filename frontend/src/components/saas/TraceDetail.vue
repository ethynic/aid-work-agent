<template>
  <div class="h-full flex flex-col bg-canvas">
    <div class="flex items-center gap-3 px-6 py-4 bg-white border-b border-default">
      <button @click="goBack" class="px-2 py-1 text-sm text-primary-600 hover:text-primary-700">
        &larr; 返回会话
      </button>
      <h1 class="text-lg font-bold text-default">Trace 详情</h1>
      <span class="text-sm text-muted font-mono">{{ traceId }}</span>
    </div>

    <div class="flex-1 overflow-auto p-6 space-y-4">
      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>
      <template v-else-if="trace">
        <!-- 基本信息 -->
        <div class="bg-white rounded-xl shadow-sm border border-default p-4">
          <div class="grid grid-cols-4 gap-4 text-sm">
            <div>
              <span class="text-muted text-xs">状态</span>
              <div :class="statusClass(trace.status)" class="inline-block px-2 py-0.5 rounded text-xs font-medium mt-1">
                {{ statusLabel(trace.status) }}
              </div>
            </div>
            <div>
              <span class="text-muted text-xs">耗时</span>
              <div class="text-default font-medium mt-1">{{ formatDuration(trace.duration_ms) }}</div>
            </div>
            <div>
              <span class="text-muted text-xs">Token</span>
              <div class="text-default font-medium mt-1">{{ formatTokens(trace.total_tokens) }}</div>
            </div>
            <div>
              <span class="text-muted text-xs">模型</span>
              <div class="text-default font-medium mt-1">{{ trace.model || '-' }} ({{ trace.provider || '-' }})</div>
            </div>
            <div>
              <span class="text-muted text-xs">迭代</span>
              <div class="text-default mt-1">{{ trace.agent_iterations }}</div>
            </div>
            <div>
              <span class="text-muted text-xs">工具调用</span>
              <div class="text-default mt-1">{{ trace.tool_calls_count }}</div>
            </div>
            <div>
              <span class="text-muted text-xs">来源</span>
              <div class="text-default mt-1">{{ trace.source_type }}</div>
            </div>
            <div>
              <span class="text-muted text-xs">时间</span>
              <div class="text-default mt-1">{{ formatDateTime(trace.created_at) }}</div>
            </div>
          </div>
          <div v-if="trace.tags.length > 0" class="mt-3 flex gap-1">
            <span v-for="tag in trace.tags" :key="tag"
              class="px-1.5 py-0.5 bg-warning-100 text-warning-700 rounded text-xs">{{ tag }}</span>
          </div>
        </div>

        <!-- 用户输入 / AI 输出 -->
        <div class="grid grid-cols-2 gap-4">
          <div class="bg-white rounded-xl shadow-sm border border-default p-4">
            <h3 class="text-sm font-medium text-muted mb-2">用户输入</h3>
            <pre class="text-sm text-default whitespace-pre-wrap break-words max-h-60 overflow-auto">{{ trace.input || '(空)' }}</pre>
          </div>
          <div class="bg-white rounded-xl shadow-sm border border-default p-4">
            <h3 class="text-sm font-medium text-muted mb-2">AI 输出</h3>
            <pre class="text-sm text-default whitespace-pre-wrap break-words max-h-60 overflow-auto">{{ trace.output || '(空)' }}</pre>
          </div>
        </div>

        <!-- 最后一次 LLM 调用上下文 -->
        <div v-if="lastLlmSpan" class="bg-white rounded-xl shadow-sm border border-default p-4">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-sm font-medium text-default">最后一次 LLM 调用上下文</h3>
            <div class="flex items-center gap-3 text-xs text-muted">
              <span v-if="lastLlmSpan.model">{{ lastLlmSpan.model }}</span>
              <span v-if="lastLlmSpan.metadata?.usage">
                {{ lastLlmSpan.metadata.usage.prompt_tokens || 0 }}+{{ lastLlmSpan.metadata.usage.completion_tokens || 0 }} tokens
              </span>
              <span>{{ formatDuration(lastLlmSpan.duration_ms) }}</span>
              <button v-if="lastLlmInput" @click="copyLlmJson" class="px-2 py-0.5 rounded text-primary-600 hover:bg-primary-50 transition-colors" :title="copySuccess ? '已复制' : '复制 JSON'">
                {{ copySuccess ? '已复制' : '复制' }}
              </button>
            </div>
          </div>
          <div class="bg-canvas rounded p-3 max-h-96 overflow-auto">
            <JsonViewer v-if="lastLlmInput" :data="lastLlmInput" :max-preview="120" :default-expand="1" />
            <span v-else class="text-xs text-muted">(无数据)</span>
          </div>
        </div>

        <!-- 错误信息 -->
        <div v-if="trace.error_message" class="bg-danger-50 rounded-xl border border-danger-200 p-4">
          <h3 class="text-sm font-medium text-danger-700 mb-1">错误信息</h3>
          <pre class="text-sm text-danger-600 whitespace-pre-wrap break-words">{{ trace.error_message }}</pre>
        </div>

        <!-- Span 时间线 -->
        <div class="bg-white rounded-xl shadow-sm border border-default p-4">
          <h3 class="text-sm font-medium text-default mb-3">Span 时间线 ({{ spans.length }})</h3>
          <div v-if="spans.length === 0" class="text-center py-6 text-muted text-sm">暂无 Span 数据</div>
          <div v-else class="space-y-2">
            <div v-for="span in spans" :key="span.span_id" class="border border-default rounded-lg">
              <div class="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-surface-hover"
                @click="toggleSpan(span.span_id)">
                <div class="flex items-center gap-2">
                  <span :class="spanIconClass(span)" class="w-2 h-2 rounded-full inline-block"></span>
                  <span class="text-sm text-default font-medium">{{ span.name }}</span>
                  <span v-if="span.model" class="text-xs text-muted">({{ span.model }})</span>
                </div>
                <div class="flex items-center gap-3 text-xs text-muted">
                  <span v-if="span.prompt_tokens || span.completion_tokens">
                    {{ span.prompt_tokens }}+{{ span.completion_tokens }}
                  </span>
                  <span>{{ formatDuration(span.duration_ms) }}</span>
                  <span>{{ expandedSpans.has(span.span_id) ? '▼' : '▶' }}</span>
                </div>
              </div>
              <div v-if="expandedSpans.has(span.span_id)" class="px-3 pb-3 border-t border-default">
                <div class="grid grid-cols-2 gap-3 mt-2">
                  <div>
                    <h4 class="text-xs text-muted font-medium mb-1">输入</h4>
                    <div class="bg-canvas rounded p-2 max-h-60 overflow-auto">
                      <template v-if="span.input">
                        <JsonViewer :data="parseJson(span.input)" :max-preview="80" :default-expand="0" />
                      </template>
                      <span v-else class="text-xs text-muted">(空)</span>
                    </div>
                  </div>
                  <div>
                    <h4 class="text-xs text-muted font-medium mb-1">输出</h4>
                    <div class="bg-canvas rounded p-2 max-h-60 overflow-auto">
                      <template v-if="span.output">
                        <JsonViewer :data="parseJson(span.output)" :max-preview="80" :default-expand="0" />
                      </template>
                      <span v-else class="text-xs text-muted">(空)</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
      <div v-else class="text-center py-12 text-muted">未找到追踪数据</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getTraceDetail, type TraceDetail as TraceDetailType, type SpanDetail } from '@/api/monitor'
import JsonViewer from '@/components/ui/JsonViewer.vue'

const router = useRouter()
const route = useRoute()
const traceId = route.params.trace_id as string

const trace = ref<TraceDetailType | null>(null)
const spans = ref<SpanDetail[]>([])
const loading = ref(false)
const expandedSpans = ref<Set<string>>(new Set())
const copySuccess = ref(false)

const lastLlmSpan = computed(() => {
  // 找到最后一个 span_type=generation 的 span
  const genSpans = spans.value.filter(s => s.span_type === 'generation')
  return genSpans.length > 0 ? genSpans[genSpans.length - 1] : null
})

const lastLlmInput = computed(() => {
  if (!lastLlmSpan.value?.input) return null
  return parseJson(lastLlmSpan.value.input)
})

function goBack() {
  router.back()
}

function copyLlmJson() {
  if (!lastLlmInput.value) return
  const jsonStr = JSON.stringify(lastLlmInput.value, null, 2)
  navigator.clipboard.writeText(jsonStr).then(() => {
    copySuccess.value = true
    setTimeout(() => { copySuccess.value = false }, 2000)
  })
}

function toggleSpan(spanId: string) {
  if (expandedSpans.value.has(spanId)) {
    expandedSpans.value.delete(spanId)
  } else {
    expandedSpans.value.add(spanId)
  }
}

function parseJson(str: string | null): any {
  if (!str) return null
  try {
    return JSON.parse(str)
  } catch {
    return str
  }
}

function spanIconClass(span: SpanDetail): string {
  if (span.status === 'failed') return 'bg-danger-500'
  if (span.span_type === 'generation') return 'bg-info-500'
  return 'bg-success-500'
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

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}

function formatDuration(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}

function formatDateTime(ts: string | null): string {
  if (!ts) return '-'
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadData() {
  loading.value = true
  try {
    const res = await getTraceDetail(traceId)
    if (res.success) {
      trace.value = res.trace
      spans.value = res.spans
    }
  } catch (e) {
    console.error('Failed to load trace detail:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

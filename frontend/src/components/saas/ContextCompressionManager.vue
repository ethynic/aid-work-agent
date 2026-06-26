<template>
  <div class="page-container">
    <!-- 工具栏 -->
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">上下文压缩管理</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">查看会话内上下文压缩记录、指标统计，支持回滚与手动触发</p>
      </div>
      <div class="page-toolbar-right">
        <BaseInput
          v-model="filterSessionId"
          size="sm"
          placeholder="按 session_id 过滤"
          class="w-72"
          @keyup.enter="handleSearch('')"
        />
        <BaseSelect v-model="filterSourceType" size="sm" class="w-32" @update:model-value="handleSearch('')">
          <option value="">全部来源</option>
          <option value="chat">chat</option>
          <option value="wecom_kf">wecom_kf</option>
          <option value="dingtalk">dingtalk</option>
          <option value="feishu">feishu</option>
          <option value="wecom_personal_rpa">wecom_personal_rpa</option>
        </BaseSelect>
        <BaseButton size="sm" intent="secondary" @click="refresh">刷新</BaseButton>
      </div>
    </div>

    <!-- 指标卡片 -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
      <BaseCard>
        <div class="text-muted text-xs">压缩调用总数</div>
        <div class="text-2xl font-bold text-default mt-1">{{ totalInvocations }}</div>
        <div class="text-xs text-muted mt-1">
          成功 {{ successCount }} · 降级 {{ fallbackCount }} · 跳过 {{ skippedCount }} · 失败 {{ failedCount }}
        </div>
      </BaseCard>
      <BaseCard>
        <div class="text-muted text-xs">成功率</div>
        <div class="text-2xl font-bold mt-1" :class="successRate >= 95 ? 'text-success-600' : successRate >= 80 ? 'text-warning-600' : 'text-danger-600'">
          {{ successRate.toFixed(1) }}%
        </div>
        <div class="text-xs text-muted mt-1">成功 / (成功 + 降级 + 失败)</div>
      </BaseCard>
      <BaseCard>
        <div class="text-muted text-xs">平均压缩比</div>
        <div class="text-2xl font-bold text-default mt-1">{{ avgRatio === 0 ? '-' : (avgRatio * 100).toFixed(1) + '%' }}</div>
        <div class="text-xs text-muted mt-1">compressed / original（越低越好）</div>
      </BaseCard>
      <BaseCard>
        <div class="text-muted text-xs">平均耗时 / P95</div>
        <div class="text-2xl font-bold text-default mt-1">{{ avgDuration.toFixed(2) }}s</div>
        <div class="text-xs text-muted mt-1">P95: {{ p95Duration.toFixed(2) }}s</div>
      </BaseCard>
    </div>

    <!-- 表格 -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="summaries" row-key="summary_id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
        <template #session_id="{ row }">
          <span class="font-mono text-xs" :title="row.session_id">
            {{ truncate(row.session_id, 20) }}
          </span>
        </template>
        <template #source_type="{ row }">
          <span class="font-mono text-xs">{{ row.source_type }}</span>
        </template>
        <template #compression_ratio="{ row }">
          <span :class="row.compression_ratio > 0.5 ? 'text-warning-600' : 'text-success-600'" class="font-mono text-xs">
            {{ (row.compression_ratio * 100).toFixed(1) }}%
          </span>
        </template>
        <template #compressed_message_count="{ row }">
          <span class="font-mono text-xs">{{ row.compressed_message_count }}</span>
        </template>
        <template #fallback_used="{ row }">
          <BaseBadge :intent="row.fallback_used ? 'warning' : 'success'">
            {{ row.fallback_used ? '降级' : '正常' }}
          </BaseBadge>
        </template>
        <template #status="{ row }">
          <BaseBadge :intent="statusIntent(row.status)">{{ statusLabel(row.status) }}</BaseBadge>
        </template>
        <template #actions="{ row }">
          <div class="flex items-center justify-center gap-1">
            <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="showDetail(row as ContextSummaryListItem)">详情</BaseButton>
            <BaseButton
              v-if="row.status === 'active' || row.status === 'superseded'"
              intent="danger-ghost"
              size="sm"
              class="whitespace-nowrap text-xs"
              @click="confirmRollback(row as ContextSummaryListItem)"
            >回滚</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2 py-12 text-muted">
            <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
            加载中...
          </div>
          <div v-else class="text-muted py-12 text-center">暂无压缩记录</div>
        </template>
      </BaseTable>
    </div>

    <!-- 分页 -->
    <BasePagination
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
    />

    <!-- 手动压缩入口 -->
    <div class="mt-4 p-3 border border-default rounded-lg bg-surface">
      <div class="text-sm font-medium text-default mb-2">手动触发压缩（运维）</div>
      <div class="flex items-center gap-2 flex-wrap">
        <BaseInput v-model="manualSessionId" size="sm" placeholder="session_id" class="w-72" />
        <BaseSelect v-model="manualSourceType" size="sm" class="w-40">
          <option value="chat">chat</option>
          <option value="wecom_kf">wecom_kf</option>
          <option value="dingtalk">dingtalk</option>
          <option value="feishu">feishu</option>
          <option value="wecom_personal_rpa">wecom_personal_rpa</option>
        </BaseSelect>
        <BaseButton size="sm" :disabled="!manualSessionId || manualLoading" @click="handleManualCompact">
          {{ manualLoading ? '压缩中...' : '立即压缩' }}
        </BaseButton>
        <span v-if="manualResult" class="text-xs ml-2" :class="manualResult.success ? 'text-success-600' : 'text-danger-600'">
          {{ manualResult.message || `summary_id=${manualResult.result?.summary_id}` }}
        </span>
      </div>
    </div>

    <!-- 详情弹框 -->
    <BaseModal
      v-model="showDetailModal"
      :title="selectedSummary ? `压缩详情 - ${selectedSummary.summary_id}` : '压缩详情'"
      size="xl"
    >
      <div v-if="detailLoading" class="py-12 text-center text-muted">加载中...</div>
      <div v-else-if="selectedSummary">
        <!-- 元数据 -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div>
            <div class="text-xs text-muted">来源</div>
            <div class="text-sm font-mono mt-0.5">{{ selectedSummary.source_type }}</div>
          </div>
          <div>
            <div class="text-xs text-muted">状态</div>
            <div class="mt-0.5">
              <BaseBadge :intent="statusIntent(selectedSummary.status)">{{ statusLabel(selectedSummary.status) }}</BaseBadge>
            </div>
          </div>
          <div>
            <div class="text-xs text-muted">压缩比</div>
            <div class="text-sm font-mono mt-0.5">{{ (selectedSummary.compression_ratio * 100).toFixed(1) }}%</div>
          </div>
          <div>
            <div class="text-xs text-muted">被压缩消息数</div>
            <div class="text-sm font-mono mt-0.5">{{ selectedSummary.compressed_message_count }}</div>
          </div>
          <div>
            <div class="text-xs text-muted">Token（前→后）</div>
            <div class="text-sm font-mono mt-0.5">
              {{ selectedSummary.original_token_count }} → {{ selectedSummary.compressed_token_count }}
            </div>
          </div>
          <div>
            <div class="text-xs text-muted">摘要 LLM</div>
            <div class="text-sm font-mono mt-0.5">
              {{ selectedSummary.llm_provider || '-' }} / {{ selectedSummary.llm_model || '-' }}
            </div>
          </div>
          <div>
            <div class="text-xs text-muted">降级</div>
            <div class="mt-0.5">
              <BaseBadge :intent="selectedSummary.fallback_used ? 'warning' : 'success'">
                {{ selectedSummary.fallback_used ? '是' : '否' }}
              </BaseBadge>
            </div>
          </div>
          <div>
            <div class="text-xs text-muted">创建时间</div>
            <div class="text-sm mt-0.5">{{ formatDateTime(selectedSummary.created_at) }}</div>
          </div>
        </div>

        <!-- 摘要文本 -->
        <div class="mb-4">
          <div class="text-sm font-medium text-default mb-1">摘要内容</div>
          <pre class="bg-canvas border border-default rounded p-3 text-xs whitespace-pre-wrap max-h-[300px] overflow-y-auto">{{ selectedSummary.summary_text || '(空)' }}</pre>
        </div>

        <!-- 被压缩原消息 -->
        <div>
          <div class="text-sm font-medium text-default mb-1">被压缩的原消息（{{ compressedMessages.length }} 条）</div>
          <div v-if="compressedMessages.length === 0" class="text-muted text-xs py-4 text-center border border-default rounded">
            无消息数据
          </div>
          <div v-else class="border border-default rounded max-h-[300px] overflow-y-auto">
            <div
              v-for="msg in compressedMessages"
              :key="msg.id"
              class="px-3 py-2 border-b border-default last:border-b-0"
            >
              <div class="flex items-center gap-2 mb-1">
                <BaseBadge :intent="roleIntent(msg.role)">{{ msg.role }}</BaseBadge>
                <span class="text-xs text-muted">#{{ msg.id }}</span>
                <span class="text-xs text-muted">{{ formatDateTime(msg.created_at) }}</span>
              </div>
              <div class="text-xs text-default whitespace-pre-wrap" style="display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;">
                {{ msg.content || '(无文本内容)' }}
              </div>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showDetailModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import { usePageContext } from '@/composables/usePageContext'
import {
  listSummaries,
  getCompressionStats,
  getSummaryDetail,
  rollbackSummary,
  manualCompact,
  type ContextSummaryListItem,
  type ContextSummaryDetail,
  type CompressedMessage,
  type CompressionStats,
} from '@/api/contextCompression'

const toast = useToast()

// ------- Columns -------
const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'created_at', label: '时间', width: '170px' },
  { key: 'session_id', label: 'Session', width: '180px' },
  { key: 'source_type', label: '来源', width: '120px' },
  { key: 'compression_ratio', label: '压缩比', width: '90px' },
  { key: 'compressed_message_count', label: '消息数', width: '80px' },
  { key: 'fallback_used', label: '降级', width: '80px' },
  { key: 'status', label: '状态', width: '110px' },
  { key: 'actions', label: '操作', width: '170px', thAlign: 'center' as const },
]

// ------- State -------
const filterSessionId = ref('')
const filterSourceType = ref('')
const summaries = ref<ContextSummaryListItem[]>([])
const total = ref(0)
const stats = ref<CompressionStats | null>(null)

const showDetailModal = ref(false)
const detailLoading = ref(false)
const selectedSummary = ref<ContextSummaryDetail | null>(null)
const compressedMessages = ref<CompressedMessage[]>([])

const manualSessionId = ref('')
const manualSourceType = ref('chat')
const manualLoading = ref(false)
const manualResult = ref<{ success: boolean; message?: string; result?: any } | null>(null)

// ------- Page Context -------
async function loadData() {
  try {
    const response = await listSummaries({
      session_id: filterSessionId.value || undefined,
      source_type: filterSourceType.value || undefined,
      limit: pageSize.value,
    })
    if (response.success) {
      summaries.value = response.items
      total.value = response.total
    } else {
      toast.error(response.error || '加载失败')
      summaries.value = []
      total.value = 0
    }
  } catch (error: any) {
    console.error('前端日志：加载压缩记录失败:', error)
    toast.error(error.message || '加载失败')
    summaries.value = []
    total.value = 0
  }
}

async function loadStats() {
  try {
    const response = await getCompressionStats()
    if (response.success && response.stats) {
      stats.value = response.stats
    }
  } catch (error) {
    console.error('前端日志：加载指标失败:', error)
  }
}

const { currentPage, pageSize, loading, seqNumber, handleSearch, refresh } =
  usePageContext(async () => { await loadData() })
pageSize.value = 20

onMounted(() => {
  loadStats()
})

// ------- Stats Derived -------
const counts = computed<Record<string, number>>(() => stats.value?.counts || {})
const totalInvocations = computed(() =>
  Object.values(counts.value).reduce((s, n) => s + (n || 0), 0)
)
const successCount = computed(() =>
  Object.entries(counts.value)
    .filter(([k]) => k.endsWith(':success'))
    .reduce((s, [, n]) => s + (n || 0), 0)
)
const fallbackCount = computed(() =>
  Object.entries(counts.value)
    .filter(([k]) => k.endsWith(':fallback'))
    .reduce((s, [, n]) => s + (n || 0), 0)
)
const skippedCount = computed(() =>
  Object.entries(counts.value)
    .filter(([k]) => k.endsWith(':skipped'))
    .reduce((s, [, n]) => s + (n || 0), 0)
)
const failedCount = computed(() =>
  Object.entries(counts.value)
    .filter(([k]) => k.endsWith(':failed'))
    .reduce((s, [, n]) => s + (n || 0), 0)
)
const successRate = computed(() => {
  const denom = successCount.value + fallbackCount.value + failedCount.value
  return denom === 0 ? 0 : (successCount.value / denom) * 100
})
const avgRatio = computed(() => stats.value?.ratio.avg || 0)
const avgDuration = computed(() => stats.value?.duration.avg || 0)
const p95Duration = computed(() => stats.value?.duration.p95 || 0)

// ------- Detail -------
async function showDetail(row: ContextSummaryListItem) {
  showDetailModal.value = true
  detailLoading.value = true
  selectedSummary.value = null
  compressedMessages.value = []
  try {
    const response = await getSummaryDetail(row.summary_id)
    if (response.success) {
      selectedSummary.value = response.summary || null
      compressedMessages.value = response.messages || []
    } else {
      toast.error(response.error || '加载详情失败')
    }
  } catch (error: any) {
    console.error('前端日志：加载压缩详情失败:', error)
    toast.error(error.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

// ------- Rollback -------
async function confirmRollback(row: ContextSummaryListItem) {
  if (!confirm(`确定回滚此次压缩？\n\n摘要 ID：${row.summary_id}\n\n回滚后，被压缩的 ${row.compressed_message_count} 条原消息将恢复 compacted=false，重新参与 LLM 上下文。`)) {
    return
  }
  try {
    const response = await rollbackSummary(row.summary_id)
    if (response.success) {
      toast.success(`已回滚，恢复 ${response.restored_message_count || 0} 条消息`)
      refresh()
      loadStats()
    } else {
      toast.error(response.error || '回滚失败')
    }
  } catch (error: any) {
    console.error('前端日志：回滚失败:', error)
    toast.error(error.message || '回滚失败')
  }
}

// ------- Manual Compact -------
async function handleManualCompact() {
  if (!manualSessionId.value) {
    toast.warning('请输入 session_id')
    return
  }
  manualLoading.value = true
  manualResult.value = null
  try {
    const response = await manualCompact(manualSessionId.value, manualSourceType.value)
    manualResult.value = response
    if (response.success) {
      if (response.result) {
        toast.success(`压缩完成：summary_id=${response.result.summary_id}`)
      } else {
        toast.info(response.message || '未触发压缩')
      }
      refresh()
      loadStats()
    } else {
      toast.error(response.error || '压缩失败')
    }
  } catch (error: any) {
    console.error('前端日志：手动压缩失败:', error)
    toast.error(error.message || '压缩失败')
  } finally {
    manualLoading.value = false
  }
}

// ------- Helpers -------
function formatDateTime(s: string | null | undefined): string {
  if (!s) return '-'
  try {
    const d = new Date(s)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
  } catch {
    return String(s)
  }
}
function truncate(s: string, n: number): string {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '...' : s
}
function statusLabel(status: string): string {
  switch (status) {
    case 'active': return '生效中'
    case 'superseded': return '已被替代'
    case 'rolled_back': return '已回滚'
    default: return status
  }
}
function statusIntent(status: string): 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  switch (status) {
    case 'active': return 'success'
    case 'superseded': return 'info'
    case 'rolled_back': return 'warning'
    default: return 'neutral'
  }
}
function roleIntent(role: string): 'primary' | 'info' | 'success' | 'warning' | 'neutral' {
  switch (role) {
    case 'user': return 'primary'
    case 'assistant': return 'info'
    case 'tool': return 'success'
    case 'system': return 'warning'
    default: return 'neutral'
  }
}
</script>

<template>
  <div class="min-h-full bg-surface text-default transition-colors duration-200">
    <!-- Header -->
    <div class="px-6 pt-6 pb-4">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-xl font-bold tracking-tight" style="font-family: 'Noto Sans SC', 'DM Sans', sans-serif;">
            跟进记录
          </h1>
          <p class="text-sm text-muted mt-0.5">客户跟进全记录与质量追踪</p>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="px-6 pb-4">
      <div class="flex items-center gap-3 p-3 rounded-xl bg-canvas border border-default">
        <select
          v-model="filters.followup_type"
          class="text-sm rounded-lg border border-default bg-surface text-default px-3 py-1.5 focus:ring-1 focus:ring-primary-600 focus:border-primary-600 outline-none"
        >
          <option value="">全部类型</option>
          <option v-for="(label, key) in typeLabels" :key="key" :value="key">{{ label }}</option>
        </select>
        <input
          v-model="filters.date_from"
          type="date"
          class="text-sm rounded-lg border border-default bg-surface text-default px-3 py-1.5 focus:ring-1 focus:ring-primary-600 focus:border-primary-600 outline-none"
        />
        <span class="text-muted text-sm">至</span>
        <input
          v-model="filters.date_to"
          type="date"
          class="text-sm rounded-lg border border-default bg-surface text-default px-3 py-1.5 focus:ring-1 focus:ring-primary-600 focus:border-primary-600 outline-none"
        />
        <div class="flex-1" />
        <button
          @click="loadRecords"
          class="text-sm px-3 py-1.5 rounded-lg bg-primary-600 text-white hover:brightness-110 transition-all"
        >
          查询
        </button>
        <button
          @click="resetFilters"
          class="text-sm px-3 py-1.5 rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors"
        >
          重置
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="px-6 pb-6">
      <div class="rounded-xl border border-default overflow-hidden bg-canvas">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-default bg-surface/50">
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider w-12">序号</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">公司名称</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">联系人</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">跟进类型</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">跟进内容</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">结果</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">质量评分</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">跟进时间</th>
              <th class="text-right px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="9" class="text-center py-12 text-muted">
                <div class="flex items-center justify-center gap-2">
                  <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                  加载中...
                </div>
              </td>
            </tr>
            <tr v-else-if="records.length === 0">
              <td colspan="9" class="text-center py-12 text-muted">
                暂无跟进记录
              </td>
            </tr>
            <tr
              v-for="(record, index) in records"
              :key="record.record_id"
              class="border-b border-default last:border-0 hover:bg-surface-hover transition-colors"
            >
              <td class="px-4 py-3 text-muted tabular-nums">{{ index + 1 }}</td>
              <td class="px-4 py-3 text-muted">{{ record.company_name || '-' }}</td>
              <td class="px-4 py-3 text-muted">{{ record.contact_name || '-' }}</td>
              <td class="px-4 py-3">
                <span
                  class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium"
                  :class="typeBadgeClass(record.followup_type)"
                >
                  {{ typeLabels[record.followup_type as keyof typeof typeLabels] || record.followup_type }}
                </span>
              </td>
              <td class="px-4 py-3 text-muted max-w-[200px]">
                <span class="block truncate" :title="record.content">{{ record.content || '-' }}</span>
              </td>
              <td class="px-4 py-3">
                <span
                  v-if="record.outcome"
                  class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium"
                  :class="outcomeBadgeClass(record.outcome)"
                >
                  {{ outcomeLabels[record.outcome as keyof typeof outcomeLabels] || record.outcome }}
                </span>
                <span v-else class="text-muted">-</span>
              </td>
              <td class="px-4 py-3">
                <span
                  v-if="record.quality_score != null"
                  class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium tabular-nums"
                  :class="scoreBadgeClass(record.quality_score)"
                >
                  {{ record.quality_score }}
                </span>
                <span v-else class="text-muted text-xs">未评估</span>
              </td>
              <td class="px-4 py-3 text-muted text-xs tabular-nums">{{ formatDate(record.followup_at) }}</td>
              <td class="px-4 py-3 text-right">
                <div class="flex items-center justify-end gap-1">
                  <button
                    v-if="record.quality_score == null"
                    @click="handleEvaluate(record.record_id)"
                    class="text-xs px-2 py-1 rounded border border-default text-muted hover:bg-surface-hover transition-colors"
                    :disabled="evaluating === record.record_id"
                  >
                    {{ evaluating === record.record_id ? '评估中...' : '评估' }}
                  </button>
                  <button
                    @click="openDetail(record)"
                    class="text-xs px-2 py-1 rounded border border-default text-muted hover:bg-surface-hover transition-colors"
                  >
                    详情
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div v-if="records.length > 0" class="flex items-center justify-between mt-4">
        <p class="text-sm text-muted">
          共 <span class="font-medium text-muted">{{ records.length }}</span> 条记录
        </p>
        <div class="flex items-center gap-1">
          <button
            @click="currentPage > 1 && (currentPage--, loadRecords())"
            :disabled="currentPage <= 1"
            class="px-3 py-1.5 text-sm rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            上一页
          </button>
          <button
            @click="loadRecords"
            class="px-3 py-1.5 text-sm rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            下一页
          </button>
        </div>
      </div>
    </div>

    <!-- Detail Modal -->
    <div
      v-if="showDetailModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="showDetailModal = false"
    >
      <div class="bg-canvas rounded-xl border border-default shadow-2xl w-full max-w-[600px] max-h-[85vh] overflow-y-auto mx-4">
        <div class="flex items-center justify-between px-6 py-4 border-b border-default">
          <h2 class="text-base font-bold text-default">跟进记录详情</h2>
          <button @click="showDetailModal = false" class="text-muted hover:text-default transition-colors">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div v-if="detailRecord" class="px-6 py-4 space-y-4">
          <div class="grid grid-cols-2 gap-4">
            <div>
              <p class="text-xs text-muted mb-1">公司名称</p>
              <p class="text-sm text-default">{{ detailRecord.company_name || '-' }}</p>
            </div>
            <div>
              <p class="text-xs text-muted mb-1">联系人</p>
              <p class="text-sm text-default">{{ detailRecord.contact_name || '-' }}</p>
            </div>
            <div>
              <p class="text-xs text-muted mb-1">跟进类型</p>
              <p class="text-sm">
                <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="typeBadgeClass(detailRecord.followup_type)">
                  {{ typeLabels[detailRecord.followup_type as keyof typeof typeLabels] || detailRecord.followup_type }}
                </span>
              </p>
            </div>
            <div>
              <p class="text-xs text-muted mb-1">跟进结果</p>
              <p class="text-sm">
                <span v-if="detailRecord.outcome" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="outcomeBadgeClass(detailRecord.outcome)">
                  {{ outcomeLabels[detailRecord.outcome as keyof typeof outcomeLabels] || detailRecord.outcome }}
                </span>
                <span v-else>-</span>
              </p>
            </div>
            <div>
              <p class="text-xs text-muted mb-1">跟进时间</p>
              <p class="text-sm text-default tabular-nums">{{ formatDateTime(detailRecord.followup_at) }}</p>
            </div>
            <div>
              <p class="text-xs text-muted mb-1">跟进时长</p>
              <p class="text-sm text-default">{{ detailRecord.duration_minutes ? detailRecord.duration_minutes + ' 分钟' : '-' }}</p>
            </div>
          </div>

          <div>
            <p class="text-xs text-muted mb-1">跟进内容</p>
            <div class="text-sm text-default bg-surface rounded-lg p-3 border border-default whitespace-pre-wrap">{{ detailRecord.content }}</div>
          </div>

          <div v-if="detailRecord.quality_score != null">
            <p class="text-xs text-muted mb-1">质量评分</p>
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium tabular-nums" :class="scoreBadgeClass(detailRecord.quality_score)">
              {{ detailRecord.quality_score }} / 10
            </span>
          </div>

          <!-- AI Call specific fields -->
          <template v-if="detailRecord.followup_type === 'ai_call'">
            <div class="border-t border-default pt-4">
              <p class="text-xs font-medium text-muted uppercase tracking-wider mb-3">AI 外呼详情</p>
              <div class="grid grid-cols-2 gap-4 mb-3">
                <div>
                  <p class="text-xs text-muted mb-1">通话ID</p>
                  <p class="text-sm text-default tabular-nums">{{ detailRecord.call_id || '-' }}</p>
                </div>
                <div>
                  <p class="text-xs text-muted mb-1">通话情感</p>
                  <p class="text-sm">
                    <span v-if="detailRecord.call_sentiment" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="outcomeBadgeClass(detailRecord.call_sentiment === 'positive' ? 'positive' : detailRecord.call_sentiment === 'negative' ? 'negative' : 'neutral')">
                      {{ detailRecord.call_sentiment === 'positive' ? '积极' : detailRecord.call_sentiment === 'negative' ? '消极' : '中性' }}
                    </span>
                    <span v-else>-</span>
                  </p>
                </div>
              </div>
              <div v-if="detailRecord.call_summary" class="mb-3">
                <p class="text-xs text-muted mb-1">AI 总结</p>
                <div class="text-sm text-default bg-surface rounded-lg p-3 border border-default">{{ detailRecord.call_summary }}</div>
              </div>
              <div v-if="detailRecord.call_transcript">
                <p class="text-xs text-muted mb-1">通话转写</p>
                <div class="text-sm text-default bg-surface rounded-lg p-3 border border-default max-h-[200px] overflow-y-auto whitespace-pre-wrap">{{ detailRecord.call_transcript }}</div>
              </div>
            </div>
          </template>

          <div v-if="detailRecord.next_action">
            <p class="text-xs text-muted mb-1">下一步计划</p>
            <p class="text-sm text-default">{{ detailRecord.next_action }}</p>
          </div>
        </div>
        <div class="px-6 py-4 border-t border-default flex justify-end">
          <button
            @click="showDetailModal = false"
            class="text-sm px-4 py-2 rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { followupAPI, type FollowupRecord } from '@/api/followup'

// --- 常量映射 ---
const typeLabels: Record<string, string> = {
  phone: '电话',
  email: '邮件',
  visit: '拜访',
  wechat: '微信',
  ai_call: 'AI外呼',
  other: '其他',
}

const outcomeLabels: Record<string, string> = {
  positive: '积极',
  neutral: '中性',
  negative: '消极',
  no_response: '无响应',
}

// --- 响应式状态 ---
const records = ref<FollowupRecord[]>([])
const loading = ref(false)
const currentPage = ref(1)
const showDetailModal = ref(false)
const detailRecord = ref<FollowupRecord | null>(null)
const evaluating = ref<string | null>(null)

const filters = ref({
  followup_type: '',
  date_from: '',
  date_to: '',
})

// --- 样式辅助 ---
function typeBadgeClass(type: string): string {
  const map: Record<string, string> = {
    phone: 'bg-info-100 text-info-700',
    email: 'bg-purple-100 text-purple-700',
    visit: 'bg-success-100 text-success-700',
    wechat: 'bg-success-100 text-success-700',
    ai_call: 'bg-amber-100 text-amber-700',
    other: 'bg-canvas text-muted',
  }
  return map[type] || map.other
}

function outcomeBadgeClass(outcome: string): string {
  const map: Record<string, string> = {
    positive: 'bg-success-100 text-success-700',
    neutral: 'bg-canvas text-muted',
    negative: 'bg-danger-100 text-danger-700',
    no_response: 'bg-amber-100 text-amber-700',
  }
  return map[outcome] || map.neutral
}

function scoreBadgeClass(score: number): string {
  if (score >= 8) return 'bg-success-100 text-success-700'
  if (score >= 5) return 'bg-amber-100 text-amber-700'
  return 'bg-danger-100 text-danger-700'
}

// --- 日期格式化 ---
function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '-' }
}

function formatDateTime(dateStr?: string | null): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '-' }
}

// --- 数据加载 ---
async function loadRecords() {
  loading.value = true
  try {
    const res = await followupAPI.listRecords({
      date_from: filters.value.date_from || undefined,
      date_to: filters.value.date_to || undefined,
      limit: 50,
    })
    records.value = res.items || []
  } catch (e) {
    console.error('加载跟进记录失败:', e)
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.value = { followup_type: '', date_from: '', date_to: '' }
  loadRecords()
}

function openDetail(record: FollowupRecord) {
  detailRecord.value = record
  showDetailModal.value = true
}

async function handleEvaluate(recordId: string) {
  evaluating.value = recordId
  try {
    const res = await followupAPI.evaluateRecord(recordId)
    const idx = records.value.findIndex(r => r.record_id === recordId)
    if (idx >= 0) {
      records.value[idx].quality_score = res.quality_score
    }
  } catch (e) {
    console.error('评估失败:', e)
  } finally {
    evaluating.value = null
  }
}

onMounted(() => {
  loadRecords()
})
</script>

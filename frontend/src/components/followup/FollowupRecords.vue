<template>
  <div class="page-container">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">跟进记录</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">客户跟进全记录与质量追踪</p>
      </div>
    </div>

    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.followup_type" size="sm" class="w-28">
        <option value="">全部类型</option>
        <option v-for="(label, key) in typeLabels" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="filters.date_from" type="date" size="sm" />
      <span class="text-muted text-sm">至</span>
      <BaseInput v-model="filters.date_to" type="date" size="sm" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch('')">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="pagedRecords" row-key="record_id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #company_name="{ row }">{{ row.company_name || '-' }}</template>
        <template #contact_name="{ row }">{{ row.contact_name || '-' }}</template>
        <template #followup_type="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="typeBadgeClass(row.followup_type)">
            {{ typeLabels[row.followup_type as keyof typeof typeLabels] || row.followup_type }}
          </span>
        </template>
        <template #content="{ row }">
          <span class="block max-w-[200px] truncate" :title="row.content">{{ row.content || '-' }}</span>
        </template>
        <template #outcome="{ row }">
          <span v-if="row.outcome" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="outcomeBadgeClass(row.outcome)">
            {{ outcomeLabels[row.outcome as keyof typeof outcomeLabels] || row.outcome }}
          </span>
          <span v-else class="text-muted">-</span>
        </template>
        <template #quality_score="{ row }">
          <span v-if="row.quality_score != null" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium tabular-nums" :class="scoreBadgeClass(row.quality_score)">
            {{ row.quality_score }}
          </span>
          <span v-else class="text-muted text-xs">未评估</span>
        </template>
        <template #followup_at="{ row }">{{ formatDate(row.followup_at) }}</template>
        <template #actions="{ row }">
          <div class="flex items-center justify-end gap-1">
            <BaseButton v-if="row.quality_score == null" intent="secondary" size="sm" :disabled="evaluating === row.record_id" @click="handleEvaluate(row.record_id)">
              {{ evaluating === row.record_id ? '评估中...' : '评估' }}
            </BaseButton>
            <BaseButton intent="ghost" size="sm" @click="openDetail(row)">详情</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            加载中...
          </div>
          <span v-else>暂无跟进记录</span>
        </template>
      </BaseTable>
    </div>

    <BasePagination
      v-if="total > 0"
      :total="total"
      v-model:current-page="currentPage"
      :page-size="pageSize"
    />

    <!-- Detail Modal -->
    <BaseModal v-model="showDetailModal" title="跟进记录详情" size="lg">
      <template v-if="detailRecord">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">公司名称</label>
            <p class="text-sm text-default">{{ detailRecord.company_name || '-' }}</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">联系人</label>
            <p class="text-sm text-default">{{ detailRecord.contact_name || '-' }}</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">跟进类型</label>
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="typeBadgeClass(detailRecord.followup_type)">
              {{ typeLabels[detailRecord.followup_type as keyof typeof typeLabels] || detailRecord.followup_type }}
            </span>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">跟进结果</label>
            <span v-if="detailRecord.outcome" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="outcomeBadgeClass(detailRecord.outcome)">
              {{ outcomeLabels[detailRecord.outcome as keyof typeof outcomeLabels] || detailRecord.outcome }}
            </span>
            <span v-else class="text-sm text-muted">-</span>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">跟进时间</label>
            <p class="text-sm text-default tabular-nums">{{ formatDateTime(detailRecord.followup_at) }}</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">跟进时长</label>
            <p class="text-sm text-default">{{ detailRecord.duration_minutes ? detailRecord.duration_minutes + ' 分钟' : '-' }}</p>
          </div>
        </div>

        <div class="mt-4">
          <label class="text-sm text-muted mb-1 block">跟进内容</label>
          <div class="text-sm text-default bg-canvas rounded-lg p-3 border border-default whitespace-pre-wrap">{{ detailRecord.content }}</div>
        </div>

        <div v-if="detailRecord.quality_score != null" class="mt-4">
          <label class="text-sm text-muted mb-1 block">质量评分</label>
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium tabular-nums" :class="scoreBadgeClass(detailRecord.quality_score)">
            {{ detailRecord.quality_score }} / 10
          </span>
        </div>

        <template v-if="detailRecord.followup_type === 'ai_call'">
          <div class="border-t border-default pt-4 mt-4">
            <h4 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">AI 外呼详情</h4>
            <div class="grid grid-cols-2 gap-4 mb-3">
              <div>
                <label class="text-sm text-muted mb-1 block">通话ID</label>
                <p class="text-sm text-default tabular-nums">{{ detailRecord.call_id || '-' }}</p>
              </div>
              <div>
                <label class="text-sm text-muted mb-1 block">通话情感</label>
                <span v-if="detailRecord.call_sentiment" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="outcomeBadgeClass(detailRecord.call_sentiment === 'positive' ? 'positive' : detailRecord.call_sentiment === 'negative' ? 'negative' : 'neutral')">
                  {{ detailRecord.call_sentiment === 'positive' ? '积极' : detailRecord.call_sentiment === 'negative' ? '消极' : '中性' }}
                </span>
                <span v-else class="text-sm text-muted">-</span>
              </div>
            </div>
            <div v-if="detailRecord.call_summary" class="mb-3">
              <label class="text-sm text-muted mb-1 block">AI 总结</label>
              <div class="text-sm text-default bg-canvas rounded-lg p-3 border border-default">{{ detailRecord.call_summary }}</div>
            </div>
            <div v-if="detailRecord.call_transcript">
              <label class="text-sm text-muted mb-1 block">通话转写</label>
              <div class="text-sm text-default bg-canvas rounded-lg p-3 border border-default max-h-[200px] overflow-y-auto whitespace-pre-wrap">{{ detailRecord.call_transcript }}</div>
            </div>
          </div>
        </template>

        <div v-if="detailRecord.next_action" class="mt-4">
          <label class="text-sm text-muted mb-1 block">下一步计划</label>
          <p class="text-sm text-default">{{ detailRecord.next_action }}</p>
        </div>
      </template>
      <template #footer>
        <BaseButton intent="secondary" @click="showDetailModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { followupAPI, type FollowupRecord } from '@/api/followup'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'

const typeLabels: Record<string, string> = {
  phone: '电话', email: '邮件', visit: '拜访', wechat: '微信', ai_call: 'AI外呼', other: '其他',
}
const outcomeLabels: Record<string, string> = {
  positive: '积极', neutral: '中性', negative: '消极', no_response: '无响应',
}

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'company_name', label: '公司名称' },
  { key: 'contact_name', label: '联系人' },
  { key: 'followup_type', label: '跟进类型' },
  { key: 'content', label: '跟进内容' },
  { key: 'outcome', label: '结果' },
  { key: 'quality_score', label: '质量评分' },
  { key: 'followup_at', label: '跟进时间' },
  { key: 'actions', label: '操作', width: '120px' },
]

const allRecords = ref<FollowupRecord[]>([])
const total = ref(0)
const showDetailModal = ref(false)
const detailRecord = ref<FollowupRecord | null>(null)
const evaluating = ref<string | null>(null)

const filters = ref({ followup_type: '', date_from: '', date_to: '' })

const { currentPage, pageSize, loading, seqNumber, handleSearch } =
  usePageContext(async () => { await loadRecords() })

const filteredRecords = computed(() => {
  let list = allRecords.value
  if (filters.value.followup_type) {
    list = list.filter(r => r.followup_type === filters.value.followup_type)
  }
  return list
})

const pagedRecords = computed(() => {
  total.value = filteredRecords.value.length
  const start = (currentPage.value - 1) * pageSize.value
  return filteredRecords.value.slice(start, start + pageSize.value)
})

function typeBadgeClass(type: string): string {
  const map: Record<string, string> = {
    phone: 'bg-info-100 text-info-700',
    email: 'bg-primary-100 text-primary-700',
    visit: 'bg-success-100 text-success-700',
    wechat: 'bg-success-100 text-success-700',
    ai_call: 'bg-warning-100 text-warning-700',
    other: 'bg-gray-100 text-muted',
  }
  return map[type] || map.other
}

function outcomeBadgeClass(outcome: string): string {
  const map: Record<string, string> = {
    positive: 'bg-success-100 text-success-700',
    neutral: 'bg-gray-100 text-muted',
    negative: 'bg-danger-100 text-danger-700',
    no_response: 'bg-warning-100 text-warning-700',
  }
  return map[outcome] || map.neutral
}

function scoreBadgeClass(score: number): string {
  if (score >= 8) return 'bg-success-100 text-success-700'
  if (score >= 5) return 'bg-warning-100 text-warning-700'
  return 'bg-danger-100 text-danger-700'
}

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

async function loadRecords() {
  try {
    const res = await followupAPI.listRecords({
      date_from: filters.value.date_from || undefined,
      date_to: filters.value.date_to || undefined,
      limit: 200,
    })
    allRecords.value = res.items || []
  } catch (e) {
    console.error('加载跟进记录失败:', e)
  }
}

function resetFilters() {
  filters.value = { followup_type: '', date_from: '', date_to: '' }
  handleSearch('')
}

function openDetail(record: any) {
  detailRecord.value = record
  showDetailModal.value = true
}

async function handleEvaluate(recordId: string) {
  evaluating.value = recordId
  try {
    const res = await followupAPI.evaluateRecord(recordId)
    const idx = allRecords.value.findIndex(r => r.record_id === recordId)
    if (idx >= 0) {
      allRecords.value[idx].quality_score = res.quality_score
    }
  } catch (e) {
    console.error('评估失败:', e)
  } finally {
    evaluating.value = null
  }
}

onMounted(() => { loadRecords() })
</script>

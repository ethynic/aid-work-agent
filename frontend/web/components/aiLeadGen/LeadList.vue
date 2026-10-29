<template>
  <div class="page-container">
    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.campaign_id" size="sm" class="w-44" @update:model-value="handleSearch()">
        <option value="">全部活动</option>
        <option v-for="opt in campaignOptions" :key="opt.id" :value="opt.id">{{ opt.campaign_name }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.status" size="sm" class="w-32" @update:model-value="handleSearch()">
        <option value="">全部状态</option>
        <option v-for="(label, key) in leadStatusMap" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="keyword" placeholder="搜索公司、国家、行业、网站..." size="sm" class="w-64" @keyup.enter="handleSearch(keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div ref="scrollContainer" class="flex-1 overflow-y-auto table-scroll-wrapper" @scroll.passive="handleScroll">
      <BaseTable :columns="columns" :data="items" row-key="id" table-fixed>
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #company_name="{ row }">
          <div class="min-w-0">
            <div class="font-medium text-default truncate" :title="row.company_name">{{ row.company_name || '-' }}</div>
            <div v-if="row.website" class="text-xs text-muted truncate" :title="row.website">{{ row.website }}</div>
          </div>
        </template>
        <template #country_city="{ row }">
          <span :title="`${row.country || ''}${row.city ? ' / ' + row.city : ''}`">
            {{ [row.country, row.city].filter(Boolean).join(' / ') || '-' }}
          </span>
        </template>
        <template #industry="{ row }">{{ row.industry || '-' }}</template>
        <template #source="{ row }">
          <span class="text-xs truncate block" :title="row.source">{{ row.source || '-' }}</span>
        </template>
        <template #score="{ row }">
          <span class="tabular-nums" :class="scoreClass(row.score)">{{ row.score ?? '-' }}</span>
        </template>
        <template #status="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="leadStatusBadge(row.status).class">
            {{ leadStatusBadge(row.status).label }}
          </span>
          <div v-if="row.status === 'rejected' && row.reject_reason" class="text-xs text-muted truncate mt-0.5" :title="row.reject_reason">{{ row.reject_reason }}</div>
        </template>
        <template #created_at="{ row }">{{ formatDate(row.created_at) }}</template>
        <template #actions="{ row }">
          <div class="flex items-center justify-end gap-1">
            <BaseButton intent="ghost" size="sm" @click="openDetail(row)">详情</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            加载中...
          </div>
          <span v-else>暂无线索公司</span>
        </template>
      </BaseTable>
    </div>

    <!-- 底部加载提示（仅手机端） -->
    <div v-if="isMobile" class="py-3 text-center text-xs text-gray-400">
      <span v-if="isLoadingMore">加载中...</span>
      <span v-else-if="!hasMore && items.length > 0">没有更多了</span>
    </div>

    <!-- Pagination -->
    <BasePagination
      v-if="!isMobile"
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      @change="refresh"
    />

    <!-- Detail Side Panel -->
    <Teleport to="body">
      <div v-if="selected" class="fixed inset-0 z-50 flex justify-end">
        <div class="absolute inset-0 bg-black/30 backdrop-blur-sm" @click="selected = null" />
        <div class="relative w-full max-w-lg bg-surface border-l border-default shadow-xl overflow-y-auto">
          <div class="sticky top-0 z-10 bg-surface border-b border-default px-6 py-4 flex items-center justify-between">
            <h2 class="text-lg font-semibold text-default">线索详情</h2>
            <button @click="selected = null" class="p-1.5 rounded-md text-muted hover:text-default hover:bg-surface-hover transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-6">
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">基本信息</h3>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div class="col-span-2"><span class="text-muted">公司名称：</span><span class="text-default font-medium">{{ selected.company_name || '-' }}</span></div>
                <div><span class="text-muted">国家/城市：</span><span class="text-default">{{ [selected.country, selected.city].filter(Boolean).join(' / ') || '-' }}</span></div>
                <div><span class="text-muted">行业：</span><span class="text-default">{{ selected.industry || '-' }}</span></div>
                <div><span class="text-muted">员工规模：</span><span class="text-default">{{ selected.employees || '-' }}</span></div>
                <div><span class="text-muted">成立年份：</span><span class="text-default">{{ selected.founded || '-' }}</span></div>
                <div><span class="text-muted">评分：</span><span class="text-default tabular-nums">{{ selected.score ?? '-' }}</span></div>
                <div>
                  <span class="text-muted">状态：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="leadStatusBadge(selected.status).class">{{ leadStatusBadge(selected.status).label }}</span>
                </div>
                <div v-if="selected.reject_reason" class="col-span-2"><span class="text-muted">排除原因：</span><span class="text-default">{{ selected.reject_reason }}</span></div>
                <div class="col-span-2"><span class="text-muted">所属活动：</span><span class="text-default">{{ selected.campaign_name || '-' }}</span></div>
              </div>
            </div>

            <div v-if="selected.website || selected.source_url">
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">链接</h3>
              <div class="space-y-2 text-sm">
                <div v-if="selected.website" class="break-all">
                  <span class="text-muted">官网：</span>
                  <a :href="selected.website" target="_blank" rel="noopener" class="text-primary-600 hover:underline break-all">{{ selected.website }}</a>
                </div>
                <div v-if="selected.source_url" class="break-all">
                  <span class="text-muted">来源：</span>
                  <a :href="selected.source_url" target="_blank" rel="noopener" class="text-primary-600 hover:underline break-all">{{ selected.source_url }}</a>
                </div>
              </div>
            </div>

            <div v-if="selected.notes">
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">备注</h3>
              <div class="rounded-lg border border-default bg-surface/50 p-4">
                <p class="text-sm text-default whitespace-pre-wrap break-words">{{ selected.notes }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { aiLeadGenAPI, leadStatusMap, leadStatusBadge, type Lead, type CampaignOption } from '@/api/aiLeadGen'
import { useLeadGenList } from './useLeadGenList'
import { formatShortDateTime as formatDate } from '@/utils/date'

const route = useRoute()

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'company_name', label: '公司名称' },
  { key: 'country_city', label: '国家/城市', width: '150px' },
  { key: 'industry', label: '行业', width: '110px' },
  { key: 'source', label: '来源', width: '110px' },
  { key: 'score', label: '评分', width: '70px' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'created_at', label: '创建时间', width: '150px' },
  { key: 'actions', label: '操作', width: '90px' },
]

const filters = ref<{ campaign_id: string; status: string }>({ campaign_id: '', status: '' })
const keyword = ref('')
const selected = ref<Lead | null>(null)
const campaignOptions = ref<CampaignOption[]>([])

const list = useLeadGenList<Lead>(
  (page, pageSize) => aiLeadGenAPI.listLeads({
    ...filters.value,
    keyword: keyword.value,
    page,
    page_size: pageSize,
  }),
  item => item.id,
)
const {
  isMobile, items, total, currentPage, pageSize, loading, isLoadingMore, hasMore,
  scrollContainer, seqNumber, handleSearch, refresh, handleScroll,
} = list

function scoreClass(score?: number) {
  if (score === null || score === undefined) return 'text-muted'
  if (score >= 70) return 'text-success-600 font-medium'
  if (score >= 40) return 'text-warning-600 font-medium'
  return 'text-muted'
}

function resetFilters() {
  filters.value = { campaign_id: '', status: '' }
  keyword.value = ''
  handleSearch('')
}

function openDetail(row: any) {
  selected.value = row as Lead
}

onMounted(async () => {
  const cid = route.query.campaign_id
  if (cid) filters.value.campaign_id = String(cid)
  try {
    const res = await aiLeadGenAPI.getCampaignOptions()
    campaignOptions.value = res.items
  } catch (e: any) {
    console.error('加载活动选项失败:', e)
  }
  refresh()
})
</script>

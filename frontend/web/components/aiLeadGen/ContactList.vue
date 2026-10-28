<template>
  <div class="page-container">
    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.campaign_id" size="sm" class="w-44" @update:model-value="handleSearch()">
        <option value="">全部活动</option>
        <option v-for="opt in campaignOptions" :key="opt.id" :value="opt.id">{{ opt.campaign_name }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.confidence" size="sm" class="w-28" @update:model-value="handleSearch()">
        <option value="">全部置信度</option>
        <option v-for="(label, key) in confidenceMap" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.email_source" size="sm" class="w-32" @update:model-value="handleSearch()">
        <option value="">全部来源</option>
        <option v-for="(label, key) in emailSourceMap" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="keyword" placeholder="搜索姓名、职务、邮箱、职能..." size="sm" class="w-56" @keyup.enter="handleSearch(keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div ref="scrollContainer" class="flex-1 overflow-y-auto table-scroll-wrapper" @scroll.passive="handleScroll">
      <BaseTable :columns="columns" :data="items" row-key="id" table-fixed>
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #name="{ row }">
          <span class="font-medium text-default truncate block" :title="row.name">{{ row.name || '-' }}</span>
        </template>
        <template #title="{ row }">
          <span class="truncate block" :title="row.title">{{ row.title || '-' }}</span>
        </template>
        <template #email="{ row }">
          <span class="text-xs break-all whitespace-normal" :title="row.email">{{ row.email || '-' }}</span>
        </template>
        <template #email_function="{ row }">{{ row.email_function || '-' }}</template>
        <template #email_source="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="emailSourceBadge(row.email_source).class">
            {{ emailSourceBadge(row.email_source).label }}
          </span>
        </template>
        <template #confidence="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="confidenceBadge(row.confidence).class">
            {{ confidenceBadge(row.confidence).label }}
          </span>
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
          <span v-else>暂无联系人</span>
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
            <h2 class="text-lg font-semibold text-default">联系人详情</h2>
            <button @click="selected = null" class="p-1.5 rounded-md text-muted hover:text-default hover:bg-surface-hover transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-6">
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">基本信息</h3>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div><span class="text-muted">姓名：</span><span class="text-default font-medium">{{ selected.name || '-' }}</span></div>
                <div><span class="text-muted">职务：</span><span class="text-default">{{ selected.title || '-' }}</span></div>
                <div class="col-span-2"><span class="text-muted">邮箱：</span><span class="text-default break-all">{{ selected.email || '-' }}</span></div>
                <div><span class="text-muted">职能：</span><span class="text-default">{{ selected.email_function || '-' }}</span></div>
                <div><span class="text-muted">语言：</span><span class="text-default">{{ selected.language || '-' }}</span></div>
                <div><span class="text-muted">电话：</span><span class="text-default">{{ selected.phone || '-' }}</span></div>
                <div><span class="text-muted">所属公司：</span><span class="text-default">{{ selected.company_name || '-' }}</span></div>
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">邮箱质量</h3>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span class="text-muted">来源：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="emailSourceBadge(selected.email_source).class">{{ emailSourceBadge(selected.email_source).label }}</span>
                </div>
                <div>
                  <span class="text-muted">置信度：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="confidenceBadge(selected.confidence).class">{{ confidenceBadge(selected.confidence).label }}</span>
                </div>
                <div><span class="text-muted">类型：</span><span class="text-default">{{ emailTypeMap[selected.email_type || ''] || selected.email_type || '-' }}</span></div>
                <div><span class="text-muted">相关性：</span><span class="text-default">{{ relevanceMap[selected.email_relevance || ''] || selected.email_relevance || '-' }}</span></div>
              </div>
            </div>

            <div v-if="selected.linkedin">
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">链接</h3>
              <a :href="selected.linkedin" target="_blank" rel="noopener" class="text-sm text-primary-600 hover:underline break-all">{{ selected.linkedin }}</a>
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
import {
  aiLeadGenAPI, confidenceMap, emailSourceMap, emailTypeMap, relevanceMap,
  emailSourceBadge, confidenceBadge, type Contact, type CampaignOption,
} from '@/api/aiLeadGen'
import { useLeadGenList } from './useLeadGenList'
import { formatShortDateTime as formatDate } from '@/utils/date'

const route = useRoute()

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'name', label: '姓名', width: '100px' },
  { key: 'title', label: '职务', width: '140px' },
  { key: 'email', label: '邮箱', width: '200px' },
  { key: 'email_function', label: '职能', width: '120px' },
  { key: 'email_source', label: '来源', width: '110px' },
  { key: 'confidence', label: '置信度', width: '80px' },
  { key: 'created_at', label: '创建时间', width: '150px' },
  { key: 'actions', label: '操作', width: '90px' },
]

const filters = ref<{ campaign_id: string; confidence: string; email_source: string }>({ campaign_id: '', confidence: '', email_source: '' })
const keyword = ref('')
const selected = ref<Contact | null>(null)
const campaignOptions = ref<CampaignOption[]>([])

const list = useLeadGenList<Contact>(
  (page, pageSize) => aiLeadGenAPI.listContacts({
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

function resetFilters() {
  filters.value = { campaign_id: '', confidence: '', email_source: '' }
  keyword.value = ''
  handleSearch('')
}

function openDetail(row: any) {
  selected.value = row as Contact
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

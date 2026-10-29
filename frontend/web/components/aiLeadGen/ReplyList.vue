<template>
  <div class="page-container">
    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.campaign_id" size="sm" class="w-44" @update:model-value="handleSearch()">
        <option value="">全部活动</option>
        <option v-for="opt in campaignOptions" :key="opt.id" :value="opt.id">{{ opt.campaign_name }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.intent" size="sm" class="w-32" @update:model-value="handleSearch()">
        <option value="">全部意图</option>
        <option v-for="(label, key) in intentMap" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="keyword" placeholder="搜索发件人、主题、摘要..." size="sm" class="w-56" @keyup.enter="handleSearch(keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div ref="scrollContainer" class="flex-1 overflow-y-auto table-scroll-wrapper" @scroll.passive="handleScroll">
      <BaseTable :columns="columns" :data="items" row-key="id" table-fixed>
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #from_addr="{ row }">
          <span class="text-xs break-all whitespace-normal" :title="row.from_addr">{{ row.from_addr || '-' }}</span>
        </template>
        <template #subject="{ row }">
          <span class="truncate block" :title="row.subject">{{ row.subject || '-' }}</span>
        </template>
        <template #intent="{ row }">
          <span v-if="row.intent" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="intentBadge(row.intent).class">
            {{ intentBadge(row.intent).label }}
          </span>
          <span v-else class="text-muted">-</span>
        </template>
        <template #intent_summary="{ row }">
          <span class="truncate block" :title="row.intent_summary">{{ row.intent_summary || '-' }}</span>
        </template>
        <template #notified="{ row }">{{ row.notified ? '是' : '否' }}</template>
        <template #received_at="{ row }">{{ row.received_at ? formatDate(row.received_at) : '-' }}</template>
        <template #actions="{ row }">
          <div class="flex items-center justify-end gap-1">
            <BaseButton intent="ghost" size="sm" @click="openBody(row)">原文</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            加载中...
          </div>
          <span v-else>暂无回复记录</span>
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

    <MessageBodyModal v-model="showBodyModal" kind="reply" :record-id="bodyRecordId" />
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
import MessageBodyModal from './MessageBodyModal.vue'
import { aiLeadGenAPI, intentMap, intentBadge, type ReplyRecord, type CampaignOption } from '@/api/aiLeadGen'
import { useLeadGenList } from './useLeadGenList'
import { formatShortDateTime as formatDate } from '@/utils/date'

const route = useRoute()

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'from_addr', label: '发件人', width: '200px' },
  { key: 'subject', label: '主题' },
  { key: 'intent', label: '意向', width: '100px' },
  { key: 'intent_summary', label: '摘要', width: '220px' },
  { key: 'notified', label: '已通知', width: '80px' },
  { key: 'received_at', label: '接收时间', width: '150px' },
  { key: 'actions', label: '操作', width: '90px' },
]

const filters = ref<{ campaign_id: string; intent: string }>({ campaign_id: '', intent: '' })
const keyword = ref('')
const campaignOptions = ref<CampaignOption[]>([])
const showBodyModal = ref(false)
const bodyRecordId = ref<number | null>(null)

const list = useLeadGenList<ReplyRecord>(
  (page, pageSize) => aiLeadGenAPI.listReplies({
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
  filters.value = { campaign_id: '', intent: '' }
  keyword.value = ''
  handleSearch('')
}

function openBody(row: any) {
  bodyRecordId.value = (row as ReplyRecord).id
  showBodyModal.value = true
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

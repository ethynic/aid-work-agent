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
        <option v-for="(label, key) in emailStatusMap" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="keyword" placeholder="搜索主题、Message-ID..." size="sm" class="w-56" @keyup.enter="handleSearch(keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div ref="scrollContainer" class="flex-1 overflow-y-auto table-scroll-wrapper" @scroll.passive="handleScroll">
      <BaseTable :columns="columns" :data="items" row-key="id" table-fixed>
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #recipient="{ row }">
          <div class="min-w-0">
            <div class="text-default truncate" :title="row.contact_name">{{ row.contact_name || '-' }}</div>
            <div class="text-xs text-muted truncate" :title="row.contact_email">{{ row.contact_email || '' }}</div>
          </div>
        </template>
        <template #ab_group="{ row }">
          <span v-if="row.ab_group" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-info-100 text-info-700">
            {{ row.ab_group }}
          </span>
          <span v-else>-</span>
        </template>
        <template #subject="{ row }">
          <span class="truncate block" :title="row.ab_group === 'B' ? row.subject_b : row.subject_a">
            {{ (row.ab_group === 'B' ? row.subject_b : row.subject_a) || '-' }}
          </span>
        </template>
        <template #status="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="emailStatusBadge(row.status).class">
            {{ emailStatusBadge(row.status).label }}
          </span>
        </template>
        <template #sent_at="{ row }">{{ row.sent_at ? formatDate(row.sent_at) : '-' }}</template>
        <template #error="{ row }">
          <span class="text-xs text-danger-500 truncate block" :title="row.error">{{ row.error || '-' }}</span>
        </template>
        <template #actions="{ row }">
          <div class="flex items-center justify-end gap-1">
            <BaseButton intent="ghost" size="sm" @click="openBody(row)">正文</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            加载中...
          </div>
          <span v-else>暂无邮件记录</span>
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

    <MessageBodyModal v-model="showBodyModal" kind="email" :record-id="bodyRecordId" />
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
import { aiLeadGenAPI, emailStatusMap, emailStatusBadge, type EmailRecord, type CampaignOption } from '@/api/aiLeadGen'
import { useLeadGenList } from './useLeadGenList'
import { formatShortDateTime as formatDate } from '@/utils/date'

const route = useRoute()

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'recipient', label: '收件人', width: '180px' },
  { key: 'ab_group', label: 'A/B', width: '60px' },
  { key: 'subject', label: '主题' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'sent_at', label: '发送时间', width: '150px' },
  { key: 'error', label: '错误', width: '140px' },
  { key: 'actions', label: '操作', width: '90px' },
]

const filters = ref<{ campaign_id: string; status: string }>({ campaign_id: '', status: '' })
const keyword = ref('')
const campaignOptions = ref<CampaignOption[]>([])
const showBodyModal = ref(false)
const bodyRecordId = ref<number | null>(null)

const list = useLeadGenList<EmailRecord>(
  (page, pageSize) => aiLeadGenAPI.listEmails({
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
  filters.value = { campaign_id: '', status: '' }
  keyword.value = ''
  handleSearch('')
}

function openBody(row: any) {
  bodyRecordId.value = (row as EmailRecord).id
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

<template>
  <div class="page-container">
    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.status" size="sm" class="w-32" @update:model-value="handleSearch()">
        <option value="">全部状态</option>
        <option v-for="(label, key) in campaignStatusMap" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.send_mode" size="sm" class="w-32" @update:model-value="handleSearch()">
        <option value="">全部模式</option>
        <option v-for="(label, key) in sendModeMap" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseInput v-model="keyword" placeholder="搜索活动名称、简报、地区..." size="sm" class="w-64" @keyup.enter="handleSearch(keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div ref="scrollContainer" class="flex-1 overflow-y-auto table-scroll-wrapper" @scroll.passive="handleScroll">
      <BaseTable :columns="columns" :data="items" row-key="id" table-fixed>
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #campaign_name="{ row }">
          <span class="font-medium text-default truncate block" :title="row.campaign_name">{{ row.campaign_name || '-' }}</span>
        </template>
        <template #target_region="{ row }">{{ row.target_region || '-' }}</template>
        <template #send_mode="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="sendModeBadge(row.send_mode).class">
            {{ sendModeBadge(row.send_mode).label }}
          </span>
        </template>
        <template #status="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="campaignStatusBadge(row.status).class">
            {{ campaignStatusBadge(row.status).label }}
          </span>
        </template>
        <template #daily_limit="{ row }">
          <span class="tabular-nums">{{ row.daily_limit ?? '-' }}</span>
        </template>
        <template #reply_mailbox="{ row }">
          <span class="text-xs break-all whitespace-normal" :title="row.reply_mailbox">{{ row.reply_mailbox || '-' }}</span>
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
          <span v-else>暂无获客活动</span>
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
            <h2 class="text-lg font-semibold text-default">活动详情</h2>
            <button @click="selected = null" class="p-1.5 rounded-md text-muted hover:text-default hover:bg-surface-hover transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-6">
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">基本信息</h3>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div class="col-span-2"><span class="text-muted">活动名称：</span><span class="text-default font-medium">{{ selected.campaign_name || '-' }}</span></div>
                <div><span class="text-muted">目标地区：</span><span class="text-default">{{ selected.target_region || '-' }}</span></div>
                <div><span class="text-muted">日发送上限：</span><span class="text-default tabular-nums">{{ selected.daily_limit ?? '-' }}</span></div>
                <div>
                  <span class="text-muted">发送模式：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="sendModeBadge(selected.send_mode).class">{{ sendModeBadge(selected.send_mode).label }}</span>
                </div>
                <div>
                  <span class="text-muted">状态：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="campaignStatusBadge(selected.status).class">{{ campaignStatusBadge(selected.status).label }}</span>
                </div>
                <div class="col-span-2"><span class="text-muted">回信邮箱：</span><span class="text-default break-all">{{ selected.reply_mailbox || '-' }}</span></div>
                <div><span class="text-muted">邮箱层级：</span><span class="text-default">{{ selected.send_mailbox_level === 'tenant' ? '租户邮箱' : selected.send_mailbox_level === 'user' ? '个人邮箱' : (selected.send_mailbox_level || '-') }}</span></div>
                <div><span class="text-muted">创建时间：</span><span class="text-default">{{ formatDateTime(selected.created_at) }}</span></div>
                <div v-if="selected.scheduler_task_id" class="col-span-2"><span class="text-muted">调度任务：</span><span class="text-default break-all text-xs">{{ selected.scheduler_task_id }}</span></div>
              </div>
            </div>

            <div v-if="selected.brief">
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">需求简报</h3>
              <div class="rounded-lg border border-default bg-surface/50 p-4">
                <p class="text-sm text-default whitespace-pre-wrap break-words">{{ selected.brief }}</p>
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">关联数据</h3>
              <div class="flex flex-wrap gap-2">
                <BaseButton intent="secondary" size="sm" @click="goRelated('leads')">查看线索</BaseButton>
                <BaseButton intent="secondary" size="sm" @click="goRelated('contacts')">查看联系人</BaseButton>
                <BaseButton intent="secondary" size="sm" @click="goRelated('emails')">查看邮件</BaseButton>
                <BaseButton intent="secondary" size="sm" @click="goRelated('replies')">查看回复</BaseButton>
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
import { useRoute, useRouter } from 'vue-router'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { aiLeadGenAPI, campaignStatusMap, sendModeMap, campaignStatusBadge, sendModeBadge, type Campaign } from '@/api/aiLeadGen'
import { useLeadGenList } from './useLeadGenList'
import { formatShortDateTime as formatDate, formatDateTime } from '@/utils/date'

const route = useRoute()
const router = useRouter()

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'campaign_name', label: '活动名称' },
  { key: 'target_region', label: '目标地区', width: '120px' },
  { key: 'send_mode', label: '发送模式', width: '90px' },
  { key: 'status', label: '状态', width: '110px' },
  { key: 'daily_limit', label: '日限额', width: '70px' },
  { key: 'reply_mailbox', label: '回信邮箱', width: '180px' },
  { key: 'created_at', label: '创建时间', width: '150px' },
  { key: 'actions', label: '操作', width: '90px' },
]

const filters = ref({ status: '', send_mode: '' })
const keyword = ref('')
const selected = ref<Campaign | null>(null)

const list = useLeadGenList<Campaign>(
  (page, pageSize) => aiLeadGenAPI.listCampaigns({
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
  filters.value = { status: '', send_mode: '' }
  keyword.value = ''
  handleSearch('')
}

function openDetail(row: any) {
  selected.value = row as Campaign
}

function goRelated(path: string) {
  if (!selected.value) return
  const tenantId = route.params.tenant_id
  router.push(`/t/${tenantId}/ai-lead-gen/${path}?campaign_id=${selected.value.id}`)
  selected.value = null
}

onMounted(() => {
  refresh()
})
</script>

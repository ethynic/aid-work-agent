<template>
  <!-- 视频库页面（Phase 6.3.1）
       列表页：显示视频名称、文件大小、创建时间、消耗积分；点击查看提示词溯源 -->
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="视频库"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <main class="flex-1 overflow-hidden p-4">
      <div class="page-container h-full">
        <div class="page-content">
          <div class="page-toolbar">
            <div class="page-toolbar-left">
              <h2 class="text-sm text-muted">已留用的视频成果</h2>
            </div>
            <div class="page-toolbar-right">
              <BaseButton size="sm" intent="ghost" @click="refresh">刷新</BaseButton>
            </div>
          </div>

          <div class="table-scroll-wrapper flex-1">
            <BaseTable :columns="columns" :data="items" row-key="id">
              <template #seq="{ index }">
                {{ seqNumber(index) }}
              </template>
              <template #file_name="{ row }">
                <button
                  class="text-primary-600 hover:underline truncate"
                  :title="row.file_name || row.summary"
                  @click="handleViewDetail(row)"
                >
                  {{ row.file_name || row.summary || `视频 #${row.id}` }}
                </button>
              </template>
              <template #size_bytes="{ row }">
                {{ formatFileSize(row.metadata?.size_bytes) }}
              </template>
              <template #credit_cost="{ row }">
                {{ row.metadata?.credit_cost ?? '-' }}
              </template>
              <template #created_at="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
              <template #actions="{ row }">
                <BaseButton size="sm" intent="ghost" @click="handleDownload(row)">下载</BaseButton>
                <BaseButton size="sm" intent="ghost" @click="handleViewDetail(row)">详情</BaseButton>
                <BaseButton size="sm" intent="danger-ghost" @click="handleDelete(row)">删除</BaseButton>
              </template>
              <template #empty>暂无视频</template>
            </BaseTable>
          </div>

          <BasePagination
            v-if="total > pageSize"
            :total="total"
            v-model:current-page="currentPage"
            :page-size="pageSize"
          />
        </div>
      </div>
    </main>

    <!-- 视频详情弹框（含提示词溯源） -->
    <BaseModal v-model="showDetailModal" title="视频详情" size="lg">
      <div v-if="detailLoading" class="text-center py-8 text-muted">加载中...</div>
      <div v-else-if="currentDetail" class="space-y-4">
        <!-- 视频预览 -->
        <div class="rounded-lg overflow-hidden bg-black aspect-video">
          <video
            v-if="currentDetail.file_id"
            :src="`/api/files/${currentDetail.file_id}/download`"
            controls
            preload="metadata"
            class="w-full h-full object-contain"
          />
        </div>

        <!-- 基本信息 -->
        <div class="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span class="text-muted">文件名：</span>
            <span class="text-default">{{ currentDetail.file_name || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">摘要：</span>
            <span class="text-default">{{ currentDetail.summary || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">消耗积分：</span>
            <span class="text-default">{{ currentDetail.metadata?.credit_cost ?? '-' }}</span>
          </div>
          <div>
            <span class="text-muted">创建时间：</span>
            <span class="text-default">{{ formatTime(currentDetail.created_at) }}</span>
          </div>
        </div>

        <!-- 提示词溯源 -->
        <div v-if="currentDetail.source_prompt" class="mt-4 p-3 rounded-lg bg-gray-50 border border-default">
          <div class="text-sm font-medium text-default mb-2">提示词溯源</div>
          <div class="space-y-1 text-xs">
            <div>
              <span class="text-muted">类别：</span>
              <BaseBadge :intent="promptBadgeIntent(currentDetail.source_prompt.category)">
                {{ promptCategoryLabel(currentDetail.source_prompt.category) }}
              </BaseBadge>
            </div>
            <div>
              <span class="text-muted">业务层：</span>
              <span class="text-default">{{ currentDetail.source_prompt.business_prompt || '-' }}</span>
            </div>
            <div>
              <span class="text-muted">工艺层：</span>
              <span class="text-default whitespace-pre-wrap">{{ currentDetail.source_prompt.craft_prompt || '-' }}</span>
            </div>
          </div>
        </div>
        <div v-else class="mt-4 p-3 rounded-lg bg-gray-50 border border-default text-xs text-muted">
          该视频未关联提示词记录
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { usePageContext } from '@/composables/usePageContext'
import {
  listVideos,
  getVideo,
  deleteVideo,
  type VideoItem,
  type PromptCategory,
} from '@/api/videoAgent'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const tenantId = computed(() => route.params.tenant_id as string | undefined)
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : null)

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  router.push(tenantId.value ? `/t/${tenantId.value}/login` : '/')
}

// 列表数据
const items = ref<VideoItem[]>([])
const total = ref(0)

const { currentPage, pageSize, seqNumber, refresh } = usePageContext(async () => {
  const res = await listVideos({
    page: currentPage.value,
    page_size: pageSize.value,
  })
  if (res.success && res.data) {
    items.value = res.data.items
    total.value = res.data.total
  } else {
    toast.error(res.error || '加载视频列表失败')
  }
})

onMounted(() => {
  refresh()
})

const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'file_name', label: '视频名称' },
  { key: 'size_bytes', label: '大小', width: '100px' },
  { key: 'credit_cost', label: '消耗积分', width: '100px' },
  { key: 'created_at', label: '创建时间', width: '160px' },
  { key: 'actions', label: '操作', width: '220px' },
]

function formatFileSize(bytes?: number): string {
  if (!bytes) return '-'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(t?: string): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function promptCategoryLabel(c?: PromptCategory): string {
  if (!c) return '-'
  return { kept: '留用', blacklist: '黑名单', template: '模版' }[c] || c
}

function promptBadgeIntent(c?: PromptCategory): 'success' | 'danger' | 'info' {
  if (c === 'kept') return 'success'
  if (c === 'blacklist') return 'danger'
  return 'info'
}

function handleDownload(row: any) {
  if (row.file_id) {
    window.open(`/api/files/${row.file_id}/download`, '_blank')
  }
}

// 详情弹框
const showDetailModal = ref(false)
const detailLoading = ref(false)
const currentDetail = ref<VideoItem | null>(null)

async function handleViewDetail(row: any) {
  showDetailModal.value = true
  detailLoading.value = true
  currentDetail.value = null
  try {
    const res = await getVideo(row.id)
    if (res.success && res.data) {
      currentDetail.value = res.data
    } else {
      toast.error(res.error || '加载详情失败')
    }
  } finally {
    detailLoading.value = false
  }
}

async function handleDelete(row: any) {
  if (!confirm(`确定删除视频「${row.file_name || row.summary || row.id}」？`)) return
  const res = await deleteVideo(row.id)
  if (res.success) {
    toast.success('删除成功')
    refresh()
  } else {
    toast.error(res.error || '删除失败')
  }
}
</script>

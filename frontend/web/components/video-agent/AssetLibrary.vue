<template>
  <!-- 素材库页面（Phase 6.2.1）
       列表页：分页 + 按 scene/source 筛选 + 手动上传 -->
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="素材库"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <main class="flex-1 overflow-hidden p-4">
      <div class="page-container h-full">
        <div class="page-content">
          <!-- 搜索区 + 操作按钮 -->
          <div class="page-toolbar">
            <div class="page-toolbar-left">
              <BaseSelect
                v-model="filterScene"
                size="sm"
                class="w-40"
                @change="handleSearch"
              >
                <option value="">全部场景</option>
                <option value="product">产品图</option>
                <option value="model">模特图</option>
                <option value="bgm">背景音乐</option>
              </BaseSelect>
              <BaseSelect
                v-model="filterSource"
                size="sm"
                class="w-40"
                @change="handleSearch"
              >
                <option value="">全部来源</option>
                <option value="video_chat">对话生成</option>
                <option value="user_upload">用户上传</option>
                <option value="other_agent_manual">其他智能体</option>
              </BaseSelect>
            </div>
            <div class="page-toolbar-right">
              <BaseButton size="sm" @click="showUploadModal = true">上传素材</BaseButton>
            </div>
          </div>

          <!-- 表格 -->
          <div class="table-scroll-wrapper flex-1">
            <BaseTable :columns="columns" :data="items" row-key="id">
              <template #seq="{ index }">
                {{ seqNumber(index) }}
              </template>
              <template #size_bytes="{ row }">
                {{ formatFileSize(row.size_bytes) }}
              </template>
              <template #scene="{ row }">
                <BaseBadge v-if="row.scene" intent="info">{{ sceneLabel(row.scene) }}</BaseBadge>
                <span v-else class="text-muted">-</span>
              </template>
              <template #source="{ row }">
                <BaseBadge :intent="sourceBadgeIntent(row.source)">{{ sourceLabel(row.source) }}</BaseBadge>
              </template>
              <template #created_at="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
              <template #actions="{ row }">
                <BaseButton size="sm" intent="ghost" @click="handleDownload(row)">下载</BaseButton>
                <BaseButton size="sm" intent="danger-ghost" @click="handleDelete(row)">删除</BaseButton>
              </template>
              <template #empty>暂无素材</template>
            </BaseTable>
          </div>

          <!-- 分页 -->
          <BasePagination
            v-if="total > pageSize"
            :total="total"
            v-model:current-page="currentPage"
            :page-size="pageSize"
          />
        </div>
      </div>
    </main>

    <!-- 上传素材弹框 -->
    <BaseModal v-model="showUploadModal" title="上传素材" size="md">
      <div class="space-y-3">
        <div>
          <label class="form-label">文件 <span class="form-required">*</span></label>
          <input
            ref="uploadInputRef"
            type="file"
            class="block w-full text-sm text-muted"
            accept="image/*,video/*"
            @change="handleFilePick"
          />
          <p class="text-xs text-muted mt-1">支持图片和视频文件</p>
        </div>
        <div>
          <label class="form-label">显示名 <span class="form-required">*</span></label>
          <BaseInput v-model="uploadForm.display_name" size="md" />
        </div>
        <div>
          <label class="form-label">场景</label>
          <BaseSelect v-model="uploadForm.scene" size="md">
            <option value="">无</option>
            <option value="product">产品图</option>
            <option value="model">模特图</option>
            <option value="bgm">背景音乐</option>
          </BaseSelect>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showUploadModal = false">取消</BaseButton>
        <BaseButton :disabled="!canSubmitUpload" @click="handleSubmitUpload">上传</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { usePageContext } from '@/composables/usePageContext'
import { listAssets, deleteAsset, manualUploadAsset, type AssetItem, type ManualUploadAssetRequest } from '@/api/videoAgent'
import { uploadFile } from '@/api/agent'
import { getAuthHeader } from '@/api/auth'

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
const items = ref<AssetItem[]>([])
const total = ref(0)
const filterScene = ref('')
const filterSource = ref('')

const { currentPage, pageSize, seqNumber, handleSearch, refresh } = usePageContext(async () => {
  const res = await listAssets({
    page: currentPage.value,
    page_size: pageSize.value,
    scene: filterScene.value || undefined,
    source: filterSource.value || undefined,
  })
  if (res.success && res.data) {
    items.value = res.data.items
    total.value = res.data.total
  } else {
    toast.error(res.error || '加载素材列表失败')
  }
})

onMounted(() => {
  refresh()
})

// 表格列定义
const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'display_name', label: '素材名称' },
  { key: 'mime_type', label: '类型', width: '140px' },
  { key: 'size_bytes', label: '大小', width: '100px' },
  { key: 'scene', label: '场景', width: '100px' },
  { key: 'source', label: '来源', width: '120px' },
  { key: 'created_at', label: '创建时间', width: '160px' },
  { key: 'actions', label: '操作', width: '140px' },
]

function formatFileSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(t?: string): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function sceneLabel(scene?: string): string {
  const map: Record<string, string> = { product: '产品', model: '模特', bgm: '背景音乐' }
  return map[scene || ''] || scene || '-'
}

function sourceLabel(source?: string): string {
  const map: Record<string, string> = {
    video_chat: '对话生成',
    user_upload: '用户上传',
    other_agent_manual: '其他智能体',
  }
  return map[source || ''] || source || '-'
}

function sourceBadgeIntent(source?: string): 'info' | 'success' | 'neutral' {
  if (source === 'video_chat') return 'info'
  if (source === 'user_upload') return 'success'
  return 'neutral'
}

function handleDownload(row: any) {
  if (row.file_id) {
    window.open(`/api/files/${row.file_id}/download`, '_blank')
  }
}

async function handleDelete(row: any) {
  if (!confirm(`确定删除素材「${row.display_name}」？`)) return
  const res = await deleteAsset(row.id)
  if (res.success) {
    toast.success('删除成功')
    refresh()
  } else {
    toast.error(res.error || '删除失败')
  }
}

// 上传素材
const showUploadModal = ref(false)
const uploadInputRef = ref<HTMLInputElement | null>(null)
const uploadForm = ref<{ display_name: string; scene: string; file: File | null }>({
  display_name: '',
  scene: '',
  file: null,
})

const canSubmitUpload = computed(() => uploadForm.value.file && uploadForm.value.display_name.trim())

function handleFilePick(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    const file = input.files[0]
    uploadForm.value.file = file
    if (!uploadForm.value.display_name) {
      uploadForm.value.display_name = file.name
    }
  }
}

async function handleSubmitUpload() {
  if (!uploadForm.value.file) return
  try {
    // 1. 先调 /api/upload 上传文件拿 file_id
    const uploadRes = await uploadFile(uploadForm.value.file, getAuthHeader())
    if (!uploadRes.file_id) {
      toast.error('文件上传失败')
      return
    }
    // 2. 调 manual_upload_asset 登记到 asset_library
    const req: ManualUploadAssetRequest = {
      file_id: uploadRes.file_id,
      display_name: uploadForm.value.display_name.trim(),
      mime_type: uploadForm.value.file.type || 'application/octet-stream',
      size_bytes: uploadForm.value.file.size,
      scene: uploadForm.value.scene || undefined,
    }
    const res = await manualUploadAsset(req)
    if (res.success) {
      toast.success('上传成功')
      showUploadModal.value = false
      uploadForm.value = { display_name: '', scene: '', file: null }
      refresh()
    } else {
      toast.error(res.error || '上传失败')
    }
  } catch (e: any) {
    toast.error(e.message || '上传异常')
  }
}
</script>

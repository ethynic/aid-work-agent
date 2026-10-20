<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <main class="flex-1 flex overflow-hidden">
      <div class="flex-1 flex flex-col min-w-0">
        <AppHeader
          title="腾讯文档导入"
          :is-logged-in="effectiveIsLoggedIn"
          :user="effectiveUser"
          @toggle-sidebar="handleToggleSidebar"
          @logout="handleLogout"
        >
          <template #menu-items="{ closeMenu }">
            <button class="block w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="goKnowledge(); closeMenu()">返回知识库</button>
          </template>
        </AppHeader>

        <div class="flex-1 overflow-y-auto p-6">
          <!-- 授权卡片 -->
          <div class="bg-white rounded-xl border border-default p-5 mb-4 flex items-center justify-between">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-default">腾讯文档账号</span>
                <BaseBadge v-if="authStatus?.bound" :intent="authStatus.status === 'active' ? 'success' : 'warning'">
                  {{ authStatus.status === 'active' ? '已绑定' : '已失效' }}
                </BaseBadge>
                <BaseBadge v-else intent="neutral">未绑定</BaseBadge>
              </div>
              <p v-if="authStatus?.bound" class="text-xs text-muted mt-1 truncate">
                Client ID：{{ authStatus.client_id_masked }}　Open ID：{{ authStatus.open_id_masked }}
              </p>
              <p v-else class="text-xs text-muted mt-1">绑定开发期个人测试凭据（Client ID / Access Token / Open ID），仅导入「在线文档」类型</p>
            </div>
            <div class="flex gap-2 flex-shrink-0">
              <BaseButton v-if="authStatus?.bound" size="sm" intent="secondary" @click="openBindModal">更新凭据</BaseButton>
              <BaseButton v-if="authStatus?.bound" size="sm" intent="danger-ghost" @click="handleUnbind">解绑</BaseButton>
              <BaseButton v-else size="sm" @click="openBindModal">绑定腾讯文档账号</BaseButton>
            </div>
          </div>

          <!-- 操作行 -->
          <div class="page-toolbar mb-3">
            <div class="page-toolbar-left">
              <BaseButton :disabled="!authStatus?.bound" @click="openFileModal">从腾讯文档导入</BaseButton>
            </div>
          </div>

          <!-- 批次列表 -->
          <div class="bg-white rounded-xl border border-default p-4">
            <div v-if="isLoadingBatches" class="flex items-center justify-center py-12">
              <span class="text-sm text-muted">加载中...</span>
            </div>
            <div v-else-if="batches.length === 0" class="empty-state py-12">
              <p class="text-sm text-muted">暂无导入任务，点击「从腾讯文档导入」创建</p>
            </div>
            <template v-else>
              <div class="table-scroll-wrapper">
                <BaseTable :columns="batchColumns" :data="batches" row-key="batch_id" table-fixed>
                  <template #status="{ row }">
                    <BaseBadge :intent="batchStatusIntent(row.status)">{{ batchStatusLabel(row.status) }}</BaseBadge>
                  </template>
                  <template #progress="{ row }">
                    <span class="text-sm text-default">
                      成功 {{ row.success_count }} / 失败 {{ row.fail_count }} / 跳过 {{ row.skip_count }} / 共 {{ row.total_count }}
                    </span>
                  </template>
                  <template #created_at="{ row }">
                    <span class="text-sm text-default">{{ formatTime(row.created_at) }}</span>
                  </template>
                  <template #actions="{ row }">
                    <div class="flex justify-center gap-1">
                      <BaseButton size="sm" intent="ghost" class="text-xs whitespace-nowrap" @click="openBatchDetail(row as TdocImportBatch)">详情</BaseButton>
                      <BaseButton
                        v-if="row.fail_count > 0 && row.status !== 'running'"
                        size="sm" intent="ghost" class="text-xs whitespace-nowrap"
                        @click="handleRetry(row as TdocImportBatch)"
                      >重试</BaseButton>
                    </div>
                  </template>
                </BaseTable>
              </div>
              <div class="mt-3 flex justify-center">
                <BasePagination
                  v-model:currentPage="currentPage"
                  :total="total"
                  :page-size="pageSize"
                  @change="loadBatches"
                />
              </div>
            </template>
          </div>
        </div>
      </div>
    </main>

    <!-- 绑定凭据弹框 -->
    <BaseModal v-model="showBindModal" title="绑定腾讯文档账号" size="md">
      <div class="modal-form">
        <div class="form-field">
          <label class="form-label">Client ID <span class="form-required">*</span></label>
          <BaseInput v-model="bindForm.client_id" placeholder="腾讯开放平台 Client ID" :state="bindErrors.client_id ? 'error' : 'default'" />
        </div>
        <div class="form-field">
          <label class="form-label">Access Token <span class="form-required">*</span></label>
          <BaseInput v-model="bindForm.access_token" type="password" placeholder="个人访问令牌" :state="bindErrors.access_token ? 'error' : 'default'" />
        </div>
        <div class="form-field">
          <label class="form-label">Open ID <span class="form-required">*</span></label>
          <BaseInput v-model="bindForm.open_id" placeholder="授权用户 Open ID" :state="bindErrors.open_id ? 'error' : 'default'" />
        </div>
        <p class="text-xs text-muted">凭据加密存储，不会明文展示或下发</p>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showBindModal = false">取消</BaseButton>
        <BaseButton :disabled="isSavingAuth" @click="handleSaveAuth">{{ isSavingAuth ? '保存中...' : '保存' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- 文件选择弹框 -->
    <BaseModal v-model="showFileModal" title="从腾讯文档导入" size="lg" :close-on-overlay="false">
      <div class="flex flex-col gap-3 min-h-[400px]">
        <!-- 面包屑 -->
        <div class="flex items-center gap-1 text-sm flex-wrap">
          <button class="text-primary-600 hover:underline" @click="navigateFolder(null)">根目录</button>
          <template v-for="(crumb, i) in folderCrumbs" :key="crumb.file_id">
            <span class="text-muted">/</span>
            <button
              :class="i === folderCrumbs.length - 1 ? 'text-default font-medium' : 'text-primary-600 hover:underline'"
              @click="navigateFolder(i)"
            >{{ crumb.title }}</button>
          </template>
        </div>

        <div v-if="isLoadingFiles" class="flex items-center justify-center py-16">
          <span class="text-sm text-muted">加载中...</span>
        </div>
        <p v-else-if="fileError" class="text-sm text-danger-500 py-8 text-center">{{ fileError }}</p>
        <div v-else-if="files.length === 0" class="empty-state py-16">
          <p class="text-sm text-muted">当前目录没有文件</p>
        </div>
        <div v-else class="border border-default rounded-lg overflow-y-auto max-h-[45vh]">
          <div
            v-for="f in files"
            :key="f.file_id"
            class="flex items-center gap-3 px-3 py-2 border-b border-default last:border-b-0 hover:bg-surface-hover"
          >
            <input
              type="checkbox"
              class="table-checkbox flex-shrink-0"
              :disabled="f.type !== 'doc'"
              :checked="selectedFileIds.has(f.file_id)"
              @change="toggleFileSelect(f)"
            />
            <span
              v-if="f.type === 'folder'"
              class="flex-1 min-w-0 truncate text-sm text-primary-600 cursor-pointer hover:underline"
              :title="`进入目录：${f.title || '(未命名)'}`"
              @click="enterFolder(f)"
            >{{ f.title || '(未命名)' }}</span>
            <span v-else class="flex-1 min-w-0 truncate text-sm text-default" :title="f.title">{{ f.title || '(未命名)' }}</span>
            <BaseBadge v-if="f.type === 'folder'" intent="neutral">目录</BaseBadge>
            <BaseBadge v-else-if="f.type !== 'doc'" intent="neutral">暂不支持</BaseBadge>
            <BaseBadge v-else-if="f.unchanged" intent="info">无变化，将跳过</BaseBadge>
            <BaseBadge v-else-if="f.imported" intent="warning">已导入，将覆盖更新</BaseBadge>
            <span class="text-xs text-muted w-16 text-right flex-shrink-0">{{ docTypeLabel(f.type) }}</span>
          </div>
        </div>

        <div class="flex items-center justify-between text-xs text-muted">
          <span>已选 {{ selectableSelectedCount }} 篇（跳过 {{ skippedSelectedCount }} 篇）</span>
          <button v-if="filesNext !== null" class="text-primary-600 hover:underline" :disabled="isLoadingFiles" @click="loadMoreFiles">加载更多</button>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showFileModal = false">取消</BaseButton>
        <BaseButton :disabled="selectableSelectedCount === 0 || isCreating" @click="handleCreateImport">
          {{ isCreating ? '创建中...' : `创建导入任务（${selectableSelectedCount}）` }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- 批次详情弹框 -->
    <BaseModal v-model="showDetailModal" :title="`导入任务详情 - ${detailBatch?.batch_id || ''}`" size="lg">
      <template v-if="detailBatch">
        <div class="flex items-center gap-3 mb-4">
          <BaseBadge :intent="batchStatusIntent(detailBatch.status)">{{ batchStatusLabel(detailBatch.status) }}</BaseBadge>
          <span class="text-sm text-muted">
            成功 {{ detailBatch.success_count }} / 失败 {{ detailBatch.fail_count }} / 跳过 {{ detailBatch.skip_count }} / 共 {{ detailBatch.total_count }}
          </span>
        </div>
        <p v-if="detailBatch.error_message" class="text-sm text-danger-500 mb-4">{{ detailBatch.error_message }}</p>
        <div v-if="isLoadingDetail" class="text-center py-8 text-sm text-muted">加载中...</div>
        <div v-else class="table-scroll-wrapper">
          <BaseTable :columns="itemColumns" :data="detailItems" row-key="id" table-fixed>
            <template #status="{ row }">
              <BaseBadge :intent="itemStatusIntent(row.status)">{{ itemStatusLabel(row.status) }}</BaseBadge>
            </template>
            <template #error_message="{ row }">
              <span class="text-xs text-danger-500 break-all whitespace-normal">{{ row.error_message || '-' }}</span>
            </template>
          </BaseTable>
        </div>
      </template>
      <template #footer>
        <BaseButton intent="secondary" @click="showDetailModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import type { TdocAuthStatus, TdocFileItem, TdocImportBatch, TdocImportItem } from '@/api/tdoc'
import {
  createTdocImport,
  deleteTdocAuth,
  getTdocAuthStatus,
  getTdocImport,
  listTdocFiles,
  listTdocImports,
  retryTdocImport,
  saveTdocAuth
} from '@/api/tdoc'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar', () => {})

const effectiveIsLoggedIn = tenantIsLoggedIn
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

function handleToggleSidebar() {
  toggleSidebarFn()
}

function goKnowledge() {
  const tid = route.params.tenant_id
  router.push(`/t/${tid}/knowledge`)
}

async function handleLogout() {
  await tenantLogout()
  router.push('/')
}

// ========== 授权状态 ==========
const authStatus = ref<TdocAuthStatus | null>(null)
const showBindModal = ref(false)
const isSavingAuth = ref(false)
const bindForm = ref({ client_id: '', access_token: '', open_id: '' })
const bindErrors = ref({ client_id: false, access_token: false, open_id: false })

async function loadAuthStatus() {
  try {
    authStatus.value = await getTdocAuthStatus()
  } catch (e: any) {
    console.error('前端日志：获取腾讯文档绑定状态失败', e)
  }
}

function openBindModal() {
  bindForm.value = { client_id: '', access_token: '', open_id: '' }
  bindErrors.value = { client_id: false, access_token: false, open_id: false }
  showBindModal.value = true
}

async function handleSaveAuth() {
  const form = bindForm.value
  bindErrors.value = {
    client_id: !form.client_id.trim(),
    access_token: !form.access_token.trim(),
    open_id: !form.open_id.trim()
  }
  if (bindErrors.value.client_id || bindErrors.value.access_token || bindErrors.value.open_id) {
    alert('请填写完整凭据信息')
    return
  }
  isSavingAuth.value = true
  try {
    const result = await saveTdocAuth({
      client_id: form.client_id.trim(),
      access_token: form.access_token.trim(),
      open_id: form.open_id.trim()
    })
    if (result.success) {
      authStatus.value = result
      showBindModal.value = false
      toast.success('凭据已保存')
    } else {
      toast.error(result.error || '凭据保存失败')
    }
  } catch (e: any) {
    toast.error(e.message || '凭据保存失败')
  } finally {
    isSavingAuth.value = false
  }
}

async function handleUnbind() {
  if (!confirm('确定解绑腾讯文档账号？解绑后需重新绑定才能导入。')) return
  try {
    const result = await deleteTdocAuth()
    if (result.success) {
      await loadAuthStatus()
      toast.success('已解绑')
    } else {
      toast.error('解绑失败')
    }
  } catch (e: any) {
    toast.error(e.message || '解绑失败')
  }
}

// ========== 文件选择 ==========
const showFileModal = ref(false)
const isLoadingFiles = ref(false)
const fileError = ref('')
const files = ref<TdocFileItem[]>([])
const filesNext = ref<number | null>(null)
const currentFolderId = ref('')
const folderCrumbs = ref<TdocFileItem[]>([])
const selectedFileIds = ref<Set<string>>(new Set())
const isCreating = ref(false)

const DOC_TYPES: Record<string, string> = {
  doc: '在线文档',
  sheet: '表格',
  slide: '幻灯片',
  file: '文件'
}

function docTypeLabel(t: string): string {
  return DOC_TYPES[t] || t || '-'
}

function openFileModal() {
  folderCrumbs.value = []
  currentFolderId.value = ''
  files.value = []
  filesNext.value = null
  selectedFileIds.value = new Set()
  fileError.value = ''
  showFileModal.value = true
  loadFiles('')
}

async function loadFiles(folderId: string, start = 0, append = false) {
  isLoadingFiles.value = true
  fileError.value = ''
  try {
    const result = await listTdocFiles(folderId, start)
    if (result.success) {
      files.value = append ? [...files.value, ...result.items] : result.items
      filesNext.value = result.next
      currentFolderId.value = folderId
    } else {
      fileError.value = result.error || result.detail || '目录加载失败'
    }
  } catch (e: any) {
    fileError.value = e.message || '目录加载失败'
  } finally {
    isLoadingFiles.value = false
  }
}

function navigateFolder(index: number | null) {
  if (index === null) {
    folderCrumbs.value = []
    selectedFileIds.value = new Set()
    loadFiles('')
    return
  }
  const crumb = folderCrumbs.value[index]
  if (!crumb) return
  folderCrumbs.value = folderCrumbs.value.slice(0, index + 1)
  selectedFileIds.value = new Set()
  loadFiles(crumb.file_id)
}

function enterFolder(f: TdocFileItem) {
  const targetId = f.content_id || f.file_id
  folderCrumbs.value = [...folderCrumbs.value, { ...f, file_id: targetId }]
  selectedFileIds.value = new Set()
  loadFiles(targetId)
}

function loadMoreFiles() {
  if (filesNext.value === null) return
  loadFiles(currentFolderId.value, filesNext.value, true)
}

function toggleFileSelect(f: TdocFileItem) {
  const next = new Set(selectedFileIds.value)
  if (next.has(f.file_id)) {
    next.delete(f.file_id)
  } else {
    next.add(f.file_id)
  }
  selectedFileIds.value = next
}

const selectableSelectedCount = computed(() => {
  return files.value.filter(f => selectedFileIds.value.has(f.file_id) && f.type === 'doc').length
})

const skippedSelectedCount = computed(() => {
  return files.value.filter(f => selectedFileIds.value.has(f.file_id) && f.type === 'doc' && f.unchanged).length
})

async function handleCreateImport() {
  const selected = files.value.filter(f => selectedFileIds.value.has(f.file_id) && f.type === 'doc')
  if (selected.length === 0) return
  isCreating.value = true
  try {
    const result = await createTdocImport(selected.map(f => ({
      file_id: f.content_id || f.file_id,
      title: f.title,
      doc_type: f.type,
      last_modify_time: f.last_modify_time
    })))
    if (result.success) {
      toast.success(`导入任务已创建：${result.queued || 0} 篇待导入${result.skipped ? `，${result.skipped} 篇无变化跳过` : ''}`)
      showFileModal.value = false
      currentPage.value = 1
      await loadBatches()
    } else {
      toast.error(result.error || result.detail || '创建导入任务失败')
    }
  } catch (e: any) {
    toast.error(e.message || '创建导入任务失败')
  } finally {
    isCreating.value = false
  }
}

// ========== 批次列表 ==========
const batches = ref<TdocImportBatch[]>([])
const isLoadingBatches = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

const batchColumns = [
  { key: 'batch_id', label: '批次号', width: '200px' },
  { key: 'status', label: '状态', width: '110px' },
  { key: 'progress', label: '进度', width: '280px' },
  { key: 'created_at', label: '创建时间', width: '170px' },
  { key: 'actions', label: '操作', width: '120px', thAlign: 'center' as const }
]

function formatTime(s: string | null | undefined): string {
  if (!s) return '-'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

function batchStatusLabel(s: string): string {
  const map: Record<string, string> = {
    queued: '排队中', running: '执行中', succeeded: '已完成', partial: '部分失败', failed: '失败'
  }
  return map[s] || s
}

function batchStatusIntent(s: string): 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  const map: Record<string, any> = {
    queued: 'info', running: 'info', succeeded: 'success', partial: 'warning', failed: 'danger'
  }
  return map[s] || 'neutral'
}

function itemStatusLabel(s: string): string {
  const map: Record<string, string> = {
    pending: '待执行', running: '执行中', succeeded: '成功', failed: '失败', skipped: '跳过'
  }
  return map[s] || s
}

function itemStatusIntent(s: string): 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  const map: Record<string, any> = {
    pending: 'neutral', running: 'info', succeeded: 'success', failed: 'danger', skipped: 'warning'
  }
  return map[s] || 'neutral'
}

async function loadBatches() {
  isLoadingBatches.value = true
  try {
    const result = await listTdocImports(currentPage.value, pageSize.value)
    if (result.success) {
      batches.value = result.items
      total.value = result.total
    }
  } catch (e: any) {
    console.error('前端日志：加载导入批次失败', e)
  } finally {
    isLoadingBatches.value = false
  }
}

// ========== 批次详情 ==========
const showDetailModal = ref(false)
const detailBatch = ref<TdocImportBatch | null>(null)
const detailItems = ref<TdocImportItem[]>([])
const isLoadingDetail = ref(false)

const itemColumns = [
  { key: 'title', label: '文档', width: '240px' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'retry_count', label: '重试次数', width: '80px' },
  { key: 'error_message', label: '错误信息' }
]

async function openBatchDetail(row: TdocImportBatch) {
  detailBatch.value = row
  detailItems.value = []
  showDetailModal.value = true
  await refreshBatchDetail(row.batch_id)
}

async function refreshBatchDetail(batchId: string) {
  isLoadingDetail.value = true
  try {
    const result = await getTdocImport(batchId)
    if (result.success) {
      detailBatch.value = result.batch
      detailItems.value = result.items
    }
  } catch (e: any) {
    console.error('前端日志：加载批次详情失败', e)
  } finally {
    isLoadingDetail.value = false
  }
}

async function handleRetry(row: TdocImportBatch) {
  try {
    const result = await retryTdocImport(row.batch_id)
    if (result.success) {
      toast.success(`已重置 ${result.reset_count || 0} 篇失败文档，等待调度器执行`)
      await loadBatches()
    } else {
      toast.error(result.error || '重试失败')
    }
  } catch (e: any) {
    toast.error(e.message || '重试失败')
  }
}

// ========== 进行中批次轮询（2s） ==========
let pollTimer: ReturnType<typeof setInterval> | null = null

function startPollingIfNeeded() {
  const hasActive = batches.value.some(b => b.status === 'queued' || b.status === 'running')
  if (hasActive && pollTimer === null) {
    pollTimer = setInterval(async () => {
      try {
        const result = await listTdocImports(currentPage.value, pageSize.value)
        if (result.success) {
          batches.value = result.items
          total.value = result.total
          if (!result.items.some(b => b.status === 'queued' || b.status === 'running')) {
            stopPolling()
            loadAuthStatus()
          }
          if (showDetailModal.value && detailBatch.value) {
            const current = result.items.find(b => b.batch_id === detailBatch.value!.batch_id)
            if (current) {
              detailBatch.value = current
              const detail = await getTdocImport(current.batch_id)
              if (detail.success) detailItems.value = detail.items
            }
          }
        }
      } catch (e) {
        console.error('前端日志：轮询导入进度失败', e)
      }
    }, 2000)
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(batches, () => startPollingIfNeeded())

onMounted(async () => {
  await Promise.all([loadAuthStatus(), loadBatches()])
})

onBeforeUnmount(() => stopPolling())
</script>

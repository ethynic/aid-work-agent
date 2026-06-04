<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <main class="flex-1 flex overflow-hidden">
      <MenuSidebar
        v-if="!isNestedRoute"
        :is-collapsed="isSidebarCollapsed"
        :current-subagent-id="currentSubagentId"
        :available-subagents="availableSubagents"
        @collapse="isSidebarCollapsed = true"
      />

      <div class="flex-1 flex flex-col min-w-0">
        <AppHeader
          title="企业知识库"
          :is-logged-in="effectiveIsLoggedIn"
          :user="effectiveUser"
          @toggle-sidebar="isSidebarCollapsed = !isSidebarCollapsed"
          @logout="handleLogout"
        />

        <div class="flex-1 overflow-hidden p-6">
          <div class="max-w-6xl mx-auto h-full flex flex-col page-container">
            <div class="page-toolbar">
              <div class="page-toolbar-left">
                <BaseInput v-model="searchQuery" placeholder="搜索文档..." size="sm" class="w-80" @keyup.enter="handleSearchInput" @input="handleSearchInput" />
                <BaseButton v-if="isSearchMode" size="sm" intent="secondary" @click="clearSearch">显示全部</BaseButton>
              </div>
              <div class="page-toolbar-right">
                <BaseButton @click="showUploadModal = true">上传文档</BaseButton>
              </div>
            </div>

            <div class="flex-1 overflow-hidden bg-white rounded-xl border border-default">
              <!-- Loading -->
              <div v-if="isLoading" class="flex items-center justify-center h-full">
                <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span class="ml-3 text-muted">加载中...</span>
              </div>

              <!-- Searching -->
              <div v-else-if="isSearching" class="flex flex-col items-center justify-center h-full">
                <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span class="ml-3 text-muted">搜索中...</span>
              </div>

              <!-- Search Error -->
              <div v-else-if="searchError && isSearchMode" class="flex flex-col items-center justify-center h-full">
                <svg class="w-16 h-16 text-danger-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <p class="text-danger-500 mb-2">搜索失败</p>
                <p class="text-sm text-muted">{{ searchError }}</p>
              </div>

              <!-- Search Results Empty -->
              <div v-else-if="isSearchMode && groupedSearchResults.length === 0 && !isLoading" class="flex flex-col items-center justify-center h-full">
                <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p class="text-muted mb-2">未找到匹配的文档</p>
                <p class="text-sm text-muted">尝试其他关键词</p>
              </div>

              <!-- Document List Empty -->
              <div v-else-if="documents.length === 0 && !isSearchMode && !isLoading" class="flex flex-col items-center justify-center h-full">
                <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p class="text-muted mb-2">暂无已上传的文档</p>
                <p class="text-sm text-muted">点击上方按钮上传文档</p>
              </div>

              <!-- Search Results -->
              <div v-else-if="isSearchMode && groupedSearchResults.length > 0" class="h-full overflow-auto p-4">
                <div class="space-y-4">
                  <div v-for="group in groupedSearchResults" :key="group.chunks[0].doc_id" class="bg-canvas rounded-lg p-4">
                    <div class="flex items-center gap-3 mb-3">
                      <div :class="getFileIconClass('.' + group.file_type)" class="w-10 h-10 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <div class="flex-1 min-w-0">
                        <h3 @click="openDocument(group.chunks[0].doc_id)" class="text-sm font-medium text-primary-600 hover:text-primary-700 hover:underline cursor-pointer truncate" :title="'点击打开原文: ' + group.title">{{ group.title }}</h3>
                        <p class="text-xs text-muted">{{ group.chunks.length }} 个相关片段</p>
                      </div>
                    </div>
                    <div class="space-y-2">
                      <div v-for="(chunk, _idx) in group.chunks.slice(0, 3)" :key="chunk.chunk_id" class="bg-white rounded p-3 text-sm text-default border border-default">
                        <p class="line-clamp-3">{{ chunk.text }}</p>
                        <p class="text-xs text-primary-500 mt-1">相关度: {{ (chunk.score * 100).toFixed(1) }}%</p>
                      </div>
                      <p v-if="group.chunks.length > 3" class="text-xs text-muted text-center">还有 {{ group.chunks.length - 3 }} 个相关片段...</p>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Table -->
              <div v-else class="h-full flex flex-col">
                <div class="flex-1 overflow-auto table-scroll-wrapper">
                  <BaseTable :columns="columns" :data="documents" row-key="id">
                    <template #index="{ index }">{{ seqNumber(index) }}</template>
                    <template #title="{ row }">
                      <div class="flex items-center gap-3">
                        <div :class="getFileIconClass(row.file_type)" class="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0">
                          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                          </svg>
                        </div>
                        <span @click="openDocument(row.id)" class="text-sm font-medium text-primary-600 hover:text-primary-700 hover:underline cursor-pointer block" :title="'点击打开原文: ' + row.title">{{ row.title }}</span>
                      </div>
                    </template>
                    <template #summary="{ row }">
                      <p v-if="row.summary" class="text-sm text-default line-clamp-2" :title="row.summary">{{ row.summary }}</p>
                      <p v-else class="text-sm text-muted">-</p>
                    </template>
                    <template #file_type="{ row }">
                      <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-default uppercase">{{ row.file_type.replace('.', '') }}</span>
                    </template>
                    <template #file_size="{ row }">{{ formatFileSize(row.file_size) }}</template>
                    <template #total_chunks="{ row }">{{ row.total_chunks }}</template>
                    <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
                    <template #actions="{ row }">
                      <BaseButton intent="danger" size="sm" @click="handleDelete(row)">删除</BaseButton>
                    </template>
                  </BaseTable>
                </div>

                <BasePagination
                  v-if="totalDocuments > pageSize"
                  :total="totalDocuments"
                  v-model:current-page="currentPage"
                  :page-size="pageSize"
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Upload Modal -->
    <div v-if="showUploadModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-50" @click.self="showUploadModal = false">
      <div class="bg-white rounded-2xl w-full max-w-lg mx-4 shadow-2xl">
        <div class="flex items-center justify-between px-6 py-4 border-b border-default">
          <h2 class="text-sm font-semibold text-default">上传文档</h2>
          <button @click="showUploadModal = false" class="p-2 text-muted hover:text-default hover:bg-surface-hover rounded-lg transition-colors">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="p-6">
          <div @dragover.prevent="isDragging = true" @dragleave.prevent="isDragging = false" @drop.prevent="handleDrop"
            :class="['border-2 border-dashed rounded-xl p-8 text-center transition-all', isDragging ? 'border-primary-500 bg-primary-50' : 'border-default hover:border-hover']">
            <input ref="fileInputRef" type="file" multiple
              accept=".docx,.xlsx,.pptx,.pdf,.txt,.md,.json,.yaml,.yml,.log,.csv,.xml,.ini,.properties,.conf,.config"
              @change="handleFileSelect" class="hidden" />

            <svg v-if="selectedFiles.length === 0" class="w-12 h-12 mx-auto text-muted mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>

            <div v-if="selectedFiles.length === 0" class="space-y-2">
              <p class="text-default font-medium">拖拽文件到此处，或<span @click="fileInputRef?.click()" class="text-primary-600 hover:text-primary-500 cursor-pointer">点击选择</span></p>
              <p class="text-sm text-muted">支持多文件上传，单文件不超过 {{ MAX_FILE_SIZE_MB }}MB</p>
              <p class="text-xs text-muted">支持格式：docx, xlsx, pptx, pdf, txt, md, json, yaml, yml, log, csv, xml, ini, properties, conf, config 等</p>
            </div>

            <div v-else class="space-y-3">
              <div class="space-y-2 max-h-48 overflow-y-auto">
                <div v-for="(file, index) in selectedFiles" :key="index" class="flex items-center justify-between gap-3 px-3 py-2 bg-canvas rounded-lg">
                  <div class="flex items-center gap-3 min-w-0 flex-1">
                    <div :class="getFileIconClassByExt(file.name)" class="w-8 h-8 rounded flex items-center justify-center flex-shrink-0">
                      <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                    </div>
                    <div class="text-left min-w-0 flex-1">
                      <p class="text-default font-medium text-sm truncate" :title="file.name">{{ file.name }}</p>
                      <p class="text-xs text-muted">{{ formatFileSize(file.size) }}</p>
                    </div>
                  </div>
                  <button @click.stop="removeFile(index)" class="p-1 text-muted hover:text-default hover:bg-surface-hover rounded flex-shrink-0">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              </div>
              <div class="flex items-center justify-between">
                <p class="text-sm text-muted">已选择 {{ selectedFiles.length }} 个文件</p>
                <button @click.stop="clearFiles" class="text-sm text-muted hover:text-default">清空全部</button>
              </div>
            </div>
          </div>

          <div v-if="uploadError" class="mt-3 p-3 bg-danger-50 rounded-lg">
            <p class="text-sm text-danger-500">{{ uploadError }}</p>
          </div>
          <div v-if="uploadErrors.length > 0" class="mt-3 p-3 bg-warning-50 rounded-lg">
            <p class="text-sm text-warning-600 font-medium mb-2">以下文件上传失败：</p>
            <ul class="text-sm text-warning-700 space-y-1">
              <li v-for="(err, index) in uploadErrors" :key="index" class="flex items-start gap-2">
                <span class="text-warning-500">•</span>
                <span>{{ err.filename }}: {{ err.error }}</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="flex justify-end gap-3 px-6 py-4 border-t border-default bg-canvas rounded-b-2xl">
          <BaseButton intent="secondary" @click="handleCancelUpload">取消</BaseButton>
          <BaseButton :disabled="selectedFiles.length === 0 || isUploading" @click="handleUpload">
            {{ uploadButtonText }}
          </BaseButton>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <BaseModal v-model="showDeleteConfirm" title="删除文档" size="md">
      <template v-if="documentToDelete">
        <div class="flex items-center gap-4">
          <div class="w-12 h-12 rounded-full bg-danger-100 flex items-center justify-center">
            <svg class="w-6 h-6 text-danger-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <div>
            <p class="text-sm text-default">确定要删除"{{ documentToDelete.title }}"吗？此操作不可恢复。</p>
          </div>
        </div>
      </template>
      <template #footer>
        <BaseButton intent="secondary" @click="showDeleteConfirm = false">取消</BaseButton>
        <BaseButton intent="danger" :disabled="isDeleting" @click="confirmDelete">{{ isDeleting ? '删除中...' : '删除' }}</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from './AppHeader.vue'
import MenuSidebar from './MenuSidebar.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'
import { type SubagentListItem } from '@/api/subagent'
import { getMyAllowedAgents } from '@/api/saasPermissions'
import { listDocuments, deleteDocument, uploadDocument, searchDocuments, getDocumentDownloadUrl, type DocumentResponse, type SearchResultItem, type BatchUploadError } from '@/api/knowledge'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()
const { user: demoUser, isLoggedIn: demoIsLoggedIn, logout: demoLogout } = useDemoAuth()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const isTenantMode = computed(() => route.path.startsWith('/t/'))

const effectiveIsLoggedIn = computed(() => {
  return isTenantMode.value ? tenantIsLoggedIn.value : demoIsLoggedIn.value
})

const effectiveUser = computed(() => {
  if (isTenantMode.value) {
    return tenantAdmin.value ? {
      user_id: tenantAdmin.value.user_id,
      username: tenantAdmin.value.username,
      phone: tenantAdmin.value.phone
    } : null
  }
  return demoUser.value
})
const toast = useToast()

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'title', label: '文档名称', width: '260px' },
  { key: 'summary', label: '摘要', width: '380px' },
  { key: 'file_type', label: '类型' },
  { key: 'file_size', label: '大小' },
  { key: 'total_chunks', label: '分块数' },
  { key: 'created_at', label: '上传时间' },
  { key: 'actions', label: '操作', width: '80px' },
]

const documents = ref<DocumentResponse[]>([])
const isLoading = ref(false)
const searchQuery = ref('')
const searchResults = ref<SearchResultItem[]>([])
const isSearching = ref(false)
const searchError = ref('')
const showUploadModal = ref(false)
const selectedFiles = ref<File[]>([])
const isDragging = ref(false)
const isUploading = ref(false)
const uploadError = ref('')
const uploadErrors = ref<BatchUploadError[]>([])
const documentToDelete = ref<DocumentResponse | null>(null)
const showDeleteConfirm = ref(false)
const isDeleting = ref(false)

const uploadProgress = ref({
  total: 0,
  current: 0,
  currentFileName: ''
})

const MAX_FILE_SIZE = (import.meta.env.VITE_MAX_KNOWLEDGE_FILE_SIZE || 50) * 1024 * 1024
const MAX_FILE_SIZE_MB = Math.round(MAX_FILE_SIZE / 1024 / 1024)

const fileInputRef = ref<HTMLInputElement | null>(null)

const isSidebarCollapsed = ref(typeof window !== 'undefined' && window.innerWidth < 768)
const isNestedRoute = computed(() => route.path.startsWith('/t/'))
const availableSubagents = ref<SubagentListItem[]>([])

const currentSubagentId = computed(() => {
  const segments = route.path.split('/').filter(p => p)
  if (segments[0] === 't' && segments.length >= 3) {
    if (segments[2] === 'chat' && segments.length >= 4) {
      return segments[3]
    }
    return segments[2]
  }
  if (segments.length >= 1) {
    if (segments[0] === 'chat' && segments.length >= 2) {
      return segments[1]
    }
    return segments[0]
  }
  return undefined
})

async function loadAvailableSubagents() {
  try {
    let res
    if (isTenantMode.value) {
      res = await getMyAllowedAgents()
    } else {
      const { listSubagents } = await import('@/api/subagent')
      res = await listSubagents()
    }
    if (res.success && res.data) {
      availableSubagents.value = res.data as SubagentListItem[]
    }
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
  }
}

const totalDocuments = ref(0)
const { currentPage, pageSize, seqNumber } =
  usePageContext(async () => {
    await loadDocuments()
  })

const uploadButtonText = computed(() => {
  if (isUploading.value) {
    const progress = uploadProgress.value
    if (progress.total > 0) {
      return `上传中 ${progress.current}/${progress.total} - ${progress.currentFileName}`
    }
    return `上传中 (${selectedFiles.value.length} 个文件)...`
  }
  return `上传 (${selectedFiles.value.length})`
})

const isSearchMode = computed(() => searchQuery.value.trim().length > 0)

let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null

async function performSearch(query: string) {
  if (!query.trim()) {
    searchResults.value = []
    searchError.value = ''
    return
  }
  isSearching.value = true
  searchError.value = ''
  try {
    const result = await searchDocuments(query)
    if (result.success) {
      searchResults.value = result.results
    } else {
      searchError.value = result.error || '搜索失败'
      searchResults.value = []
    }
  } catch (error: any) {
    console.error('前端日志：搜索文档失败', error)
    searchError.value = error.message || '搜索失败'
    searchResults.value = []
  } finally {
    isSearching.value = false
  }
}

function handleSearchInput() {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
  }
  searchDebounceTimer = setTimeout(() => {
    if (isSearchMode.value) {
      performSearch(searchQuery.value)
    } else {
      searchResults.value = []
    }
  }, 300)
}

function clearSearch() {
  searchQuery.value = ''
  searchResults.value = []
  searchError.value = ''
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
}

const groupedSearchResults = computed(() => {
  const groups: Record<number, { title: string; file_type: string; chunks: SearchResultItem[]; maxScore: number }> = {}
  for (const result of searchResults.value) {
    if (!groups[result.doc_id]) {
      groups[result.doc_id] = {
        title: result.title,
        file_type: result.file_type,
        chunks: [],
        maxScore: 0
      }
    }
    groups[result.doc_id].chunks.push(result)
    if (result.score > groups[result.doc_id].maxScore) {
      groups[result.doc_id].maxScore = result.score
    }
  }
  return Object.values(groups)
    .map(group => ({
      ...group,
      chunks: group.chunks.sort((a, b) => b.score - a.score)
    }))
    .sort((a, b) => b.maxScore - a.maxScore)
})

async function loadDocuments() {
  isLoading.value = true
  try {
    const result = await listDocuments(pageSize.value, (currentPage.value - 1) * pageSize.value)
    documents.value = result.items
    totalDocuments.value = result.total
  } catch (error: any) {
    console.error('前端日志：加载文档列表失败', error)
  } finally {
    isLoading.value = false
  }
}

const ALLOWED_EXTENSIONS = ['.docx', '.xlsx', '.pptx', '.pdf', '.txt', '.md', '.json', '.yaml', '.yml', '.log', '.csv', '.xml', '.ini', '.properties', '.conf', '.config']

function validateFile(file: File): { valid: boolean; error?: string } {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return { valid: false, error: `不支持的文件格式: ${ext}` }
  }
  if (file.size > MAX_FILE_SIZE) {
    return { valid: false, error: `文件过大，最大支持 ${MAX_FILE_SIZE_MB}MB` }
  }
  return { valid: true }
}

function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    const newFiles: File[] = []
    const errors: string[] = []
    for (let i = 0; i < input.files.length; i++) {
      const file = input.files[i]
      const validation = validateFile(file)
      if (validation.valid) {
        if (!selectedFiles.value.some(f => f.name === file.name && f.size === file.size)) {
          newFiles.push(file)
        }
      } else {
        errors.push(`${file.name}: ${validation.error}`)
      }
    }
    if (newFiles.length > 0) {
      selectedFiles.value = [...selectedFiles.value, ...newFiles]
      uploadError.value = ''
      uploadErrors.value = []
    }
    if (errors.length > 0) {
      uploadError.value = errors.join('；')
    }
  }
}

function handleDrop(event: DragEvent) {
  isDragging.value = false
  if (event.dataTransfer?.files && event.dataTransfer.files.length > 0) {
    const newFiles: File[] = []
    const errors: string[] = []
    for (let i = 0; i < event.dataTransfer.files.length; i++) {
      const file = event.dataTransfer.files[i]
      const validation = validateFile(file)
      if (validation.valid) {
        if (!selectedFiles.value.some(f => f.name === file.name && f.size === file.size)) {
          newFiles.push(file)
        }
      } else {
        errors.push(`${file.name}: ${validation.error}`)
      }
    }
    if (newFiles.length > 0) {
      selectedFiles.value = [...selectedFiles.value, ...newFiles]
      uploadError.value = ''
      uploadErrors.value = []
    }
    if (errors.length > 0) {
      uploadError.value = errors.join('；')
    }
  }
}

function removeFile(index: number) {
  selectedFiles.value.splice(index, 1)
}

function clearFiles() {
  selectedFiles.value = []
  uploadError.value = ''
  uploadErrors.value = []
}

function handleCancelUpload() {
  clearFiles()
  showUploadModal.value = false
}

async function handleUpload() {
  if (selectedFiles.value.length === 0) return
  isUploading.value = true
  uploadError.value = ''
  uploadErrors.value = []

  const filesToUpload = [...selectedFiles.value]
  const totalFiles = filesToUpload.length

  uploadProgress.value = {
    total: totalFiles,
    current: 0,
    currentFileName: ''
  }

  const successResults: any[] = []
  const errorResults: BatchUploadError[] = []

  try {
    for (let i = 0; i < filesToUpload.length; i++) {
      const file = filesToUpload[i]
      uploadProgress.value.current = i + 1
      uploadProgress.value.currentFileName = file.name

      try {
        const result = await uploadDocument(file)
        successResults.push(result)
        await loadDocuments()
      } catch (error: any) {
        const errorMsg = error.response?.data?.error || error.message || '上传失败'
        errorResults.push({ filename: file.name, error: errorMsg })
      }
    }

    showUploadModal.value = false
    clearSearch()
    selectedFiles.value = []
    currentPage.value = 1
    await loadDocuments()

    if (successResults.length > 0) {
      if (successResults.length === totalFiles) {
        toast.success('全部 ' + successResults.length + ' 个文件上传成功')
      } else {
        toast.success('' + successResults.length + ' 个文件上传成功')
      }
    }
    if (errorResults.length > 0) {
      uploadErrors.value = errorResults
      toast.warning('' + errorResults.length + ' 个文件上传失败')
    }
  } catch (error: any) {
    const errorMsg = error.response?.data?.error || error.message || '上传失败'
    uploadError.value = errorMsg
    toast.error(errorMsg)
  } finally {
    isUploading.value = false
    uploadProgress.value = { total: 0, current: 0, currentFileName: '' }
  }
}

function handleDelete(doc: any) {
  documentToDelete.value = doc
  showDeleteConfirm.value = true
}

async function confirmDelete() {
  if (!documentToDelete.value) return
  isDeleting.value = true
  try {
    await deleteDocument(documentToDelete.value.id)
    showDeleteConfirm.value = false
    documentToDelete.value = null
    if (documents.value.length === 1 && currentPage.value > 1) {
      currentPage.value--
    }
    await loadDocuments()
  } catch (error: any) {
    console.error('前端日志：删除文档失败', error)
    toast.error(error.response?.data?.error || '删除失败')
  } finally {
    isDeleting.value = false
  }
}

function formatFileSize(bytes: number | null): string {
  if (bytes === null || bytes === 0) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatTime(isoString: string): string {
  if (!isoString) return '-'
  const date = new Date(isoString)
  const year = date.getFullYear()
  const month = date.getMonth() + 1
  const day = date.getDate()
  const hours = date.getHours().toString().padStart(2, '0')
  const minutes = date.getMinutes().toString().padStart(2, '0')
  return year + '-' + month + '-' + day + ' ' + hours + ':' + minutes
}

function getFileIconClass(fileType: string): string {
  const ext = fileType.toLowerCase()
  if (ext === 'pdf' || ext === '.pdf') return 'bg-danger-500'
  if (ext === 'docx' || ext === '.docx' || ext === 'doc' || ext === '.doc') return 'bg-primary-500'
  if (ext === 'xlsx' || ext === '.xlsx' || ext === 'xls' || ext === '.xls') return 'bg-success-500'
  if (ext === 'pptx' || ext === '.pptx' || ext === 'ppt' || ext === '.ppt') return 'bg-warning-500'
  return 'bg-gray-500'
}

function getFileIconClassByExt(filename: string): string {
  const ext = '.' + filename.split('.').pop()?.toLowerCase()
  return getFileIconClass(ext)
}

function openDocument(docId: number) {
  const url = getDocumentDownloadUrl(docId)
  window.open(url, '_blank')
}

async function handleLogout() {
  if (isTenantMode.value) {
    await tenantLogout()
  } else {
    await demoLogout()
  }
  router.push(isTenantMode.value ? route.path.replace(/\/knowledge.*/, '') : '/')
}

onMounted(() => {
  loadAvailableSubagents()
  loadDocuments()
})
</script>

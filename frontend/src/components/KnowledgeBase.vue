<template>
  <div class="h-screen flex flex-col bg-slate-50">
    <!-- Header -->
    <header class="flex-shrink-0 border-b border-slate-200 bg-white/80 backdrop-blur-sm">
      <div class="max-w-7xl mx-auto px-4 py-4">
        <div class="flex items-center gap-4">
          <button
            @click="goBack"
            class="p-2 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
            title="返回"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
          </button>
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-indigo-600 flex items-center justify-center">
              <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
              </svg>
            </div>
            <div>
              <h1 class="text-xl font-semibold text-slate-800">企业知识库</h1>
              <p class="text-sm text-slate-500">管理已上传的文档</p>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 overflow-hidden p-6">
      <div class="max-w-6xl mx-auto h-full flex flex-col">
        <!-- Toolbar -->
        <div class="flex items-center justify-between mb-4">
          <!-- Search -->
          <div class="relative flex-1 max-w-md">
            <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              v-model="searchQuery"
              type="text"
              placeholder="搜索文档..."
              class="w-full pl-10 pr-4 py-2.5 bg-white border border-slate-200 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-100 transition-all"
            />
          </div>

          <!-- Upload Button -->
          <button
            @click="showUploadModal = true"
            class="ml-4 flex items-center gap-2 px-4 py-2.5 bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            上传文档
          </button>
        </div>

        <!-- Document List -->
        <div class="flex-1 overflow-hidden bg-white rounded-xl border border-slate-200">
          <!-- Loading -->
          <div v-if="isLoading" class="flex items-center justify-center h-full">
            <svg class="w-8 h-8 animate-spin text-purple-600" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="ml-3 text-slate-500">加载中...</span>
          </div>

          <!-- Empty -->
          <div v-else-if="filteredDocuments.length === 0 && !isLoading" class="flex flex-col items-center justify-center h-full">
            <svg class="w-16 h-16 text-slate-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p class="text-slate-500 mb-2">{{ searchQuery ? '未找到匹配的文档' : '暂无已上传的文档' }}</p>
            <p class="text-sm text-slate-400">{{ searchQuery ? '尝试其他关键词' : '点击上方按钮上传文档' }}</p>
          </div>

          <!-- Table -->
          <div v-else class="h-full overflow-auto">
            <table class="w-full">
              <thead class="sticky top-0 bg-slate-50 border-b border-slate-200">
                <tr>
                  <th class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">文档名称</th>
                  <th class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">类型</th>
                  <th class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">大小</th>
                  <th class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">分块数</th>
                  <th class="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">上传时间</th>
                  <th class="px-6 py-3 text-right text-xs font-medium text-slate-500 uppercase tracking-wider">操作</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr
                  v-for="doc in filteredDocuments"
                  :key="doc.id"
                  class="hover:bg-slate-50 transition-colors"
                >
                  <td class="px-6 py-4">
                    <div class="flex items-center gap-3">
                      <!-- File Icon -->
                      <div :class="getFileIconClass(doc.file_type)" class="w-10 h-10 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <span class="text-sm font-medium text-slate-800 truncate max-w-xs">{{ doc.title }}</span>
                    </div>
                  </td>
                  <td class="px-6 py-4">
                    <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600 uppercase">
                      {{ doc.file_type.replace('.', '') }}
                    </span>
                  </td>
                  <td class="px-6 py-4 text-sm text-slate-600">{{ formatFileSize(doc.file_size) }}</td>
                  <td class="px-6 py-4 text-sm text-slate-600">{{ doc.total_chunks }}</td>
                  <td class="px-6 py-4 text-sm text-slate-600">{{ formatTime(doc.created_at) }}</td>
                  <td class="px-6 py-4 text-right">
                    <button
                      @click="handleDelete(doc)"
                      class="p-2 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
                      title="删除"
                    >
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </main>

    <!-- Upload Modal -->
    <div
      v-if="showUploadModal"
      class="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      @click.self="showUploadModal = false"
    >
      <div class="bg-white rounded-2xl w-full max-w-lg mx-4 shadow-2xl">
        <div class="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 class="text-lg font-semibold text-slate-800">上传文档</h2>
          <button
            @click="showUploadModal = false"
            class="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="p-6">
          <!-- Drop Zone -->
          <div
            @dragover.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
            :class="[
              'border-2 border-dashed rounded-xl p-8 text-center transition-all',
              isDragging ? 'border-purple-500 bg-purple-50' : 'border-slate-300 hover:border-slate-400'
            ]"
          >
            <input
              ref="fileInputRef"
              type="file"
              accept=".docx,.xlsx,.pptx,.pdf"
              @change="handleFileSelect"
              class="hidden"
            />

            <svg v-if="!selectedFile" class="w-12 h-12 mx-auto text-slate-400 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>

            <div v-if="!selectedFile" class="space-y-2">
              <p class="text-slate-600 font-medium">拖拽文件到此处，或<span @click="fileInputRef?.click()" class="text-purple-600 hover:text-purple-500 cursor-pointer">点击选择</span></p>
              <p class="text-sm text-slate-400">支持 docx, xlsx, pptx, pdf 格式</p>
            </div>

            <div v-else class="space-y-2">
              <div class="flex items-center justify-center gap-3">
                <div :class="getFileIconClass(selectedFile.type)" class="w-10 h-10 rounded-lg flex items-center justify-center">
                  <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <div class="text-left">
                  <p class="text-slate-800 font-medium">{{ selectedFile.name }}</p>
                  <p class="text-sm text-slate-400">{{ formatFileSize(selectedFile.size) }}</p>
                </div>
              </div>
              <button
                @click.stop="selectedFile = null"
                class="text-sm text-slate-500 hover:text-slate-700"
              >
                移除
              </button>
            </div>
          </div>

          <!-- Error Message -->
          <p v-if="uploadError" class="mt-3 text-sm text-red-500">{{ uploadError }}</p>
        </div>

        <div class="flex justify-end gap-3 px-6 py-4 border-t border-slate-200 bg-slate-50 rounded-b-2xl">
          <button
            @click="showUploadModal = false"
            class="px-4 py-2 text-slate-600 hover:text-slate-800 hover:bg-slate-200 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="handleUpload"
            :disabled="!selectedFile || isUploading"
            class="px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:bg-purple-400 text-white rounded-lg transition-colors flex items-center gap-2"
          >
            <svg v-if="isUploading" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            {{ isUploading ? '上传中...' : '上传' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <div
      v-if="documentToDelete"
      class="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      @click.self="documentToDelete = null"
    >
      <div class="bg-white rounded-2xl w-full max-w-md mx-4 shadow-2xl p-6">
        <div class="flex items-center gap-4 mb-4">
          <div class="w-12 h-12 rounded-full bg-red-100 flex items-center justify-center">
            <svg class="w-6 h-6 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <div>
            <h3 class="text-lg font-semibold text-slate-800">删除文档</h3>
            <p class="text-sm text-slate-500">确定要删除"{{ documentToDelete.title }}"吗？此操作不可恢复。</p>
          </div>
        </div>
        <div class="flex justify-end gap-3">
          <button
            @click="documentToDelete = null"
            class="px-4 py-2 text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmDelete"
            :disabled="isDeleting"
            class="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:bg-red-400 text-white rounded-lg transition-colors"
          >
            {{ isDeleting ? '删除中...' : '删除' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listDocuments, deleteDocument, uploadDocument, type DocumentResponse } from '@/api/knowledge'

const router = useRouter()

const documents = ref<DocumentResponse[]>([])
const isLoading = ref(false)
const searchQuery = ref('')
const showUploadModal = ref(false)
const selectedFile = ref<File | null>(null)
const isDragging = ref(false)
const isUploading = ref(false)
const uploadError = ref('')
const documentToDelete = ref<DocumentResponse | null>(null)
const isDeleting = ref(false)

const fileInputRef = ref<HTMLInputElement | null>(null)

// Filtered documents based on search
const filteredDocuments = computed(() => {
  if (!searchQuery.value.trim()) {
    return documents.value
  }
  const query = searchQuery.value.toLowerCase()
  return documents.value.filter(doc =>
    doc.title.toLowerCase().includes(query)
  )
})

// Load documents
async function loadDocuments() {
  isLoading.value = true
  try {
    documents.value = await listDocuments()
  } catch (error: any) {
    console.error('前端日志：加载文档列表失败', error)
  } finally {
    isLoading.value = false
  }
}

// Handle file selection
function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files[0]) {
    selectedFile.value = input.files[0]
    uploadError.value = ''
  }
}

// Handle drag and drop
function handleDrop(event: DragEvent) {
  isDragging.value = false
  if (event.dataTransfer?.files && event.dataTransfer.files[0]) {
    const file = event.dataTransfer.files[0]
    const allowedTypes = ['.docx', '.xlsx', '.pptx', '.pdf']
    const ext = '.' + file.name.split('.').pop()?.toLowerCase()
    if (allowedTypes.includes(ext)) {
      selectedFile.value = file
      uploadError.value = ''
    } else {
      uploadError.value = `不支持的文件格式。支持：${allowedTypes.join(', ')}`
    }
  }
}

// Handle upload
async function handleUpload() {
  if (!selectedFile.value) return

  isUploading.value = true
  uploadError.value = ''

  try {
    await uploadDocument(selectedFile.value)
    showUploadModal.value = false
    selectedFile.value = null
    await loadDocuments()
  } catch (error: any) {
    uploadError.value = error.response?.data?.error || error.message || '上传失败'
  } finally {
    isUploading.value = false
  }
}

// Handle delete
function handleDelete(doc: DocumentResponse) {
  documentToDelete.value = doc
}

async function confirmDelete() {
  if (!documentToDelete.value) return

  isDeleting.value = true
  try {
    await deleteDocument(documentToDelete.value.id)
    documentToDelete.value = null
    await loadDocuments()
  } catch (error: any) {
    console.error('前端日志：删除文档失败', error)
    alert(error.response?.data?.error || '删除失败')
  } finally {
    isDeleting.value = false
  }
}

// Format file size
function formatFileSize(bytes: number | null): string {
  if (bytes === null || bytes === 0) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

// Format time
function formatTime(isoString: string): string {
  if (!isoString) return '-'
  const dateStr = isoString.endsWith('Z') ? isoString : isoString + 'Z'
  const date = new Date(dateStr)
  const month = date.getMonth() + 1
  const day = date.getDate()
  const hours = date.getHours().toString().padStart(2, '0')
  const minutes = date.getMinutes().toString().padStart(2, '0')
  return `${month}月${day}日 ${hours}:${minutes}`
}

// Get file icon class based on type
function getFileIconClass(fileType: string): string {
  const ext = fileType.toLowerCase()
  if (ext === '.pdf') return 'bg-red-500'
  if (ext === '.docx' || ext === '.doc') return 'bg-blue-500'
  if (ext === '.xlsx' || ext === '.xls') return 'bg-green-500'
  if (ext === '.pptx' || ext === '.ppt') return 'bg-orange-500'
  return 'bg-slate-500'
}

// Go back
function goBack() {
  router.back()
}

onMounted(() => {
  loadDocuments()
})
</script>

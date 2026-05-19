<template>
  <div
    ref="panelRef"
    class="w-full md:max-w-[80vw] flex flex-col bg-white h-full flex-shrink-0 pb-[env(safe-area-inset-bottom)] relative"
    :style="panelStyle"
  >
    <!-- 拖拽调整宽度的手柄 -->
    <div
      class="hidden md:block absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 hover:bg-primary-300 active:bg-primary-400 transition-colors group"
      @mousedown="startResize"
    >
      <div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-0.5 h-8 bg-gray-300 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"></div>
    </div>
    <!-- Header -->
    <div class="flex items-center gap-2 px-4 py-3 border-b border-gray-200 bg-gray-50 flex-shrink-0">
      <!-- 文件图标 -->
      <svg class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      <!-- 文件名 -->
      <span class="text-sm font-medium text-gray-700 truncate flex-1" :title="attachment?.name">
        {{ attachment?.name || '文件预览' }}
      </span>
      <!-- 文件大小 -->
      <span v-if="attachment?.size" class="text-xs text-gray-400 flex-shrink-0">
        {{ formatFileSize(attachment.size) }}
      </span>
      <!-- 下载按钮 -->
      <a
        v-if="attachment?.file_id"
        :href="getFileDownloadUrl(attachment.file_id)"
        download
        class="p-1.5 rounded-lg text-gray-400 hover:text-primary-500 hover:bg-primary-50 transition-colors flex-shrink-0"
        title="下载文件"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
      </a>
      <!-- 关闭按钮 -->
      <button
        @click="emit('close')"
        class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-colors flex-shrink-0"
        title="关闭"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- Preview Content -->
    <div class="flex-1 overflow-auto relative">
      <!-- Loading -->
      <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
        <div class="flex items-center gap-2 text-gray-400">
          <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span class="text-sm">加载中...</span>
        </div>
      </div>

      <!-- Error -->
      <div v-if="error" class="flex flex-col items-center justify-center h-full gap-3 p-6 text-center">
        <svg class="w-12 h-12 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
        <p class="text-sm text-gray-500">{{ error }}</p>
        <a
          v-if="attachment?.file_id"
          :href="getFileDownloadUrl(attachment.file_id)"
          download
          class="inline-flex items-center gap-1.5 px-4 py-2 bg-primary-500 text-white rounded-lg text-sm hover:bg-primary-600 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          下载文件
        </a>
      </div>

      <!-- Image Preview -->
      <div v-if="previewType === 'image' && attachment?.file_id" class="flex items-center justify-center p-4 min-h-full">
        <img
          :src="getFileUrl(attachment.file_id)"
          :alt="attachment.name"
          class="max-w-full max-h-[calc(100vh-120px)] object-contain rounded shadow-sm"
          @load="loading = false"
          @error="handleImageError"
        />
      </div>

      <!-- PDF Preview -->
      <iframe
        v-if="previewType === 'pdf' && attachment?.file_id"
        :src="getFileUrl(attachment.file_id)"
        class="w-full h-full border-0"
        @load="loading = false"
      ></iframe>

      <!-- HTML Preview -->
      <iframe
        v-if="previewType === 'html' && attachment?.file_id"
        :src="getFileUrl(attachment.file_id)"
        class="w-full h-full border-0"
        sandbox="allow-same-origin"
        @load="loading = false"
      ></iframe>

      <!-- Text/Code Preview -->
      <div v-if="previewType === 'text' || previewType === 'markdown'" class="p-4">
        <div
          v-if="previewType === 'markdown'"
          class="markdown-content prose prose-slate max-w-none text-sm"
          v-html="renderedTextContent"
        ></div>
        <pre v-else class="bg-gray-900 text-gray-100 rounded-lg p-4 text-sm overflow-x-auto"><code :class="`hljs language-${textLanguage}`">{{ textContent }}</code></pre>
      </div>

      <!-- DOCX Preview -->
      <div v-if="previewType === 'docx'" ref="docxContainer" class="p-4 min-h-full"></div>

      <!-- Unsupported -->
      <div v-if="previewType === 'unsupported'" class="flex flex-col items-center justify-center h-full gap-4 p-6 text-center">
        <svg class="w-16 h-16 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <div>
          <p class="text-sm font-medium text-gray-600">此文件类型无法预览</p>
          <p class="text-xs text-gray-400 mt-1">{{ attachment?.name }}</p>
        </div>
        <a
          v-if="attachment?.file_id"
          :href="getFileDownloadUrl(attachment.file_id)"
          download
          class="inline-flex items-center gap-1.5 px-4 py-2 bg-primary-500 text-white rounded-lg text-sm hover:bg-primary-600 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          下载文件
        </a>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import type { AttachmentInfo } from '@/types'
import { getFileUrl, getFileDownloadUrl } from '@/api/agent'
import { renderMarkdown } from '@/utils/markdown'

interface Props {
  attachment: AttachmentInfo | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'close'): void
}>()

const loading = ref(false)
const error = ref<string | null>(null)
const textContent = ref('')
const textLanguage = ref('plaintext')
const docxContainer = ref<HTMLDivElement | null>(null)
const panelRef = ref<HTMLDivElement | null>(null)
const panelWidth = ref(480)

const panelStyle = computed(() => ({
  width: `${panelWidth.value}px`,
  minWidth: '320px',
  borderLeft: '1px solid #e5e7eb',
}))

// 拖拽调整宽度
function startResize(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const startWidth = panelWidth.value

  function onMouseMove(ev: MouseEvent) {
    // 面板在右侧，向左拖 = 变宽，向右拖 = 变窄
    const delta = startX - ev.clientX
    const newWidth = Math.min(Math.max(startWidth + delta, 320), window.innerWidth * 0.8)
    panelWidth.value = newWidth
  }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// 判断预览类型
const previewType = computed(() => {
  if (!props.attachment) return 'unsupported'
  const mime = props.attachment.mime_type || ''
  const name = props.attachment.name || ''
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''

  // 图片
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif'].includes(ext)) return 'image'

  // PDF
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'

  // Markdown
  if (ext === 'md' || ext === 'markdown') return 'markdown'

  // HTML
  if (ext === 'html' || ext === 'htm' || mime === 'text/html') return 'html'

  // 文本/代码
  const textExts = ['txt', 'json', 'csv', 'js', 'ts', 'py', 'vue', 'css', 'xml', 'yaml', 'yml', 'sh', 'bat', 'sql', 'log', 'ini', 'conf', 'md']
  const textMimes = ['text/', 'application/json', 'application/javascript', 'application/xml']
  if (textExts.includes(ext) || textMimes.some(m => mime.startsWith(m))) return 'text'

  // DOCX
  if (mime.includes('wordprocessing') || ext === 'docx') return 'docx'

  return 'unsupported'
})

// Markdown 渲染后的内容
const renderedTextContent = computed(() => {
  if (previewType.value === 'markdown' && textContent.value) {
    return renderMarkdown(textContent.value)
  }
  return ''
})

// 文本文件语言检测
function detectLanguage(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  const langMap: Record<string, string> = {
    'js': 'javascript', 'ts': 'typescript', 'py': 'python', 'vue': 'html',
    'html': 'html', 'css': 'css', 'json': 'json', 'xml': 'xml',
    'yaml': 'yaml', 'yml': 'yaml', 'sh': 'bash', 'bat': 'bat',
    'sql': 'sql', 'md': 'markdown', 'csv': 'plaintext', 'txt': 'plaintext',
    'log': 'log', 'ini': 'ini', 'conf': 'nginx'
  }
  return langMap[ext] || 'plaintext'
}

// 加载文本内容
async function loadTextContent() {
  if (!props.attachment?.file_id) return
  if (previewType.value !== 'text' && previewType.value !== 'markdown') return

  loading.value = true
  error.value = null

  try {
    const url = getFileUrl(props.attachment.file_id)
    const response = await fetch(url)
    if (!response.ok) throw new Error('文件加载失败')
    textContent.value = await response.text()
    textLanguage.value = detectLanguage(props.attachment.name)
  } catch (e) {
    error.value = '文件内容加载失败，请尝试下载查看'
  } finally {
    loading.value = false
  }
}

// 加载 DOCX
async function loadDocx() {
  if (!props.attachment?.file_id) return
  if (previewType.value !== 'docx') return

  loading.value = true
  error.value = null

  try {
    const [{ renderAsync }, docxBlob] = await Promise.all([
      import('docx-preview'),
      fetch(getFileUrl(props.attachment.file_id)).then(r => {
        if (!r.ok) throw new Error('文件加载失败')
        return r.blob()
      })
    ])

    await nextTick()
    if (docxContainer.value) {
      await renderAsync(docxBlob, docxContainer.value, undefined, {
        className: 'docx-preview-wrapper',
        inWrapper: true,
        ignoreWidth: false,
        ignoreHeight: true,
        ignoreFonts: false,
        breakPages: true,
      })
    }
  } catch (e) {
    error.value = '文档预览加载失败，请尝试下载查看'
  } finally {
    loading.value = false
  }
}

// 图片加载错误
function handleImageError() {
  loading.value = false
  error.value = '图片加载失败，文件可能已过期'
}

// 监听附件变化
watch(() => props.attachment, (newAtt) => {
  // 重置状态
  textContent.value = ''
  error.value = null

  if (!newAtt) return

  // 图片、PDF、HTML 设置 loading
  if (previewType.value === 'image' || previewType.value === 'pdf' || previewType.value === 'html') {
    loading.value = true
  }

  // 文本文件需要 fetch 内容
  if (previewType.value === 'text' || previewType.value === 'markdown') {
    loadTextContent()
  }

  // DOCX 需要 fetch 并渲染
  if (previewType.value === 'docx') {
    loadDocx()
  }
}, { immediate: true })

// ESC 关闭
function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    emit('close')
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
})

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
</script>

<style scoped>
:deep(.docx-preview-wrapper) {
  padding: 0;
}
:deep(.docx-preview-wrapper .docx-wrapper) {
  background: white;
  padding: 0;
}
</style>

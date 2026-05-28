<template>
  <!-- 可预览文件：点击打开预览面板 -->
  <button
    v-if="isPreviewable"
    class="inline-flex items-center gap-3 p-3 rounded-lg border border-gray-200 bg-gray-50
           hover:bg-info-50 hover:border-blue-300 transition-colors cursor-pointer group
           w-full md:w-auto md:min-w-[240px] md:max-w-[320px]"
    @click="handleClick"
  >
    <div
      class="w-9 h-9 rounded flex items-center justify-center flex-shrink-0"
      :class="iconBgClass"
    >
      <span class="text-lg">{{ iconEmoji }}</span>
    </div>

    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium text-gray-800 truncate group-hover:text-info-700">
        {{ file.file_name }}
      </div>
      <div class="text-xs text-gray-400 mt-0.5">
        {{ formatSize(file.file_size) }}
        <span v-if="fileTypeLabel"> · {{ fileTypeLabel }}</span>
      </div>
    </div>

    <div class="flex-shrink-0 text-gray-400 group-hover:text-info-600 transition-colors">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
      </svg>
    </div>
  </button>

  <!-- 不可预览文件：点击下载 -->
  <button
    v-else
    class="inline-flex items-center gap-3 p-3 rounded-lg border border-gray-200 bg-gray-50
           hover:bg-info-50 hover:border-blue-300 transition-colors cursor-pointer group
           w-full md:w-auto md:min-w-[240px] md:max-w-[320px]"
    :disabled="downloading"
    @click="handleDownload"
  >
    <div
      class="w-9 h-9 rounded flex items-center justify-center flex-shrink-0"
      :class="iconBgClass"
    >
      <span class="text-lg">{{ iconEmoji }}</span>
    </div>

    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium text-gray-800 truncate group-hover:text-info-700">
        {{ file.file_name }}
      </div>
      <div class="text-xs text-gray-400 mt-0.5">
        {{ formatSize(file.file_size) }}
        <span v-if="fileTypeLabel"> · {{ fileTypeLabel }}</span>
      </div>
    </div>

    <div class="flex-shrink-0 text-gray-400 group-hover:text-info-600 transition-colors">
      <svg v-if="!downloading" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586
                 a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
      <svg v-else class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    </div>
  </button>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { DownloadableFile, AttachmentInfo } from '@/types'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'

const props = defineProps<{ file: DownloadableFile }>()

const { openPreview } = useAttachmentPreview()

const downloading = ref(false)

async function handleDownload() {
  if (downloading.value) return
  downloading.value = true
  try {
    const url = props.file.download_url.startsWith('/')
      ? `${window.location.origin}${props.file.download_url}`
      : props.file.download_url
    const response = await fetch(url)
    if (!response.ok) throw new Error(`下载失败: ${response.status}`)
    const blob = await response.blob()
    const blobUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = props.file.file_name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
  } catch (e) {
    console.error('文件下载失败:', e)
    // fallback: 在新窗口打开下载链接
    window.open(props.file.download_url, '_blank')
  } finally {
    downloading.value = false
  }
}

const ext = computed(() => {
  const name = props.file.file_name.toLowerCase()
  return name.includes('.') ? name.split('.').pop()! : ''
})

const mime = computed(() => props.file.mime_type || '')

// 判断是否可预览（HTML、PDF、图片、Markdown）
const isPreviewable = computed(() => {
  if (['html', 'htm'].includes(ext.value) || mime.value === 'text/html') return true
  if (ext.value === 'pdf' || mime.value === 'application/pdf') return true
  if (mime.value.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif'].includes(ext.value)) return true
  if (['md', 'markdown'].includes(ext.value)) return true
  return false
})

function handleClick() {
  // 将 DownloadableFile 转换为 AttachmentInfo 格式，打开预览面板
  const attachment: AttachmentInfo = {
    file_id: props.file.file_id,
    name: props.file.file_name,
    size: props.file.file_size,
    mime_type: props.file.mime_type || '',
    type: mime.value.startsWith('image/') ? 'image' : 'file',
  }
  openPreview(attachment)
}

const iconEmoji = computed(() => {
  const name = props.file.file_name.toLowerCase()
  if (mime.value.startsWith('image/')) return '🖼️'
  if (['html', 'htm'].includes(ext.value)) return '🌐'
  if (name.endsWith('.pdf')) return '📕'
  if (name.endsWith('.doc') || name.endsWith('.docx')) return '📘'
  if (name.endsWith('.xls') || name.endsWith('.xlsx')) return '📊'
  if (name.endsWith('.ppt') || name.endsWith('.pptx')) return '📙'
  if (name.endsWith('.zip')) return '🗜️'
  if (name.endsWith('.txt') || name.endsWith('.csv')) return '📄'
  return '📄'
})

const iconBgClass = computed(() => {
  const name = props.file.file_name.toLowerCase()
  if (['html', 'htm'].includes(ext.value)) return 'bg-orange-100'
  if (name.endsWith('.pdf')) return 'bg-danger-100'
  if (name.endsWith('.doc') || name.endsWith('.docx')) return 'bg-info-100'
  if (name.endsWith('.xls') || name.endsWith('.xlsx')) return 'bg-success-100'
  if (name.endsWith('.ppt') || name.endsWith('.pptx')) return 'bg-orange-100'
  return 'bg-gray-100'
})

const fileTypeLabel = computed(() => {
  const name = props.file.file_name.toLowerCase()
  if (['html', 'htm'].includes(ext.value)) return 'HTML 文档'
  if (name.endsWith('.pdf')) return 'PDF 文档'
  if (name.endsWith('.doc') || name.endsWith('.docx')) return 'Word 文档'
  if (name.endsWith('.xls') || name.endsWith('.xlsx')) return 'Excel 表格'
  if (name.endsWith('.ppt') || name.endsWith('.pptx')) return 'PPT 演示'
  if (name.endsWith('.zip')) return 'ZIP 压缩包'
  if (name.endsWith('.txt')) return '文本文件'
  if (name.endsWith('.csv')) return 'CSV 文件'
  if (mime.value.startsWith('image/')) return '图片'
  return ''
})

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  const size = (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)
  return `${size} ${units[i]}`
}
</script>

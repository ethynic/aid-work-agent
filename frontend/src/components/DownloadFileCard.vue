<template>
  <a
    :href="file.download_url"
    download
    class="inline-flex items-center gap-3 p-3 rounded-lg border border-gray-200 bg-gray-50
           hover:bg-blue-50 hover:border-blue-300 transition-colors cursor-pointer group no-underline
           w-full md:w-auto md:min-w-[240px] md:max-w-[320px]"
  >
    <div
      class="w-9 h-9 rounded flex items-center justify-center flex-shrink-0"
      :class="iconBgClass"
    >
      <span class="text-lg">{{ iconEmoji }}</span>
    </div>

    <div class="flex-1 min-w-0">
      <div class="text-sm font-medium text-gray-800 truncate group-hover:text-blue-700">
        {{ file.file_name }}
      </div>
      <div class="text-xs text-gray-400 mt-0.5">
        {{ formatSize(file.file_size) }}
        <span v-if="fileTypeLabel"> · {{ fileTypeLabel }}</span>
      </div>
    </div>

    <div class="flex-shrink-0 text-gray-400 group-hover:text-blue-600 transition-colors">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586
                 a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    </div>
  </a>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DownloadableFile } from '@/types'

const props = defineProps<{ file: DownloadableFile }>()

const iconEmoji = computed(() => {
  const mime = props.file.mime_type || ''
  const name = props.file.file_name.toLowerCase()
  if (mime.startsWith('image/')) return '🖼️'
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
  if (name.endsWith('.pdf')) return 'bg-red-100'
  if (name.endsWith('.doc') || name.endsWith('.docx')) return 'bg-blue-100'
  if (name.endsWith('.xls') || name.endsWith('.xlsx')) return 'bg-green-100'
  if (name.endsWith('.ppt') || name.endsWith('.pptx')) return 'bg-orange-100'
  return 'bg-gray-100'
})

const fileTypeLabel = computed(() => {
  const name = props.file.file_name.toLowerCase()
  if (name.endsWith('.pdf')) return 'PDF 文档'
  if (name.endsWith('.doc') || name.endsWith('.docx')) return 'Word 文档'
  if (name.endsWith('.xls') || name.endsWith('.xlsx')) return 'Excel 表格'
  if (name.endsWith('.ppt') || name.endsWith('.pptx')) return 'PPT 演示'
  if (name.endsWith('.zip')) return 'ZIP 压缩包'
  if (name.endsWith('.txt')) return '文本文件'
  if (name.endsWith('.csv')) return 'CSV 文件'
  const mime = props.file.mime_type || ''
  if (mime.startsWith('image/')) return '图片'
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

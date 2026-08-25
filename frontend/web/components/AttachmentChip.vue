<template>
  <button
    class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all cursor-pointer group"
    :class="[
      clickable
        ? 'bg-white border-gray-200 hover:border-primary-300 hover:bg-primary-50 hover:shadow-sm'
        : 'bg-gray-50 border-gray-200 cursor-default'
    ]"
    @click="clickable && emit('preview')"
  >
    <!-- 文件类型图标 -->
    <svg v-if="fileCategory === 'image'" class="w-4 h-4 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
    </svg>
    <svg v-else-if="fileCategory === 'pdf'" class="w-4 h-4 text-danger-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
    </svg>
    <svg v-else-if="fileCategory === 'doc' || fileCategory === 'xls'" class="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
    <svg v-else-if="fileCategory === 'code'" class="w-4 h-4 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
    </svg>
    <svg v-else class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>

    <!-- 文件名 -->
    <span class="text-sm text-gray-600 max-w-[200px] truncate group-hover:text-primary-600">
      {{ attachment.name }}
    </span>

    <!-- 文件大小 -->
    <span v-if="'size' in attachment && attachment.size" class="text-xs text-gray-400 flex-shrink-0">
      {{ formatFileSize(attachment.size) }}
    </span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AttachmentInfo } from '@/types'
import { formatFileSize } from '@/utils/file'

interface Props {
  attachment: AttachmentInfo | { name: string }
  clickable?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  clickable: true
})

const emit = defineEmits<{
  (e: 'preview'): void
}>()

const fileCategory = computed(() => {
  const att = props.attachment as AttachmentInfo
  if (!att.mime_type && !att.name) return 'other'

  const mime = att.mime_type || ''
  const name = att.name || ''
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''

  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif'].includes(ext)) return 'image'
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'
  if (mime.includes('wordprocessing') || ['doc', 'docx'].includes(ext)) return 'doc'
  if (mime.includes('spreadsheet') || ['xls', 'xlsx'].includes(ext)) return 'xls'
  if (['txt', 'md', 'json', 'csv', 'js', 'ts', 'py', 'vue', 'html', 'css', 'xml', 'yaml', 'yml', 'sh', 'bat', 'sql', 'log'].includes(ext)) return 'code'

  return 'other'
})
</script>

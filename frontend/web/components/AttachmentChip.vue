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
    <!-- 文件类型图标：品牌色徽标，各类型独立可辨（word/excel/ppt/pdf/图片/md/txt…） -->
    <FileTypeIcon :kind="iconKind" class="w-4 h-4 flex-shrink-0" />

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
import { formatFileSize, detectFileIconKind, type FileIconKind } from '@/utils/file'
import FileTypeIcon from './FileTypeIcon.vue'

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

const iconKind = computed<FileIconKind>(() => {
  const att = props.attachment as AttachmentInfo
  return detectFileIconKind(att.mime_type || '', att.name || '')
})
</script>

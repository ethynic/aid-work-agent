<template>
  <div class="reply-content">
    <ImageGallery v-if="beforeImages.length" :images="beforeImages" />
    <div v-if="message.cancelled" class="mb-1">
      <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-warning-100 text-warning-700">用户已取消本轮回复</span>
    </div>
    <div v-if="message.content || !message.cancelled"
      class="text-gray-700 leading-relaxed markdown-content prose-sm md:prose-base prose-slate max-w-none"
      v-html="renderedContent"></div>
    <p v-if="message.waitingNotice" class="mt-2 text-sm text-muted" role="status">{{ message.waitingNotice }}</p>
    <ImageGallery v-if="afterImages.length" :images="afterImages" />
    <ImageGallery v-if="inlineImages.length" :images="inlineImages" />
    <div v-if="attachments.length || legacyAttachments.length" class="mt-2 flex flex-wrap gap-2">
      <AttachmentChip v-for="att in attachments" :key="att.file_id" :attachment="att" @preview="openPreview(att)" />
      <AttachmentChip v-for="(att, index) in legacyAttachments" :key="'legacy-' + index" :attachment="att" :clickable="false" />
    </div>
    <div v-if="files.length" class="mt-3 flex flex-wrap gap-2">
      <DownloadFileCard v-for="file in files" :key="file.file_id" :file="file" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ChatMessage } from '@/types'
import ImageGallery from '@/components/ui/ImageGallery.vue'
import AttachmentChip from '@/components/AttachmentChip.vue'
import DownloadFileCard from '@/components/DownloadFileCard.vue'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { renderMarkdown } from '@/utils/markdown'
import { replyFileId, useReplyResources } from '@/utils/replyResources'

const props = withDefaults(defineProps<{ message: ChatMessage; isProcessing?: boolean }>(), { isProcessing: false })
const { openPreview } = useAttachmentPreview()
const { resolver } = useReplyResources()
const content = computed(() => props.message.content
  .replace(/<!--process-->[\s\S]*?<!--\/process-->\n?/g, '')
  .replace(/\n\n\[附件:.*?\]$/s, '')
  .replace(/\n\n【已上传文件路径】[\s\S]*?请使用上述路径读取文件内容。/, ''))
const renderedContent = computed(() => renderMarkdown(content.value, resolver))
const images = computed(() => (props.message.images || []).map(image => {
  const fileId = replyFileId(image.download_url || '')
  return resolver && fileId ? { ...image, download_url: resolver(fileId) } : image
}))
const beforeImages = computed(() => images.value.filter(image => image.placement === 'before_text'))
const afterImages = computed(() => images.value.filter(image => !image.placement || image.placement === 'after_text'))
const inlineImages = computed(() => images.value.filter(image => image.placement === 'inline'))
const attachments = computed(() => props.message.attachments || [])
const legacyAttachments = computed(() => {
  if (attachments.value.length) return []
  const match = props.message.content.match(/\[附件:\s*(.*?)\]/)
  return match ? match[1].split(',').map(name => ({ name: name.trim() })).filter(item => item.name) : []
})
const files = computed(() => (!props.isProcessing || content.value.trim()) ? (props.message.downloadableFiles || []) : [])
</script>

<style scoped>
.markdown-content :deep(> :first-child) { margin-top: 0; }
.markdown-content :deep(> :last-child) { margin-bottom: 0; }
</style>

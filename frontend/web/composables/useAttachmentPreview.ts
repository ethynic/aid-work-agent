import { ref, computed } from 'vue'
import type { AttachmentInfo } from '@/types'

// 模块级单例状态（所有组件共享同一实例）
const previewAttachment = ref<AttachmentInfo | null>(null)
const isPreviewOpen = computed(() => previewAttachment.value !== null)

export function useAttachmentPreview() {
  function openPreview(attachment: AttachmentInfo) {
    previewAttachment.value = attachment
  }

  function closePreview() {
    previewAttachment.value = null
  }

  return {
    previewAttachment,
    isPreviewOpen,
    openPreview,
    closePreview,
  }
}

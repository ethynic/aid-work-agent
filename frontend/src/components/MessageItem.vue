<template>
  <!-- 用户消息：右对齐，左侧留缩进，无头像 -->
  <div
    v-if="message.role === 'user'"
    class="message-enter-active message-user-wrapper"
  >
    <div class="message-user-bubble bg-primary-600">
      <div class="message-user-content text-white">
        <div class="leading-relaxed" v-html="renderedContent"></div>

        <!-- 附件标签 -->
        <div v-if="displayAttachments.length > 0 || legacyAttachments.length > 0" class="mt-2 flex flex-wrap gap-2">
          <AttachmentChip
            v-for="att in displayAttachments"
            :key="att.file_id"
            :attachment="att"
            @preview="handlePreview(att)"
          />
          <AttachmentChip
            v-for="(att, idx) in legacyAttachments"
            :key="'legacy-' + idx"
            :attachment="att"
            :clickable="false"
          />
        </div>
      </div>
    </div>
  </div>

  <!-- AI消息：全宽，无头像，无缩进 -->
  <div
    v-else
    class="message-enter-active message-ai-wrapper"
  >
    <div class="message-ai-content">
      <div
        class="text-gray-700 leading-relaxed markdown-content prose-sm md:prose-base prose-slate max-w-none"
        v-html="renderedContent"
      ></div>

      <!-- 附件标签 -->
      <div v-if="displayAttachments.length > 0 || legacyAttachments.length > 0" class="mt-2 flex flex-wrap gap-2">
        <AttachmentChip
          v-for="att in displayAttachments"
          :key="att.file_id"
          :attachment="att"
          @preview="handlePreview(att)"
        />
        <AttachmentChip
          v-for="(att, idx) in legacyAttachments"
          :key="'legacy-' + idx"
          :attachment="att"
          :clickable="false"
        />
      </div>

      <!-- 下载文件卡片 -->
      <div v-if="downloadableFiles.length > 0" class="mt-3 flex flex-wrap gap-2">
        <DownloadFileCard
          v-for="file in downloadableFiles"
          :key="file.file_id"
          :file="file"
        />
      </div>

      <!-- 执行详情 -->
      <div v-if="hasProgress" class="mt-2">
        <button
          @click="toggleExpanded"
          class="flex items-center gap-1 p-2 -m-2 min-w-[44px] min-h-[44px] text-xs text-gray-400 hover:text-gray-600 transition-colors"
        >
          <svg
            :class="['w-3 h-3 transition-transform', isExpanded ? 'rotate-90' : '']"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
          <span>{{ isExpanded ? '收起' : '展开' }}执行详情 {{ totalCount }}条</span>
        </button>

        <div
          :class="[
            'mt-1 overflow-hidden transition-all',
            isExpanded ? 'max-h-none' : 'max-h-[96px]'
          ]"
        >
          <div class="space-y-0.5">
            <div
              v-for="(msg, index) in displayMessages"
              :key="index"
              :class="[
                'text-xs py-1 px-2 rounded text-gray-500',
                getProgressClass(msg.type)
              ]"
            >
              <span class="mr-1">{{ getProgressIcon(msg.type) }}</span>
              <span class="opacity-80">{{ formatProgressContent(msg) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="isProcessing" class="flex items-center gap-2 mt-2">
        <span class="text-xs text-primary-400 animate-pulse">
          生成中...
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ChatMessage, AttachmentInfo, DownloadableFile, ProgressMessage } from '@/types'
import AttachmentChip from './AttachmentChip.vue'
import DownloadFileCard from './DownloadFileCard.vue'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { renderMarkdown } from '@/utils/markdown'

interface Props {
  message: ChatMessage
  isProcessing?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isProcessing: false
})

const { openPreview } = useAttachmentPreview()

const isExpanded = ref(false)

const hasProgress = computed(() => {
  return props.message.progressMessages && props.message.progressMessages.length > 0
})
const totalCount = computed(() => props.message.progressMessages?.length || 0)
const displayMessages = computed(() => {
  if (!props.message.progressMessages) return []
  if (isExpanded.value) return props.message.progressMessages
  return props.message.progressMessages.slice(0, 5)
})

function toggleExpanded() {
  isExpanded.value = !isExpanded.value
}

function getProgressClass(type: string): string {
  switch (type) {
    case 'error': return 'bg-danger-50 text-danger-600'
    case 'complete': return 'bg-success-50 text-success-600'
    case 'thinking': return 'bg-primary-50 text-primary-600'
    case 'tool_start': return 'bg-info-50 text-info-600'
    case 'tool_result': return 'bg-primary-50 text-primary-600'
    default: return 'bg-gray-100 text-gray-500'
  }
}

function getProgressIcon(type: string): string {
  switch (type) {
    case 'error': return '❌'
    case 'complete': return '✅'
    case 'thinking': return '🤔'
    case 'tool_start': return '🔧'
    case 'tool_result': return '📤'
    default: return '🔄'
  }
}

function formatProgressContent(msg: ProgressMessage): string {
  if (msg.type === 'tool_start' && msg.toolName) {
    return `🔧 需要调用工具【${msg.toolName}】`
  }
  if (msg.type === 'tool_result' && msg.toolName) {
    const success = msg.success !== false
    return success ? `✅ ${msg.toolName}执行完成` : `❌ ${msg.toolName}执行失败`
  }

  const cleaned = msg.content.replace(/[\u{1F300}-\u{1F9FF}]/gu, '').trim()
  return cleaned.length > 100 ? cleaned.slice(0, 100) + '...' : cleaned
}

const displayAttachments = computed<AttachmentInfo[]>(() => {
  if (props.message.attachments && props.message.attachments.length > 0) {
    return props.message.attachments
  }
  return []
})

const legacyAttachments = computed<{ name: string }[]>(() => {
  if (displayAttachments.value.length > 0) return []
  const match = props.message.content.match(/\[附件:\s*(.*?)\]/)
  if (!match) return []
  return match[1].split(',').map(name => ({ name: name.trim() })).filter(a => a.name)
})

const downloadableFiles = computed<DownloadableFile[]>(() => {
  return props.message.downloadableFiles || []
})

const displayContent = computed(() => {
  let content = props.message.content
  content = content.replace(/<!--process-->[\s\S]*?<!--\/process-->\n?/g, '')
  content = content.replace(/\n\n\[附件:.*?\]$/s, '')
  content = content.replace(/\n\n【已上传文件路径】[\s\S]*?请使用上述路径读取文件内容。/, '')
  return content
})

const renderedContent = computed(() => {
  return renderMarkdown(displayContent.value)
})

function handlePreview(attachment: AttachmentInfo) {
  openPreview(attachment)
}
</script>

<style scoped>
/* 用户消息：右对齐气泡 */
.message-user-wrapper {
  display: flex;
  justify-content: flex-end;
  padding-left: 48px;
}

.message-user-bubble {
  max-width: 85%;
  border: none;
  border-radius: 16px 16px 4px 16px;
  padding: 10px 14px;
  transition: all 0.2s;
}

.message-user-content {
  word-break: break-word;
}

/* AI消息：全宽，无缩进 */
.message-ai-wrapper {
  width: 100%;
}

.message-ai-content {
  background-color: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px 16px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

/* 桌面端适配 */
@media (min-width: 768px) {
  .message-user-wrapper {
    padding-left: 96px;
  }

  .message-user-bubble {
    max-width: 70%;
    padding: 12px 16px;
  }

  .message-ai-content {
    padding: 14px 20px;
  }
}
</style>

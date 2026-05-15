<template>
  <div
    :class="[
      'flex gap-2 md:gap-3 p-3 md:p-4 rounded-2xl transition-all message-enter-active',
      message.role === 'user'
        ? 'bg-primary-50 border border-primary-200 md:ml-12'
        : 'bg-white border border-gray-200 shadow-message'
    ]"
  >
    <!-- Avatar -->
    <div
      :class="[
        'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0',
        message.role === 'user'
          ? 'bg-gradient-to-br from-primary-400 to-primary-600'
          : 'bg-gradient-to-br from-gray-400 to-gray-500'
      ]"
    >
      <svg v-if="message.role === 'user'" class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
      </svg>
      <svg v-else class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    </div>

    <!-- Content -->
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2 mb-1">
        <span class="text-sm font-medium text-gray-700">
          {{ message.role === 'user' ? '你' : 'AI助手' }}
        </span>
        <span v-if="timestamp" class="text-xs text-gray-400">
          {{ formatTime(timestamp) }}
        </span>
        <span v-if="isProcessing" class="text-xs text-primary-400 animate-pulse">
          生成中...
        </span>
      </div>

      <!-- Message Content (Markdown) -->
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

      <!-- 下载文件卡片（仅助手消息显示，在执行详情上方） -->
      <div
        v-if="message.role === 'assistant' && downloadableFiles.length > 0"
        class="mt-3 flex flex-wrap gap-2"
      >
        <DownloadFileCard
          v-for="file in downloadableFiles"
          :key="file.file_id"
          :file="file"
        />
      </div>

      <!-- 执行详情（仅 AI 回复显示） -->
      <div v-if="message.role === 'assistant' && hasProgress" class="mt-2">
        <!-- 展开/折叠按钮 -->
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

        <!-- 执行详情内容 -->
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

const timestamp = computed(() => props.message.timestamp)
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
  // 扁平格式：顶层 type 就是 tool_start/tool_result
  if (msg.type === 'tool_start' && msg.toolName) {
    return `🔧 需要调用工具【${msg.toolName}】`
  }
  if (msg.type === 'tool_result' && msg.toolName) {
    const success = msg.success !== false
    return success ? `✅ ${msg.toolName}执行完成` : `❌ ${msg.toolName}执行失败`
  }

  // content 始终为 string（ProgressMessage 类型定义），移除 emoji 并截断
  const cleaned = msg.content.replace(/[\u{1F300}-\u{1F9FF}]/gu, '').trim()
  return cleaned.length > 100 ? cleaned.slice(0, 100) + '...' : cleaned
}

// 结构化附件列表（来自 attachments 字段）
const displayAttachments = computed<AttachmentInfo[]>(() => {
  if (props.message.attachments && props.message.attachments.length > 0) {
    return props.message.attachments
  }
  return []
})

// 旧消息兼容：从内容中解析 [附件: ...] 文本
const legacyAttachments = computed<{ name: string }[]>(() => {
  if (displayAttachments.value.length > 0) return []
  const match = props.message.content.match(/\[附件:\s*(.*?)\]/)
  if (!match) return []
  return match[1].split(',').map(name => ({ name: name.trim() })).filter(a => a.name)
})

// 可下载文件列表（LLM 生成的文件）
const downloadableFiles = computed<DownloadableFile[]>(() => {
  return props.message.downloadableFiles || []
})

// 用于渲染的内容（移除附件标注文本和文件路径上下文，避免重复显示）
const displayContent = computed(() => {
  let content = props.message.content
  // 移除末尾的 [附件: ...] 标注
  content = content.replace(/\n\n\[附件:.*?\]$/s, '')
  // 移除后端追加的文件路径上下文（供 LLM 使用的，不需要展示给用户）
  content = content.replace(/\n\n【已上传文件路径】[\s\S]*?请使用上述路径读取文件内容。/, '')
  return content
})

const renderedContent = computed(() => {
  // 使用 marked 渲染 Markdown，支持标题、表格、粗体、斜体、代码块、列表等
  return renderMarkdown(displayContent.value)
})

function handlePreview(attachment: AttachmentInfo) {
  openPreview(attachment)
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

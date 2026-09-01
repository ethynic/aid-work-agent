<template>
  <!-- 用户消息：右对齐，左侧留缩进，无头像 -->
  <div
    v-if="message.role === 'user'"
    class="message-enter-active message-user-wrapper"
  >
    <div class="flex flex-col items-end w-full">
      <div class="message-user-bubble bg-primary-600 w-fit">
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
      <!-- 时间戳 - 用户消息右下 -->
      <div v-if="formattedTime" class="text-xs text-muted mt-1 mr-2">
        {{ formattedTime }}
      </div>
    </div>
  </div>

  <!-- AI消息：全宽，无头像，无缩进 -->
  <div
    v-else
    class="message-enter-active message-ai-wrapper"
  >
    <div class="message-ai-content">
      <!-- AI 正在输入提示（白框内无内容时显示）；
           verbose 中间提示（Phase 2）原地替换占位文案，不新增聊天气泡 -->
      <div v-if="showInputHint" class="flex items-center gap-2">
        <span class="inline-block w-1.5 h-1.5 rounded-full bg-primary-400 animate-pulse"></span>
        <span class="text-xs text-muted verbose-hint" aria-live="polite">{{ liveHintText }}</span>
      </div>

      <!-- before_text 图片：文本上方（Phase 2 P2.7） -->
      <ImageGallery
        v-if="!showInputHint && beforeTextImages.length"
        :images="beforeTextImages"
      />

      <div
        v-if="!showInputHint"
        class="text-gray-700 leading-relaxed markdown-content prose-sm md:prose-base prose-slate max-w-none"
        v-html="renderedContent"
      ></div>

      <!-- after_text 图片：文本下方（默认位置，Phase 2 P2.7） -->
      <ImageGallery
        v-if="!showInputHint && afterTextImages.length"
        :images="afterTextImages"
      />

      <!-- inline 图片：Phase 2 仍按 after_text 渲染（精确行内留 Phase 3） -->
      <ImageGallery
        v-if="!showInputHint && inlineImages.length"
        :images="inlineImages"
      />

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

      <HumanAssistanceCard
        v-if="message.browserAssistance"
        :assistance="message.browserAssistance"
        :auth-headers="authHeaders"
        @updated="updateAssistance"
        @continuation="handleContinuation"
      />

      <!-- 编号选择按钮（设计 §5.1 选择交互）：点击即发送序号，与手动回复数字等效；
           点击一次后整组置灰（选择已发出，避免重复回复） -->
      <div v-if="quickOptions.length" class="mt-3 flex flex-wrap gap-2">
        <button
          v-for="(option, i) in quickOptions"
          :key="option.key"
          type="button"
          :disabled="optionClicked || isProcessing"
          :title="option.description"
          class="px-3 py-1.5 bg-white border border-gray-200 rounded-full text-sm text-gray-600 hover:border-primary-300 hover:text-primary-600 transition-all disabled:opacity-50"
          @click="handleOptionClick(i)"
        >
          {{ i + 1 }}. {{ option.label }}
        </button>
      </div>

      <!-- 执行详情（仅 debug 模式显示） -->
      <div v-if="isDebugEnabled && hasProgress" class="mt-2">
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
                getProgressClass(msg)
              ]"
            >
              <span class="mr-1">{{ getProgressIcon(msg) }}</span>
              <span class="opacity-80">{{ formatProgressContent(msg) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
    <!-- 时间戳 - AI消息左下 -->
    <div v-if="formattedTime && !showInputHint" class="text-xs text-muted mt-1 ml-2">
      {{ formattedTime }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ChatMessage, AttachmentInfo, DownloadableFile, InputHintState, ProgressMessage, QuickOption } from '@/types'
import AttachmentChip from './AttachmentChip.vue'
import DownloadFileCard from './DownloadFileCard.vue'
import ImageGallery from './ui/ImageGallery.vue'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { renderMarkdown } from '@/utils/markdown'
import { useDebugMode } from '@/composables/useDebugMode'
import HumanAssistanceCard from './browser/HumanAssistanceCard.vue'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAgent } from '@/composables/useAgent'

interface Props {
  message: ChatMessage
  isProcessing?: boolean
  inputHintState?: InputHintState
}

const props = withDefaults(defineProps<Props>(), {
  isProcessing: false,
  inputHintState: 'idle'
})

const emit = defineEmits<{
  (e: 'send', content: string): void
}>()

// 编号选择按钮（§5.1）：仅 assistant 消息且 ≥2 项时渲染；点击后本地置灰（不持久化）
const quickOptions = computed<QuickOption[]>(() =>
  props.message.role === 'assistant' ? (props.message.quickOptions || []) : []
)
const optionClicked = ref(false)

function handleOptionClick(index: number) {
  if (optionClicked.value) return
  // 流式进行中（工具结果先于最终回复到达）时发送会被会话 guard 拦截，
  // 此时忽略点击不置灰，等回复完成后再选
  if (props.isProcessing) return
  optionClicked.value = true
  // 序号即消息内容，agent 按列表顺序解析（与用户手动回复数字完全同链路）
  emit('send', String(index + 1))
}

const { openPreview } = useAttachmentPreview()
const { isDebugEnabled } = useDebugMode()
const authHeaders = computed(() => window.location.pathname.startsWith('/t/')
  ? useTenantAuth().getAuthHeader()
  : useDemoAuth().getAuthHeader())

function updateAssistance(assistance: any) {
  props.message.browserAssistance = assistance
}

function handleContinuation(events: any[]) {
  const responseText = events
    .filter(event => event.type === 'response' && typeof event.data === 'string')
    .map(event => event.data)
    .join('')
  if (responseText) props.message.content += responseText
  const nextAssistance = events.find(event => event.type === 'browser_human_required')
  if (nextAssistance) {
    props.message.browserAssistance = { ...nextAssistance, state: 'pending' }
    return
  }
  const terminal = events.find(event =>
    event.type === 'agent_continuation_completed' || event.type === 'browser_run_closed'
  )
  if (!terminal || !props.message.browserAssistance) return
  props.message.browserAssistance.state = terminal.type === 'browser_run_closed'
    ? 'failed'
    : 'resumed'
}

const isExpanded = ref(false)

const showInputHint = computed(() => {
  return (
    props.message.role === 'assistant' &&
    props.isProcessing &&
    !props.message.content &&
    (props.inputHintState === 'thinking' || props.inputHintState === 'working')
  )
})

// verbose 中间提示（Phase 2，设计 §8.2）：assistant 正文为空且处理中时，
// 用当前会话的 live verbose 文案替换「对方正在输入中...」占位（同一占位区，
// 不新增聊天气泡）；response 开始后 showInputHint 变 false 自动隐藏。
// 历史 metadata 中的 verboseMessages 不在此展示（默认不在已完成消息下展开）。
const { liveVerbose } = useAgent()
const liveHintText = computed(() => liveVerbose.value?.data || '对方正在输入中...')

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

function getProgressClass(msg: ProgressMessage): string {
  if (msg.type === 'tool_result' && msg.success === false) {
    return 'bg-danger-50 text-danger-600'
  }
  switch (msg.type) {
    case 'error': return 'bg-danger-50 text-danger-600'
    case 'complete': return 'bg-success-50 text-success-600'
    case 'thinking': return 'bg-primary-50 text-primary-600'
    case 'tool_start': return 'bg-info-50 text-info-600'
    case 'tool_result': return 'bg-primary-50 text-primary-600'
    default: return 'bg-gray-100 text-gray-500'
  }
}

function getProgressIcon(msg: ProgressMessage): string {
  if (msg.type === 'tool_result' && msg.success === false) {
    return '❌'
  }
  switch (msg.type) {
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
    return `🔧 需要调用工具【${msg.displayName || msg.toolName}】`
  }
  if (msg.type === 'tool_result' && msg.toolName) {
    const success = msg.success !== false
    const name = msg.displayName || msg.toolName
    const error = typeof msg.result?.error === 'string' ? msg.result.error : ''
    return success
      ? `✅ ${name}执行完成`
      : `❌ ${name}执行失败${error ? `: ${error}` : ''}`
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

const displayContent = computed(() => {
  let content = props.message.content
  content = content.replace(/<!--process-->[\s\S]*?<!--\/process-->\n?/g, '')
  content = content.replace(/\n\n\[附件:.*?\]$/s, '')
  content = content.replace(/\n\n【已上传文件路径】[\s\S]*?请使用上述路径读取文件内容。/, '')
  return content
})

const downloadableFiles = computed<DownloadableFile[]>(() => {
  // 与「正在输入中」用同一套判断逻辑：
  // 仅当消息不在处理中，或 response 已开始流式（content 非空）时才展示下载卡片
  // 避免工具执行阶段提前展示中间产物
  const files = props.message.downloadableFiles || []
  if (files.length === 0) return []
  const hasResponse = displayContent.value.trim().length > 0
  return (!props.isProcessing || hasResponse) ? files : []
})

const renderedContent = computed(() => {
  return renderMarkdown(displayContent.value)
})

// Phase 2 P2.7：按 placement 分组图片
// 历史消息兼容：旧消息无 placement 字段时按 after_text 处理（默认值）
const beforeTextImages = computed(() =>
  (props.message.images || []).filter(img => img.placement === 'before_text')
)
const afterTextImages = computed(() =>
  (props.message.images || []).filter(img => !img.placement || img.placement === 'after_text')
)
const inlineImages = computed(() =>
  (props.message.images || []).filter(img => img.placement === 'inline')
)

const formattedTime = computed<string | null>(() => {
  if (!props.message.timestamp) return null
  const date = new Date(props.message.timestamp)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  const seconds = String(date.getSeconds()).padStart(2, '0')
  return `${month}-${day} ${hours}:${minutes}:${seconds}`
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

/* verbose 中间提示最多两行，超出省略（设计 §8.2） */
.verbose-hint {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
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

<template>
  <!-- 用户消息：右对齐，左侧留缩进，无头像 -->
  <div
    v-if="message.role === 'user'"
    class="message-enter-active message-user-wrapper"
  >
    <div class="flex flex-col items-end w-full">
      <div class="flex w-full items-center justify-end gap-1">
        <div class="message-user-bubble bg-primary-600 w-fit min-w-0">
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
        <div v-if="submitFailure || message.runnerFailure" class="flex shrink-0 items-center" aria-label="失败消息操作">
          <BaseButton type="button" intent="ghost" size="sm" class="!w-8 !px-0"
            :aria-label="failureBusy ? '正在处理失败消息' : '重试消息'"
            :title="failureBusy ? '正在处理失败消息' : '重试消息'"
            :disabled="failureBusy || !(submitFailure?.retry || message.runnerFailure?.retry)"
            @click="retryFailure">
            <svg class="h-4 w-4" :class="{ 'animate-spin': message.runnerFailure?.retrying }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 17.9 17" />
            </svg>
          </BaseButton>
          <BaseButton v-if="removeFailure" type="button" intent="ghost" size="sm" class="!w-8 !px-0"
            aria-label="删除失败消息" title="删除失败消息" :disabled="failureBusy" @click="removeFailure?.()">
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5" />
            </svg>
          </BaseButton>
        </div>
        </div>
      <!-- 历史只读已答卡片（supplementalInputs 扩展投影，设计 §7.1）：挂在结构化答案消息下 -->
      <div v-if="message.role === 'user' && message.answeredForm" class="w-full">
        <QuestionCard :answered="message.answeredForm" :auth-headers="authHeaders" />
      </div>
      <!-- 时间戳 - 用户消息右下 -->
      <div v-if="formattedTime" class="text-xs text-muted mt-1 mr-2">
        {{ formattedTime }}
      </div>
      <div v-if="submitFailure || message.runnerFailure" class="mt-1 flex max-w-full items-center justify-end gap-1 text-xs text-muted" role="alert">
        <svg class="h-3.5 w-3.5 shrink-0" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <circle cx="10" cy="10" r="7.5" /><path stroke-linecap="round" d="M10 6v4M10 13h.01" />
        </svg>
        <span>
          {{ submitFailure?.kind === 'retrying'
            ? '发送状态确认中，正在自动重试'
            : '消息未发送成功' }}
        </span>
      </div>
    </div>
  </div>

  <!-- AI消息：全宽，无头像，无缩进 -->
  <div
    v-else-if="!submitFailure"
    class="message-enter-active message-ai-wrapper"
  >
    <div class="message-ai-content">
      <!-- AI 正在输入提示（白框内无内容时显示）；
           verbose 中间提示（Phase 2）原地替换占位文案，不新增聊天气泡 -->
      <div v-if="showInputHint" class="flex items-center gap-2">
        <span class="inline-block w-1.5 h-1.5 rounded-full bg-primary-400 animate-pulse"></span>
        <span class="text-xs text-muted verbose-hint" aria-live="polite">{{ liveHintText }}</span>
      </div>

      <ReplyContent v-if="!showInputHint" :message="message" :is-processing="isProcessing" />

      <HumanAssistanceCard
        v-if="message.browserAssistance"
        :key="message.browserAssistance.run_id + ':' + message.browserAssistance.assistance_id"
        :runner-id="message.runnerId"
        :assistance="message.browserAssistance"
        :auth-headers="authHeaders"
        @updated="updateAssistance"
        @continuation="handleContinuation"
      />

      <!-- 结构化提问卡片（ask_user，WP4）：waiting.form 投影到问题消息时可作答，
           与下方 quickOptions（非阻塞编号按钮）视觉区分（卡片有边框与标题区） -->
      <QuestionCard
        v-if="message.questionCard"
        :key="message.questionCard.runnerId + ':' + message.questionCard.waitId"
        :card="message.questionCard"
        :auth-headers="authHeaders"
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

      <!-- 执行中过程反馈：runner 仍在运行时展示最近工具活动，让长执行可见；终态后收起 -->
      <div v-if="message.runnerActive && hasProgress && !isDebugEnabled" class="mt-2">
        <div class="space-y-0.5">
          <div
            v-for="(msg, index) in liveProgressTail"
            :key="index"
            :class="['text-xs py-1 px-2 rounded text-gray-500', getProgressClass(msg)]"
          >
            <span class="opacity-80">{{ formatProgressContent(msg) }}</span>
          </div>
        </div>
      </div>

      <!-- 执行详情（仅 debug 模式显示；执行中由上方过程反馈承接，避免重复） -->
      <div v-if="isDebugEnabled && hasProgress && !message.runnerActive" class="mt-2">
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
    <div v-if="!showInputHint && (formattedTime || canShare)" class="ml-2 mt-1 flex min-h-7 items-center gap-2 text-xs text-muted">
      <span v-if="formattedTime" class="tabular-nums">{{ formattedTime }}</span>
      <span v-if="formattedTime && canShare" aria-hidden="true" class="text-gray-300">·</span>
      <BaseButton v-if="canShare" type="button" intent="ghost" size="sm"
        class="!h-7 !px-1.5 !text-xs !font-normal text-muted focus:!ring-0 focus-visible:!ring-2 focus-visible:!ring-primary-500/40"
        aria-label="复制分享链接" title="复制分享链接" @click="showShareDialog = true">
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M10 13a5 5 0 0 0 7.1 0l3-3a5 5 0 0 0-7.1-7.1l-1.7 1.7m2.7 6.4a5 5 0 0 0-7.1 0l-3 3a5 5 0 0 0 7.1 7.1l1.7-1.7" />
        </svg>
        分享
      </BaseButton>
    </div>
    <MessageShareDialog v-if="showShareDialog && sessionId && message.messageId"
      :session-id="sessionId" :message-id="shareMessageId" @close="showShareDialog = false" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, defineAsyncComponent } from 'vue'
import type { ChatMessage, AttachmentInfo, InputHintState, ProgressMessage, QuickOption } from '@/types'
import AttachmentChip from './AttachmentChip.vue'
import ReplyContent from './chat/ReplyContent.vue'
import { getRuntime } from '@/platform/runtime'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { renderMarkdown } from '@/utils/markdown'
import { useDebugMode } from '@/composables/useDebugMode'
import HumanAssistanceCard from './browser/HumanAssistanceCard.vue'
import QuestionCard from './chat/QuestionCard.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAgent } from '@/composables/useAgent'
import BaseButton from './ui/BaseButton.vue'

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
const authHeaders = computed(() => useTenantAuth().getAuthHeader())

function updateAssistance(assistance: any) {
  if (props.message.runnerId) return
  props.message.browserAssistance = assistance
}

function handleContinuation(events: any[]) {
  if (props.message.runnerId) return
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
    !props.message.waitingNotice &&
    !props.message.submitFailure &&
    !props.message.content &&
    (props.inputHintState === 'thinking' || props.inputHintState === 'working')
  )
})

// 失败提示显示在用户消息旁；乐观 assistant 只作为内部提交占位，不渲染空框。
const submitFailure = computed(() => props.message.submitFailure)
const failureBusy = computed(() => !!(props.message.runnerFailure?.retrying || props.message.runnerFailure?.deleting))
const removeFailure = computed(() => submitFailure.value?.remove || props.message.runnerFailure?.remove)
function retryFailure() {
  const retry = submitFailure.value?.retry || props.message.runnerFailure?.retry
  retry?.()
}

// verbose 中间提示（Phase 2，设计 §8.2）：assistant 正文为空且处理中时，
// 用当前会话的 live verbose 文案替换「对方正在输入中...」占位（同一占位区，
// 不新增聊天气泡）；response 开始后 showInputHint 变 false 自动隐藏。
// 历史 metadata 中的 verboseMessages 不在此展示（默认不在已完成消息下展开）。
const { liveVerbose, sessionId } = useAgent()
const MessageShareDialog = defineAsyncComponent(() => import('./chat/MessageShareDialog.vue'))
const showShareDialog = ref(false)
// Runner projections can reuse an intermediate history ID; share the final view shown here.
const shareMessageId = computed(() => props.message.messageId?.endsWith(':final') ? props.message.messageId : props.message.runnerId && props.message.runnerActive !== undefined
  ? props.message.runnerId + ':assistant' : props.message.messageId!)
const canShare = computed(() => getRuntime().target === 'web' && !!sessionId?.value && !!props.message.messageId
  && !props.isProcessing && !props.message.runnerActive && !props.message.questionCard
  && !!(props.message.content.trim() || props.message.images?.length || props.message.attachments?.length || props.message.downloadableFiles?.length))
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
/** 执行中只展示最近的工具活动，避免长任务把气泡刷屏 */
const liveProgressTail = computed(() => (props.message.progressMessages || []).slice(-4))

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

const renderedContent = computed(() => {
  return renderMarkdown(displayContent.value)
})

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
  padding: 8px 12px;
  transition: all 0.2s;
}

.message-user-content {
  word-break: break-word;
}

.message-user-content :deep(p:first-child),
.markdown-content :deep(> :first-child) {
  margin-top: 0;
}

.message-user-content :deep(p:last-child),
.markdown-content :deep(> :last-child) {
  margin-bottom: 0;
}

/* AI消息：全宽，无缩进 */
.message-ai-wrapper {
  width: 100%;
}

.message-ai-content {
  background-color: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 10px 14px;
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
    padding: 8px 12px;
  }

  .message-ai-content {
    padding: 10px 14px;
  }
}
</style>

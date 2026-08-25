<template>
  <div class="max-w-none md:max-w-4xl mx-auto">
    <div class="relative">
      <!-- 子智能体快捷按钮（点击即发送预设消息，与手动输入同链路） -->
      <div v-if="quickPrompts && quickPrompts.length > 0" class="mb-2 flex flex-wrap gap-2">
        <button
          v-for="p in quickPrompts"
          :key="p.label"
          type="button"
          :disabled="disabled"
          class="px-3 py-1.5 bg-white border border-gray-200 rounded-full text-sm text-gray-600 hover:border-primary-300 hover:text-primary-600 transition-all disabled:opacity-50"
          @click="emit('send', p.message)"
        >
          {{ p.label }}
        </button>
      </div>
      <!-- 附件预览区 -->
      <div v-if="files.length > 0" class="mb-3 flex flex-wrap gap-2">
        <div
          v-for="file in files"
          :key="file.file_id"
          class="flex items-stretch bg-gray-100 rounded-lg border border-gray-200 overflow-hidden"
        >
          <button
            v-if="file.file_id"
            type="button"
            @click="openPreview(file)"
            class="flex items-center gap-2 pl-3 pr-2 py-1.5 text-left hover:bg-primary-50 transition-colors min-w-0"
            :title="`预览 ${file.name}`"
          >
            <svg v-if="file.type === 'image'" class="w-4 h-4 text-primary-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <svg v-else class="w-4 h-4 text-primary-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <span class="text-sm text-gray-600 max-w-32 truncate">{{ file.name }}</span>
          </button>
          <span
            v-else
            class="flex items-center gap-2 pl-3 pr-2 py-1.5 min-w-0"
            :title="`上传中：${file.name}`"
          >
            <svg v-if="file.type === 'image'" class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <svg v-else class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <span class="text-sm text-gray-400 max-w-32 truncate">{{ file.name }}</span>
          </span>
          <button
            type="button"
            @click.stop="emit('remove', file.file_id)"
            class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-danger-500 hover:bg-danger-50 transition-colors"
            title="移除附件"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- 大圆角输入框（Kimi 风格）：textarea + 底部工具行（左➕附件 / 右圆形发送） -->
      <div
        class="rounded-3xl border bg-white transition-colors"
        :class="disabled ? 'border-gray-200 opacity-70' : 'border-gray-300 focus-within:border-primary-500 focus-within:ring-1 focus-within:ring-primary-500'"
      >
        <textarea
          ref="inputRef"
          v-model="inputText"
          @keydown.enter.exact="handleEnter"
          @keydown.shift.enter="newLine"
          @paste="handlePaste"
          @input="autoResize"
          :disabled="disabled"
          :placeholder="isMobile ? '输入您的问题或任务...' : '输入您的问题或任务，Enter 发送，Shift+Enter 换行，Ctrl+V 粘贴附件'"
          rows="2"
          inputmode="text"
          class="box-border w-full min-h-[68px] max-h-[160px] px-4 pt-3 pb-1 bg-transparent border-0 text-gray-800 placeholder-gray-400 resize-none outline-none disabled:opacity-50 overflow-y-auto"
        ></textarea>

        <!-- 底部工具行 -->
        <div class="flex items-center justify-between px-3 pb-2.5">
          <div class="flex items-center gap-1">
            <!-- 附件上传按钮（加号按钮硬编码，所有智能体共有） -->
            <button
              @click="triggerFileInput"
              :disabled="disabled || isProcessing"
              class="w-8 h-8 rounded-full border border-gray-300 text-gray-500 hover:text-primary-600 hover:border-primary-400 transition-colors disabled:opacity-50 flex items-center justify-center"
              title="添加附件（也可 Ctrl+V 粘贴）"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
              </svg>
            </button>

            <!-- 隐藏的文件输入（accept 由当前会话 subagent.upload_accept 决定，支持多选） -->
            <input
              ref="fileInputRef"
              type="file"
              multiple
              class="hidden"
              :accept="currentUploadAccept"
              @change="handleFileChange"
            />

            <!-- 子智能体工具栏额外按钮（Phase 5.1.3）
                 按钮组由 currentToolbarButtons 决定，主智能体无额外按钮 -->
            <ChatToolbar :button-ids="currentToolbarButtons" />
          </div>

          <div class="flex items-center gap-2">
            <!-- Processing indicator -->
            <span v-if="isProcessing" class="flex items-center gap-1.5 text-primary-500 text-xs">
              <svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              处理中
            </span>

            <!-- Stop Button（处理中替换发送按钮） -->
            <button
              v-if="isProcessing"
              @click="emit('stop')"
              class="w-9 h-9 rounded-full bg-danger-50 text-danger-600 border border-danger-200 hover:bg-danger-100 transition-colors flex items-center justify-center"
              title="停止生成"
            >
              <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
            <!-- Send Button：圆形向上箭头 -->
            <button
              v-else
              @click="handleSend"
              :disabled="(!inputText.trim() && files.length === 0) || disabled"
              :class="[
                'w-9 h-9 rounded-full transition-all flex items-center justify-center',
                (inputText.trim() || files.length > 0) && !disabled
                  ? 'bg-primary-600 text-white hover:bg-primary-500 shadow-md shadow-primary-500/25'
                  : 'bg-gray-200 text-gray-400 cursor-not-allowed'
              ]"
              title="发送"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useMobile } from '@/composables/useMobile'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import type { UploadedFile } from '@/api/agent'
import type { QuickPrompt } from '@/utils/quickPrompts'
import ChatToolbar from './chat/ChatToolbar.vue'

interface Props {
  disabled: boolean
  isProcessing: boolean
  files: UploadedFile[]
  /** 当前会话 subagent 的上传文件类型限定（如 "image/*"），未声明时 fallback 到默认白名单 */
  uploadAccept?: string | null
  /** 当前会话 subagent 的工具栏额外按钮 id 列表（如 ['video_gen']） */
  toolbarButtons?: string[] | null
  /** 子智能体快捷按钮（utils/quickPrompts.ts 按 subagent_type 映射）；空/缺省不渲染 */
  quickPrompts?: QuickPrompt[] | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'send', content: string): void
  (e: 'upload', file: File): void
  (e: 'remove', file_id: string): void
  (e: 'stop'): void
}>()

const { isMobile } = useMobile()
const { openPreview } = useAttachmentPreview()
const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

// 默认上传白名单（与原 ChatInput 硬编码 accept 一致，对应 chat.default_upload_accept）
const DEFAULT_UPLOAD_ACCEPT = '.pdf,.doc,.docx,.xls,.xlsx,.txt,.png,.jpg,.jpeg,.gif,.ppt,.pptx'

/** 当前生效的上传 accept（subagent 声明覆盖时用声明值，否则用默认白名单） */
const currentUploadAccept = computed(() => props.uploadAccept || DEFAULT_UPLOAD_ACCEPT)

/** 当前生效的工具栏按钮 id 列表 */
const currentToolbarButtons = computed(() => props.toolbarButtons || [])

const canSend = computed(() => {
  return !props.disabled
})

// 前端日志：输入框从禁用恢复时自动聚焦，提升用户体验
watch(() => props.disabled, (newVal, oldVal) => {
  if (oldVal === true && newVal === false) {
    nextTick(() => {
      inputRef.value?.focus()
    })
  }
})

function handleEnter(e: KeyboardEvent) {
  if (isMobile.value) {
    // 移动端：Enter 换行，不阻止默认行为，仅触发自动调整高度
    setTimeout(autoResize, 0)
    return
  }
  // 处理中不允许 Enter 发送
  if (props.isProcessing) {
    e.preventDefault()
    return
  }
  // 桌面端：Enter 发送
  e.preventDefault()
  handleSend()
}

function handleSend() {
  if (!canSend.value) return

  // 有文字内容或有附件时都可以发送
  if (!inputText.value.trim() && props.files.length === 0) return

  emit('send', inputText.value.trim())
  inputText.value = ''

  // Reset textarea height
  if (inputRef.value) {
    inputRef.value.style.height = 'auto'
  }
}

function newLine() {
  // Allow default behavior for Shift+Enter
  // Auto-resize textarea is handled by CSS
  setTimeout(autoResize, 0)
}

function autoResize() {
  const textarea = inputRef.value
  if (textarea) {
    textarea.style.height = 'auto'
    // scrollHeight 包含 padding 但不包含 border；
    // 由于 textarea 使用 box-sizing: border-box，设置的 height 是 box 总高度，
    // 需要把上下 border 厚度补回去，否则 box 高度比内容所需小几个像素，
    // 会导致右侧出现纵向滚动条。
    const cs = window.getComputedStyle(textarea)
    const borderVertical =
      parseFloat(cs.borderTopWidth || '0') + parseFloat(cs.borderBottomWidth || '0')
    const target = Math.min(textarea.scrollHeight + borderVertical, 160)
    textarea.style.height = target + 'px'
  }
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    // 支持多选：逐个 emit，与粘贴多文件行为保持一致
    for (let i = 0; i < input.files.length; i++) {
      const file = input.files[i]
      if (file && isAcceptedFile(file)) {
        emit('upload', file)
      }
    }
    // 清空input以允许重复选择同一文件
    input.value = ''
  }
}

// 支持的附件后缀（与 <input type="file" accept> 保持一致）
const ACCEPTED_EXTENSIONS = [
  '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.txt',
  '.png', '.jpg', '.jpeg', '.gif', '.ppt', '.pptx',
]

/** 判断文件是否符合当前 currentUploadAccept（Phase 5.1.3）
 *  - MIME 前缀（如 "image/*"）：按 MIME 类型匹配
 *  - 扩展名列表（如 ".pdf,.docx"）：按扩展名匹配
 *  - 空值：使用默认白名单
 */
function isAcceptedFile(file: File): boolean {
  const accept = currentUploadAccept.value
  if (accept && accept.includes('/*')) {
    // MIME 前缀模式，如 "image/*" / "video/*" / "audio/*"
    const prefixes = accept.split(',').map((s) => s.trim()).filter(Boolean)
    return prefixes.some((prefix) => {
      if (prefix.endsWith('/*')) {
        const typePrefix = prefix.slice(0, -1) // "image/"
        return file.type.startsWith(typePrefix)
      }
      // 也可能混入扩展名
      return file.name.toLowerCase().endsWith(prefix)
    })
  }
  // 默认白名单模式
  const lowerName = file.name.toLowerCase()
  if (ACCEPTED_EXTENSIONS.some((ext) => lowerName.endsWith(ext))) {
    return true
  }
  // 兜底：按 MIME 类型判断（部分剪贴板截图没有扩展名）
  return /^image\//.test(file.type)
    || /^application\/(pdf|msword|vnd\.openxmlformats|vnd\.ms-excel|ms-powerpoint|vnd\.ms-powerpoint)/.test(file.type)
    || file.type === 'text/plain'
}

function handlePaste(event: ClipboardEvent) {
  if (props.disabled || props.isProcessing) return
  const items = event.clipboardData?.items
  if (!items || items.length === 0) return

  const files: File[] = []
  for (let i = 0; i < items.length; i++) {
    const item = items[i]
    if (item.kind === 'file') {
      const file = item.getAsFile()
      if (file && isAcceptedFile(file)) {
        files.push(file)
      }
    }
  }

  if (files.length === 0) return

  // 有可识别的附件：阻止默认粘贴（避免截图等被转成 base64 文字污染输入框）
  event.preventDefault()
  files.forEach((file) => emit('upload', file))
}
</script>

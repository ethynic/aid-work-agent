<template>
  <div class="max-w-none md:max-w-4xl mx-auto">
    <div class="relative">
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

      <!-- Text Input -->
      <div class="flex items-start gap-3">
        <!-- 上传按钮 -->
        <button
          @click="triggerFileInput"
          :disabled="disabled || isProcessing"
          class="flex-shrink-0 box-border h-[46px] px-3.5 rounded-xl bg-gray-100 border border-gray-200 text-gray-500 hover:text-primary-500 hover:border-primary-300 transition-all disabled:opacity-50 flex items-center justify-center"
          title="添加附件"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
          </svg>
        </button>

        <!-- 隐藏的文件输入 -->
        <input
          ref="fileInputRef"
          type="file"
          class="hidden"
          accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.png,.jpg,.jpeg,.gif,.ppt,.pptx"
          @change="handleFileChange"
        />

        <div class="flex-1 relative">
          <textarea
            ref="inputRef"
            v-model="inputText"
            @keydown.enter.exact="handleEnter"
            @keydown.shift.enter="newLine"
            @paste="handlePaste"
            @input="autoResize"
            :disabled="disabled"
            :placeholder="isMobile ? '输入您的问题或任务...' : '输入您的问题或任务，Ctrl+V 粘贴附件...'"
            rows="1"
            inputmode="text"
            class="box-border w-full min-h-[46px] max-h-[120px] px-4 py-2 bg-white border border-gray-300 rounded-xl text-gray-800 placeholder-gray-400 resize-none outline-none focus:border-primary-500 disabled:opacity-50 overflow-y-auto"
            :class="[isProcessing ? 'pr-12' : 'pr-4']"
          ></textarea>

          <!-- Processing indicator -->
          <div
            v-if="isProcessing"
            class="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-2 text-primary-500 bg-white pl-2"
          >
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-xs">处理中</span>
          </div>
        </div>

        <!-- Stop Button -->
        <button
          v-if="isProcessing"
          @click="emit('stop')"
          class="box-border h-[46px] min-h-[44px] min-w-[44px] px-4 sm:px-5 rounded-xl font-medium transition-all flex items-center justify-center gap-2 bg-danger-50 text-danger-600 border border-danger-200 hover:bg-danger-100 hover:border-red-300"
        >
          <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
          <span class="hidden sm:inline">停止</span>
        </button>
        <!-- Send Button -->
        <button
          v-else
          @click="handleSend"
          :disabled="!inputText.trim() && files.length === 0"
          :class="[
            'box-border h-[46px] min-h-[44px] min-w-[44px] px-4 sm:px-5 rounded-xl font-medium transition-all flex items-center justify-center gap-2',
            inputText.trim() || files.length > 0
              ? 'bg-gradient-to-r from-primary-500 to-primary-700 text-white hover:from-primary-400 hover:to-primary-600 shadow-lg shadow-primary-500/25'
              : 'bg-gray-200 text-gray-400 cursor-not-allowed'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
          <span class="hidden sm:inline">发送</span>
        </button>
      </div>

      <!-- Hint -->
      <div class="mt-2 text-xs text-gray-500 text-center hidden md:block">
        <span>按</span>
        <kbd class="px-1.5 py-0.5 bg-gray-100 border border-gray-300 rounded text-gray-600 mx-1">Enter</kbd>
        <span>发送</span>
        <span class="mx-2">|</span>
        <span>按</span>
        <kbd class="px-1.5 py-0.5 bg-gray-100 border border-gray-300 rounded text-gray-600 mx-1">Shift + Enter</kbd>
        <span>换行</span>
        <span class="mx-2">|</span>
        <span>按</span>
        <kbd class="px-1.5 py-0.5 bg-gray-100 border border-gray-300 rounded text-gray-600 mx-1">Ctrl + V</kbd>
        <span>粘贴附件</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useMobile } from '@/composables/useMobile'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import type { UploadedFile } from '@/api/agent'

interface Props {
  disabled: boolean
  isProcessing: boolean
  files: UploadedFile[]
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
    const target = Math.min(textarea.scrollHeight + borderVertical, 120)
    textarea.style.height = target + 'px'
  }
} {
  const textarea = inputRef.value
  if (textarea) {
    textarea.style.height = 'auto'
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px'
  }
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    const file = input.files[0]
    emit('upload', file)
    // 清空input以允许重复选择同一文件
    input.value = ''
  }
}

// 支持的附件后缀（与 <input type="file" accept> 保持一致）
const ACCEPTED_EXTENSIONS = [
  '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.txt',
  '.png', '.jpg', '.jpeg', '.gif', '.ppt', '.pptx',
]

function isAcceptedFile(file: File): boolean {
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

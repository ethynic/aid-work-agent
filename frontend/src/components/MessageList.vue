<template>
  <div class="h-full overflow-y-auto p-2 md:p-4 bg-gray-50" ref="containerRef">
    <div class="max-w-4xl mx-auto space-y-4 md:space-y-6 px-2 md:px-4">
      <!-- Empty State -->
      <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-center px-2 md:px-4">
        <div class="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-100 to-primary-200 flex items-center justify-center mb-6 shadow-message">
          <svg class="w-10 h-10 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
        </div>
        <h3 class="text-lg font-semibold text-gray-800 mb-2">开始新对话</h3>
        <p class="text-gray-500 max-w-md mb-6 text-sm md:text-base leading-relaxed">
          输入您的问题或任务，AI助手将为您处理。可以上传文件进行智能分析。
        </p>
        <div class="flex flex-wrap justify-center gap-3">
          <button
            @click="$emit('quick-prompt', '帮我写一份周报')"
            class="px-4 py-2 bg-white border border-gray-200 rounded-xl text-sm text-gray-600 hover:border-primary-300 hover:text-primary-600 hover:shadow-message transition-all"
          >
            帮我写一份周报
          </button>
          <button
            @click="$emit('quick-prompt', '分析上传的文件')"
            class="px-4 py-2 bg-white border border-gray-200 rounded-xl text-sm text-gray-600 hover:border-primary-300 hover:text-primary-600 hover:shadow-message transition-all"
          >
            分析上传的文件
          </button>
        </div>
      </div>

      <!-- Messages -->
      <template v-else>
        <MessageItem
          v-for="(message, index) in messages"
          :key="index"
          :message="message"
          :is-processing="isProcessing && index === messages.length - 1 && message.role === 'assistant'"
          :input-hint-state="isProcessing && index === messages.length - 1 && message.role === 'assistant' ? inputHintState : 'idle'"
        />
        
        <!-- Typing Indicator -->
        <div v-if="isProcessing && messages[messages.length - 1]?.role === 'user'" class="typing-indicator">
          <div class="flex gap-1.5 py-2">
            <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 0ms"></span>
            <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 150ms"></span>
            <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 300ms"></span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { ChatMessage, InputHintState } from '@/types'
import MessageItem from './MessageItem.vue'

interface Props {
  messages: ChatMessage[]
  isProcessing: boolean
  inputHintState: InputHintState
}

const props = defineProps<Props>()
defineEmits<{
  (e: 'quick-prompt', prompt: string): void
}>()
const containerRef = ref<HTMLElement | null>(null)

// 自动滚动到底部
watch(() => props.messages.length, async () => {
  await nextTick()
  scrollToBottom()
})

watch(() => props.isProcessing, async (processing) => {
  if (processing) {
    await nextTick()
    scrollToBottom()
  }
})

function scrollToBottom() {
  if (containerRef.value) {
    containerRef.value.scrollTop = containerRef.value.scrollHeight
  }
}
</script>

<style scoped>
.typing-indicator {
  background-color: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 4px 16px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
</style>

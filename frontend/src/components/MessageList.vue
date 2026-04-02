<template>
  <div class="h-full overflow-y-auto p-4 bg-gray-50" ref="containerRef">
    <div class="max-w-4xl mx-auto space-y-6">
      <!-- Empty State -->
      <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-center">
        <div class="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-100 to-primary-200 flex items-center justify-center mb-6">
          <svg class="w-10 h-10 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
        </div>
        <h3 class="text-xl font-medium text-gray-700 mb-2">开始新对话</h3>
        <p class="text-gray-500 max-w-md">
          输入您的问题或任务，AI助手将为您处理。可以上传文件进行智能分析。
        </p>
      </div>

      <!-- Messages -->
      <template v-else>
        <MessageItem 
          v-for="(message, index) in messages" 
          :key="index"
          :message="message"
          :is-processing="isProcessing && index === messages.length - 1 && message.role === 'assistant'"
        />
        
        <!-- Typing Indicator -->
        <div v-if="isProcessing && messages[messages.length - 1]?.role === 'user'" class="flex gap-3 p-4 rounded-2xl bg-white border border-gray-200">
          <div class="w-8 h-8 rounded-full bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center flex-shrink-0">
            <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <div class="flex-1 space-y-2">
            <div class="flex gap-1">
              <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 0ms"></span>
              <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 150ms"></span>
              <span class="w-2 h-2 bg-primary-400 rounded-full animate-bounce" style="animation-delay: 300ms"></span>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { ChatMessage } from '@/types'
import MessageItem from './MessageItem.vue'

interface Props {
  messages: ChatMessage[]
  isProcessing: boolean
}

const props = defineProps<Props>()
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

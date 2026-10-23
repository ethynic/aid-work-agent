<template>
  <div class="h-full overflow-y-auto p-2 md:p-4 bg-gray-50" ref="containerRef">
    <div class="max-w-4xl mx-auto space-y-4 md:space-y-6 px-2 md:px-4">
      <div v-if="history" class="flex flex-wrap items-center gap-2 text-sm text-muted" aria-label="任务历史记录">
        <p role="status" class="min-w-0 flex-1">{{ history.loading ? '正在加载历史记录…' : history.older ? '正在查看较早记录' : '显示最近记录，较早内容已保存。' }}</p>
        <BaseButton v-if="history.hasMore" intent="secondary" size="sm" :disabled="history.loading" @click="$emit('history', 'older')">更早记录</BaseButton>
        <BaseButton v-if="history.older || history.error" intent="secondary" size="sm" :disabled="history.loading" @click="$emit('history', 'latest')">返回最新</BaseButton>
        <p v-if="history.error" role="alert" class="w-full text-danger-600">{{ history.error }}</p>
      </div>

      <!-- Empty State -->
      <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-center px-2 md:px-4">
        <div class="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-100 to-primary-200 flex items-center justify-center mb-6 shadow-message">
          <svg class="w-10 h-10 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
        </div>
        <h3 class="text-lg font-semibold text-gray-800 mb-2">{{ subagentName ? `开始与${subagentName}对话` : '开始新对话' }}</h3>
        <p class="text-gray-500 max-w-md mb-6 text-sm md:text-base leading-relaxed">
          {{ greeting?.summary }}
        </p>
        <div v-if="greeting?.prompts.length" class="flex flex-wrap justify-center gap-3">
          <button
            v-for="p in greeting.prompts"
            :key="p.label"
            @click="$emit('quick-prompt', p.message)"
            class="px-4 py-2 bg-white border border-gray-200 rounded-xl text-sm text-gray-600 hover:border-primary-300 hover:text-primary-600 hover:shadow-message transition-all"
          >
            {{ p.label }}
          </button>
        </div>
      </div>

      <!-- Messages -->
      <template v-else>
        <MessageItem
          v-for="(message, index) in messages"
          :key="message.messageId || index"
          :message="message"
          :is-processing="isProcessing && index === messages.length - 1 && message.role === 'assistant'"
          :input-hint-state="isProcessing && index === messages.length - 1 && message.role === 'assistant' ? inputHintState : 'idle'"
          @send="$emit('send', $event)"
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
import { DEFAULT_GREETING, type SubagentGreeting } from '@/utils/sessionGreetings'
import MessageItem from './MessageItem.vue'
import BaseButton from './ui/BaseButton.vue'

interface Props {
  messages: ChatMessage[]
  isProcessing: boolean
  inputHintState: InputHintState
  followLatest?: boolean
  history?: { older: boolean; hasMore: boolean; loading: boolean; error?: string }
  /** 当前数字员工显示名称（空态标题展示；无则主智能体） */
  subagentName?: string | null
  /** 当前数字员工的空态摘要与快捷按钮；无则渲染默认兜底 */
  greeting?: SubagentGreeting | null
}

const props = withDefaults(defineProps<Props>(), {
  subagentName: null,
  followLatest: true,
  greeting: () => DEFAULT_GREETING,
})
defineEmits<{
  (e: 'quick-prompt', prompt: string): void
  /** 消息内动作触发的发送（如 §5.1 编号选择按钮），与输入框/空态快捷按钮同链路 */
  (e: 'send', content: string): void
  (e: 'history', action: 'older' | 'latest'): void
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

watch(() => props.followLatest, async follow => {
  if (follow) { await nextTick(); scrollToBottom() }
})

function scrollToBottom() {
  if (props.followLatest && containerRef.value) {
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

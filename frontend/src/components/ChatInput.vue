<template>
  <div class="max-w-4xl mx-auto">
    <div class="relative">
      <!-- Text Input -->
      <div class="flex items-end gap-3">
        <div class="flex-1 relative">
          <textarea
            ref="inputRef"
            v-model="inputText"
            @keydown.enter.exact.prevent="handleSend"
            @keydown.shift.enter="newLine"
            :disabled="disabled"
            placeholder="输入您的问题或任务，按Enter发送..."
            rows="1"
            class="w-full px-4 py-3 bg-slate-700/50 border border-slate-600 rounded-xl text-white placeholder-slate-400 resize-none focus:outline-none focus:ring-2 focus:ring-cyan-500/50 focus:border-cyan-500 transition-all disabled:opacity-50"
            :class="[isProcessing ? 'pr-20' : '']"
          ></textarea>
          
          <!-- Processing indicator -->
          <div 
            v-if="isProcessing" 
            class="absolute right-3 bottom-3 flex items-center gap-2 text-cyan-400"
          >
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-xs">处理中</span>
          </div>
        </div>

        <!-- Send Button -->
        <button
          @click="handleSend"
          :disabled="!canSend || !inputText.trim()"
          :class="[
            'px-6 py-3 rounded-xl font-medium transition-all flex items-center gap-2',
            canSend && inputText.trim()
              ? 'bg-gradient-to-r from-cyan-500 to-blue-600 text-white hover:from-cyan-400 hover:to-blue-500 shadow-lg shadow-cyan-500/25'
              : 'bg-slate-700 text-slate-400 cursor-not-allowed'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
          <span class="hidden sm:inline">发送</span>
        </button>
      </div>

      <!-- Hint -->
      <div class="mt-2 text-xs text-slate-500 text-center">
        <span>按</span>
        <kbd class="px-1.5 py-0.5 bg-slate-700 rounded text-slate-400 mx-1">Enter</kbd>
        <span>发送</span>
        <span class="mx-2">|</span>
        <span>按</span>
        <kbd class="px-1.5 py-0.5 bg-slate-700 rounded text-slate-400 mx-1">Shift + Enter</kbd>
        <span>换行</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

interface Props {
  disabled: boolean
  isProcessing: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'send', content: string): void
}>()

const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement | null>(null)

const canSend = computed(() => {
  return !props.disabled && !props.isProcessing
})

function handleSend() {
  if (!canSend.value || !inputText.value.trim()) return
  
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
}
</script>

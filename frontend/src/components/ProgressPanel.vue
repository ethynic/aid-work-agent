<template>
  <div class="flex-shrink-0 border-t border-slate-700 bg-slate-800/30">
    <div class="max-w-4xl mx-auto">
      <!-- Header -->
      <button 
        @click="isExpanded = !isExpanded"
        class="w-full px-4 py-2 flex items-center justify-between text-sm text-slate-400 hover:text-slate-300 transition-colors"
      >
        <div class="flex items-center gap-2">
          <svg 
            :class="['w-4 h-4 transition-transform', isExpanded ? 'rotate-90' : '']" 
            fill="none" 
            stroke="currentColor" 
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
          <span>执行进度</span>
          <span class="px-2 py-0.5 bg-slate-700 rounded-full text-xs">
            {{ messages.length }} 条
          </span>
        </div>
        <span v-if="isProcessing" class="flex items-center gap-1 text-cyan-400">
          <span class="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-pulse"></span>
          处理中
        </span>
      </button>

      <!-- Content -->
      <div v-show="isExpanded" class="px-4 pb-3 max-h-48 overflow-y-auto">
        <div class="space-y-1.5">
          <div 
            v-for="(msg, index) in messages" 
            :key="index"
            :class="[
              'text-sm py-1.5 px-3 rounded-lg transition-colors',
              msg.type === 'error' ? 'bg-red-500/10 text-red-300 border-l-2 border-red-500' :
              msg.type === 'complete' ? 'bg-green-500/10 text-green-300 border-l-2 border-green-500' :
              'bg-slate-700/30 text-slate-300 border-l-2 border-cyan-500'
            ]"
          >
            <span class="mr-2">{{ getIcon(msg.type) }}</span>
            <span>{{ msg.content }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { ProgressMessage } from '@/types'

interface Props {
  messages: ProgressMessage[]
  isProcessing: boolean
}

const props = defineProps<Props>()
const isExpanded = ref(true)

function getIcon(type: ProgressMessage['type']): string {
  switch (type) {
    case 'error': return '❌'
    case 'complete': return '✅'
    default: return '•'
  }
}
</script>

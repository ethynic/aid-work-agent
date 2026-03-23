<template>
  <div 
    :class="[
      'flex gap-3 p-4 rounded-2xl transition-all',
      message.role === 'user' 
        ? 'bg-cyan-600/20 border border-cyan-500/30 ml-12' 
        : 'bg-slate-800/50 border border-slate-700/50'
    ]"
  >
    <!-- Avatar -->
    <div 
      :class="[
        'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0',
        message.role === 'user' 
          ? 'bg-gradient-to-br from-cyan-500 to-cyan-600' 
          : 'bg-gradient-to-br from-slate-600 to-slate-700'
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
        <span class="text-sm font-medium text-slate-200">
          {{ message.role === 'user' ? '你' : 'AI助手' }}
        </span>
        <span v-if="timestamp" class="text-xs text-slate-500">
          {{ formatTime(timestamp) }}
        </span>
        <span v-if="isProcessing" class="text-xs text-cyan-400 animate-pulse">
          生成中...
        </span>
      </div>
      
      <!-- Message Content (Markdown) -->
      <div 
        class="text-slate-300 leading-relaxed markdown-content"
        v-html="renderedContent"
      ></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ChatMessage } from '@/types'

interface Props {
  message: ChatMessage
  isProcessing?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isProcessing: false
})

const timestamp = computed(() => props.message.timestamp)

const renderedContent = computed(() => {
  // 简单的Markdown渲染
  let content = escapeHtml(props.message.content)
  
  // 代码块
  content = content.replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
  
  // 行内代码
  content = content.replace(/`([^`]+)`/g, '<code>$1</code>')
  
  // 粗体
  content = content.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  
  // 斜体
  content = content.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  
  // 链接
  content = content.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  
  // 列表
  content = content.replace(/^- (.+)$/gm, '<li>$1</li>')
  content = content.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
  
  // 换行
  content = content.replace(/\n/g, '<br>')
  
  return content
})

function escapeHtml(text: string): string {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

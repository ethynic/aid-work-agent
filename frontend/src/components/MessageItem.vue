<template>
  <div
    :class="[
      'flex gap-3 p-4 rounded-2xl transition-all',
      message.role === 'user'
        ? 'bg-cyan-50 border border-cyan-200 ml-12'
        : 'bg-white border border-slate-200'
    ]"
  >
    <!-- Avatar -->
    <div
      :class="[
        'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0',
        message.role === 'user'
          ? 'bg-gradient-to-br from-cyan-400 to-cyan-500'
          : 'bg-gradient-to-br from-slate-400 to-slate-500'
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
        <span class="text-sm font-medium text-slate-700">
          {{ message.role === 'user' ? '你' : 'AI助手' }}
        </span>
        <span v-if="timestamp" class="text-xs text-slate-400">
          {{ formatTime(timestamp) }}
        </span>
        <span v-if="isProcessing" class="text-xs text-cyan-400 animate-pulse">
          生成中...
        </span>
      </div>

      <!-- Message Content (Markdown) -->
      <div
        class="text-slate-700 leading-relaxed markdown-content prose prose-slate max-w-none"
        v-html="renderedContent"
      ></div>

      <!-- 执行详情（仅 AI 回复显示） -->
      <div v-if="message.role === 'assistant' && hasProgress" class="mt-2">
        <!-- 展开/折叠按钮 -->
        <button
          @click="toggleExpanded"
          class="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 transition-colors"
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

        <!-- 执行详情内容 -->
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
                'text-xs py-1 px-2 rounded text-slate-500',
                getProgressClass(msg.type)
              ]"
            >
            <div v-if="false">{{ msg }}</div>
              <span class="mr-1">{{ getProgressIcon(msg.type) }}</span>
              <span class="opacity-80">{{ formatProgressContent(msg) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js'
import type { ChatMessage, ProgressMessage } from '@/types'

// 配置 marked 使用 highlight.js 进行代码高亮
marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code: string, lang: string) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext'
    return hljs.highlight(code, { language }).value
  }
}))

interface Props {
  message: ChatMessage
  isProcessing?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isProcessing: false
})

const isExpanded = ref(false)

const timestamp = computed(() => props.message.timestamp)
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

function getProgressClass(type: string): string {
  switch (type) {
    case 'error': return 'bg-red-50 text-red-600'
    case 'complete': return 'bg-green-50 text-green-600'
    case 'thinking': return 'bg-purple-50 text-purple-600'
    case 'tool_start': return 'bg-cyan-50 text-cyan-600'
    case 'tool_result': return 'bg-blue-50 text-blue-600'
    default: return 'bg-slate-100 text-slate-500'
  }
}

function getProgressIcon(type: string): string {
  switch (type) {
    case 'error': return '❌'
    case 'complete': return '✅'
    case 'thinking': return '🤔'
    case 'tool_start': return '🔧'
    case 'tool_result': return '📤'
    default: return '🔄'
  }
}

function formatProgressContent(msg: string | Record<string, any>): string {
  // 实时消息格式: { type: "progress", content: string|object, timestamp: number }
  // content 类型:
  //   - string: 直接显示
  //   - { type: "tool_start", toolName, toolArgs }: 显示调用工具名称
  //   - { type: "tool_result", data }: 显示 data
  //   - { type: "progress", data }: 显示 data

  if (!msg || typeof msg !== 'object') {
    return String(msg ?? '')
  }

  const content = msg.content

  // content 是字符串：直接显示
  if (typeof content === 'string') {
    // 移除 emoji，截断过长的内容
    const cleaned = content.replace(/[\u{1F300}-\u{1F9FF}]/gu, '').trim()
    return cleaned.length > 100 ? cleaned.slice(0, 100) + '...' : cleaned
  }

  // content 是对象：根据 type 处理
  if (typeof content === 'object' && content !== null) {
    const c = content as Record<string, any>

    // type 为 tool_start：显示调用工具名称
    if (c.type === 'tool_start' && c.toolName) {
      return `🔧 正在执行 ${c.toolName}`
    }

    // type 为 tool_result：显示 data
    if (c.type === 'tool_result' && c.data !== undefined) {
      const data = c.data
      if (typeof data === 'string') {
        return data.replace(/[\u{1F300}-\u{1F9FF}]/gu, '').trim()
      }
      return JSON.stringify(data).slice(0, 100)
    }

    // type 为 progress：显示 data
    if (c.type === 'progress' && c.data !== undefined) {
      const data = c.data
      if (typeof data === 'string') {
        return data.replace(/[\u{1F300}-\u{1F9FF}]/gu, '').trim()
      }
      return JSON.stringify(data).slice(0, 100)
    }

    // 其他情况，JSON 化
    return JSON.stringify(c).slice(0, 100)
  }

  return String(content ?? '')
}

const renderedContent = computed(() => {
  // 使用 marked 渲染 Markdown，支持标题、表格、粗体、斜体、代码块、列表等
  return marked(props.message.content)
})

function formatTime(timestamp: number): string {
  const date = new Date(timestamp)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

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
          <span>执行详情</span>
          <span class="px-2 py-0.5 bg-slate-700 rounded-full text-xs">
            {{ visibleMessages.length }} 条
          </span>
        </div>
        <span v-if="isProcessing" class="flex items-center gap-1 text-cyan-400">
          <span class="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-pulse"></span>
          处理中
        </span>
      </button>

      <!-- Content -->
      <div v-show="isExpanded" ref="scrollContainer" class="px-4 pb-3 max-h-[60vh] overflow-y-auto">
        <div class="space-y-1">
          <template v-for="(msg, index) in visibleMessages" :key="index">
            <div
              v-if="shouldShowMessage(msg)"
              :class="[
                'text-sm py-1.5 px-3 rounded-lg transition-all',
                getClassByType(msg.type)
              ]"
          >
            <div class="flex items-start gap-2">
              <span class="flex-shrink-0 mt-0.5">{{ getIconByType(msg.type) }}</span>
              <div class="flex-1 min-w-0">
                <p class="whitespace-pre-wrap break-words">{{ formatContent(msg.content) }}</p>
                <p v-if="msg.timestamp" class="text-xs opacity-60 mt-1">
                  {{ formatTime(msg.timestamp) }}
                </p>
              </div>
            </div>
          </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, computed } from 'vue'
import type { ProgressMessage } from '@/types'

interface Props {
  messages: ProgressMessage[]
  isProcessing: boolean
}

const props = defineProps<Props>()
const isExpanded = ref(true)
const scrollContainer = ref<HTMLElement | null>(null)

// 过滤后的可见消息
const visibleMessages = computed(() => {
  return props.messages.filter(msg => shouldShowMessage(msg))
})

// 自动滚动到最新内容
const scrollToBottom = () => {
  nextTick(() => {
    if (scrollContainer.value) {
      scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight
    }
  })
}

// 监听消息变化，自动滚动到底部
watch(
  () => props.messages.length,
  () => {
    if (isExpanded.value) {
      scrollToBottom()
    }
  }
)

function getClassByType(type: ProgressMessage['type']): string {
  switch (type) {
    case 'error':
      return 'bg-red-500/10 text-red-300 border-l-2 border-red-500'
    case 'complete':
      return 'bg-green-500/10 text-green-300 border-l-2 border-green-500'
    case 'thinking':
      return 'bg-purple-500/10 text-purple-300 border-l-2 border-purple-500'
    case 'tool_start':
      return 'bg-cyan-500/10 text-cyan-300 border-l-2 border-cyan-500'
    case 'tool_result':
      // 根据成功失败状态返回不同样式
      return 'bg-cyan-500/10 text-cyan-300 border-l-2 border-cyan-500'
    default:
      return 'bg-slate-700/30 text-slate-300 border-l-2 border-cyan-500'
  }
}

function getIconByType(type: ProgressMessage['type']): string {
  switch (type) {
    case 'error': return '❌'
    case 'complete': return '✅'
    case 'thinking': return '🤔'
    case 'tool_start': return '🔧'
    case 'tool_result': return '📤'
    default: return '🔄'
  }
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

/**
 * 格式化消息内容，如果是 type 为 progress 的对象或 JSON，只显示 data 部分
 */
function formatContent(content: string | Record<string, any>): string {
  // 如果是对象
  if (content && typeof content === 'object') {
    if (content.type === 'progress' && content.data) {
      const data = content.data

      // 如果 data 是对象，特殊处理 tool_start 等类型
      if (typeof data === 'object') {
        if (data.type === 'tool_start' && data.toolName) {
          return `即将调用【${data.toolName}】工具`
        }
        if (data.type === 'tool_result' && data.toolName) {
          const resultContent = data.result?.content
          if (resultContent && typeof resultContent === 'string') {
            const preview = resultContent.length > 100 ? resultContent.slice(0, 100) + '...' : resultContent
            return `${data.toolName} 调用完成,结果：${preview}`
          }
          return `${data.toolName} 调用完成`
        }
        // 递归处理嵌套的 progress
        if (data.type === 'progress') {
          return formatContent(data as Record<string, any>)
        }
        // 其他对象类型，转为字符串
        return JSON.stringify(data)
      }

      // 如果 data 是字符串，直接返回
      if (typeof data === 'string') {
        return data
      }
    }
    return JSON.stringify(content)
  }

  // 如果是字符串
  if (typeof content !== 'string') return String(content ?? '')
  const trimmed = content.trim()
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
    try {
      const obj = JSON.parse(trimmed)
      if (obj.type === 'progress' && obj.data) {
        const data = obj.data

        // 如果 data 是对象，特殊处理 tool_start 等类型
        if (typeof data === 'object') {
          if (data.type === 'tool_start' && data.toolName) {
            return `即将调用【${data.toolName}】工具`
          }
          if (data.type === 'tool_result' && data.toolName) {
            const resultContent = data.result?.content
            if (resultContent && typeof resultContent === 'string') {
              const preview = resultContent.length > 100 ? resultContent.slice(0, 100) + '...' : resultContent
              return `${data.toolName} 调用完成,结果：${preview}`
            }
            return `${data.toolName} 调用完成`
          }
          // 递归处理嵌套的 progress
          if (data.type === 'progress') {
            return formatContent(data as Record<string, any>)
          }
          // 其他对象类型，转为字符串
          return JSON.stringify(data)
        }

        // 如果 data 是字符串，直接返回
        if (typeof data === 'string') {
          return data
        }
      }
    } catch {
      // 解析失败，返回原始内容
    }
  }
  return content
}

/**
 * 判断消息是否应该显示
 * content 为空时不显示
 */
function shouldShowMessage(msg: ProgressMessage): boolean {
  if (!msg || !msg.content) return false
  const content = msg.content
  if (content && typeof content === 'object') {
    // 非 progress 类型的对象不显示
    return (content as any).type === 'progress'
  }
  return true
}
</script>

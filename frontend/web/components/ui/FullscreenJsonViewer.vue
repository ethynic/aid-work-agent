<template>
  <Teleport to="body">
    <div
      v-if="modelValue"
      class="fixed inset-0 z-[70] bg-white flex flex-col"
      tabindex="-1"
      @keydown="onKeydown"
    >
      <!-- 顶部工具栏 -->
      <div class="flex items-center justify-between px-4 py-2 border-b border-gray-200 flex-shrink-0">
        <div class="flex items-center gap-3 min-w-0 flex-1">
          <h3 class="text-sm font-semibold text-gray-800 truncate">{{ title }}</h3>
          <span class="text-[11px] text-gray-400 tabular-nums flex-shrink-0">
            {{ formatBytes(byteSize) }} · {{ lineCount }} 行
          </span>
        </div>

        <div class="flex items-center gap-2 flex-shrink-0">
          <!-- 复制按钮 -->
          <button type="button"
            class="px-3 py-1 text-xs border border-gray-300 text-gray-600 rounded hover:bg-gray-50"
            :title="copySuccess ? '已复制' : '复制 JSON'"
            @click="onCopy">
            {{ copySuccess ? '已复制' : '复制' }}
          </button>
          <!-- 展开/折叠所有 -->
          <div class="inline-flex rounded-md border border-gray-200 text-xs overflow-hidden">
            <button type="button"
              :class="['px-2 py-1 transition-colors', expandDepth >= 99 ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
              title="全部展开"
              @click="expandAll">展开</button>
            <button type="button"
              :class="['px-2 py-1 transition-colors', expandDepth === 0 ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
              title="全部折叠"
              @click="collapseAll">折叠</button>
          </div>
          <!-- 关闭按钮 -->
          <button type="button"
            class="text-gray-400 hover:text-gray-600 p-1"
            title="关闭 (Esc)"
            @click="onClose">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- 主体：JSON 内容 -->
      <div ref="contentEl" class="flex-1 min-h-0 overflow-auto p-4 bg-canvas">
        <JsonViewer
          v-if="parsedData !== null"
          :key="renderKey"
          :data="parsedData"
          :max-preview="99999"
          :default-expand="expandDepth"
        />
        <pre v-else-if="rawText" class="font-mono text-sm whitespace-pre-wrap break-words text-default">{{ rawText }}</pre>
        <div v-else class="text-sm text-muted text-center py-12">(空)</div>
      </div>

      <!-- 底部状态栏 -->
      <div class="flex items-center justify-between px-4 py-1 border-t border-gray-200 text-[11px] text-gray-400 flex-shrink-0">
        <div class="flex items-center gap-3">
          <span>Esc 关闭</span>
        </div>
        <div class="flex items-center gap-3">
          <span>深 {{ expandDepth >= 99 ? '全部' : expandDepth }} 级</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import JsonViewer from './JsonViewer.vue'

/**
 * FullscreenJsonViewer - 全屏 JSON 查看器
 *
 * 用于大段 JSON 内容的全屏浏览：
 * - 全屏显示，不受父容器滚动条限制
 * - 支持复制（navigator.clipboard）
 * - 支持一键展开/折叠所有层级
 * - Esc 关闭
 * - 锁滚（body.overflow=hidden）
 */
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    /** JSON 字符串或已经解析过的对象 */
    data: any
  }>(),
  {
    title: 'JSON 详情',
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
}>()

const contentEl = ref<HTMLDivElement | null>(null)
const expandDepth = ref(2)
const copySuccess = ref(false)
const renderKey = ref(0)

const parsedData = computed<any>(() => {
  if (props.data === null || props.data === undefined) return null
  if (typeof props.data === 'string') {
    try {
      return JSON.parse(props.data)
    } catch {
      return null
    }
  }
  return props.data
})

const rawText = computed<string | null>(() => {
  if (props.data === null || props.data === undefined) return null
  if (typeof props.data !== 'string') return null
  try {
    JSON.parse(props.data)
    return null
  } catch {
    return props.data
  }
})

const byteSize = computed(() => {
  const text = typeof props.data === 'string' ? props.data : JSON.stringify(props.data ?? '')
  return new Blob([text]).size
})

const lineCount = computed(() => {
  const text = typeof props.data === 'string' ? props.data : JSON.stringify(props.data ?? '', null, 2)
  if (!text) return 0
  return text.split('\n').length
})

function expandAll() {
  expandDepth.value = 99
  renderKey.value++
}

function collapseAll() {
  expandDepth.value = 0
  renderKey.value++
}

async function onCopy() {
  const text = typeof props.data === 'string' ? props.data : JSON.stringify(props.data, null, 2)
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copySuccess.value = true
    setTimeout(() => { copySuccess.value = false }, 2000)
  } catch (e) {
    // 降级：使用 textarea 选中方式复制
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      copySuccess.value = true
      setTimeout(() => { copySuccess.value = false }, 2000)
    } catch (err) {
      console.error('复制失败:', err)
    } finally {
      document.body.removeChild(ta)
    }
  }
}

function onClose() {
  emit('update:modelValue', false)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    onClose()
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

watch(
  () => props.modelValue,
  (val) => {
    document.body.style.overflow = val ? 'hidden' : ''
    if (val) {
      // 每次打开重置为默认展开深度
      expandDepth.value = 2
      renderKey.value++
      nextTick(() => {
        contentEl.value?.focus()
      })
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
</script>

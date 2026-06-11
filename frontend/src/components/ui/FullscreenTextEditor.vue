<template>
  <div
    class="fixed inset-0 z-[70] bg-white flex flex-col"
    tabindex="-1"
    @keydown="onKeydown"
  >
    <!-- 顶部工具栏 -->
    <div class="flex items-center justify-between px-4 py-2 border-b border-gray-200 flex-shrink-0">
      <div class="flex items-center gap-3 min-w-0 flex-1">
        <h3 class="text-sm font-semibold text-gray-800 truncate">{{ title }}</h3>
        <span v-if="showCharCount" class="text-[11px] text-gray-400 tabular-nums flex-shrink-0">
          {{ charCount }} 字符
        </span>
        <span v-if="busy" class="inline-flex items-center text-[11px] text-info-600 flex-shrink-0">
          <svg class="w-3 h-3 animate-spin mr-1" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span>{{ busyText }}</span>
        </span>
        <!-- 字号调节 -->
        <div v-if="enableMarkdownPreview && effectiveMode !== 'off'" class="flex items-center gap-1 flex-shrink-0">
          <span class="text-[11px] text-gray-400">字号</span>
          <div class="inline-flex rounded-md border border-gray-200 text-[11px] overflow-hidden">
            <button v-for="opt in fontSizeOptions" :key="opt.value"
              type="button"
              :class="['px-1.5 py-0.5 transition-colors',
                previewFontSize === opt.value ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
              :title="opt.title"
              @click="setFontSize(opt.value)">{{ opt.label }}</button>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-2 flex-shrink-0">
        <!-- #extra 透传（AI 优化等业务按钮） -->
        <slot name="extra" />

        <!-- 编辑/双栏/预览 Tab -->
        <div v-if="enableMarkdownPreview"
          class="inline-flex rounded-md border border-gray-200 text-xs overflow-hidden">
          <button type="button"
            :class="['px-3 py-1 transition-colors', effectiveMode === 'off' ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
            @click="setMode('off')">编辑</button>
          <button type="button"
            :class="['px-3 py-1 transition-colors', effectiveMode === 'split' ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
            @click="setMode('split')" title="左编辑右预览">双栏</button>
          <button type="button"
            :class="['px-3 py-1 transition-colors', effectiveMode === 'preview' ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
            @click="setMode('preview')">预览</button>
        </div>

        <button type="button"
          class="px-3 py-1 text-xs border border-gray-300 text-gray-600 rounded hover:bg-gray-50"
          @click="onCancel">取消</button>
        <button type="button"
          class="px-3 py-1 text-xs bg-primary-600 text-white rounded hover:bg-primary-700 disabled:opacity-50"
          :disabled="!isDirty"
          @click="onSave">保存到编辑区</button>
        <button type="button"
          class="text-gray-400 hover:text-gray-600 p-1"
          title="关闭 (Esc)"
          @click="onCancel">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 主体 -->
    <div class="flex-1 min-h-0 overflow-hidden flex">
      <!-- 单栏预览 -->
      <MarkdownPreview
        v-if="effectiveMode === 'preview' && enableMarkdownPreview"
        :source="draft"
        :font-size="previewFontSize"
        class="flex-1"
      />
      <!-- 双栏：左编辑右预览 -->
      <div v-else-if="effectiveMode === 'split' && enableMarkdownPreview" class="flex flex-1 gap-2 p-2 min-h-0">
        <textarea
          ref="textareaEl"
          v-model="draft"
          :class="[
            'flex-1 p-3 text-sm border border-gray-200 rounded-lg outline-none resize-none overflow-auto',
            monospace ? 'font-mono' : '',
          ]"
          :placeholder="placeholder"
          spellcheck="false"
          @input="onInput"
          @scroll="onEditorScroll"
        />
        <div ref="previewWrapEl"
          class="flex-1 min-w-0 border border-gray-200 rounded-lg bg-white overflow-auto"
          @scroll="onPreviewScroll">
          <MarkdownPreview :source="draft" :font-size="previewFontSize" class="h-full" />
        </div>
      </div>
      <!-- 单栏编辑 -->
      <textarea
        v-else
        ref="textareaEl"
        v-model="draft"
        :class="[
          'flex-1 w-full p-4 text-sm border-0 outline-none resize-none overflow-auto',
          monospace ? 'font-mono' : '',
        ]"
        :placeholder="placeholder"
        spellcheck="false"
        @input="onInput"
      />
    </div>

    <!-- 底部状态栏 -->
    <div v-if="showStatusBar"
      class="flex items-center justify-between px-4 py-1 border-t border-gray-200 text-[11px] text-gray-400 flex-shrink-0">
      <div class="flex items-center gap-3">
        <span v-if="isDirty" class="text-warning-600">● 未保存</span>
        <span v-else>已保存</span>
        <span v-if="enableMarkdownPreview">行 {{ lineCount }} · 列 {{ colCount }}</span>
      </div>
      <div class="flex items-center gap-3">
        <span>Esc 关闭</span>
        <span>Ctrl/Cmd+S 保存</span>
        <span v-if="enableMarkdownPreview">Ctrl/Cmd+P 切换预览</span>
      </div>
    </div>

    <!-- 脏数据确认 -->
    <Teleport to="body">
      <div
        v-if="showDirtyConfirm"
        class="fixed inset-0 z-[80] flex items-center justify-center bg-black/40"
        @click.self="cancelDirtyConfirm"
      >
        <div class="bg-white rounded-lg shadow-xl w-full max-w-sm p-5">
          <div class="text-base font-semibold text-gray-800 mb-2">未保存的修改</div>
          <div class="text-sm text-gray-500 mb-5">当前编辑有未保存的修改，确定要关闭吗？</div>
          <div class="flex justify-end gap-2">
            <button
              class="h-9 px-4 rounded-lg text-sm font-medium border border-gray-300 text-gray-600 hover:bg-gray-50"
              @click="cancelDirtyConfirm"
            >继续编辑</button>
            <button
              class="h-9 px-4 rounded-lg text-sm font-medium bg-danger-600 text-white hover:bg-danger-700"
              @click="confirmDiscard"
            >不保存关闭</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MarkdownPreview from './MarkdownPreview.vue'

/**
 * FullscreenTextEditor - 全屏文本编辑器
 *
 * 由 MyTextarea 触发使用；也可单独被其他页面复用。
 *
 * 行为：
 * - 草稿模式：内部维护 draft，关闭/取消时若与初始值不同则弹"未保存"确认
 * - 保存：通过 update:modelValue 把 draft 写回父组件
 * - 快捷键：Esc 关闭、Ctrl/Cmd+S 保存、Ctrl/Cmd+P 在三模式间循环
 * - 锁滚：挂载时设 body.overflow=hidden，卸载时恢复
 * - 三模式：编辑 / 双栏（左编辑右预览，滚动同步）/ 单栏预览
 * - 字号：sm / base / lg（仅在预览模式可见调节按钮）
 * - busy：异步操作（如 AI 优化）时顶部显示旋转图标
 *
 * 事件：
 * - update:modelValue
 * - close：payload.saved 表示是否保存
 * - optimize：父组件可在 #extra 内的 AI 优化按钮触发此事件
 *
 * Slots：
 * - extra：工具栏右侧的扩展按钮（AI 优化等）
 */
type PreviewMode = 'off' | 'preview' | 'split'
type FontSize = 'sm' | 'base' | 'lg'

const fontSizeOptions: { value: FontSize; label: string; title: string }[] = [
  { value: 'sm', label: '小', title: '小号' },
  { value: 'base', label: '中', title: '中号' },
  { value: 'lg', label: '大', title: '大号' },
]

const props = withDefaults(
  defineProps<{
    modelValue: string
    title: string
    placeholder?: string
    monospace?: boolean
    enableMarkdownPreview?: boolean
    showStatusBar?: boolean
    showCharCount?: boolean
    /** 预览区字号 */
    previewFontSize?: FontSize
    /** 异步操作中（如 AI 优化）：true 时顶部显示旋转图标和文字 */
    busy?: boolean
    busyText?: string
  }>(),
  {
    placeholder: '',
    monospace: false,
    enableMarkdownPreview: false,
    showStatusBar: true,
    showCharCount: true,
    previewFontSize: 'sm',
    busy: false,
    busyText: '处理中...',
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'close', payload: { saved: boolean }): void
  (e: 'optimize'): void
}>()

const textareaEl = ref<HTMLTextAreaElement | null>(null)
const previewWrapEl = ref<HTMLDivElement | null>(null)
const draft = ref<string>(props.modelValue ?? '')
const initial = ref<string>(props.modelValue ?? '')
const internalMode = ref<PreviewMode>('off')
const internalFontSize = ref<FontSize>(props.previewFontSize)
const showDirtyConfirm = ref(false)

const effectiveMode = computed<PreviewMode>(() => internalMode.value)
const previewFontSize = computed<FontSize>(() => internalFontSize.value)
const charCount = computed(() => draft.value.length)
const isDirty = computed(() => draft.value !== initial.value)

// 行/列：从 textarea 选区计算
const lineCount = computed(() => {
  if (!textareaEl.value) return draft.value.split('\n').length
  return draft.value.substr(0, textareaEl.value.selectionStart).split('\n').length
})
const colCount = computed(() => {
  if (!textareaEl.value) return 1
  const before = draft.value.substr(0, textareaEl.value.selectionStart)
  const lastLineLen = before.split('\n').pop()?.length ?? 0
  return lastLineLen + 1
})

watch(
  () => props.modelValue,
  (v) => {
    // 父组件外部变化时（如有别的编辑入口）同步到 draft，但不要覆盖用户当前未保存输入
    if (v !== draft.value) {
      draft.value = v ?? ''
      initial.value = v ?? ''
    }
  },
)

watch(
  () => props.previewFontSize,
  (v) => {
    if (v) internalFontSize.value = v
  },
)

function setMode(mode: PreviewMode) {
  internalMode.value = mode
  scrollingFrom.value = null
}

function setFontSize(size: FontSize) {
  internalFontSize.value = size
}

/**
 * 滚动同步（双栏时）
 * 互斥机制：scrollingFrom 记录上一次滚动源，避免无限循环
 */
const scrollingFrom = ref<'editor' | 'preview' | null>(null)

function onEditorScroll(e: Event) {
  if (effectiveMode.value !== 'split') return
  if (scrollingFrom.value === 'preview') {
    scrollingFrom.value = null
    return
  }
  const ta = e.target as HTMLTextAreaElement
  const wrap = previewWrapEl.value
  if (!wrap) return
  const max = ta.scrollHeight - ta.clientHeight
  if (max <= 0) return
  const ratio = ta.scrollTop / max
  scrollingFrom.value = 'editor'
  wrap.scrollTop = ratio * (wrap.scrollHeight - wrap.clientHeight)
}

function onPreviewScroll(e: Event) {
  if (effectiveMode.value !== 'split') return
  if (scrollingFrom.value === 'editor') {
    scrollingFrom.value = null
    return
  }
  const wrap = e.target as HTMLDivElement
  const ta = textareaEl.value
  if (!ta) return
  const max = wrap.scrollHeight - wrap.clientHeight
  if (max <= 0) return
  const ratio = wrap.scrollTop / max
  scrollingFrom.value = 'preview'
  ta.scrollTop = ratio * (ta.scrollHeight - ta.clientHeight)
}

function onInput() {
  // 草稿本地变化，isDirty 自动计算
}

function onSave() {
  if (!isDirty.value) return
  emit('update:modelValue', draft.value)
  initial.value = draft.value
  emit('close', { saved: true })
}

function tryClose() {
  if (isDirty.value) {
    showDirtyConfirm.value = true
  } else {
    doClose(false)
  }
}

function onCancel() {
  tryClose()
}

function confirmDiscard() {
  showDirtyConfirm.value = false
  // 还原草稿到初始值，再关闭（不写回父组件）
  draft.value = initial.value
  doClose(false)
}

function cancelDirtyConfirm() {
  showDirtyConfirm.value = false
}

function doClose(saved: boolean) {
  emit('close', { saved })
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    onCancel()
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    onSave()
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p' && props.enableMarkdownPreview) {
    e.preventDefault()
    // 在 off / split / preview 三态间循环
    const order: PreviewMode[] = ['off', 'split', 'preview']
    const idx = order.indexOf(internalMode.value)
    setMode(order[(idx + 1) % order.length])
  }
}

onMounted(() => {
  document.body.style.overflow = 'hidden'
  // 挂载后自动聚焦并定位光标到末尾
  nextTick(() => {
    const el = textareaEl.value
    if (el) {
      el.focus()
      const len = el.value.length
      el.setSelectionRange(len, len)
    }
  })
})

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
</script>

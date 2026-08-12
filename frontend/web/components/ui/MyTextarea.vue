<template>
  <div :class="['flex flex-col relative', containerClass]">
    <!-- 顶部工具栏：标签 + busy + 字数 + 额外 slot + 全屏按钮 -->
    <div v-if="label || showCharCount || showFullscreen || $slots.extra || busy"
      class="flex items-center justify-between mb-1 gap-2">
      <div v-if="label || busy" class="text-xs truncate flex-1 min-w-0 flex items-center gap-1.5">
        <span v-if="busy" class="inline-flex items-center text-info-600">
          <svg class="w-3 h-3 animate-spin mr-1" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span>{{ busyText }}</span>
        </span>
        <span v-if="label" :class="busy ? 'text-gray-400' : 'text-gray-500'">
          <slot name="label">{{ label }}</slot>
        </span>
      </div>
      <div v-else class="flex-1" />
      <div class="flex items-center gap-2 flex-shrink-0">
        <slot name="extra" />
        <span v-if="showCharCount" class="text-[10px] text-gray-400 tabular-nums">
          {{ charCount }}{{ maxLength ? ` / ${maxLength}` : '' }}
        </span>
        <button
          v-if="showFullscreen"
          type="button"
          class="text-gray-400 hover:text-primary-600 p-0.5 rounded transition-colors"
          :title="isFullscreen ? '退出全屏' : '最大化编辑'"
          @click="openFullscreen"
        >
          <svg v-if="!isFullscreen" class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M4 8V4m0 0h4M4 4l5 5m11-5h-4m4 0v4m0-4l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
          </svg>
          <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M9 9V5a2 2 0 00-2-2H4M9 9H5a2 2 0 00-2 2v2M9 9l-5 5m11-5h4a2 2 0 012 2v2m0 0v4a2 2 0 01-2 2h-2m2-6l-5 5" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 主体：单栏预览 / 双栏 / 编辑 -->
    <div v-if="enablePreview && effectiveMode !== 'off'" class="flex-1 min-h-0">
      <!-- 单栏预览 -->
      <MarkdownPreview
        v-if="effectiveMode === 'preview'"
        :source="modelValue"
        :font-size="previewFontSize"
      />
      <!-- 双栏：左编辑右预览 -->
      <div v-else-if="effectiveMode === 'split'" class="flex gap-2 h-full">
        <textarea
          ref="textareaEl"
          :value="modelValue"
          :placeholder="placeholder"
          :disabled="disabled"
          :readonly="readonly"
          :class="[
            'flex-1 p-2 text-sm border border-gray-200 rounded-lg',
            'focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-100',
            'resize-none overflow-auto transition-colors',
            monospace ? 'font-mono' : '',
            disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white',
            textareaClass,
          ]"
          :style="autoResize === false ? { minHeight: minHeight } : null"
          @input="onInput"
          @blur="$emit('blur', $event)"
          @focus="$emit('focus', $event)"
        />
        <div class="flex-1 min-w-0 border border-gray-200 rounded-lg bg-white overflow-hidden">
          <MarkdownPreview
            :source="modelValue"
            :font-size="previewFontSize"
            class="h-full"
          />
        </div>
      </div>
    </div>
    <div v-else class="flex-1 min-h-0">
      <textarea
        ref="textareaEl"
        :value="modelValue"
        :placeholder="placeholder"
        :rows="rows"
        :disabled="disabled"
        :readonly="readonly"
        :class="[
          'w-full p-2 text-sm border border-gray-200 rounded-lg',
          'focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-100',
          'resize-none overflow-auto transition-colors',
          monospace ? 'font-mono' : '',
          disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white',
          textareaClass,
        ]"
        :style="autoResize === false ? { minHeight: minHeight } : null"
        @input="onInput"
        @blur="$emit('blur', $event)"
        @focus="$emit('focus', $event)"
      />
    </div>

    <!-- 预览模式切换（编辑 / 双栏 / 预览） -->
    <div v-if="enablePreview" class="flex items-center justify-end mt-1">
      <div class="inline-flex rounded-md border border-gray-200 text-[10px] overflow-hidden">
        <button type="button"
          :class="['px-2 py-0.5 transition-colors', effectiveMode === 'off' ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
          @click="setMode('off')">编辑</button>
        <button type="button"
          :class="['px-2 py-0.5 transition-colors', effectiveMode === 'split' ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
          @click="setMode('split')" title="左编辑右预览">双栏</button>
        <button type="button"
          :class="['px-2 py-0.5 transition-colors', effectiveMode === 'preview' ? 'bg-primary-50 text-primary-700' : 'bg-white text-gray-500 hover:bg-gray-50']"
          @click="setMode('preview')">预览</button>
      </div>
    </div>

    <!-- 全屏编辑器（Teleport 到 body，避免父级 overflow 裁剪） -->
    <Teleport to="body">
      <FullscreenTextEditor
        v-if="fullscreenOpen"
        :model-value="modelValue"
        :title="fullscreenTitle"
        :monospace="monospace"
        :enable-markdown-preview="enablePreview"
        :preview-font-size="previewFontSize"
        :busy="busy"
        :busy-text="busyText"
        :show-status-bar="true"
        @update:model-value="onFullscreenUpdate"
        @close="closeFullscreen"
        @optimize="$emit('optimize')"
      >
        <template v-if="$slots.extra" #extra>
          <slot name="extra" />
        </template>
      </FullscreenTextEditor>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import MarkdownPreview from './MarkdownPreview.vue'
import FullscreenTextEditor from './FullscreenTextEditor.vue'

/**
 * MyTextarea - 通用大文本框组件
 *
 * 能力：
 * - 受控 v-model
 * - 标签 / 占位符 / 等宽字体
 * - 字数统计
 * - 自动撑高（autoResize）
 * - 全屏编辑（showFullscreen）
 * - MD 预览（默认开启）：编辑 / 单栏预览 / 双栏同屏（enablePreview + previewMode）
 *   普通文本按 MD 渲染是 no-op，不会破坏体验；模板占位符语法按字面显示也无破坏性
 * - 预览字号：sm / base / lg（previewFontSize）
 * - busy 状态：异步操作（如 AI 优化）时整个工具栏显示"处理中"
 *
 * 扩展：
 * - #extra slot：工具栏右侧放业务按钮（如 AI 优化、占位符列表）
 *   该 slot 在普通态和全屏态都会渲染
 * - #label slot：自定义 label 渲染
 *
 * 事件：
 * - update:modelValue
 * - input / blur / focus
 * - update:previewMode（受控模式切换时回传）
 * - optimize：父组件可在 #extra 内的 AI 优化按钮触发此事件
 */
type PreviewMode = 'off' | 'preview' | 'split'

const props = withDefaults(
  defineProps<{
    modelValue: string
    label?: string
    placeholder?: string
    rows?: number
    monospace?: boolean
    showCharCount?: boolean
    maxLength?: number
    showFullscreen?: boolean
    enablePreview?: boolean
    /** 预览模式：编辑 / 单栏预览 / 双栏同屏（受控） */
    previewMode?: PreviewMode
    /** 预览区字号 */
    previewFontSize?: 'sm' | 'base' | 'lg'
    autoResize?: boolean
    disabled?: boolean
    readonly?: boolean
    containerClass?: string
    textareaClass?: string
    /** 自动撑高模式下初始最小高度 */
    minHeight?: string
    /** 异步操作中（如 AI 优化）：true 时工具栏显示 busyText，文本框可继续编辑但视觉提示忙碌 */
    busy?: boolean
    busyText?: string
  }>(),
  {
    rows: 4,
    monospace: false,
    showCharCount: false,
    showFullscreen: true,
    enablePreview: true,
    previewMode: 'off',
    previewFontSize: 'sm',
    autoResize: true,
    disabled: false,
    readonly: false,
    minHeight: '72px',
    busy: false,
    busyText: '处理中...',
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'input', event: Event): void
  (e: 'blur', event: FocusEvent): void
  (e: 'focus', event: FocusEvent): void
  (e: 'optimize'): void
  (e: 'update:previewMode', mode: PreviewMode): void
}>()

const textareaEl = ref<HTMLTextAreaElement | null>(null)
const internalMode = ref<PreviewMode>(props.previewMode)
const isFullscreen = ref(false)
const fullscreenOpen = ref(false)
const fullscreenTitle = ref('')

/** 始终跟随外部受控值；若外部传 'off' 则用内部值（让非受控用法也能切换） */
const effectiveMode = computed<PreviewMode>(() => {
  if (props.previewMode && props.previewMode !== 'off') return props.previewMode
  if (props.previewMode === 'off' && internalMode.value !== 'off') return internalMode.value
  return 'off'
})

function setMode(mode: PreviewMode) {
  internalMode.value = mode
  emit('update:previewMode', mode)
}

// 外部 previewMode 变化时同步内部状态
watch(
  () => props.previewMode,
  (v) => {
    if (v) internalMode.value = v
  },
)

const charCount = computed(() => (props.modelValue || '').length)

/** 自动撑高 */
function autoResize() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

function onInput(event: Event) {
  const target = event.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
  emit('input', event)
  if (props.autoResize) {
    nextTick(autoResize)
  }
}

// 外部 modelValue 变化时（如全屏关闭回写）也保持撑高
watch(
  () => props.modelValue,
  () => {
    if (props.autoResize) nextTick(autoResize)
  },
)

// 初始挂载后撑高一次
nextTick(autoResize)

/** 暴露给父组件手动触发撑高（兼容旧 watch 链路） */
defineExpose({
  resize: autoResize,
  focus: () => textareaEl.value?.focus(),
})

function openFullscreen() {
  fullscreenTitle.value = props.label || '编辑'
  isFullscreen.value = true
  fullscreenOpen.value = true
  document.body.style.overflow = 'hidden'
}

function closeFullscreen(payload: { saved: boolean }) {
  fullscreenOpen.value = false
  isFullscreen.value = false
  document.body.style.overflow = ''
  nextTick(() => textareaEl.value?.focus())
  void payload
}

function onFullscreenUpdate(v: string) {
  emit('update:modelValue', v)
}
</script>

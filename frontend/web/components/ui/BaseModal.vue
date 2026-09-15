<template>
  <Teleport to="body">
    <div v-if="modelValue" :class="[slots.overlay(), modalClass]" @mousedown="onOverlayMouseDown" @click.self="handleOverlayClick">
      <div ref="dialogElement" :class="[slots.content(), contentClass]" :role="accessible ? 'dialog' : undefined" :aria-modal="accessible ? 'true' : undefined" :aria-labelledby="accessible ? titleId : undefined" :tabindex="accessible ? -1 : undefined">
        <div :class="slots.header()">
          <h3 :id="accessible ? titleId : undefined" :class="slots.title()">
            <slot name="title">{{ title }}</slot>
          </h3>
          <div class="flex items-center gap-1">
            <slot name="header-extra"></slot>
            <button
              class="w-7 h-7 flex items-center justify-center rounded text-muted hover:text-default hover:bg-surface-hover transition-colors"
              :title="isFullscreen ? '退出全屏' : '全屏'"
              @click="toggleFullscreen"
            >
              <svg v-if="!isFullscreen" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
              </svg>
              <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 14h6v6m10-10h-6V4M14 10l7-7M3 21l7-7"/>
              </svg>
            </button>
            <button :class="slots.close()" :aria-label="accessible ? '关闭对话框' : undefined" @click="handleCloseClick">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
        <div :class="slots.body()">
          <slot />
        </div>
        <div v-if="$slots.footer" :class="slots.footer()">
          <slot name="footer" />
        </div>
      </div>
    </div>

    <!-- 编辑模式：脏数据确认对话框 -->
    <div
      v-if="showDirtyConfirm"
      class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
      @click.self="cancelDirtyConfirm"
    >
      <div class="bg-surface rounded-lg shadow-xl w-full max-w-sm p-5">
        <div class="text-base font-semibold text-default mb-2">未保存的修改</div>
        <div class="text-sm text-muted mb-5">当前页面有未保存的修改，确定要关闭吗？</div>
        <div class="flex justify-end gap-2">
          <button
            class="h-9 px-4 rounded-lg text-sm font-medium border border-default text-default hover:bg-surface-hover"
            @click="cancelDirtyConfirm"
          >取消</button>
          <button
            class="h-9 px-4 rounded-lg text-sm font-medium bg-danger-600 text-white hover:bg-danger-700"
            @click="confirmDiscard"
          >不保存关闭</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch, nextTick, getCurrentInstance, onBeforeUnmount } from 'vue'
import { modal } from '@/variants/modal'

const props = withDefaults(defineProps<{
  modelValue: boolean
  title?: string
  size?: 'sm' | 'md' | 'lg' | 'lgx' | 'xl'
  scrollable?: boolean
  /** 显式启用对话框语义、键盘焦点管理；既有调用者默认行为不变。 */
  accessible?: boolean
  /**
   * 弹框模式，决定点击遮罩层和关闭按钮的行为：
   * - 'create'（默认）：新增页，点击遮罩不关闭，避免用户填写大量数据时误关闭
   * - 'edit'：编辑页，点击遮罩时若 isDirty 为 true 则弹出确认对话框；否则直接关闭
   * - 'view'：详情页，点击遮罩直接关闭
   */
  mode?: 'create' | 'edit' | 'view'
  /**
   * 编辑模式下是否有未保存的修改。
   * - 函数：每次点击遮罩/关闭按钮时调用
   * - 布尔：直接判断
   * 当 mode='edit' 时生效
   */
  isDirty?: boolean | (() => boolean)
  /**
   * 是否允许点击遮罩关闭。
   * 默认 false（安全默认值），可通过 mode 属性自动控制；
   * 显式设置 true 时强制允许，false 时强制禁止。
   */
  closeOnOverlay?: boolean
  // Class bindings for fullscreen support
  class?: string | Record<string, boolean>
  contentClass?: string | Record<string, boolean>
}>(), {
  size: 'md',
  scrollable: true,
  accessible: false,
  mode: 'create',
  closeOnOverlay: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const slots = computed(() =>
  modal({ size: props.size, scrollable: props.scrollable })
)

const modalClass = computed(() => {
  if (!props.class) return ''
  if (typeof props.class === 'string') return props.class
  return Object.entries(props.class).filter(([, v]) => v).map(([k]) => k).join(' ')
})
const contentClass = computed(() => {
  const classes: string[] = []
  if (props.contentClass) {
    if (typeof props.contentClass === 'string') classes.push(props.contentClass)
    else classes.push(...Object.entries(props.contentClass).filter(([, v]) => v).map(([k]) => k))
  }
  if (isFullscreen.value) classes.push('modal-fullscreen')
  return classes.join(' ')
})

const isFullscreen = ref(false)
function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

const showDirtyConfirm = ref(false)
const dialogElement = ref<HTMLElement>()
const titleId = `base-modal-title-${getCurrentInstance()?.uid}`
let previousFocus: HTMLElement | null = null
let focusGeneration = 0

function restoreFocus() {
  document.removeEventListener('keydown', handleAccessibleKeydown)
  if (previousFocus?.isConnected) previousFocus.focus()
  previousFocus = null
}
function handleAccessibleKeydown(event: KeyboardEvent) {
  if (!props.accessible || !props.modelValue || !dialogElement.value || showDirtyConfirm.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    handleCloseClick()
    return
  }
  if (event.key !== 'Tab') return
  const dialog = dialogElement.value
  const controls = Array.from(dialog.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, [tabindex]')).filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && !element.closest('[hidden], [inert]') && getComputedStyle(element).display !== 'none' && getComputedStyle(element).visibility !== 'hidden')
  const first = controls[0], last = controls[controls.length - 1]
  if (!first) { event.preventDefault(); dialog.focus(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus() }
}
watch(() => props.accessible && props.modelValue, async visible => {
  const generation = ++focusGeneration
  if (!visible) { restoreFocus(); return }
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  if (generation !== focusGeneration || !props.accessible || !props.modelValue) return
  dialogElement.value?.focus()
  document.addEventListener('keydown', handleAccessibleKeydown)
}, { immediate: true })
onBeforeUnmount(() => { focusGeneration++; restoreFocus() })

function checkDirty(): boolean {
  if (typeof props.isDirty === 'function') return props.isDirty()
  return !!props.isDirty
}

function doClose() {
  emit('update:modelValue', false)
}

function tryClose() {
  // 显式 closeOnOverlay 优先
  if (props.closeOnOverlay === false) return

  if (props.closeOnOverlay === true) {
    doClose()
    return
  }

  // 根据 mode 走默认行为
  if (props.mode === 'view') {
    doClose()
  } else if (props.mode === 'edit') {
    if (checkDirty()) {
      showDirtyConfirm.value = true
    } else {
      doClose()
    }
  }
  // mode === 'create'：不关闭
}

// 记录 mousedown 是否落在遮罩层上（弹框外）
let overlayMouseDown = false

function onOverlayMouseDown(e: MouseEvent) {
  overlayMouseDown = e.target === e.currentTarget
}

function handleOverlayClick() {
  // 从弹框内拖拽选择文本、在弹框外松开鼠标时，浏览器会把 click 派发到
  // mousedown 与 mouseup 的共同祖先（即遮罩层），触发 @click.self。
  // 此时 mousedown 实际发生在弹框内部，应忽略，不能关闭弹框。
  if (!overlayMouseDown) return
  tryClose()
}

function handleCloseClick() {
  // 右上角 X 按钮：edit 模式仍需脏检测；create 模式允许关闭（用户主动点 X 视为确认）
  if (props.mode === 'edit' && checkDirty()) {
    showDirtyConfirm.value = true
  } else {
    doClose()
  }
}

function cancelDirtyConfirm() {
  showDirtyConfirm.value = false
}

function confirmDiscard() {
  showDirtyConfirm.value = false
  doClose()
}

watch(() => props.modelValue, (val) => {
  document.body.style.overflow = val ? 'hidden' : ''
  if (!val) {
    showDirtyConfirm.value = false
    isFullscreen.value = false
  }
})
</script>

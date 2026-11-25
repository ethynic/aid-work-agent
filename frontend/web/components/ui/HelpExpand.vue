<template>
  <div>
    <button
      type="button"
      class="inline-flex items-center gap-1 text-xs text-muted hover:text-primary-600 transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-primary-400 rounded"
      :aria-expanded="isOpen"
      @click="toggle"
    >
      <svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.8">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M12 18h.01" />
        <circle cx="12" cy="12" r="9" />
      </svg>
      <span>{{ label }}</span>
      <svg class="w-3 h-3 flex-shrink-0 transition-transform" :class="isOpen ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </button>
    <div v-show="isOpen" class="mt-2 p-3 bg-canvas border border-default rounded-lg text-xs text-muted leading-relaxed">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

/*
 * 轻量可展开帮助说明组件。
 *
 * 【文案同步要求】本组件承载的帮助文案源自《智能体后台操作指引》（产品经理维护）。
 * 相关功能改版/变化时，必须同步修改对应页面的帮助文字。
 *
 * 展开状态支持两种模式：
 * - 非受控：不传 open，点击按钮在内部切换（原有行为）。
 * - 受控：传 open，点击按钮只发出 update:open，由外部决定展开状态。
 */
const props = withDefaults(defineProps<{
  /** 触发按钮文案，默认「填写指引」 */
  label?: string
  /** 外部控制的展开状态；不传时为非受控模式 */
  open?: boolean
}>(), {
  label: '填写指引',
  // 显式默认 undefined：避免 Vue 把未传的布尔属性转成 false 而误入受控模式
  open: undefined,
})

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
}>()

const innerOpen = ref(false)

const isOpen = computed(() => (props.open !== undefined ? props.open : innerOpen.value))

function toggle() {
  if (props.open === undefined) {
    innerOpen.value = !innerOpen.value
  } else {
    emit('update:open', !props.open)
  }
}
</script>

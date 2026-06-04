<template>
  <Teleport to="body">
    <div v-if="modelValue" :class="[slots.overlay(), modalClass]" @click.self="handleOverlayClick">
      <div :class="[slots.content(), contentClass]">
        <div :class="slots.header()">
          <h3 :class="slots.title()">
            <slot name="title">{{ title }}</slot>
          </h3>
          <div class="flex items-center gap-1">
            <slot name="header-extra"></slot>
            <button :class="slots.close()" @click="close">
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
  </Teleport>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { modal } from '@/variants/modal'

const props = withDefaults(defineProps<{
  modelValue: boolean
  title?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  scrollable?: boolean
  closeOnOverlay?: boolean
  // Class bindings for fullscreen support
  class?: string | Record<string, boolean>
  contentClass?: string | Record<string, boolean>
}>(), {
  size: 'md',
  scrollable: true,
  closeOnOverlay: true,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const slots = computed(() =>
  modal({ size: props.size, scrollable: props.scrollable })
)

// Merge user-provided classes with variant classes (support string or object)
const modalClass = computed(() => {
  if (!props.class) return ''
  if (typeof props.class === 'string') return props.class
  return Object.entries(props.class).filter(([, v]) => v).map(([k]) => k).join(' ')
})
const contentClass = computed(() => {
  if (!props.contentClass) return ''
  if (typeof props.contentClass === 'string') return props.contentClass
  return Object.entries(props.contentClass).filter(([, v]) => v).map(([k]) => k).join(' ')
})

function close() {
  emit('update:modelValue', false)
}

function handleOverlayClick() {
  if (props.closeOnOverlay) close()
}

watch(() => props.modelValue, (val) => {
  document.body.style.overflow = val ? 'hidden' : ''
})
</script>

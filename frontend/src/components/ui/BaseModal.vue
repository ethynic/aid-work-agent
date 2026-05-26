<template>
  <Teleport to="body">
    <div v-if="modelValue" :class="slots.overlay()" @click.self="handleOverlayClick">
      <div :class="slots.content()">
        <div :class="slots.header()">
          <h3 :class="slots.title()">
            <slot name="title">{{ title }}</slot>
          </h3>
          <button :class="slots.close()" @click="close">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
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

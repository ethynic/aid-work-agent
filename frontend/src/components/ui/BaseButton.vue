<template>
  <button :class="classes" :disabled="disabled" @click="$emit('click', $event)">
    <slot />
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { button } from '@/variants/button'

const props = withDefaults(defineProps<{
  intent?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  fullWidth?: boolean
  disabled?: boolean
}>(), {
  intent: 'primary',
  size: 'md',
  fullWidth: false,
  disabled: false,
})

defineEmits<{ click: [e: MouseEvent] }>()

const classes = computed(() =>
  button({ intent: props.intent, size: props.size, fullWidth: props.fullWidth })
)
</script>

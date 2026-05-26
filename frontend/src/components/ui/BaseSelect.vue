<template>
  <select
    :class="classes"
    :value="modelValue"
    :disabled="disabled"
    @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <slot />
  </select>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { select } from '@/variants/select'

const props = withDefaults(defineProps<{
  modelValue?: string
  state?: 'default' | 'error'
  size?: 'sm' | 'md' | 'lg'
  disabled?: boolean
}>(), {
  state: 'default',
  size: 'md',
  disabled: false,
})

defineEmits<{
  'update:modelValue': [value: string]
}>()

const classes = computed(() =>
  select({ state: props.state, size: props.size })
)
</script>

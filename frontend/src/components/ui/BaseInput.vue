<template>
  <input
    :class="classes"
    :type="type"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    @focus="$emit('focus', $event)"
    @blur="$emit('blur', $event)"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { input } from '@/variants/input'

const props = withDefaults(defineProps<{
  modelValue?: string
  type?: string
  placeholder?: string
  state?: 'default' | 'error' | 'success'
  size?: 'sm' | 'md' | 'lg'
  disabled?: boolean
}>(), {
  type: 'text',
  state: 'default',
  size: 'md',
  disabled: false,
})

defineEmits<{
  'update:modelValue': [value: string]
  focus: [e: FocusEvent]
  blur: [e: FocusEvent]
}>()

const classes = computed(() =>
  input({ state: props.state, size: props.size })
)
</script>

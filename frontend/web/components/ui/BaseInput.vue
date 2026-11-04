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
import { computed, useAttrs } from 'vue'
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

const attrs = useAttrs()

// 父组件传入宽度类（如 w-[400px]）时去掉 variant 的 w-full，
// 否则编译后 CSS 中 w-full 排在后面会把父传宽度覆盖掉（与 BaseSelect 同模式）
const classes = computed(() => {
  const base = input({ state: props.state, size: props.size })
  const extra = (attrs.class as string) || ''
  return extra.trim() ? base.replace('w-full', '').trim() + ' ' + extra : base
})
</script>

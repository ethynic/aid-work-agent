<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :aria-label="ariaLabel"
    :disabled="disabled"
    :class="[
      'relative inline-flex h-[22px] w-[40px] flex-none items-center rounded-full transition-colors',
      'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2',
      modelValue ? 'bg-primary-600' : 'bg-gray-300',
      disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
    ]"
    @click="$emit('update:modelValue', !modelValue)"
  >
    <span
      :class="[
        'inline-block h-[18px] w-[18px] rounded-full bg-white shadow transition-transform',
        modelValue ? 'translate-x-[20px]' : 'translate-x-[2px]',
      ]"
    ></span>
  </button>
</template>

<script setup lang="ts">
defineProps<{
  modelValue: boolean
  disabled?: boolean
  ariaLabel?: string
}>()

defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

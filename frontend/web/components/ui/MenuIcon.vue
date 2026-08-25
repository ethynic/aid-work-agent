<template>
  <!-- 当 icon 是非 ASCII 字符（emoji）时按原样渲染；否则当作 SVG path 渲染 -->
  <span v-if="isEmoji" class="text-base leading-none">{{ icon }}</span>
  <svg
    v-else
    class="w-5 h-5 flex-shrink-0"
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path :d="icon" />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  icon: string
}>()

// 仅当 icon 字符串中包含非 ASCII 字符（如 emoji）时按 emoji 渲染
const isEmoji = computed(() => {
  const s = props.icon || ''
  if (!s) return false
  // 任一字符码点 > 127 即视为 emoji
  for (let i = 0; i < s.length; i++) {
    if (s.charCodeAt(i) > 127) return true
  }
  return false
})
</script>

<template>
  <span :class="classes">
    <slot />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { badge } from '@/variants/badge'

const props = withDefaults(defineProps<{
  intent?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  size?: 'sm' | 'md' | 'lg'
}>(), {
  intent: 'neutral',
  size: 'md',
})

const classes = computed(() =>
  badge({ intent: props.intent, size: props.size })
)
</script>

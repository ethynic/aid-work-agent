<template>
  <div :class="slots.wrapper()">
    <div v-if="$slots.header || title" :class="slots.header()">
      <slot name="header">
        <h3 :class="slots.title()">{{ title }}</h3>
      </slot>
    </div>
    <div :class="slots.body()">
      <slot />
    </div>
    <div v-if="$slots.footer" :class="slots.footer()">
      <slot name="footer" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { card } from '@/variants/card'

const props = withDefaults(defineProps<{
  title?: string
  padding?: 'sm' | 'md' | 'lg'
  hoverable?: boolean
}>(), {
  padding: 'md',
  hoverable: false,
})

const slots = computed(() =>
  card({ padding: props.padding, hoverable: props.hoverable })
)
</script>

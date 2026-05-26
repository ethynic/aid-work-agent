<template>
  <div :class="slots.wrapper()">
    <span :class="slots.info()">
      显示 {{ (currentPage - 1) * pageSize + 1 }}-{{ Math.min(currentPage * pageSize, total) }} 条，共 {{ total }} 条
    </span>
    <div :class="slots.buttons()">
      <button
        :class="[slots.button(), !hasPrev && 'opacity-50 pointer-events-none']"
        :disabled="!hasPrev"
        @click="emit('update:currentPage', currentPage - 1)"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
        </svg>
      </button>
      <button
        v-for="page in visiblePages"
        :key="page"
        :class="[slots.pageButton(), page === currentPage ? 'bg-primary-600 text-white font-medium' : 'text-default hover:bg-gray-100']"
        @click="emit('update:currentPage', page)"
      >
        {{ page }}
      </button>
      <button
        :class="[slots.button(), !hasNext && 'opacity-50 pointer-events-none']"
        :disabled="!hasNext"
        @click="emit('update:currentPage', currentPage + 1)"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { pagination } from '@/variants/pagination'

const props = defineProps<{
  total: number
  currentPage: number
  pageSize: number
}>()

const emit = defineEmits<{
  'update:currentPage': [page: number]
}>()

const slots = computed(() => pagination())

const totalPages = computed(() => Math.ceil(props.total / props.pageSize))
const hasPrev = computed(() => props.currentPage > 1)
const hasNext = computed(() => props.currentPage < totalPages.value)

const visiblePages = computed(() => {
  const pages: number[] = []
  const total = totalPages.value
  const current = props.currentPage

  let start = Math.max(1, current - 2)
  let end = Math.min(total, current + 2)

  if (end - start < 4) {
    if (start === 1) {
      end = Math.min(total, start + 4)
    } else {
      start = Math.max(1, end - 4)
    }
  }

  for (let i = start; i <= end; i++) {
    pages.push(i)
  }
  return pages
})
</script>

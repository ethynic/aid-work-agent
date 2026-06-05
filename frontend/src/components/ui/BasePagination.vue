<template>
  <div :class="slots.wrapper()">
    <span :class="slots.info()">
      共 {{ total }} 条
    </span>
    <BaseSelect
      v-if="showSizeChanger"
      :model-value="String(pageSize)"
      size="sm"
      class="w-24"
      @update:model-value="handleSizeChange"
    >
      <option v-for="opt in mergedSizeOptions" :key="opt" :value="String(opt)">{{ opt }}条/页</option>
    </BaseSelect>
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
          :class="[slots.pageButton(), page === currentPage ? 'bg-primary-600 !text-white font-medium hover:!bg-primary-600' : 'text-default']"
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
import BaseSelect from './BaseSelect.vue'

const props = withDefaults(defineProps<{
  total: number
  currentPage: number
  pageSize: number
  showSizeChanger?: boolean
  sizeOptions?: number[]
}>(), {
  showSizeChanger: true,
  sizeOptions: () => [10, 20, 50, 100, 200],
})

const emit = defineEmits<{
  'update:currentPage': [page: number]
  'update:pageSize': [size: number]
}>()

const slots = computed(() => pagination())

const mergedSizeOptions = computed(() => props.sizeOptions)

const totalPages = computed(() => Math.ceil(props.total / props.pageSize))
const hasPrev = computed(() => props.currentPage > 1)
const hasNext = computed(() => props.currentPage < totalPages.value)

function handleSizeChange(val: string) {
  emit('update:pageSize', Number(val))
}

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

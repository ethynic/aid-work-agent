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
          @click="goToPage(currentPage - 1)"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <button
          v-for="page in visiblePages"
          :key="page"
          :class="[slots.pageButton(), page === currentPage ? 'bg-primary-600 !text-white font-medium hover:!bg-primary-600' : 'text-default']"
          @click="goToPage(page)"
        >
          {{ page }}
        </button>
        <button
          :class="[slots.button(), !hasNext && 'opacity-50 pointer-events-none']"
          :disabled="!hasNext"
          @click="goToPage(currentPage + 1)"
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

/**
 * 事件说明：
 *
 * - `change(page, size)`：分页器发生任何变化（翻页 / 改变每页行数）时触发。
 *   父组件应当在此回调内**重新拉取数据**（如果当前页是服务端分页）。
 *   客户端切片的场景可以忽略此事件，响应式会自动重算切片。
 *
 * - `update:currentPage` / `update:pageSize`：Vue 标准的 v-model 同步事件。
 *   仅同步 ref 的值，**不会**触发数据拉取。请不要在"需要拉数据"的场景下
 *   只用 v-model 而不监听 @change —— 这正是历史上"分页器不工作" bug 的根因。
 */
const emit = defineEmits<{
  'change': [page: number, size: number]
  'update:currentPage': [page: number]
  'update:pageSize': [size: number]
}>()

const slots = computed(() => pagination())

const mergedSizeOptions = computed(() => props.sizeOptions)

const totalPages = computed(() => Math.ceil(props.total / props.pageSize))
const hasPrev = computed(() => props.currentPage > 1)
const hasNext = computed(() => props.currentPage < totalPages.value)

// 翻页：同步 ref + 通知父组件需要重新拉数据
function goToPage(page: number) {
  if (page < 1 || page > totalPages.value || page === props.currentPage) {
    return
  }
  emit('update:currentPage', page)
  emit('change', page, props.pageSize)
}

// 改变每页行数：同步 ref + 重置到第 1 页 + 通知父组件需要重新拉数据
function handleSizeChange(val: string) {
  const size = Number(val)
  if (size === props.pageSize) return
  emit('update:pageSize', size)
  emit('update:currentPage', 1)
  emit('change', 1, size)
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

<template>
  <div :class="slots.wrapper()">
    <table :class="slots.table()">
      <thead :class="slots.thead()">
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            :class="slots.th()"
            :style="[
              col.width ? { width: col.width } : undefined,
              col.minWidth ? { minWidth: col.minWidth } : undefined,
              col.thAlign ? { textAlign: col.thAlign } : undefined
            ]"
          >
            <slot :name="col.key + '_header'" :row="null" :index="-1">
              {{ col.label }}
            </slot>
          </th>
        </tr>
      </thead>
      <tbody :class="slots.tbody()">
        <tr v-if="data.length === 0">
          <td :colspan="columns.length" :class="slots.empty()">
            <slot name="empty">暂无数据</slot>
          </td>
        </tr>
        <tr
          v-for="(row, index) in data"
          :key="rowKey ? row[rowKey] : index"
          :class="slots.tr({ stripe: getStripeClass(index) })"
        >
          <td
            v-for="col in columns"
            :key="col.key"
            :class="slots.td()"
            :title="getCellTitle(row, col)"
          >
            <slot :name="col.key" :row="row" :index="index">
              {{ row[col.key] }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { table } from '@/variants/table'

export interface TableColumn {
  key: string
  label: string
  width?: string
  minWidth?: string
  thAlign?: 'left' | 'center' | 'right'
  /** 自定义悬停提示内容，不填则取 row[col.key] */
  tooltip?: string | ((row: Record<string, any>) => string | undefined)
}

defineProps<{
  columns: TableColumn[]
  data: Record<string, any>[]
  rowKey?: string
}>()

const slots = computed(() => table())

// 获取单元格 title（悬停显示完整内容）
function getCellTitle(row: Record<string, any>, col: TableColumn): string | undefined {
  if (col.tooltip) {
    if (typeof col.tooltip === 'function') {
      return col.tooltip(row)
    }
    return col.tooltip
  }
  const value = row[col.key]
  if (value == null) return undefined
  return String(value)
}

// 斑马线：奇数行白色背景，偶数行主题色浅色背景
function getStripeClass(index: number): 'odd' | 'even' {
  return index % 2 === 0 ? 'odd' : 'even'
}
</script>

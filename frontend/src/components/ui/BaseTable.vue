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
              col.thAlign ? { textAlign: col.thAlign } : undefined
            ]"
          >
            {{ col.label }}
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
          :class="slots.tr()"
        >
          <td
            v-for="col in columns"
            :key="col.key"
            :class="slots.td()"
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
  thAlign?: 'left' | 'center' | 'right'
}

defineProps<{
  columns: TableColumn[]
  data: Record<string, any>[]
  rowKey?: string
}>()

const slots = computed(() => table())
</script>

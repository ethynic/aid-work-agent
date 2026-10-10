<template>
  <!-- 品牌色圆角方块 + 白色类型字标/线性路径：尺寸由使用方通过 class 控制（如 w-4 h-4 / w-9 h-9） -->
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <rect x="1.5" y="1.5" width="21" height="21" rx="4.5" :fill="meta.color" />
    <text
      v-if="meta.text !== undefined"
      x="12" y="12.5" text-anchor="middle" dominant-baseline="central"
      :font-size="meta.fontSize" font-weight="700"
      font-family="Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif"
      fill="#fff"
    >{{ meta.text }}</text>
    <path
      v-else
      :d="meta.path ?? ''"
      stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" fill="none"
    />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { FileIconKind } from '@/utils/file'

const props = defineProps<{ kind: FileIconKind }>()

interface IconMeta {
  color: string
  /** 文字字标（与 path 二选一） */
  text?: string
  fontSize?: number
  /** Lucide 线性路径（与 text 二选一），风格与 agentIcons 一致 */
  path?: string
}

const META: Record<FileIconKind, IconMeta> = {
  word: { color: '#2563EB', text: 'W', fontSize: 12 },
  excel: { color: '#16A34A', text: 'X', fontSize: 12 },
  ppt: { color: '#EA580C', text: 'P', fontSize: 12 },
  pdf: { color: '#DC2626', text: 'PDF', fontSize: 7.5 },
  image: { color: '#8B5CF6', path: 'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z' },
  markdown: { color: '#4F46E5', text: 'MD', fontSize: 10 },
  text: { color: '#64748B', text: 'TXT', fontSize: 7.5 },
  code: { color: '#0891B2', path: 'M16 18l6-6-6-6M8 6l-6 6 6 6' },
  html: { color: '#F59E0B', text: '</>', fontSize: 8 },
  archive: { color: '#A16207', path: 'M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8M10 12h4M2 8h20' },
  other: { color: '#6B7280', path: 'M15 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V7z M14 2v4a2 2 0 002 2h4 M16 13H8 M16 17H8 M10 9H8' },
}

const meta = computed(() => META[props.kind])
</script>

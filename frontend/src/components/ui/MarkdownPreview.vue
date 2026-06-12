<template>
  <div
    :class="[
      'markdown-body prose max-w-none p-4 overflow-auto h-full break-words',
      fontSizeClass,
    ]"
    v-html="rendered"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

/**
 * Markdown 预览组件
 *
 * 复用项目全局 utils/markdown.ts（marked + highlight.js），
 * 避免重复注册 hljs 语言。
 *
 * 警告：使用 v-html 渲染，调用方需保证 source 来源可信。
 * 内部 Prompt 场景下用户为已登录内部人员，P0 阶段风险可接受。
 */
type FontSize = 'sm' | 'base' | 'lg'

const props = withDefaults(
  defineProps<{
    source: string
    breaks?: boolean  // 将 \n 转换为 <br>，默认 true（适合单行换行的 prompt）
    /** 字号：sm 对应 prose-sm，base 对应 prose-base，lg 对应 prose-lg */
    fontSize?: FontSize
  }>(),
  { breaks: true, fontSize: 'sm' },
)

const fontSizeClass = computed(() => {
  switch (props.fontSize) {
    case 'lg': return 'prose-lg'
    case 'base': return 'prose-base'
    case 'sm':
    default: return 'prose-sm'
  }
})

const rendered = computed(() => {
  if (!props.source) return ''
  return renderMarkdown(props.source)
})
</script>

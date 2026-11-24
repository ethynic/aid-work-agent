<template>
  <div class="weixin-summary-markdown markdown-content prose prose-sm min-w-0 max-w-none overflow-x-auto text-default [overflow-wrap:anywhere]" v-html="html" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps<{ source: string }>()

// 群聊中出现的媒体地址不应在查看摘要时自动请求。
const html = computed(() => renderMarkdown(props.source, undefined, { textOnly: true }))
</script>

<style scoped>
.weixin-summary-markdown :deep(> :first-child) {
  margin-top: 0;
}

.weixin-summary-markdown :deep(> :last-child) {
  margin-bottom: 0;
}
</style>

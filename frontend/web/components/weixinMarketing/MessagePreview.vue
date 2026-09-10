<template>
  <!-- 内容包预览：按块顺序模拟微信消息气泡（只读，不含表单逻辑） -->
  <div class="rounded-lg border border-default bg-canvas p-3">
    <div class="flex items-start gap-2">
      <div class="w-8 h-8 rounded-full bg-primary-100 text-primary-700 flex items-center justify-center flex-shrink-0 text-xs font-medium">
        机器人
      </div>
      <div class="flex-1 min-w-0 space-y-1.5">
        <template v-if="blocks.length">
          <div
            v-for="(block, index) in blocks"
            :key="index"
            class="max-w-[420px] w-fit rounded-lg bg-white border border-default px-3 py-2 text-sm text-default break-all"
          >
            <template v-if="block.type === 'text'">{{ block.text_content }}</template>
            <a
              v-else-if="block.type === 'link' && isSafeUrl(block.url)"
              :href="block.url"
              target="_blank"
              rel="noopener noreferrer"
              class="text-primary-600 hover:text-primary-700 underline break-all"
            >{{ block.url }}</a>
            <span v-else-if="block.type === 'link'" class="text-muted break-all">{{ block.url }}</span>
            <div v-else class="flex items-center gap-2 text-muted">
              <span class="w-10 h-10 rounded border border-dashed border-default flex items-center justify-center text-xs">图</span>
              <span class="text-xs break-all">图片素材 {{ block.asset_id }}（P4 后可预览）</span>
            </div>
          </div>
        </template>
        <div v-else class="text-sm text-muted">暂无内容块</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** MessagePreview：内容包预览（MessagePreview.vue，微信计划 §8） */
import type { ContentBlockSpec } from '@/api/weixinMarketing'

defineProps<{
  blocks: ContentBlockSpec[]
}>()

/** 仅 http(s) 渲染为可点击链接，防止存量数据注入 javascript: 等协议 */
function isSafeUrl(url: string): boolean {
  return /^https?:\/\//i.test(url.trim())
}
</script>

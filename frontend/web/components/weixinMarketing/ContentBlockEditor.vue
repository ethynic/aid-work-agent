<template>
  <!-- 有序内容块编辑：text/link 逐条编辑 + 上下移动（按钮与 Alt+方向键）+ 上限校验 -->
  <div class="space-y-3">
    <div class="flex items-center justify-between">
      <div class="text-sm text-muted">
        共 {{ blocks.length }} / {{ maxBlocks }} 条，按顺序发送
      </div>
      <div class="flex gap-2">
        <BaseButton size="sm" :disabled="disabled || blocks.length >= maxBlocks" @click="addBlock('text')">加文字</BaseButton>
        <BaseButton size="sm" intent="secondary" :disabled="disabled || blocks.length >= maxBlocks" @click="addBlock('link')">加网址</BaseButton>
        <BaseButton size="sm" intent="secondary" :disabled="true" title="图片块随素材库（P4）开放">加图片</BaseButton>
      </div>
    </div>

    <div
      v-for="(block, index) in blocks"
      :key="index"
      class="rounded-lg border border-default bg-surface p-3 space-y-2"
      tabindex="0"
      @keydown.alt.up.prevent="moveBlock(index, -1)"
      @keydown.alt.down.prevent="moveBlock(index, 1)"
    >
      <div class="flex items-center gap-2">
        <BaseBadge :intent="block.type === 'text' ? 'primary' : block.type === 'link' ? 'info' : 'warning'">
          #{{ index + 1 }} {{ block.type === 'text' ? '文字' : block.type === 'link' ? '网址' : '图片' }}
        </BaseBadge>
        <span class="text-xs text-muted">Alt+↑ / Alt+↓ 调整顺序</span>
        <span class="flex-spacer"></span>
        <BaseButton size="sm" intent="ghost" :disabled="disabled || index === 0" @click="moveBlock(index, -1)">上移</BaseButton>
        <BaseButton size="sm" intent="ghost" :disabled="disabled || index === blocks.length - 1" @click="moveBlock(index, 1)">下移</BaseButton>
        <BaseButton size="sm" intent="danger-ghost" :disabled="disabled" @click="removeBlock(index)">删除</BaseButton>
      </div>

      <!-- 文字块 -->
      <template v-if="block.type === 'text'">
        <input
          :value="block.text_content"
          type="text"
          :maxlength="BLOCK_TEXT_MAX_LENGTH"
          :disabled="disabled"
          class="w-full rounded-lg border bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          :class="fieldError(index) ? 'border-danger-500' : 'border-default'"
          placeholder="单条消息正文（不支持换行）"
          @change="e => updateBlock(index, { text_content: (e.target as HTMLInputElement).value } as TextBlockSpec)"
        />
        <div class="flex justify-between text-xs" :class="fieldError(index) ? 'text-danger-600' : 'text-muted'">
          <span>{{ fieldError(index) || `${block.text_content.length}/${BLOCK_TEXT_MAX_LENGTH} 字符` }}</span>
        </div>
      </template>

      <!-- 网址块 -->
      <template v-else-if="block.type === 'link'">
        <input
          :value="block.url"
          type="text"
          :maxlength="BLOCK_TEXT_MAX_LENGTH"
          :disabled="disabled"
          class="w-full rounded-lg border bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          :class="fieldError(index) ? 'border-danger-500' : 'border-default'"
          placeholder="https://example.com（以 http:// 或 https:// 开头）"
          @change="e => updateBlock(index, { url: (e.target as HTMLInputElement).value } as LinkBlockSpec)"
        />
        <div v-if="fieldError(index)" class="text-xs text-danger-600">{{ fieldError(index) }}</div>
      </template>

      <!-- 图片块（P4 前只读展示，无法新建） -->
      <template v-else>
        <div class="text-sm text-default break-all">asset_id：{{ block.asset_id }}</div>
        <p class="text-xs text-muted">图片素材库在 P4 交付，现有图片块保持原样提交。</p>
      </template>
    </div>

    <div v-if="!blocks.length" class="empty-state">
      <div class="empty-state-icon">▦</div>
      <p class="text-sm text-muted">暂无内容块，至少添加 1 条（文字或网址）</p>
    </div>
    <p v-if="lengthError" class="text-xs text-danger-600">{{ lengthError }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * ContentBlockEditor：ContentBlockSpec 有序块编辑。
 * - 文字/网址单行输入（后端 BLOCK_TEXT_FORBIDDEN_CHARS 禁换行/NUL，长度上限 500）；
 * - 排序：上移/下移按钮 + 块卡片聚焦后 Alt+↑/↓；
 * - 上限：maxBlocks（后端 DEFAULT_MAX_BLOCKS=20，超出拒绝新增并在超限时提示）。
 */
import { computed } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  BLOCK_TEXT_MAX_LENGTH,
  MAX_BLOCKS,
  type ContentBlockSpec,
  type LinkBlockSpec,
  type TextBlockSpec,
} from '@/api/weixinMarketing'
import { validateBlock } from './weixinDisplay'

const props = defineProps<{
  blocks: ContentBlockSpec[]
  disabled?: boolean
  /** 超过上限仍强制送入（如后端返回数 > 前端上限）时的提示 */
  forceOverflow?: boolean
}>()

const emit = defineEmits<{
  'update:blocks': [blocks: ContentBlockSpec[]]
}>()

const maxBlocks = MAX_BLOCKS

const lengthError = computed(() => {
  if (props.forceOverflow && props.blocks.length > maxBlocks) {
    return `内容块数量 ${props.blocks.length} 超过上限 ${maxBlocks}，保存前请删除多余条目`
  }
  return ''
})

function fieldError(index: number): string {
  const block = props.blocks[index]
  return block ? validateBlock(block) : ''
}

function addBlock(kind: 'text' | 'link') {
  if (props.disabled || props.blocks.length >= maxBlocks) return
  const block: ContentBlockSpec =
    kind === 'text' ? { type: 'text', text_content: '' } : { type: 'link', url: '' }
  emit('update:blocks', [...props.blocks, block])
}

function updateBlock(index: number, patch: TextBlockSpec | LinkBlockSpec) {
  const next = props.blocks.map((b, i) => (i === index ? { ...b, ...patch } : b))
  emit('update:blocks', next)
}

function removeBlock(index: number) {
  if (props.disabled) return
  emit('update:blocks', props.blocks.filter((_, i) => i !== index))
}

function moveBlock(index: number, delta: -1 | 1) {
  if (props.disabled) return
  const target = index + delta
  if (target < 0 || target >= props.blocks.length) return
  const next = [...props.blocks]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  emit('update:blocks', next)
}
</script>

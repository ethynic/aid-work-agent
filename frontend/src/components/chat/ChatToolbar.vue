<template>
  <!-- 聊天工具栏容器（Phase 5.1.2）
       接收 buttonIds prop，按注册表顺序渲染额外按钮；
       空数组时整体不显示。
       ChatToolbar 与 ChatInput 中硬编码的加号按钮并列渲染。 -->
  <div v-if="buttons.length > 0" class="flex items-center gap-1">
    <component
      :is="meta.component"
      v-for="meta in buttons"
      :key="meta.id"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { resolveToolbarButtons } from './toolbar-buttons/registry'

interface Props {
  /** 当前会话 subagent 的 chat_toolbar 字段（按钮 id 列表） */
  buttonIds?: string[] | null
}

const props = defineProps<Props>()

const buttons = computed(() => resolveToolbarButtons(props.buttonIds))
</script>

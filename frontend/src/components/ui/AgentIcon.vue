<template>
  <!-- 数字员工图标：根据 agentId 稳定匹配到 AGENT_ICON_PATHS 中的一个 path 渲染 -->
  <svg
    class="w-5 h-5 flex-shrink-0"
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path :d="iconPath" />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { getAgentIconPath } from '@/utils/agentIcons'

/**
 * 数字员工图标组件
 *
 * 通过 agentId 稳定匹配到图标库中的一个 path 进行渲染。
 * 同一 agentId 始终渲染同一图标（视觉记忆稳定）。
 *
 * 样式规范与 MenuIcon.vue / BusinessPageIcon.vue 一致：
 * - 24×24 viewBox
 * - 描边宽度 1.6
 * - stroke=currentColor（继承父级颜色）
 *
 * 通过外部类名控制颜色和尺寸（如 `text-primary-600 w-6 h-6`）。
 */
const props = defineProps<{
  /** 数字员工 ID，用于稳定匹配图标 */
  agentId: string
}>()

const iconPath = computed(() => getAgentIconPath(props.agentId))
</script>

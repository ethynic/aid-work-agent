<template>
  <!-- 右上角全屏按钮 -->
  <button
    type="button"
    class="text-gray-400 hover:text-primary-600 p-0.5 rounded transition-colors"
    title="全屏选择"
    @click="open"
  >
    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M4 8V4m0 0h4M4 4l5 5m11-5h-4m4 0v4m0-4l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
    </svg>
  </button>

  <!-- 全屏弹层 -->
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[70] bg-white flex flex-col"
      tabindex="-1"
      @keydown="onKeydown"
    >
      <!-- 顶部工具栏 -->
      <div class="flex items-center justify-between px-4 py-2 border-b border-gray-200 flex-shrink-0">
        <h3 class="text-sm font-semibold text-gray-800 truncate">{{ title }}</h3>
        <div class="flex items-center gap-2 flex-shrink-0">
          <button type="button"
            class="px-3 py-1 text-xs bg-primary-600 text-white rounded hover:bg-primary-700"
            @click="close">完成</button>
          <button type="button" class="text-gray-400 hover:text-gray-600 p-1" title="关闭 (Esc)" @click="close">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- 主体：放大的选项列表 -->
      <div class="flex-1 min-h-0 overflow-y-auto p-4 bg-canvas">
        <div class="max-w-3xl mx-auto">
          <slot name="content" />
        </div>
      </div>

      <!-- 底部状态栏 -->
      <div class="flex items-center justify-between px-4 py-1 border-t border-gray-200 text-[11px] text-gray-400 flex-shrink-0">
        <span>已选 {{ selectedCount }} 项</span>
        <span>Esc 关闭</span>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

/**
 * FullscreenPicker - 全屏选项选择器外壳
 *
 * 用于选项很多的列表（如工具/技能配置）：右上角提供全屏按钮，
 * 点击后弹出全屏弹层放大查看/勾选，解决可视区域过小滚动不便的问题。
 *
 * 特点：
 * - 选中状态由父组件通过 #content slot 直接绑定，实时同步，关闭即生效（无需保存动作）
 * - Esc / 右上角"完成"或关闭按钮退出
 * - 锁滚（body.overflow=hidden）
 *
 * Slots:
 * - #content：全屏弹层主体内容（放大的选项列表）
 */
withDefaults(
  defineProps<{
    title: string
    selectedCount?: number
  }>(),
  {
    selectedCount: 0,
  },
)

const isOpen = ref(false)

function open() {
  isOpen.value = true
  document.body.style.overflow = 'hidden'
}

function close() {
  isOpen.value = false
  document.body.style.overflow = ''
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  }
}

onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
</script>

<template>
  <!-- 视频生成按钮（Phase 5.1.4）
       点击打开参数弹框，确认后写入 useVideoGenParams 状态 -->
  <button
    type="button"
    @click="openDialog"
    class="w-8 h-8 rounded-full border border-gray-300 text-gray-500 hover:text-primary-600 hover:border-primary-400 transition-colors flex items-center justify-center"
    title="视频生成参数"
  >
    <!-- 视频图标（电影胶片样式） -->
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
    </svg>
  </button>

  <!-- 参数弹框 -->
  <VideoGenParamsDialog
    :open="isDialogOpen"
    :draft="draft"
    @close="closeDialog"
    @commit="commitDialog"
    @update="updateDraft"
  />
</template>

<script setup lang="ts">
import { useVideoGenParams } from '@/composables/useVideoGenParams'
import VideoGenParamsDialog from '../VideoGenParamsDialog.vue'

const { isDialogOpen, draft, openDialog, closeDialog, commitDialog, updateDraft } = useVideoGenParams()
</script>

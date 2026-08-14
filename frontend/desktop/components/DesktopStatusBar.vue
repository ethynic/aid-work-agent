<template>
  <footer class="desktop-statusbar" aria-label="应用状态">
    <div role="status" aria-live="polite" class="status-cluster">
      <span :class="['status-dot', connectivity]" aria-hidden="true" />
      <span>{{ connectivity === 'online' ? '云端已连接' : '云端离线 · 只读' }}</span>
      <button v-if="connectivity === 'offline'" type="button" class="status-action" @click="$emit('retry')">重试</button>
    </div>
    <div class="status-cluster"><span>本地执行</span><span class="status-muted">Phase E 接入</span></div>
  </footer>
</template>

<script setup lang="ts">
import type { Connectivity } from '@desktop/state/startup'
defineProps<{ connectivity: Connectivity }>()
defineEmits<{ retry: [] }>()
</script>

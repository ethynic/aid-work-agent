<template>
  <header class="desktop-titlebar" data-testid="desktop-titlebar">
    <div class="desktop-brand-mark" aria-hidden="true">A</div>
    <div class="desktop-title-copy">
      <strong>AID Agent</strong>
      <span>执行控制台</span>
    </div>
    <div class="desktop-title-spacer" />
    <span class="desktop-environment">{{ platformLabel }}</span>
  </header>
</template>

<script setup lang="ts">
defineProps<{ platformLabel: string }>()
</script>

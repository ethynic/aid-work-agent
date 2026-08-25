<template>
  <div class="desktop-frame">
    <DesktopTitlebar :platform-label="platformLabel" />
    <DesktopSidebar :username="username" :tenant-code="tenantCode" @sign-out="$emit('sign-out')" />
    <main id="desktop-workspace" class="desktop-workspace" tabindex="-1"><RouterView /></main>
    <DesktopStatusBar :connectivity="connectivity" @retry="$emit('retry')" />
  </div>
</template>

<script setup lang="ts">
import DesktopSidebar from './DesktopSidebar.vue'
import DesktopStatusBar from './DesktopStatusBar.vue'
import DesktopTitlebar from './DesktopTitlebar.vue'
import type { Connectivity } from '@desktop/state/startup'

defineProps<{ username: string; tenantCode: string; connectivity: Connectivity; platformLabel: string }>()
defineEmits<{ retry: []; 'sign-out': [] }>()
</script>

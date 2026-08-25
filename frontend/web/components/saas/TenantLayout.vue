<template>
  <div class="h-screen flex bg-surface-hover">
    <MenuSidebar
      v-if="isLoggedIn"
      :is-collapsed="sidebarCollapsed"
      :is-mobile="isMobile"
      :show-history="true"
      :show-new-session="true"
      :current-subagent-id="currentSubagentId"
      :available-subagents="availableSubagents"
      @collapse="sidebarCollapsed = true"
    />

    <main class="flex-1 flex flex-col overflow-hidden">
      <router-view class="flex-1 min-h-0" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import MenuSidebar from '@/components/MenuSidebar.vue'
import { useMobile } from '@/composables/useMobile'
import { useSubagentList } from '@/composables/useSubagentList'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const { isLoggedIn, init } = useTenantAuth()
const { isMobile } = useMobile()
const { availableSubagents, loadAvailableSubagents } = useSubagentList()

const sidebarCollapsed = ref(typeof window !== 'undefined' && window.innerWidth < 768)
const tenantId = computed(() => route.params.tenant_id as string)
const currentSubagentId = computed(() => {
  const segments = route.path.split('/').filter(Boolean)
  if (segments.length < 3) return undefined
  return segments[2] === 'chat' && segments.length >= 4 ? segments[3] : segments[2]
})

watch(() => route.path, (path) => {
  if ((path.endsWith('/login') || path.endsWith('/reset-password')) && isMobile.value) {
    sidebarCollapsed.value = true
  }
})
watch(isLoggedIn, (loggedIn, wasLoggedIn) => {
  if (!loggedIn) {
    sidebarCollapsed.value = true
  } else if (wasLoggedIn === false && typeof window !== 'undefined' && window.innerWidth >= 768) {
    sidebarCollapsed.value = false
  }
})

provide('sidebarCollapsed', sidebarCollapsed)
provide('toggleSidebar', () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
})
provide('collapseSidebar', () => {
  sidebarCollapsed.value = true
})

onMounted(async () => {
  const routeName = route.name as string | undefined
  if (routeName?.includes('login') || routeName?.includes('reset-password')) return

  await init()
  if (!isLoggedIn.value) {
    await router.push(`/t/${tenantId.value}/login`)
    return
  }
  await loadAvailableSubagents(true)
})
</script>

<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="视频创作"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <main class="flex-1 overflow-y-auto">
      <VideoGeneration />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import VideoGeneration from './VideoGeneration.vue'

const route = useRoute()
const router = useRouter()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const { user: demoUser, isLoggedIn: demoIsLoggedIn, logout: demoLogout } = useDemoAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const tenantId = computed(() => route.params.tenant_id as string | undefined)
const isTenantPath = computed(() => Boolean(tenantId.value))
const effectiveIsLoggedIn = computed(() => isTenantPath.value ? tenantIsLoggedIn.value : demoIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : demoUser.value)

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

async function handleLogout() {
  if (isTenantPath.value) {
    await tenantLogout()
  } else {
    await demoLogout()
  }
  router.push(tenantId.value ? `/t/${tenantId.value}/login` : '/')
}
</script>

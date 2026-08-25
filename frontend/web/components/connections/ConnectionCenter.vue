<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- 顶部标题栏 -->
    <AppHeader
      title="连接中心"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <!-- Tab 切换 -->
    <div class="flex-shrink-0 bg-white border-b border-default">
      <div class="px-6 flex gap-6">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          @click="activeTab = tab.key"
          :class="[
            'py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === tab.key
              ? 'text-primary-600 border-primary-600'
              : 'text-muted border-transparent hover:text-default hover:border-hover'
          ]"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <!-- Tab 内容 -->
    <div class="flex-1 min-h-0 overflow-hidden">
      <ApiConfigTab v-show="activeTab === 'api-config'" />
      <EnvVarsTab v-show="activeTab === 'env-vars'" />

      <!-- 内置连接器：Phase 2 实现 -->
      <div v-if="activeTab === 'builtin-connectors'" class="h-full flex items-center justify-center">
        <div class="text-center">
          <div class="inline-flex items-center justify-center w-12 h-12 rounded-full bg-primary-50 text-primary-500 mb-3">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 7V3M15 7V3M9 21v-4M15 21v-4M5 12H3M21 12h-2M7 9h10a2 2 0 012 2v2a2 2 0 01-2 2H7a2 2 0 01-2-2v-2a2 2 0 012-2z" />
            </svg>
          </div>
          <p class="text-sm text-default font-medium">即将上线</p>
          <p class="text-xs text-muted mt-1">内置连接器（Tavily、SMTP、飞书、企查查等 12-15 个）将在 Phase 2 上线</p>
        </div>
      </div>

      <!-- 自定义连接器：Phase 4 实现 -->
      <div v-if="activeTab === 'custom-connectors'" class="h-full flex items-center justify-center">
        <div class="text-center">
          <div class="inline-flex items-center justify-center w-12 h-12 rounded-full bg-primary-50 text-primary-500 mb-3">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 4v16m8-8H4" />
            </svg>
          </div>
          <p class="text-sm text-default font-medium">即将上线</p>
          <p class="text-xs text-muted mt-1">自定义连接器（MCP / OpenAPI 双轨）将在 Phase 4 上线</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import ApiConfigTab from './ApiConfigTab.vue'
import EnvVarsTab from './EnvVarsTab.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const tenantId = computed(() => route.params.tenant_id as string)
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone,
  } : null
})

const toggleSidebarFn = inject<() => void>('toggleSidebar')

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}

type TabKey = 'api-config' | 'env-vars' | 'builtin-connectors' | 'custom-connectors'

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: 'api-config', label: 'API 配置' },
  { key: 'env-vars', label: '环境变量' },
  { key: 'builtin-connectors', label: '内置连接器' },
  { key: 'custom-connectors', label: '自定义连接器' },
]

const activeTab = ref<TabKey>('api-config')
</script>

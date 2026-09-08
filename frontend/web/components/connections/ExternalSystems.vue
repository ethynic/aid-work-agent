<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="外部系统"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="flex-1 overflow-y-auto p-6">
      <div v-if="isLoading" class="flex items-center justify-center py-12 text-sm text-muted">
        加载中...
      </div>

      <div v-else-if="systems.length === 0" class="flex flex-col items-center justify-center py-20">
        <svg class="w-12 h-12 text-gray-300 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0zM12 8v4M12 16h.01"/>
        </svg>
        <p class="text-sm text-muted">暂无可用外部系统</p>
        <p class="text-xs text-muted mt-1">外部系统由租户接口文档（pre-sales-api.md）的 sso 配置声明</p>
      </div>

      <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 max-w-5xl">
        <div
          v-for="item in systems"
          :key="item.system_id"
          class="bg-surface border border-default rounded-lg p-5 flex flex-col"
        >
          <div class="flex items-center gap-3 mb-3">
            <div class="w-10 h-10 rounded-lg bg-primary-50 flex items-center justify-center">
              <svg class="w-5 h-5 text-primary-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/>
              </svg>
            </div>
            <div class="min-w-0">
              <p class="text-sm font-medium text-default truncate">{{ item.name }}</p>
              <p class="text-xs text-muted">{{ modeLabel(item.mode) }}</p>
            </div>
          </div>
          <div class="flex-spacer"></div>
          <BaseButton v-if="item.sso_ready" :disabled="openingId === item.system_id" @click="handleOpen(item)">
            {{ openingId === item.system_id ? '打开中...' : '打开' }}
          </BaseButton>
          <template v-else>
            <BaseButton disabled>打开</BaseButton>
            <p class="text-xs text-warning-600 mt-2">SSO 配置不完整，请联系管理员检查接口文档 sso 配置</p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useToast } from 'vue-toastification'
import { listExternalSystems, openExternalSystem } from '@/api/externalSystems'
import type { ExternalSystem } from '@/api/externalSystems'

const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init, isInitialized, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

const isLoading = ref(true)
const systems = ref<ExternalSystem[]>([])
const openingId = ref('')

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

function handleLogout() {
  if (tenantLogout) tenantLogout()
}

function modeLabel(mode: ExternalSystem['mode']): string {
  const map: Record<ExternalSystem['mode'], string> = {
    direct_url: '直接打开入口网址',
    ticket_redirect: '单点登录（免密跳转）',
    token_param: '单点登录（Token 直连）',
    form_submit: '账号代填',
  }
  return map[mode] || mode
}

async function handleOpen(item: ExternalSystem) {
  openingId.value = item.system_id
  try {
    const result = await openExternalSystem(item)
    if (!result.ok && result.message) {
      toast.warning(result.message)
    }
  } finally {
    openingId.value = ''
  }
}

onMounted(async () => {
  if (!isInitialized.value) await init()
  try {
    systems.value = await listExternalSystems()
  } catch (e) {
    toast.error((e as Error).message || '获取外部系统列表失败')
  } finally {
    isLoading.value = false
  }
})
</script>

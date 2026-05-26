<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Header Bar -->
    <AppHeader
      title="企业设置"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button
          @click="goToChat(); closeMenu()"
          class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          返回对话
        </button>
        <button
          @click="openCustomerInfo(); closeMenu()"
          class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
          </svg>
          我的客户
        </button>
        <button
          @click="openScheduledTasks(); closeMenu()"
          class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          我的定时任务
        </button>
      </template>
    </AppHeader>

    <!-- Main Content -->
    <div class="flex-1 overflow-y-auto p-6">
      <h1 class="text-2xl font-bold text-default mb-6">企业设置</h1>

      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

      <div v-else class="max-w-lg">
        <div class="bg-white rounded-xl shadow-sm border border-default p-6">
          <div class="space-y-4">
            <div>
              <label class="block text-sm text-default mb-1">企业名称</label>
              <input v-model="form.company_name" type="text"
                class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
            </div>
            <div>
              <label class="block text-sm text-default mb-1">联系人</label>
              <input v-model="form.contact_name" type="text"
                class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
            </div>
            <div>
              <label class="block text-sm text-default mb-1">联系电话</label>
              <input v-model="form.contact_phone" type="tel"
                class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
            </div>
          </div>

          <div v-if="message" class="mt-4 p-3 rounded-lg text-sm"
            :class="messageType === 'success' ? 'bg-success-50 border border-success-200 text-success-600' : 'bg-danger-50 border border-danger-200 text-danger-600'">
            {{ message }}
          </div>

          <button
            @click="handleSave"
            :disabled="saving"
            class="mt-6 w-full py-2.5 bg-primary-500 hover:bg-primary-600 disabled:bg-surface-hover text-white rounded-lg font-medium transition-colors"
          >
            {{ saving ? '保存中...' : '保存设置' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import { getTenantInfo, updateTenantInfo } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const tenantId = computed(() => route.params.tenant_id as string)
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

// 统一的用户信息
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 从 PortalLayout 注入侧边栏状态
const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')

// 侧边栏折叠状态
const localSidebarCollapsed = ref(false)
const isSidebarCollapsed = computed({
  get: () => sidebarCollapsed?.value ?? localSidebarCollapsed.value,
  set: (val: boolean) => {
    if (sidebarCollapsed) {
      sidebarCollapsed.value = val
    } else {
      localSidebarCollapsed.value = val
    }
  }
})

function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  } else {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}

function goToChat() {
  router.push(`/t/${tenantId.value}/chat`)
}

function openCustomerInfo() {
  const userId = effectiveUser.value?.user_id
  if (userId) {
    window.open(`/customer-info?user_id=${userId}`, '_blank')
  } else {
    toast.warning('请先登录')
  }
}

function openScheduledTasks() {
  window.open('/scheduled-tasks', '_blank')
}

const loading = ref(true)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

const form = ref({
  company_name: '',
  contact_name: '',
  contact_phone: ''
})

async function loadSettings() {
  loading.value = true
  try {
    const res = await getTenantInfo()
    if (res.tenant) {
      form.value = {
        company_name: res.tenant.company_name || '',
        contact_name: res.tenant.contact_name || '',
        contact_phone: res.tenant.contact_phone || ''
      }
    }
  } catch (e) {
    console.error('加载企业设置失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  message.value = ''
  try {
    await updateTenantInfo(form.value)
    message.value = '保存成功'
    messageType.value = 'success'
  } catch (e: any) {
    message.value = e.message || '保存失败'
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

onMounted(() => loadSettings())
</script>

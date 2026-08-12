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
    </AppHeader>

    <!-- Main Content -->
    <div class="flex-1 overflow-y-auto p-6">
      <h1 class="text-2xl font-bold text-default mb-6">企业设置</h1>

      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

      <div v-else class="max-w-lg">
        <div class="bg-white rounded-xl shadow-sm border border-default p-6">
          <div class="space-y-4">
            <div>
              <label class="block text-sm text-default mb-1">Logo</label>
              <LogoUpload v-model="form.logo_file_id" />
            </div>
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
import AppHeader from '@/components/AppHeader.vue'
import LogoUpload from '@/components/ui/LogoUpload.vue'
import { getTenantInfo, updateTenantInfo } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
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

const loading = ref(true)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

const form = ref({
  company_name: '',
  contact_name: '',
  contact_phone: '',
  logo_file_id: null as string | null
})

async function loadSettings() {
  loading.value = true
  try {
    const res = await getTenantInfo()
    if (res.tenant) {
      form.value = {
        company_name: res.tenant.company_name || '',
        contact_name: res.tenant.contact_name || '',
        contact_phone: res.tenant.contact_phone || '',
        logo_file_id: res.tenant.logo_file_id || null
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

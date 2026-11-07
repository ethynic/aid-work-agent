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

      <div v-else class="max-w-lg space-y-6">
        <div class="bg-white rounded-xl shadow-sm border border-default p-6">
          <div class="space-y-4">
            <div>
              <label class="block text-sm text-default mb-1">Logo</label>
              <LogoUpload v-model="form.logo_file_id" :tenant-id="tenantId" />
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

        <!-- 计划任务设置（§15：本租户每人活跃任务上限；全局最短间隔只读） -->
        <div id="tenant-scheduler" class="scroll-mt-4">
          <div class="bg-white rounded-xl shadow-sm border border-default p-6 space-y-4">
            <div>
              <h2 class="text-base font-semibold text-default">计划任务</h2>
              <p class="mt-1 text-sm text-muted">本租户成员创建与恢复定时任务时生效的全局约束。</p>
            </div>
            <div v-if="schedulerLoading" class="text-sm text-muted">加载计划任务设置…</div>
            <template v-else-if="scheduler">
              <div>
                <label class="block text-sm text-default mb-1">最短执行间隔（全局，只读）</label>
                <input type="text" :value="`${scheduler.min_interval_hours} 小时（整数）`" disabled
                  class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-muted" />
                <p class="mt-1 text-xs text-muted">间隔任务只接受不低于该值的整数小时；由平台配置管理，租户不可修改。</p>
              </div>
              <div>
                <label for="tenant-scheduler-max-tasks" class="block text-sm text-default mb-1">每人活跃任务上限</label>
                <div class="flex items-center gap-2">
                  <input id="tenant-scheduler-max-tasks" v-model="schedulerMaxTasks" type="number" min="1" step="1"
                    class="w-32 px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400"
                    :disabled="schedulerSaving" />
                  <button @click="handleSaveScheduler" :disabled="schedulerSaving"
                    class="px-4 py-2 bg-primary-500 hover:bg-primary-600 disabled:bg-surface-hover text-white rounded-lg text-sm font-medium transition-colors">
                    {{ schedulerSaving ? '保存中…' : '保存' }}
                  </button>
                  <button v-if="scheduler.max_tasks_per_user !== null" @click="handleResetScheduler" :disabled="schedulerSaving"
                    class="px-4 py-2 border border-hover hover:bg-surface-hover text-default rounded-lg text-sm transition-colors">
                    恢复默认
                  </button>
                </div>
                <p class="mt-1 text-xs text-muted">
                  当前生效：{{ scheduler.effective_max_tasks_per_user }} 个/人
                  {{ scheduler.max_tasks_per_user === null ? '（未设置覆盖，使用全局默认）' : '（本租户覆盖值）' }}。
                  暂停/完成/过期任务不占名额；调低上限不影响已有任务，超额时仅限制新建与恢复。
                </p>
              </div>
            </template>
            <div v-else class="text-sm text-danger-600">计划任务设置加载失败，请刷新重试。</div>
            <div v-if="schedulerMessage" class="p-3 rounded-lg text-sm"
              :class="schedulerMessageType === 'success' ? 'bg-success-50 border border-success-200 text-success-600' : 'bg-danger-50 border border-danger-200 text-danger-600'">
              {{ schedulerMessage }}
            </div>
          </div>
        </div>

        <!-- 显示偏好（会话图片“AI生成”角标开关） -->
        <div class="bg-white rounded-xl shadow-sm border border-default p-6 space-y-4">
          <div>
            <h2 class="text-base font-semibold text-default">显示偏好</h2>
            <p class="mt-1 text-sm text-muted">会话界面的内容标识显示开关，对本租户所有用户生效。</p>
          </div>
          <div>
            <label class="flex items-center gap-3 cursor-pointer">
              <input v-model="showAiBadge" type="checkbox" :disabled="prefSaving"
                class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
              <span class="text-sm text-default">会话图片显示“AI 生成”角标</span>
            </label>
            <p class="mt-1 text-xs text-muted">
              按国家《人工智能生成合成内容标识办法》，AI 生成内容应有显式标识；关闭后由本企业自行承担合规责任。生成图片文件本身不含水印。
            </p>
          </div>
          <button @click="handleSavePreferences" :disabled="prefSaving"
            class="px-4 py-2 bg-primary-500 hover:bg-primary-600 disabled:bg-surface-hover text-white rounded-lg text-sm font-medium transition-colors">
            {{ prefSaving ? '保存中…' : '保存' }}
          </button>
          <div v-if="prefMessage" class="p-3 rounded-lg text-sm"
            :class="prefMessageType === 'success' ? 'bg-success-50 border border-success-200 text-success-600' : 'bg-danger-50 border border-danger-200 text-danger-600'">
            {{ prefMessage }}
          </div>
        </div>

        <!-- 租户邮箱（邮箱三层解析第 2 层兜底，AI获客等数字员工发信用） -->
        <div id="tenant-email" class="scroll-mt-4">
          <TenantEmailSettings />
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
import TenantEmailSettings from '@/components/email/TenantEmailSettings.vue'
import { getTenantInfo, updateTenantInfo, getTenantScheduler, updateTenantScheduler, updateTenantPreferences, type TenantSchedulerSettings } from '@/api/saasTenant'
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

onMounted(async () => {
  await loadSettings()
  void loadSchedulerSettings()
  // 旧 hash 深链重定向（如防滥用中心「通知渠道」入口）：通知配置已迁至独立页面
  if (route.hash === '#notification-channels') {
    router.replace({ path: `/t/${tenantId.value}/notification-rules` })
  }
})

// ==================== 计划任务设置（§15：每人活跃任务上限） ====================

const scheduler = ref<TenantSchedulerSettings | null>(null)
const schedulerLoading = ref(true)
const schedulerSaving = ref(false)
const schedulerMaxTasks = ref('')
const schedulerMessage = ref('')
const schedulerMessageType = ref<'success' | 'error'>('success')

async function loadSchedulerSettings() {
  schedulerLoading.value = true
  try {
    const value = await getTenantScheduler()
    scheduler.value = value
    schedulerMaxTasks.value = String(value.max_tasks_per_user ?? value.effective_max_tasks_per_user)
  } catch {
    scheduler.value = null
  } finally {
    schedulerLoading.value = false
  }
}

async function handleSaveScheduler() {
  const value = Number(schedulerMaxTasks.value.trim())
  if (!Number.isInteger(value) || value < 1) {
    schedulerMessage.value = '每人活跃任务上限必须为正整数'
    schedulerMessageType.value = 'error'
    return
  }
  await saveSchedulerOverride(value)
}

async function handleResetScheduler() {
  await saveSchedulerOverride(null)
}

async function saveSchedulerOverride(value: number | null) {
  schedulerSaving.value = true
  schedulerMessage.value = ''
  try {
    await updateTenantScheduler(value)
    const fresh = await getTenantScheduler()
    scheduler.value = fresh
    schedulerMaxTasks.value = String(fresh.max_tasks_per_user ?? fresh.effective_max_tasks_per_user)
    schedulerMessage.value = value === null ? '已恢复默认（使用全局默认上限）' : '已保存'
    schedulerMessageType.value = 'success'
  } catch (e: any) {
    schedulerMessage.value = e?.message || '保存失败'
    schedulerMessageType.value = 'error'
  } finally {
    schedulerSaving.value = false
  }
}

// ==================== 显示偏好（“AI生成”角标开关） ====================

const { tenant: tenantInfo } = useTenantAuth()
const showAiBadge = ref(true)
const prefSaving = ref(false)
const prefMessage = ref('')
const prefMessageType = ref<'success' | 'error'>('success')

showAiBadge.value = tenantInfo.value?.show_ai_badge !== false

async function handleSavePreferences() {
  prefSaving.value = true
  prefMessage.value = ''
  try {
    const res = await updateTenantPreferences(showAiBadge.value)
    // 同步本地登录态里的租户信息，角标开关立即生效（无需刷新/重新登录）
    if (tenantInfo.value) {
      tenantInfo.value.show_ai_badge = res.show_ai_badge
    }
    prefMessage.value = '已保存'
    prefMessageType.value = 'success'
  } catch (e: any) {
    prefMessage.value = e?.message || '保存失败'
    prefMessageType.value = 'error'
  } finally {
    prefSaving.value = false
  }
}
</script>

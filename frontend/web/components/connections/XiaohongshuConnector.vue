<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="小红书连接器"
      :is-logged-in="isLoggedIn"
      :user="user"
      @toggle-sidebar="toggleSidebar?.()"
      @logout="logout"
    />
    <main class="flex-1 overflow-y-auto p-4 sm:p-6">
      <section class="max-w-xl rounded-lg border border-default bg-surface p-4 sm:p-5 space-y-4">
        <div class="flex flex-wrap justify-between items-center gap-2">
          <div class="min-w-0">
            <h2 class="text-sm font-medium text-default">所选设备</h2>
            <p class="text-sm text-default mt-1 break-words">{{ device?.name || (device ? '本机 Runtime 设备' : '未选择设备') }}</p>
          </div>
          <BaseButton intent="secondary" :disabled="loading || busy !== null" @click="refresh">刷新设备</BaseButton>
        </div>
        <p v-if="loading" class="text-sm text-muted" role="status">正在查询设备…</p>
        <p v-else-if="status !== 'ready' && !deviceError" class="text-sm text-muted" role="status">{{ deviceStatusText[status] }}</p>
        <p v-if="deviceError" class="text-sm text-danger-600" role="alert">{{ deviceError }}</p>
        <RouterLink
          v-if="status !== 'ready' && !loading"
          :to="{ name: 'tenant-local-tools', params: { tenant_id: route.params.tenant_id } }"
          class="inline-block text-sm font-medium text-primary-600 hover:underline"
        >管理本地设备</RouterLink>
        <template v-if="status === 'ready'">
          <p class="text-sm text-muted">打开扫码窗口后，用小红书 App 扫描所选设备上 Chrome 显示的二维码。扫码完成后点击「打开小红书」。</p>
          <div class="flex flex-wrap gap-2">
            <BaseButton :disabled="loading || busy !== null" @click="run('login')">{{ busy === 'login' ? '正在打开扫码窗口…' : '扫码登录' }}</BaseButton>
            <BaseButton intent="secondary" :disabled="loading || busy !== null" @click="run('open')">{{ busy === 'open' ? '正在打开小红书…' : '打开小红书' }}</BaseButton>
          </div>
        </template>
        <p v-if="actionError" class="text-sm text-danger-600" role="alert">{{ actionError }}</p>
        <p v-else-if="result" class="text-sm text-primary-700" role="status">{{ result }}</p>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { getXiaohongshuAction, getXiaohongshuDevice, startXiaohongshuAction } from '@/api/localTools'
import type { XiaohongshuAction, XiaohongshuDevice } from '@/api/localTools'

const route = useRoute()
const { admin, init, isInitialized, isLoggedIn, logout } = useTenantAuth()
const user = computed(() => admin.value ? {
  user_id: admin.value.user_id, username: admin.value.username, phone: admin.value.phone,
} : null)
const toggleSidebar = inject<() => void>('toggleSidebar')

const loading = ref(false)
const device = ref<XiaohongshuDevice['device']>(null)
const status = ref<XiaohongshuDevice['status']>('no_device')
const busy = ref<XiaohongshuAction | null>(null)
const result = ref('')
const deviceError = ref('')
const actionError = ref('')
let scope = new AbortController()

const deviceStatusText = {
  no_device: '请先在本地工具中配对并选定一台 Windows 设备。',
  offline: '所选设备已离线，请在设备上启动 Runtime。',
  missing_plugin: '请在所选设备安装支持小红书连接器的 Runtime 和插件，然后刷新设备。',
  ready: '',
}
const actionErrors: Record<string, string> = {
  XHS_LOGIN_REQUIRED: '登录会话尚未建立或已过期，请先扫码登录。',
  XHS_CHROME_UNAVAILABLE: '请在所选设备安装 Google Chrome。',
  XHS_BROWSER_UNAVAILABLE: '启动 Chrome 失败，请检查设备桌面是否可用。',
  XHS_PROFILE_IN_USE: '请关闭占用此连接的 Chrome 窗口后重试。',
  XHS_PAGE_UNCONFIRMED: '无法确认小红书网页的登录状态，请检查所选设备的 Chrome 窗口。',
  XHS_BUSY: '设备正在打开小红书，请稍后重试。',
  XHS_CANCELLED: '设备已取消本次操作。',
  XHS_QUEUE_TIMEOUT: '设备未在规定时间内接收操作，请检查 Runtime 后重试。',
  PROVIDER_NOT_AVAILABLE: '小红书插件暂不可用，请检查所选设备的 Runtime。',
  TOOL_NOT_ALLOWED: '所选设备缺少操作能力，请检查 Runtime 和插件版本。',
}

async function refresh() {
  const signal = scope.signal
  loading.value = true
  deviceError.value = ''
  result.value = ''
  actionError.value = ''
  try {
    if (!isInitialized.value) await init()
    if (signal.aborted) return
    const view = await getXiaohongshuDevice(signal)
    if (!signal.aborted) {
      device.value = view.device
      status.value = view.status
    }
  } catch (error) {
    if (!signal.aborted) {
      device.value = null
      status.value = 'no_device'
      deviceError.value = error instanceof Error ? error.message : '查询设备失败'
    }
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

async function run(action: XiaohongshuAction) {
  if (busy.value || loading.value || status.value !== 'ready' || !device.value) return
  const pageSignal = scope.signal
  const request = new AbortController()
  const abort = () => request.abort()
  pageSignal.addEventListener('abort', abort, { once: true })
  const timeout = setTimeout(abort, 95_000)
  busy.value = action
  result.value = ''
  actionError.value = ''
  try {
    const id = await startXiaohongshuAction(action, device.value.device_id, request.signal)
    while (!request.signal.aborted) {
      const view = await getXiaohongshuAction(id, request.signal)
      if (request.signal.aborted) break
      if (view.status === 'failed') {
        actionError.value = actionErrors[view.code] || '本机操作未完成，请检查所选设备后重试。'
        return
      }
      if (view.status === 'qr_ready' || view.status === 'opened') {
        result.value = view.status === 'qr_ready'
          ? '二维码已显示在所选设备的 Chrome 中。扫码完成后点击「打开小红书」。'
          : '已用登录会话打开小红书。'
        return
      }
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
    if (!pageSignal.aborted) actionError.value = '暂未确认操作结果，请检查所选设备的 Chrome 窗口。'
  } catch (error) {
    if (!pageSignal.aborted) actionError.value = request.signal.aborted
      ? '暂未确认操作结果，请检查所选设备的 Chrome 窗口。'
      : error instanceof Error ? error.message : '小红书操作失败'
  } finally {
    clearTimeout(timeout)
    pageSignal.removeEventListener('abort', abort)
    if (!pageSignal.aborted) busy.value = null
  }
}

watch(() => route.params.tenant_id, () => {
  scope.abort()
  scope = new AbortController()
  busy.value = null
  device.value = null
  status.value = 'no_device'
  void refresh()
}, { immediate: true })
onBeforeUnmount(() => scope.abort())
</script>

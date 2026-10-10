<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader title="通知设置" :is-logged-in="isLoggedIn" :user="admin" @toggle-sidebar="toggleSidebar?.()" @logout="logout" />
    <div class="flex-1 overflow-y-auto p-4 sm:p-6">
      <div class="max-w-2xl space-y-4">
        <BaseCard title="浏览器任务提醒">
          <p class="text-sm text-muted mb-4">开启后，新接单任务完成或失败时会显示系统通知。关闭本站标签页后仍可接收；到达时间受浏览器、系统和网络影响。</p>
          <p v-if="loading" role="status" class="text-muted">正在检查通知状态…</p>
          <template v-else>
            <BaseBadge :intent="enabled ? 'success' : 'neutral'">{{ enabled ? '本浏览器已开启' : '本浏览器未开启' }}</BaseBadge>
            <p class="text-sm text-muted mt-3">{{ availability }}</p>
            <p v-if="foreignBinding" class="text-sm text-warning-700 mt-3">本浏览器已绑定其他账号或租户。开启前将先撤销旧绑定；旧账号已退出时，需先登录旧账号关闭通知。</p>
          </template>
          <p v-if="error" role="alert" class="text-sm text-danger-600 mt-4">{{ error }}</p>
          <p v-if="notice" role="status" class="text-sm text-success-700 mt-4">{{ notice }}</p>
          <div class="flex flex-wrap gap-3 mt-5">
            <BaseButton :disabled="busy || loading || !available || permission === 'denied'" @click="enable">{{ busy ? '处理中…' : enabled ? '同步通知状态' : '开启本浏览器通知' }}</BaseButton>
            <BaseButton intent="secondary" :disabled="busy || loading || !enabled" @click="test">发送测试通知</BaseButton>
            <BaseButton intent="secondary" :disabled="busy || loading || !ownedActiveBinding" @click="disable">关闭本浏览器通知</BaseButton>
            <BaseButton intent="ghost" :disabled="busy || loading" @click="refresh">刷新</BaseButton>
          </div>
        </BaseCard>
        <p class="text-sm text-muted">同一浏览器一次提醒一个账号和租户；关闭仅影响本浏览器。测试发送成功表示已加入投递队列，请以收到系统通知为准。</p>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { notificationApi, type NotificationCapabilities } from './api'
import { cleanupPending, disableNotifications, enableNotifications, ownsBinding, readBinding, currentSubject, supportsNotifications, registerNotificationWorker, syncRevokedTarget, type BrowserBinding } from './client'
const { admin, isLoggedIn, logout, init } = useTenantAuth()
const toggleSidebar = inject<() => void>('toggleSidebar')
const loading = ref(true), busy = ref(false), error = ref(''), notice = ref('')
const binding = ref<BrowserBinding | null>(readBinding())
const capabilities = ref<NotificationCapabilities | null>(null)
const preferenceEnabled = ref(false)
const permission = ref<NotificationPermission | 'unsupported'>('Notification' in window ? Notification.permission : 'unsupported')
const available = computed(() => supportsNotifications() && !!capabilities.value?.enabled && !!capabilities.value?.web_push.enabled)
const ownedActiveBinding = computed(() => !!binding.value && ownsBinding(binding.value) && binding.value.state === 'active')
const enabled = computed(() => ownedActiveBinding.value && preferenceEnabled.value && permission.value === 'granted')
const foreignBinding = computed(() => !!binding.value && !ownsBinding(binding.value))
const availability = computed(() => !supportsNotifications() ? '当前环境不支持推送，请使用 HTTPS 和支持推送的浏览器。' : !available.value ? '服务端暂未开放浏览器通知。' : permission.value === 'denied' ? '浏览器已拒绝通知权限，请在浏览器网站设置中允许通知。' : '通知只包含会话名称和任务状态。')
async function refresh() {
  loading.value = true; error.value = ''; binding.value = readBinding()
  try {
    await init()
    if (supportsNotifications()) await cleanupPending()
    permission.value = 'Notification' in window ? Notification.permission : 'unsupported'
    if (permission.value === 'denied' && binding.value && ownsBinding(binding.value) && binding.value.state === 'active') {
      await disableNotifications(binding.value)
      binding.value = readBinding()
    }
    const [caps, prefs, targets] = await Promise.all([notificationApi.capabilities(), notificationApi.preferences(), notificationApi.targets()])
    capabilities.value = caps
    preferenceEnabled.value = prefs.preferences.some(p => p.category === 'task' && p.channel === 'web_push' && p.provider === 'web_push' && p.enabled)
    if (binding.value && ownsBinding(binding.value)) {
      const target = targets.targets.find(t => t.target_id === binding.value?.target_id)
      if (target && binding.value.state === 'revoked' && target.state === 'revoked') {
        await syncRevokedTarget(target)
        binding.value = readBinding()
      }
      // A changed active server version is never silently adopted by this tab.
      if (!target || target.state !== 'active' || target.binding_version !== binding.value?.binding_version) preferenceEnabled.value = false
    }
    if (supportsNotifications() && binding.value?.state === 'active') {
      const reg = await registerNotificationWorker()
      if (!await reg.pushManager.getSubscription()) preferenceEnabled.value = false
    }
  } catch (e) { error.value = e instanceof Error ? e.message : '读取通知状态失败。' }
  finally { loading.value = false }
}
async function action(fn: () => Promise<void>) {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    if (currentSubject()?.user_id !== admin.value?.user_id) throw new Error('账号已变化，请刷新后重试。')
    await fn()
  } catch (e) { error.value = e instanceof Error ? e.message : '通知操作失败。' }
  finally { binding.value = readBinding(); permission.value = 'Notification' in window ? Notification.permission : 'unsupported'; busy.value = false }
}
function enable() { return action(async () => { binding.value = await enableNotifications(binding.value); preferenceEnabled.value = true; notice.value = '本浏览器通知已开启。' }) }
function disable() { return action(async () => { if (binding.value) await disableNotifications(binding.value); notice.value = '本浏览器通知已关闭。' }) }
function test() { return action(async () => { if (binding.value && ownsBinding(binding.value) && binding.value.binding_version === readBinding()?.binding_version) { await notificationApi.test(binding.value); notice.value = '测试通知已加入投递队列，请检查系统通知。' } else throw new Error('绑定已变化，请刷新后重试。') }) }
function changed(event: Event) {
  if (event instanceof StorageEvent && event.key !== 'task-notifications:binding:v1') return
  binding.value = readBinding(); notice.value = ''; preferenceEnabled.value = false
}
onMounted(() => { void refresh(); window.addEventListener('storage', changed); window.addEventListener('task-notifications-binding', changed) })
onUnmounted(() => { window.removeEventListener('storage', changed); window.removeEventListener('task-notifications-binding', changed) })
</script>

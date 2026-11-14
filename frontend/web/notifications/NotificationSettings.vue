<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader title="通知设置" :is-logged-in="isLoggedIn" :user="admin" @toggle-sidebar="toggleSidebar?.()" @logout="logout" />
    <div class="flex-1 overflow-y-auto p-4 sm:p-6">
      <div class="max-w-none">
        <!-- 通知渠道单卡片（方案 B，2026-11-14 精简改版）：每渠道一行 -->
        <BaseCard>
          <template #header>
            <div class="flex items-center gap-2.5">
              <h3 class="text-base font-semibold text-default">通知渠道</h3>
              <BaseBadge :intent="usableCount === 2 ? 'success' : 'neutral'">{{ usableCount }} / 2 可用</BaseBadge>
            </div>
          </template>
          <!-- 渠道行 1：浏览器任务提醒 -->
          <div class="flex items-center gap-3 py-3.5" :class="wecomAppAvailable ? 'border-b border-gray-100' : ''">
            <div class="w-9 h-9 rounded-[10px] bg-primary-50 text-primary-600 flex items-center justify-center flex-none">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /><path stroke-linecap="round" stroke-linejoin="round" d="M9 10a1 1 0 011-1h4a1 1 0 011 1v1a1 1 0 01-1 1h-4a1 1 0 01-1-1v-1zM3 12h18M12 3v18" /></svg>
            </div>
            <div class="flex-1 min-w-0">
              <div class="text-sm font-semibold text-default">浏览器任务提醒 <BaseBadge :intent="browserEnabled ? 'success' : 'neutral'">{{ browserEnabled ? '已开启' : '未开启' }}</BaseBadge></div>
              <div class="text-xs text-gray-500 mt-0.5">显示系统通知，关闭标签页仍可接收</div>
            </div>
            <button v-if="browserEnabled" class="text-sm text-primary-600 hover:underline cursor-pointer flex-none" :disabled="busy" @click="test">测试</button>
            <BaseSwitch :model-value="browserEnabled" :disabled="busy || loading || !browserAvailable || permission === 'denied'" aria-label="浏览器任务提醒开关" @update:model-value="toggleBrowser" />
          </div>
          <!-- 渠道行 2：企微通知（独立行组件，含绑定弹窗） -->
          <WecomTaskNotifyCard @availability="wecomAppAvailable = $event" />
          <p v-if="error" role="alert" class="text-sm text-danger-600 pt-2">{{ error }}</p>
          <p v-if="notice" role="status" class="text-sm text-success-700 pt-2">{{ notice }}</p>
        </BaseCard>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { notificationApi, type NotificationCapabilities } from './api'
import { cleanupPending, disableNotifications, enableNotifications, ownsBinding, readBinding, currentSubject, supportsNotifications, registerNotificationWorker, syncRevokedTarget, type BrowserBinding } from './client'
import WecomTaskNotifyCard from './WecomTaskNotifyCard.vue'
const { admin, isLoggedIn, logout, init } = useTenantAuth()
const toggleSidebar = inject<() => void>('toggleSidebar')
const loading = ref(true), busy = ref(false), error = ref(''), notice = ref('')
const binding = ref<BrowserBinding | null>(readBinding())
const capabilities = ref<NotificationCapabilities | null>(null)
const preferenceEnabled = ref(false)
const wecomAppAvailable = ref(false)
const permission = ref<NotificationPermission | 'unsupported'>('Notification' in window ? Notification.permission : 'unsupported')
const browserAvailable = computed(() => supportsNotifications() && !!capabilities.value?.enabled && !!capabilities.value?.web_push.enabled)
const ownedActiveBinding = computed(() => !!binding.value && ownsBinding(binding.value) && binding.value.state === 'active')
const browserEnabled = computed(() => ownedActiveBinding.value && preferenceEnabled.value && permission.value === 'granted')
const usableCount = computed(() => (browserAvailable.value ? 1 : 0) + (wecomAppAvailable.value ? 1 : 0))
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
// 开关：开→关（撤销绑定与偏好）；关→开（开启通知；binding active 但偏好关闭的
// 中间态同样走 enable，enableNotifications 幂等登记）
function toggleBrowser(on: boolean) {
  if (on === browserEnabled.value) return
  return on ? enable() : disable()
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

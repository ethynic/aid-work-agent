<template>
  <div class="min-h-screen bg-canvas flex items-center justify-center p-4">
    <BaseCard title="任务通知" class="w-full max-w-lg">
      <p v-if="loading" class="text-muted" role="status">正在核验通知和原会话权限…</p>
      <p v-else role="alert" class="text-danger-600">{{ error }}</p>
      <BaseButton v-if="!loading" class="mt-4" intent="secondary" @click="router.push('/')">返回登录</BaseButton>
    </BaseCard>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { credentialGet } from '@/platform/credentialStore'
import { notificationRequest, NotificationApiError } from './api'
import { notificationReturnUrl } from './returnUrl'
const route = useRoute(), router = useRouter()
const loading = ref(true), error = ref('')
onMounted(async () => {
  const returnTo = notificationReturnUrl(route.fullPath)
  if (!returnTo) { error.value = '通知链接无效。'; loading.value = false; return }
  const tenant = typeof route.query.tenant === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(route.query.tenant) ? route.query.tenant : null
  const token = credentialGet(tenant ? `saas_token_${tenant}` : 'portal_token')
  const loginPath = tenant ? `/t/${encodeURIComponent(tenant)}/login` : '/'
  const login = () => router.replace({ path: loginPath, query: { return_to: returnTo } })
  if (!token) { await login(); return }
  try {
    const result = await notificationRequest<{ notification_ref: string; event_type: string; resource: { tenant_id?: string | null; session_id?: string; subagent_id?: string; instance_id?: string; runner_id?: string } }>(`/messages/${encodeURIComponent(String(route.query.ref))}`, {}, { Authorization: `Bearer ${token}`, ...(tenant ? { 'X-Tenant-Id': tenant } : {}) })
    const resource = result.resource
    if (!resource?.session_id) { error.value = '测试通知已成功到达，本通知没有关联会话。'; loading.value = false; return }
    if (resource.tenant_id == null && !tenant) {
      await router.replace({ path: '/portal/notification-session', query: { ref: String(route.query.ref) } })
      return
    }
    if (!resource.tenant_id || !/^[a-zA-Z0-9_-]{1,128}$/.test(resource.tenant_id) || (tenant && resource.tenant_id !== tenant)) throw new Error('通知所属租户与当前账号不匹配。')
    // No title or task input is transported by the navigation contract.
    await router.replace({ path: `/t/${encodeURIComponent(resource.tenant_id)}/chat${resource.subagent_id && resource.subagent_id !== 'main' ? `/${encodeURIComponent(resource.subagent_id)}` : ''}`, query: { _load_session: resource.session_id, ...(resource.instance_id ? { instance_id: resource.instance_id } : {}), ...(resource.runner_id ? { runner_id: resource.runner_id } : {}) } })
  } catch (e) {
    if (e instanceof NotificationApiError && e.status === 401) { await login(); return }
    error.value = e instanceof Error ? e.message : '通知暂时无法打开。'; loading.value = false
  }
})
</script>

<template>
  <div class="min-h-screen bg-canvas flex items-center justify-center p-4">
    <BaseCard title="任务通知" class="w-full max-w-lg">
      <p v-if="loading" class="text-muted" role="status">正在核验通知和原会话权限…</p>
      <p v-else role="alert" class="text-danger-600">{{ error }}</p>
      <div v-if="!loading" class="mt-4 flex items-center gap-2">
        <BaseButton v-if="retryable" intent="primary" @click="resolve">重试</BaseButton>
        <BaseButton intent="secondary" @click="router.push('/')">返回登录</BaseButton>
      </div>
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

// scheduled 事件（设计 §8.2）：terminal（runner_result）/needs_action（runner_waiting）/
// paused（scheduled_task）统一路由任务视图，不进聊天形态（设计 §6）
const SCHEDULED_TASK_EVENTS = new Set(['task.completed', 'task.failed', 'task.needs_action', 'task.paused'])

const route = useRoute(), router = useRouter()
const loading = ref(true), error = ref(''), retryable = ref(false)

async function resolve(): Promise<void> {
  loading.value = true
  error.value = ''
  retryable.value = false
  const returnTo = notificationReturnUrl(route.fullPath)
  if (!returnTo) { error.value = '通知链接无效。'; loading.value = false; return }
  const tenant = typeof route.query.tenant === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(route.query.tenant) ? route.query.tenant : null
  const token = credentialGet(tenant ? `saas_token_${tenant}` : 'portal_token')
  const loginPath = tenant ? `/t/${encodeURIComponent(tenant)}/login` : '/'
  const login = () => router.replace({ path: loginPath, query: { return_to: returnTo } })
  if (!token) { await login(); return }
  const authHeaders = { Authorization: `Bearer ${token}`, ...(tenant ? { 'X-Tenant-Id': tenant } : {}) }
  try {
    const result = await notificationRequest<{ notification_ref: string; event_type: string; resource: { tenant_id?: string | null; session_id?: string; subagent_id?: string; instance_id?: string; runner_id?: string; task_id?: string; scheduled_task_id?: string | null } | null }>(`/messages/${encodeURIComponent(String(route.query.ref))}`, {}, authHeaders)
    const resource = result.resource
    if (!resource?.session_id && !resource?.task_id) { error.value = '测试通知已成功到达，本通知没有关联会话。'; loading.value = false; return }
    if (resource.tenant_id == null && !tenant) {
      await router.replace({ path: '/portal/notification-session', query: { ref: String(route.query.ref) } })
      return
    }
    if (!resource.tenant_id || !/^[a-zA-Z0-9_-]{1,128}$/.test(resource.tenant_id) || (tenant && resource.tenant_id !== tenant)) throw new Error('通知所属租户与当前账号不匹配。')
    // scheduled 事件 → 任务视图（设计 §8.2）：task.paused 资源（scheduled_task）
    // 自带 task_id；runner_result/runner_waiting 资源由资源解析透出
    // scheduled_task_id（scheduled_tasks.session_id 子查询，Phase 5 二轮评审第
    // 8 项）——均为权威标记，直接路由，不再调任务列表匹配。
    if (SCHEDULED_TASK_EVENTS.has(result.event_type)) {
      const taskId = resource.task_id || resource.scheduled_task_id
      if (taskId) {
        await router.replace({ path: `/t/${encodeURIComponent(resource.tenant_id)}/tasks/${encodeURIComponent(taskId)}` })
        return
      }
      // task.completed/task.failed 是 chat 与 scheduled runner 共用的事件名
      // （producer 同名生产）：无任务标记 = 普通聊天通知，按既有契约回会话。
      // task.needs_action 仅 scheduled 生产，资源解析必带 task_id——无标记说明
      // 任务行已删除（资源子查询为 NULL），如实报错不回退聊天形态。
      if (result.event_type === 'task.needs_action') {
        error.value = '关联的任务暂时无法解析（可能已被删除或网络异常），无法打开任务视图。'
        loading.value = false
        return
      }
    }
    // No title or task input is transported by the navigation contract.
    await router.replace({ path: `/t/${encodeURIComponent(resource.tenant_id)}/chat${resource.subagent_id && resource.subagent_id !== 'main' ? `/${encodeURIComponent(resource.subagent_id)}` : ''}`, query: { _load_session: resource.session_id, ...(resource.instance_id ? { instance_id: resource.instance_id } : {}), ...(resource.runner_id ? { runner_id: resource.runner_id } : {}) } })
  } catch (e) {
    if (e instanceof NotificationApiError && e.status === 401) { await login(); return }
    // 瞬时失败（网络异常/5xx）可重试；403/404 等确定性错误仅如实展示
    if (!(e instanceof NotificationApiError) || e.status >= 500) retryable.value = true
    error.value = e instanceof Error ? e.message : '通知暂时无法打开。'; loading.value = false
  }
}

onMounted(() => { void resolve() })
</script>

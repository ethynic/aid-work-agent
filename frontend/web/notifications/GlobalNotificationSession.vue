<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader :title="title || '任务通知'" :is-logged-in="isLoggedIn" :user="admin" @toggle-sidebar="toggleSidebar?.()" @logout="logout" />
    <div class="flex-1 overflow-y-auto p-4 sm:p-6">
      <div class="max-w-4xl mx-auto space-y-4">
        <BaseCard title="原会话 · 只读查看">
          <p class="text-sm text-muted">此页恢复通知对应的原会话和任务结果，不能发送新任务。</p>
          <p v-if="loading" role="status" class="text-muted mt-3">正在核验原会话和任务权限…</p>
          <p v-else-if="error" role="alert" class="text-danger-600 mt-3">{{ error }}</p>
          <BaseBadge v-else :intent="status === 'completed' ? 'success' : status === 'failed' ? 'danger' : 'neutral'" class="mt-3">{{ statusText }}</BaseBadge>
          <BaseButton intent="secondary" class="mt-4" :disabled="loading" @click="load">刷新结果</BaseButton>
        </BaseCard>
        <template v-if="!loading && !error">
          <p v-if="!messages.length" class="text-muted text-sm">原会话暂无可展示的消息。</p>
          <MessageItem v-for="message in messages" :key="message.messageId || message.runnerId" :message="message" :is-processing="false" input-hint-state="idle" />
        </template>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import MessageItem from '@/components/MessageItem.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { getAuthHeader } from '@/api/auth'
import { getSession, getSessionMessages } from '@/api/session'
import { RunnerClient, type RunnerStatus } from '@/api/runner'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { historyMessages, mergeRunnerMessages } from '@/utils/runnerMessages'
import type { ChatMessage } from '@/types'
import { notificationRequest } from './api'
const route = useRoute()
const { admin, isLoggedIn, init, logout } = useTenantAuth()
const toggleSidebar = inject<() => void>('toggleSidebar')
const loading = ref(true), error = ref(''), title = ref(''), messages = ref<ChatMessage[]>([])
const status = ref<RunnerStatus>('queued')
const statusText = computed(() => ({ completed: '任务已完成', failed: '任务执行失败', cancelled: '任务已取消' }[status.value as 'completed' | 'failed' | 'cancelled'] || '任务状态已更新'))
const runner = new RunnerClient()
let generation = 0
async function load() {
  const requestGeneration = ++generation
  loading.value = true; error.value = ''; title.value = ''; messages.value = []
  try {
    await init()
    if (admin.value?.role !== 'platform_admin') throw new Error('当前账号无权查看全局会话。')
    const ref = route.query.ref
    if (typeof ref !== 'string' || !/^[a-zA-Z0-9_-]{1,160}$/.test(ref)) throw new Error('通知链接无效。')
    const auth = getAuthHeader()
    const subject = admin.value.user_id
    const { resource } = await notificationRequest<{ resource: { tenant_id: string | null; session_id?: string; runner_id?: string } }>(`/messages/${encodeURIComponent(ref)}`, {}, auth)
    if (resource.tenant_id != null || !resource.session_id || !resource.runner_id) throw new Error('通知与当前全局会话范围不匹配。')
    const session = await getSession(resource.session_id)
    if (session.session_id !== resource.session_id || session.tenant_id != null || session.user_id !== subject) throw new Error('当前账号无权查看原会话。')
    const [history, task] = await Promise.all([getSessionMessages(session.session_id), runner.get(resource.runner_id, auth)])
    if (requestGeneration !== generation) return
    if (auth.Authorization !== getAuthHeader().Authorization || admin.value?.user_id !== subject) throw new Error('账号已变化，请刷新后重试。')
    if (task.runner_id !== resource.runner_id || task.session.kind !== 'web' || task.session.session_id !== session.session_id) throw new Error('通知与原任务不匹配。')
    title.value = session.title || '未命名会话'
    status.value = task.status
    messages.value = mergeRunnerMessages(historyMessages(history.messages), [task]).map(message => ({ ...message, quickOptions: [], browserAssistance: undefined, submitFailure: undefined, runnerActive: false }))
  } catch { if (requestGeneration === generation) error.value = '原会话或任务暂时无法查看，请确认当前账号及资源权限后重试。' }
  finally { if (requestGeneration === generation) loading.value = false }
}
onMounted(() => { void load() })
watch(() => route.query.ref, () => { void load() })
onUnmounted(() => { generation++; runner.detach() })
</script>

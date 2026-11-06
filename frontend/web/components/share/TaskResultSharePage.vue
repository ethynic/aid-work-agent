<template>
  <div class="h-safe-screen flex bg-canvas">
    <main class="min-w-0 flex-1 overflow-y-auto">
      <header class="border-b border-default bg-surface px-4 py-4 text-default font-medium">任务执行结果</header>
      <div class="mx-auto max-w-4xl space-y-4 px-4 py-6">
        <p v-if="busy" role="status" class="text-sm text-muted">正在加载任务结果…</p>
        <template v-else-if="view">
          <BaseCard>
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <h1 class="break-words text-lg font-semibold text-default">{{ view.task_name }}</h1>
                <p class="mt-1 text-sm text-muted">{{ view.runner_status === 'completed' ? '本次执行已完成' : '本次执行失败' }} · 任务{{ taskStatus(view.task_status) }}</p>
                <p class="mt-1 text-xs text-muted">完成时间：{{ finishedAt(view.finished_at) }}</p>
              </div>
              <BaseButton intent="secondary" @click="goLogin">登录查看完整任务</BaseButton>
            </div>
          </BaseCard>
          <BaseCard>
            <ReplyContent :message="view.message" />
          </BaseCard>
          <p class="text-xs text-muted">此页面只展示本次执行的最终回复。</p>
        </template>
        <BaseCard v-else-if="gone">
          <p role="alert" class="text-sm text-muted">任务已删除或链接已失效。</p>
          <div class="mt-4"><BaseButton intent="secondary" @click="goLogin">返回登录</BaseButton></div>
        </BaseCard>
        <BaseCard v-else-if="error">
          <p role="alert" class="text-sm text-danger-600">{{ error }}</p>
          <div class="mt-4"><BaseButton intent="secondary" @click="load">重试</BaseButton></div>
        </BaseCard>
      </div>
    </main>
    <AttachmentPreviewPanel v-if="isPreviewOpen" :attachment="previewAttachment" @close="closePreview" />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import ReplyContent from '@/components/chat/ReplyContent.vue'
import AttachmentPreviewPanel from '@/components/AttachmentPreviewPanel.vue'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { credentialGet } from '@/platform/credentialStore'
import { replyFileResolverKey } from '@/utils/replyResources'
import { taskResultShareFileUrl, TaskResultShareError, viewTaskResultShare, type TaskResultShareView } from '@/api/taskResultShare'

const route = useRoute(), router = useRouter()
const view = ref<TaskResultShareView | null>(null)
const busy = ref(false), gone = ref(false), error = ref('')
const ticket = ref('')
const { previewAttachment, isPreviewOpen, closePreview } = useAttachmentPreview()
provide(replyFileResolverKey, (alias, download) => taskResultShareFileUrl(String(route.params.share_key), alias, ticket.value, download))
let request = 0
onBeforeUnmount(() => { request++; closePreview() })
watch(() => route.params.share_key, () => { closePreview(); view.value = null; ticket.value = ''; void load() }, { immediate: true })

function taskStatus(status: string) {
  return ({ active: '运行中', paused: '已暂停', completed: '已完成', expired: '已到期' } as Record<string, string>)[status] || status
}

function finishedAt(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function goLogin() {
  const tenant = view.value?.tenant_id
  const key = String(route.params.share_key)
  if (!tenant) { void router.push('/'); return }
  void router.push({ path: `/t/${encodeURIComponent(tenant)}/login`, query: { return_to: `/share/task-result/${key}` } })
}

async function load() {
  const current = ++request
  busy.value = true
  gone.value = false
  error.value = ''
  view.value = null
  try {
    const key = String(route.params.share_key)
    let result = await viewTaskResultShare(key)
    if (current !== request) return
    ticket.value = result.access_ticket
    view.value = result
    busy.value = false
    const token = credentialGet(`saas_token_${result.tenant_id}`)
    if (token) {
      try {
        result = await viewTaskResultShare(key, token, result.tenant_id)
        if (current !== request) return
        if (result.viewer_is_owner) {
          await router.replace({ path: `/t/${encodeURIComponent(result.tenant_id)}/tasks/${encodeURIComponent(result.task_id)}`,
            query: { runner_id: result.runner_id } })
          return
        }
        ticket.value = result.access_ticket
        view.value = result
      } catch (cause) {
        if (current !== request) return
        if (cause instanceof TaskResultShareError && cause.status === 404) throw cause
        // The public result remains readable when the optional identity check fails.
      }
    }
  } catch (cause) {
    if (current !== request) return
    gone.value = cause instanceof TaskResultShareError && cause.status === 404
    view.value = null
    ticket.value = ''
    if (!gone.value) error.value = cause instanceof Error ? cause.message : '加载失败，请稍后重试。'
  } finally {
    if (current === request) busy.value = false
  }
}
</script>

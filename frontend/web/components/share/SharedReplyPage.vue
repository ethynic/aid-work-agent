<template>
  <div class="h-safe-screen flex bg-canvas">
    <main class="min-w-0 flex-1 overflow-y-auto">
      <header class="border-b border-default bg-surface px-4 py-4 text-default font-medium">分享的 Agent 回复</header>
      <div class="mx-auto max-w-4xl px-4 py-6">
        <p v-if="busy" role="status" class="text-sm text-muted">正在加载…</p>
        <form v-else-if="needsPassword" class="mx-auto max-w-sm space-y-4 rounded-xl border border-default bg-surface p-6" @submit.prevent="load(password)">
          <h1 class="text-lg font-medium text-default">请输入访问密码</h1>
          <label for="shared-reply-password" class="block text-sm text-muted">分享人设置了密码，验证后即可查看回复、图片和附件；密码区分大小写。</label>
          <div class="relative">
            <BaseInput id="shared-reply-password" v-model="password" class="!pr-10" :type="showPassword ? 'text' : 'password'" autocomplete="off" required autofocus />
            <button type="button" class="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-muted hover:text-default"
              :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" @click="showPassword = !showPassword">
              <svg v-if="showPassword" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" />
              </svg>
              <svg v-else class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
                <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
              </svg>
            </button>
          </div>
          <p v-if="error" role="alert" class="text-sm text-danger-600">{{ error }}</p>
          <BaseButton type="submit" :disabled="!password">查看回复</BaseButton>
        </form>
        <template v-else-if="message">
          <div class="shared-reply rounded-xl border border-default bg-surface shadow-message">
            <ReplyContent :message="message" />
          </div>
          <p class="mt-3 text-xs text-muted">此页面展示分享人选择的这一条回复。</p>
        </template>
        <div v-else-if="error" role="alert" class="space-y-3 text-sm text-danger-600">
          <p>{{ error }}</p>
          <BaseButton intent="secondary" @click="load()">重试</BaseButton>
        </div>
      </div>
    </main>
    <AttachmentPreviewPanel v-if="isPreviewOpen" :attachment="previewAttachment" @close="closePreview" />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { ChatMessage } from '@/types'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import ReplyContent from '@/components/chat/ReplyContent.vue'
import AttachmentPreviewPanel from '@/components/AttachmentPreviewPanel.vue'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { sharedReplyFileUrl, viewMessageShare } from '@/api/messageShare'
import { replyFileResolverKey } from '@/utils/replyResources'

const route = useRoute()
const message = ref<ChatMessage | null>(null)
const password = ref('')
const showPassword = ref(false)
const needsPassword = ref(false)
const busy = ref(false)
const error = ref('')
const ticket = ref('')
const { previewAttachment, isPreviewOpen, closePreview } = useAttachmentPreview()
provide(replyFileResolverKey, (fileId, download) => sharedReplyFileUrl(String(route.params.share_key), fileId, ticket.value, download))
let request = 0
onBeforeUnmount(() => { request++; closePreview(); password.value = '' })
watch(() => route.params.share_key, () => { closePreview(); message.value = null; needsPassword.value = false; password.value = ''; showPassword.value = false; void load() }, { immediate: true })

async function load(inputPassword?: string) {
  const current = ++request
  busy.value = true
  error.value = ''
  try {
    const result = await viewMessageShare(String(route.params.share_key), inputPassword)
    if (current !== request) return
    if (result.state === 'password_required') {
      needsPassword.value = true
    } else {
      ticket.value = result.access_ticket
      message.value = result.message
      needsPassword.value = false
    }
  } catch (cause) {
    if (current === request) error.value = cause instanceof Error ? cause.message : '加载失败，请稍后重试。'
  } finally {
    if (current === request) { busy.value = false; password.value = '' }
  }
}
</script>

<style scoped>
.shared-reply {
  width: 100%;
  min-width: 0;
  padding: 10px 14px;
}
</style>

<template>
  <BaseModal :model-value="true" title="分享这条回复" size="md" accessible @update:model-value="emit('close')">
    <form v-if="!shareUrl" id="reply-share-form" class="space-y-4" @submit.prevent="generate">
      <fieldset :disabled="busy" class="space-y-3">
        <legend class="mb-3 text-default">对方访问时是否需要密码？</legend>
        <label class="flex items-center gap-2"><input v-model="access" type="radio" name="reply-share-access" value="public" /> 无需密码</label>
        <label class="flex items-center gap-2"><input v-model="access" type="radio" name="reply-share-access" value="password" @change="makePassword" /> 需要密码</label>
        <div v-if="access === 'password'" class="space-y-2">
          <label for="reply-share-password" class="block text-sm text-muted">访问密码</label>
          <BaseInput id="reply-share-password" v-model="password" autocomplete="off" maxlength="32" required />
        </div>
      </fieldset>
    </form>
    <div v-else class="space-y-3">
      <p class="text-sm text-muted">链接已生成，可以复制发给对方。</p>
      <label for="reply-share-text" class="block text-sm text-muted">分享内容</label>
      <textarea id="reply-share-text" :value="shareText" readonly rows="4"
        class="w-full rounded-lg border border-default bg-surface p-3 text-sm text-default break-all"></textarea>
      <BaseButton v-if="access === 'password'" intent="ghost" size="sm" @click="copy(true)">仅复制链接</BaseButton>
    </div>
    <p v-if="feedback" :role="copyFailed ? 'alert' : 'status'" class="mt-3 text-sm text-muted">{{ feedback }}</p>
    <p v-if="error" role="alert" class="mt-3 text-sm text-danger-600">{{ error }}</p>
    <template #footer>
      <BaseButton intent="secondary" @click="emit('close')">关闭</BaseButton>
      <BaseButton v-if="!shareUrl" type="submit" form="reply-share-form" :disabled="busy || !access || (access === 'password' && !password)">
        {{ busy ? '生成中…' : '生成并复制' }}
      </BaseButton>
      <BaseButton v-else @click="copy(false)">复制分享内容</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { createMessageShare } from '@/api/messageShare'

const props = defineProps<{ sessionId: string; messageId: string }>()
const emit = defineEmits<{ close: [] }>()
const access = ref<'' | 'public' | 'password'>('')
const password = ref('')
const shareUrl = ref('')
const busy = ref(false)
const error = ref('')
const feedback = ref('')
const copyFailed = ref(false)
let closed = false
onBeforeUnmount(() => { closed = true; password.value = '' })
const shareText = computed(() => shareUrl.value + (access.value === 'password' ? '\n访问密码：' + password.value : ''))

function makePassword() {
  if (password.value || !globalThis.crypto?.getRandomValues) return
  const alphabet = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
  password.value = Array.from(crypto.getRandomValues(new Uint8Array(8)), value => alphabet[value % alphabet.length]).join('')
}
async function copy(linkOnly = false) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(linkOnly ? shareUrl.value : shareText.value)
    if (closed) return
    copyFailed.value = false
    feedback.value = '已复制'
  } catch {
    if (closed) return
    copyFailed.value = true
    feedback.value = '链接已生成，自动复制未成功，请点击复制或手动选择上方内容。'
  }
}
async function generate() {
  if (busy.value || !access.value || (access.value === 'password' && !password.value)) return
  busy.value = true
  error.value = ''
  try {
    const result = await createMessageShare(props.sessionId, props.messageId, access.value === 'password' ? password.value : undefined)
    if (closed) return
    shareUrl.value = new URL(result.share_path, window.location.origin).href
    await copy(false)
  } catch (cause) {
    if (!closed) error.value = cause instanceof Error ? cause.message : '生成链接失败，请稍后重试。'
  } finally {
    busy.value = false
  }
}
</script>

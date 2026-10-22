<template>
  <BaseModal :model-value="true" title="分享回复" size="sm" accessible :show-fullscreen="false" class="px-4" @update:model-value="emit('close')">
    <template #title>
      <span class="flex items-center gap-2.5">
        <span class="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M10 13a5 5 0 0 0 7.1 0l3-3a5 5 0 0 0-7.1-7.1l-1.7 1.7m2.7 6.4a5 5 0 0 0-7.1 0l-3 3a5 5 0 0 0 7.1 7.1l1.7-1.7" />
          </svg>
        </span>
        <span class="text-base font-semibold">分享回复</span>
      </span>
    </template>
    <div class="space-y-4 text-sm">
      <p class="text-xs leading-5 text-muted">{{ shareUrl ? '链接已生成，发给对方即可查看。' : '分享这一条回复，图片和附件一并保留。' }}</p>
      <form v-if="!shareUrl" id="reply-share-form" @submit.prevent="generate">
        <fieldset :disabled="busy" class="space-y-3">
          <legend class="sr-only">访问方式</legend>
          <div class="grid grid-cols-2 gap-2">
            <label v-for="option in accessOptions" :key="option.value"
              class="relative flex cursor-pointer flex-col items-start gap-2 rounded-lg border px-3 py-2.5 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary-500/40"
              :class="access === option.value ? 'border-primary-300 bg-primary-50 text-primary-700' : 'border-default text-muted hover:bg-surface-hover'">
              <input v-model="access" class="sr-only" type="radio" name="reply-share-access" :value="option.value" @change="ensurePassword" />
              <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
                <path v-if="option.value === 'public'" stroke-linecap="round" stroke-linejoin="round" d="M10 13a5 5 0 0 0 7.1 0l3-3a5 5 0 0 0-7.1-7.1l-1.7 1.7m2.7 6.4a5 5 0 0 0-7.1 0l-3 3a5 5 0 0 0 7.1 7.1l1.7-1.7" />
                <path v-else stroke-linecap="round" stroke-linejoin="round" d="M7 10V7a5 5 0 0 1 10 0v3M6 10h12a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Zm6 4v3" />
              </svg>
              <span class="whitespace-nowrap text-sm font-medium">{{ option.label }}</span>
              <svg v-if="access === option.value" class="absolute right-3 top-3 h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="m4 10 4 4 8-8" />
              </svg>
            </label>
          </div>
          <p class="text-xs leading-5 text-muted">{{ access === 'public' ? '任何获得链接的人都可以查看。' : '对方输入密码后，即可查看回复和附件。' }}</p>
          <div v-if="access === 'password'" class="rounded-lg border border-default bg-canvas px-3 py-3">
            <div class="flex items-center justify-between gap-2">
              <label for="reply-share-password" class="shrink-0 text-xs text-muted">访问密码</label>
              <div class="flex min-w-0 items-center gap-1.5">
                <BaseInput id="reply-share-password" v-model="password" class="!h-8 !w-28 text-center font-mono !text-sm tracking-widest"
                  autocomplete="off" spellcheck="false" maxlength="32" required @focus="selectText" />
                <BaseButton type="button" intent="ghost" class="!h-8 !w-8 !px-0" aria-label="重新生成访问密码" title="换一组密码" @click="makePassword">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 17.9 17" />
                  </svg>
                </BaseButton>
              </div>
            </div>
            <p class="mt-2 text-xs leading-5 text-muted">自动生成 6 位密码，也可直接修改。</p>
          </div>
        </fieldset>
      </form>
      <div v-else class="space-y-3">
        <div class="space-y-2">
          <label for="reply-share-url" class="block text-xs text-muted">分享链接</label>
          <div class="flex items-center gap-2">
            <BaseInput id="reply-share-url" :model-value="shareUrl" readonly class="min-w-0 !h-9 !text-xs" @focus="selectText" />
            <BaseButton v-if="access === 'password'" intent="ghost" class="!h-9 !w-9 shrink-0 !px-0" aria-label="仅复制链接" title="仅复制链接" @click="copy(true)">
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="M8 5V3h13v13h-2M3 8h13v13H3z" />
              </svg>
            </BaseButton>
          </div>
        </div>
        <div v-if="access === 'password'" class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-canvas px-3 py-2.5">
          <span class="text-xs text-muted">访问密码</span>
          <span class="min-w-0 break-all font-mono text-sm font-medium tracking-widest text-default">{{ password }}</span>
        </div>
      </div>
      <p v-if="feedback" :role="copyFailed ? 'alert' : 'status'" class="flex items-start gap-1.5 text-xs leading-5"
        :class="copyFailed ? 'text-warning-700' : 'text-success-700'">
        <svg class="mt-0.5 h-4 w-4 shrink-0" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
          <path v-if="!copyFailed" stroke-linecap="round" stroke-linejoin="round" d="m4 10 4 4 8-8" />
          <template v-else><circle cx="10" cy="10" r="7.5" /><path stroke-linecap="round" d="M10 6v4m0 3h.01" /></template>
        </svg>
        {{ feedback }}
      </p>
      <p v-if="error" role="alert" class="text-xs leading-5 text-danger-600">{{ error }}</p>
    </div>
    <template #footer>
      <BaseButton type="button" intent="ghost" @click="emit('close')">关闭</BaseButton>
      <BaseButton v-if="!shareUrl" type="submit" form="reply-share-form" :disabled="busy || (access === 'password' && !password.trim())">
        <svg class="h-4 w-4" :class="busy ? 'animate-spin' : ''" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
          <path v-if="busy" stroke-linecap="round" d="M20 12a8 8 0 1 1-8-8" />
          <path v-else stroke-linecap="round" stroke-linejoin="round" d="M8 5V3h13v13h-2M3 8h13v13H3z" />
        </svg>
        {{ busy ? '生成中…' : '生成并复制' }}
      </BaseButton>
      <BaseButton v-else @click="copy(false)">
        <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M8 5V3h13v13h-2M3 8h13v13H3z" />
        </svg>
        {{ access === 'password' ? '复制链接和密码' : '复制链接' }}
      </BaseButton>
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
const accessOptions = [{ value: 'public', label: '无需密码' }, { value: 'password', label: '需要密码' }] as const
const access = ref<'public' | 'password'>('public')
const password = ref('')
const shareUrl = ref('')
const busy = ref(false)
const error = ref('')
const feedback = ref('')
const copyFailed = ref(false)
let closed = false
onBeforeUnmount(() => { closed = true; password.value = '' })
const shareText = computed(() => shareUrl.value + (access.value === 'password' ? '\n访问密码：' + password.value : ''))

function ensurePassword() {
  if (access.value === 'password' && !password.value) makePassword()
}
function makePassword() {
  if (!globalThis.crypto?.getRandomValues) return
  const alphabet = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
  password.value = Array.from(crypto.getRandomValues(new Uint8Array(6)), value => alphabet[value % alphabet.length]).join('')
}
function selectText(event: FocusEvent) {
  (event.target as HTMLInputElement).select()
}
async function copy(linkOnly = false) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(linkOnly ? shareUrl.value : shareText.value)
    if (closed) return
    copyFailed.value = false
    feedback.value = linkOnly || access.value === 'public' ? '链接已复制' : '链接和密码已复制'
  } catch {
    if (closed) return
    copyFailed.value = true
    feedback.value = '自动复制未成功，请重试或选中链接手动复制。'
  }
}
async function generate() {
  if (busy.value || (access.value === 'password' && !password.value.trim())) return
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

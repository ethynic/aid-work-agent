<template>
  <BaseModal v-model="visible" :title="title" size="lg">
    <div v-if="loading" class="flex items-center justify-center py-12 text-sm text-muted">
      <svg class="w-5 h-5 animate-spin mr-2" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
      加载中...
    </div>
    <div v-else-if="error" class="py-12 text-center text-sm text-muted">{{ error }}</div>
    <div v-else-if="record" class="space-y-4">
      <div class="grid grid-cols-2 gap-3 text-sm">
        <div v-for="field in metaFields" :key="field.label" :class="field.full ? 'col-span-2' : ''">
          <span class="text-muted">{{ field.label }}：</span>
          <span class="text-default break-all whitespace-normal">{{ field.value || '-' }}</span>
        </div>
      </div>
      <div v-if="badges.length" class="flex flex-wrap items-center gap-2">
        <span
          v-for="badge in badges"
          :key="badge.label"
          class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium"
          :class="badge.class"
        >{{ badge.label }}</span>
      </div>
      <div>
        <h3 class="text-sm font-medium text-muted mb-2">正文</h3>
        <div class="max-h-[60vh] overflow-y-auto rounded-lg border border-default bg-surface/50 p-4">
          <p class="text-sm text-default whitespace-pre-wrap break-words">{{ record.body || '（无正文）' }}</p>
        </div>
      </div>
    </div>
    <template #footer>
      <BaseButton intent="secondary" @click="visible = false">关闭</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { aiLeadGenAPI, type EmailRecord, type ReplyRecord, emailStatusBadge, intentBadge } from '@/api/aiLeadGen'

const props = defineProps<{
  modelValue: boolean
  kind: 'email' | 'reply'
  recordId: number | null
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const record = ref<EmailRecord | ReplyRecord | null>(null)
const loading = ref(false)
const error = ref('')

const title = computed(() => (props.kind === 'email' ? '邮件正文' : '回复原文'))

const metaFields = computed(() => {
  if (!record.value) return []
  if (props.kind === 'email') {
    const r = record.value as EmailRecord
    return [
      { label: '活动', value: r.campaign_name },
      { label: '收件人', value: r.contact_name ? `${r.contact_name} (${r.contact_email || '-'})` : r.contact_email },
      { label: '主题A', value: r.subject_a },
      { label: '主题B', value: r.subject_b },
      { label: 'A/B 分组', value: r.ab_group },
      { label: '发送时间', value: r.sent_at },
      { label: '错误信息', value: r.error, full: true },
      { label: 'Message-ID', value: r.message_id, full: true },
    ]
  }
  const r = record.value as ReplyRecord
  return [
    { label: '活动', value: r.campaign_name },
    { label: '联系人', value: r.contact_name ? `${r.contact_name} (${r.contact_email || '-'})` : r.contact_email },
    { label: '发件人', value: r.from_addr },
    { label: '主题', value: r.subject },
    { label: '接收时间', value: r.received_at },
    { label: '意图摘要', value: r.intent_summary, full: true },
    { label: 'Message-ID', value: r.message_id, full: true },
  ]
})

const badges = computed(() => {
  if (!record.value) return []
  if (props.kind === 'email') {
    const b = emailStatusBadge((record.value as EmailRecord).status)
    return b.label === '-' ? [] : [b]
  }
  const b = intentBadge((record.value as ReplyRecord).intent)
  return b.label === '-' ? [] : [b]
})

watch(() => [props.modelValue, props.recordId], async ([open, id]) => {
  if (!open || typeof id !== 'number') return
  loading.value = true
  error.value = ''
  record.value = null
  try {
    record.value = props.kind === 'email'
      ? await aiLeadGenAPI.getEmailDetail(id)
      : await aiLeadGenAPI.getReplyDetail(id)
  } catch (e: any) {
    error.value = e.message || '加载正文失败'
  } finally {
    loading.value = false
  }
}, { immediate: true })
</script>

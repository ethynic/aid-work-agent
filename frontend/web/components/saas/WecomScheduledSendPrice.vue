<template>
  <BaseCard title="企业微信定时消息单价">
    <p v-if="loading" role="status" class="m-0 text-sm text-muted">正在读取单价…</p>
    <form v-else-if="price" class="flex flex-wrap items-end gap-3" @submit.prevent="save(input)">
      <div>
        <label for="wecom-scheduled-price" class="form-label">每条消息单价（积分）</label>
        <BaseInput id="wecom-scheduled-price" v-model="input" inputmode="decimal" maxlength="13"
          class="w-36" :disabled="saving" />
      </div>
      <BaseButton type="submit" :disabled="saving">保存单价</BaseButton>
      <BaseButton type="button" intent="secondary" :disabled="saving || !price.overridden" @click="save(null)">恢复默认</BaseButton>
      <span class="text-sm text-muted">当前 {{ price.unit_price }} · 默认 {{ price.default_price }}</span>
    </form>
    <BaseButton v-else type="button" intent="secondary" @click="load">重试</BaseButton>
    <p v-if="price" class="mt-3 mb-0 text-xs text-muted">设备取得发送授权时锁定单价，确认成功后才扣费。</p>
    <p v-if="error" role="alert" class="mt-2 mb-0 text-sm text-danger-700">{{ error }}</p>
    <p v-if="notice" role="status" class="mt-2 mb-0 text-sm text-success-700">{{ notice }}</p>
  </BaseCard>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { wecomScheduledSendPrice, type WecomScheduledSendPrice } from '@/api/adminReports'

const price = ref<WecomScheduledSendPrice | null>(null)
const input = ref('')
const error = ref('')
const notice = ref('')
const loading = ref(false)
const saving = ref(false)
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e)

async function load() {
  loading.value = true
  error.value = ''
  try {
    price.value = await wecomScheduledSendPrice()
    input.value = price.value.unit_price
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

async function save(value: string | null) {
  if (saving.value) return
  error.value = ''
  notice.value = ''
  const checked = typeof value === 'string' ? value.trim() : value
  if (checked !== null && !/^\d{1,10}(\.\d{1,2})?$/.test(checked)) {
    error.value = '请输入非负积分，最多保留两位小数。'
    return
  }
  saving.value = true
  try {
    price.value = await wecomScheduledSendPrice(checked)
    input.value = price.value.unit_price
    notice.value = checked === null ? '已恢复默认单价' : '单价已保存'
  } catch (e) {
    error.value = errorText(e)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="bg-surface rounded-xl shadow-sm border border-default p-6">
    <div class="flex items-center justify-between mb-2">
      <h2 class="text-base font-semibold text-default">租户邮箱</h2>
      <button
        v-if="bound"
        @click="handleDelete"
        :disabled="saving"
        class="text-xs text-danger-600 hover:text-danger-700 underline"
      >删除配置</button>
    </div>
    <p class="text-xs text-muted mb-4">
      本租户共用的企业发件邮箱（SMTP/IMAP）。用户未配置个人邮箱时，AI获客等数字员工用此邮箱发信并接收回复。
    </p>

    <div v-if="loading" class="text-center py-6 text-muted text-sm">加载中...</div>

    <template v-else>
      <div v-if="bound" class="mb-4 p-3 rounded-lg bg-success-50 border border-success-200 text-sm text-success-700">
        已配置 <b>{{ form.email_address }}</b>（密码不回显；如需更换请重新填写完整表单并保存）
      </div>

      <div class="space-y-4">
        <div>
          <label class="block text-sm text-default mb-1">邮箱地址 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="form.email_address" placeholder="name@company.com" size="md" />
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm text-default mb-1">SMTP 服务器 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="form.smtp_server" placeholder="smtp.example.com" size="md" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">SMTP 端口 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="form.smtp_port" placeholder="465" size="md" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm text-default mb-1">SMTP 用户名 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="form.smtp_user" placeholder="通常为邮箱地址" size="md" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">SMTP 密码/授权码 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="form.smtp_password" type="password" placeholder="SMTP 密码或授权码" size="md" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm text-default mb-1">SMTP 加密</label>
            <BaseSelect v-model="form.smtp_encryption" size="md">
              <option v-for="opt in ENCRYPTION_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </BaseSelect>
          </div>
          <div>
            <label class="block text-sm text-default mb-1">IMAP 服务器 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="form.imap_server" placeholder="imap.example.com" size="md" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm text-default mb-1">IMAP 端口 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="form.imap_port" placeholder="993" size="md" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">IMAP 加密</label>
            <BaseSelect v-model="form.imap_encryption" size="md">
              <option v-for="opt in ENCRYPTION_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </BaseSelect>
          </div>
        </div>

        <div v-if="message" class="p-3 rounded-lg text-sm"
          :class="messageType === 'success' ? 'bg-success-50 border border-success-200 text-success-600' : 'bg-danger-50 border border-danger-200 text-danger-600'">
          {{ message }}
        </div>

        <div class="flex justify-end">
          <BaseButton :disabled="saving || !canSave" @click="handleSave">
            {{ saving ? '保存中...' : bound ? '保存更新' : '保存启用' }}
          </BaseButton>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import {
  getTenantEmailSettings,
  saveTenantEmailSettings,
  deleteTenantEmailSettings,
  type TenantEmailConfig
} from '@/api/tenantEmail'

// 端口在表单里用字符串（BaseInput 的 modelValue 为 string），提交时转数字
type TenantEmailForm = Omit<TenantEmailConfig, 'smtp_port' | 'imap_port'>
  & { smtp_port: string; imap_port: string }

const ENCRYPTION_OPTIONS = [
  { value: 'ssl', label: 'SSL（465/993）' },
  { value: 'tls', label: 'STARTTLS（587/143）' },
  { value: 'none', label: '无加密（不推荐）' }
]

const loading = ref(true)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const bound = ref(false)

const form = ref<TenantEmailForm>({
  email_address: '',
  smtp_server: '',
  smtp_port: '465',
  smtp_user: '',
  smtp_password: '',
  smtp_encryption: 'ssl',
  imap_server: '',
  imap_port: '993',
  imap_encryption: 'ssl'
})

// 已绑定且密码为掩码回显时，禁止把 "****" 当新密码提交
const canSave = computed(() =>
  !!form.value.email_address.trim()
  && !!form.value.smtp_server.trim()
  && !!form.value.smtp_user.trim()
  && !!form.value.smtp_password.trim()
  && form.value.smtp_password !== '****'
  && !!form.value.imap_server.trim()
)

async function load() {
  loading.value = true
  try {
    const res = await getTenantEmailSettings()
    if (res.success && res.data) {
      bound.value = true
      form.value = {
        ...form.value,
        ...res.data,
        smtp_password: '****',
        smtp_port: String(res.data.smtp_port ?? ''),
        imap_port: String(res.data.imap_port ?? '')
      }
    }
  } catch (e) {
    console.error('加载租户邮箱配置失败:', e)
    message.value = '加载租户邮箱配置失败'
    messageType.value = 'error'
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  if (!canSave.value) return
  if (!confirm('保存前会发送一封测试邮件验证配置，确定继续？')) return
  saving.value = true
  message.value = ''
  try {
    const payload: TenantEmailConfig = {
      ...form.value,
      smtp_port: Number(form.value.smtp_port) || 0,
      imap_port: Number(form.value.imap_port) || 0
    }
    const res = await saveTenantEmailSettings(payload)
    if (res.success) {
      bound.value = true
      form.value.smtp_password = '****'
      message.value = '配置成功（测试邮件已发送，请查收）'
      messageType.value = 'success'
    } else {
      message.value = res.error || '保存失败'
      messageType.value = 'error'
    }
  } catch (e: any) {
    message.value = e.message || '保存失败'
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

async function handleDelete() {
  if (!confirm('确定删除租户邮箱配置？删除后未配置个人邮箱的用户将无法用 AI获客 发信。')) return
  saving.value = true
  message.value = ''
  try {
    const res = await deleteTenantEmailSettings()
    if (res.success) {
      bound.value = false
      form.value = { ...form.value, email_address: '', smtp_password: '' }
      message.value = '已删除'
      messageType.value = 'success'
    } else {
      message.value = res.error || '删除失败'
      messageType.value = 'error'
    }
  } catch (e: any) {
    message.value = e.message || '删除失败'
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold text-slate-800 mb-6">企业设置</h1>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <div v-else class="max-w-lg">
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-slate-600 mb-1">企业名称</label>
            <input v-model="form.company_name" type="text"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">联系人</label>
            <input v-model="form.contact_name" type="text"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">联系电话</label>
            <input v-model="form.contact_phone" type="tel"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
        </div>

        <div v-if="message" class="mt-4 p-3 rounded-lg text-sm"
          :class="messageType === 'success' ? 'bg-green-50 border border-green-200 text-green-600' : 'bg-red-50 border border-red-200 text-red-600'">
          {{ message }}
        </div>

        <button
          @click="handleSave"
          :disabled="saving"
          class="mt-6 w-full py-2.5 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
        >
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getTenantInfo, updateTenantInfo } from '@/api/saasTenant'

const loading = ref(true)
const saving = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

const form = ref({
  company_name: '',
  contact_name: '',
  contact_phone: ''
})

async function loadSettings() {
  loading.value = true
  try {
    const res = await getTenantInfo()
    if (res.tenant) {
      form.value = {
        company_name: res.tenant.company_name || '',
        contact_name: res.tenant.contact_name || '',
        contact_phone: res.tenant.contact_phone || ''
      }
    }
  } catch (e) {
    console.error('加载企业设置失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  message.value = ''
  try {
    await updateTenantInfo(form.value)
    message.value = '保存成功'
    messageType.value = 'success'
  } catch (e: any) {
    message.value = e.message || '保存失败'
    messageType.value = 'error'
  } finally {
    saving.value = false
  }
}

onMounted(() => loadSettings())
</script>

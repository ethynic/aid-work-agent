<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-slate-800">渠道配置</h1>
      <button
        @click="openAddChannel"
        class="px-4 py-2 bg-cyan-500 hover:bg-cyan-600 text-white rounded-lg text-sm font-medium transition-colors"
      >
        添加渠道
      </button>
    </div>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <!-- 渠道列表 -->
    <div v-else-if="channels.length > 0" class="space-y-4">
      <div v-for="ch in channels" :key="ch.config_id" class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
              {{ channelTypeLabel(ch.channel_type) }}
            </span>
            <span
              class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
              :class="ch.verified ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'"
            >
              {{ ch.verified ? '已验证' : '未验证' }}
            </span>
            <span class="text-sm text-slate-500">{{ ch.created_at }}</span>
          </div>
          <div class="flex items-center gap-2">
            <button @click="handleVerify(ch.config_id)" class="text-xs px-2 py-1 bg-green-100 text-green-700 rounded hover:bg-green-200 transition-colors">验证</button>
            <button @click="editChannel(ch)" class="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 transition-colors">编辑</button>
            <button @click="handleDelete(ch.config_id)" class="text-xs px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 transition-colors">删除</button>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="text-center py-12 text-slate-500">
      <p class="text-lg mb-2">暂无渠道配置</p>
      <p class="text-sm">点击"添加渠道"配置 IM 渠道凭证</p>
    </div>

    <!-- 添加/编辑渠道弹窗 -->
    <div v-if="showForm" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showForm = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h3 class="text-lg font-bold text-slate-800 mb-4">{{ editingId ? '编辑渠道' : '添加渠道' }}</h3>
        <div class="space-y-4">
          <div v-if="!editingId">
            <label class="block text-sm text-slate-600 mb-1">渠道类型</label>
            <select v-model="form.channel_type"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400">
              <option value="wecom">企业微信</option>
              <option value="dingtalk">钉钉</option>
              <option value="feishu">飞书</option>
            </select>
          </div>
          <div v-for="field in channelFields" :key="field.key">
            <label class="block text-sm text-slate-600 mb-1">{{ field.label }}</label>
            <input v-model="form.config[field.key]" type="text" :placeholder="field.placeholder"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
        </div>
        <div v-if="formError" class="mt-3 p-2 bg-red-50 border border-red-200 rounded text-red-600 text-sm">{{ formError }}</div>
        <div class="flex gap-3 mt-6">
          <button @click="showForm = false" class="flex-1 py-2 border border-slate-300 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
          <button @click="handleSubmit" :disabled="submitting" class="flex-1 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg transition-colors">
            {{ submitting ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { listChannels, createChannel, updateChannel, deleteChannel, verifyChannel } from '@/api/saasTenant'

const loading = ref(true)
const channels = ref<any[]>([])
const showForm = ref(false)
const submitting = ref(false)
const formError = ref('')
const editingId = ref<string | null>(null)

const form = ref<{ channel_type: string; config: Record<string, string> }>({
  channel_type: 'wecom',
  config: {}
})

const channelFieldMap: Record<string, { key: string; label: string; placeholder: string }[]> = {
  wecom: [
    { key: 'corp_id', label: '企业 ID (CorpID)', placeholder: 'ww...' },
    { key: 'agent_secret', label: '应用 Secret', placeholder: '' },
    { key: 'token', label: 'Token', placeholder: '' },
    { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '' },
  ],
  dingtalk: [
    { key: 'app_key', label: 'App Key', placeholder: '' },
    { key: 'app_secret', label: 'App Secret', placeholder: '' },
  ],
  feishu: [
    { key: 'app_id', label: 'App ID', placeholder: 'cli_' },
    { key: 'app_secret', label: 'App Secret', placeholder: '' },
    { key: 'verification_token', label: 'Verification Token', placeholder: '' },
    { key: 'encrypt_key', label: 'Encrypt Key', placeholder: '' },
  ],
}

const channelFields = computed(() => channelFieldMap[form.value.channel_type] || [])

function channelTypeLabel(type: string) {
  const map: Record<string, string> = { wecom: '企业微信', dingtalk: '钉钉', feishu: '飞书' }
  return map[type] || type
}

function openAddChannel() {
  editingId.value = null
  form.value = { channel_type: 'wecom', config: {} }
  formError.value = ''
  showForm.value = true
}

function editChannel(ch: any) {
  editingId.value = ch.config_id
  form.value = { channel_type: ch.channel_type, config: { ...ch.config } }
  formError.value = ''
  showForm.value = true
}

async function loadChannels() {
  loading.value = true
  try {
    const res = await listChannels()
    channels.value = res.channels || []
  } catch (e) {
    console.error('加载渠道列表失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleSubmit() {
  submitting.value = true
  formError.value = ''
  try {
    if (editingId.value) {
      await updateChannel(editingId.value, { config: form.value.config })
    } else {
      await createChannel(form.value)
    }
    showForm.value = false
    await loadChannels()
  } catch (e: any) {
    formError.value = e.message || '保存失败'
  } finally {
    submitting.value = false
  }
}

async function handleVerify(configId: string) {
  try {
    const res = await verifyChannel(configId)
    alert(res.message || (res.verified ? '验证通过' : '验证失败'))
    await loadChannels()
  } catch (e: any) {
    alert(e.message || '验证失败')
  }
}

async function handleDelete(configId: string) {
  if (!confirm('确定要删除此渠道配置吗？')) return
  try {
    await deleteChannel(configId)
    await loadChannels()
  } catch (e: any) {
    alert(e.message || '删除失败')
  }
}

onMounted(() => loadChannels())
</script>

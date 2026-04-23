<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-50 flex items-center justify-center">
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="$emit('close')"></div>

      <!-- Dialog -->
      <div class="relative bg-white rounded-2xl shadow-2xl w-full max-w-2xl mx-4 max-h-[85vh] flex flex-col overflow-hidden">
        <!-- Header -->
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 class="text-base font-semibold text-gray-800">设置</h2>
          <button @click="$emit('close')" class="p-1 text-gray-400 hover:text-gray-600 rounded transition-colors text-base">&times;</button>
        </div>

        <!-- Body: Left Nav + Right Content -->
        <div class="flex flex-1 min-h-0">
          <!-- Left Sidebar Navigation -->
          <nav class="w-40 flex-shrink-0 border-r border-gray-200 bg-gray-50 py-3">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              @click="activeTab = tab.key"
              :class="[
                'w-full flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors text-left',
                activeTab === tab.key
                  ? 'bg-blue-50 text-blue-700 font-medium border-r-2 border-blue-600'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-800'
              ]"
            >
              <component :is="tab.icon" :class="['w-4 h-4 flex-shrink-0', activeTab === tab.key ? 'text-blue-600' : 'text-gray-400']" />
              {{ tab.label }}
            </button>
          </nav>

          <!-- Right Content Area -->
          <div class="flex-1 overflow-y-auto p-6">

            <!-- ====== Basic Settings ====== -->
            <div v-if="activeTab === 'basic'" class="space-y-5">
              <!-- Avatar -->
              <div class="flex items-center gap-4">
                <div class="w-16 h-16 rounded-full bg-gray-200 flex items-center justify-center overflow-hidden flex-shrink-0">
                  <img v-if="profileForm.avatar_url" :src="profileForm.avatar_url" alt="头像" class="w-full h-full object-cover" />
                  <svg v-else class="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
                <div class="flex-1">
                  <label class="block text-sm font-medium text-gray-700 mb-1">头像 URL</label>
                  <input
                    v-model="profileForm.avatar_url"
                    type="text"
                    placeholder="输入头像图片链接"
                    class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  />
                </div>
              </div>

              <!-- Display Name -->
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">显示名称</label>
                <input
                  v-model="profileForm.username"
                  type="text"
                  placeholder="请输入显示名称"
                  class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>

              <!-- Phone (readonly) -->
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">手机号码</label>
                <input
                  :value="user?.phone || '未绑定'"
                  type="text"
                  readonly
                  class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 text-gray-500"
                />
              </div>

              <!-- Save Button -->
              <div class="pt-3 flex justify-end">
                <button
                  @click="saveProfile"
                  :disabled="profileSaving"
                  class="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {{ profileSaving ? '保存中...' : '保存' }}
                </button>
              </div>
            </div>

            <!-- ====== Email Settings ====== -->
            <div v-if="activeTab === 'email'">
              <!-- Email Bound Status -->
              <div v-if="emailBound && !showEmailForm" class="space-y-4">
                <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                  <div class="flex items-center gap-2 text-green-700">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span class="font-medium">邮箱已绑定</span>
                  </div>
                  <p class="mt-1 text-sm text-green-600">{{ emailConfig?.email_address }}</p>
                </div>

                <div class="bg-gray-50 rounded-lg p-4 space-y-2 text-sm">
                  <div class="flex justify-between"><span class="text-gray-500">SMTP 服务器</span><span>{{ emailConfig?.smtp_server }}:{{ emailConfig?.smtp_port }}</span></div>
                  <div class="flex justify-between"><span class="text-gray-500">SMTP 用户名</span><span>{{ emailConfig?.smtp_user }}</span></div>
                  <div class="flex justify-between"><span class="text-gray-500">SMTP 加密</span><span>{{ emailConfig?.smtp_encryption?.toUpperCase() }}</span></div>
                  <div class="flex justify-between"><span class="text-gray-500">IMAP 服务器</span><span>{{ emailConfig?.imap_server }}:{{ emailConfig?.imap_port }}</span></div>
                </div>

                <div class="flex gap-3">
                  <button @click="showEmailForm = true" class="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50 transition-colors">
                    修改配置
                  </button>
                  <button @click="handleDeleteEmail" :disabled="emailDeleting" class="px-4 py-2 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50 disabled:opacity-50 transition-colors">
                    {{ emailDeleting ? '删除中...' : '解除绑定' }}
                  </button>
                </div>
              </div>

              <!-- Email Form (create / edit) -->
              <div v-else class="space-y-4">
                <div class="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-700">
                  保存时系统会向您的邮箱发送一封测试邮件，验证通过后才会保存配置。
                </div>

                <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">邮箱地址</label>
                  <input v-model="emailForm.email_address" type="email" placeholder="your@email.com" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                </div>

                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">SMTP 服务器</label>
                    <input v-model="emailForm.smtp_server" type="text" placeholder="smtp.example.com" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                  </div>
                  <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">SMTP 端口</label>
                    <input v-model.number="emailForm.smtp_port" type="number" placeholder="465" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                  </div>
                </div>

                <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">SMTP 用户名</label>
                  <input v-model="emailForm.smtp_user" type="text" placeholder="通常与邮箱地址相同" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                </div>

                <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">SMTP 密码</label>
                  <input v-model="emailForm.smtp_password" type="password" :placeholder="emailBound ? '不修改请留空' : '请输入邮箱密码/授权码'" autocomplete="off" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                </div>

                <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">SMTP 加密方式</label>
                  <select v-model="emailForm.smtp_encryption" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500">
                    <option value="ssl">SSL/TLS</option>
                    <option value="tls">STARTTLS</option>
                    <option value="none">无加密（不推荐）</option>
                  </select>
                </div>

                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">IMAP 服务器</label>
                    <input v-model="emailForm.imap_server" type="text" placeholder="imap.example.com" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                  </div>
                  <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">IMAP 端口</label>
                    <input v-model.number="emailForm.imap_port" type="number" placeholder="993" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                  </div>
                </div>

                <div>
                  <label class="block text-sm font-medium text-gray-700 mb-1">IMAP 加密方式</label>
                  <select v-model="emailForm.imap_encryption" class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500">
                    <option value="ssl">SSL/TLS</option>
                    <option value="tls">STARTTLS</option>
                    <option value="none">无加密（不推荐）</option>
                  </select>
                </div>

                <!-- Error Message -->
                <div v-if="emailError" class="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-600">
                  {{ emailError }}
                </div>

                <div class="flex gap-3 pt-2">
                  <button
                    v-if="emailBound"
                    @click="cancelEmailEdit"
                    class="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50 transition-colors"
                  >
                    取消
                  </button>
                  <button
                    @click="saveEmailSettings"
                    :disabled="emailSaving"
                    class="flex-1 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {{ emailSaving ? '正在发送测试邮件...' : '保存并测试' }}
                  </button>
                </div>
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, reactive, watch, h } from 'vue'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useToast } from 'vue-toastification'
import {
  getEmailSettings as apiGetEmailSettings,
  saveEmailSettings as apiSaveEmailSettings,
  deleteEmailSettings as apiDeleteEmailSettings,
  updateProfile as apiUpdateProfile,
} from '@/api/settings'
import type { EmailSettings } from '@/api/settings'

// ====== Icon Components ======
const IconUser = {
  render() {
    return h('svg', { fill: 'none', stroke: 'currentColor', viewBox: '0 0 24 24' }, [
      h('path', { 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'stroke-width': '2', d: 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z' })
    ])
  }
}
const IconMail = {
  render() {
    return h('svg', { fill: 'none', stroke: 'currentColor', viewBox: '0 0 24 24' }, [
      h('path', { 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'stroke-width': '2', d: 'M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z' })
    ])
  }
}

const props = defineProps<{
  visible: boolean
}>()

defineEmits<{
  (e: 'close'): void
}>()

const { user, setLogin } = useDemoAuth()
const toast = useToast()

const tabs = [
  { key: 'basic', label: '基础设置', icon: IconUser },
  { key: 'email', label: '邮箱设置', icon: IconMail },
]
const activeTab = ref('basic')

// ====== Basic Profile ======
const profileForm = reactive({
  username: '',
  avatar_url: '',
})
const profileSaving = ref(false)

watch(() => props.visible, (val) => {
  if (val && user.value) {
    profileForm.username = user.value.username || ''
    profileForm.avatar_url = user.value.avatar_url || ''
    loadEmailSettings()
  }
})

async function saveProfile() {
  profileSaving.value = true
  try {
    const result = await apiUpdateProfile({
      username: profileForm.username,
      avatar_url: profileForm.avatar_url || undefined,
    })
    if (result.success && result.user) {
      setLogin(localStorage.getItem('demo_token')!, result.user)
      toast.success('资料更新成功')
    } else {
      toast.error(result.error || '更新失败')
    }
  } catch (e: any) {
    toast.error('更新失败: ' + e.message)
  } finally {
    profileSaving.value = false
  }
}

// ====== Email Settings ======
const emailBound = ref(false)
const emailConfig = ref<EmailSettings | null>(null)
const showEmailForm = ref(true)
const emailSaving = ref(false)
const emailDeleting = ref(false)
const emailError = ref('')

const emailForm = reactive<EmailSettings>({
  email_address: '',
  smtp_server: '',
  smtp_port: 465,
  smtp_user: '',
  smtp_password: '',
  smtp_encryption: 'ssl',
  imap_server: '',
  imap_port: 993,
  imap_encryption: 'ssl',
})

async function loadEmailSettings() {
  try {
    const result = await apiGetEmailSettings()
    if (result.success && result.bound && result.data) {
      emailBound.value = true
      emailConfig.value = result.data
      showEmailForm.value = false
      emailForm.email_address = result.data.email_address
      emailForm.smtp_server = result.data.smtp_server
      emailForm.smtp_port = result.data.smtp_port
      emailForm.smtp_user = result.data.smtp_user
      emailForm.smtp_password = ''
      emailForm.smtp_encryption = result.data.smtp_encryption
      emailForm.imap_server = result.data.imap_server
      emailForm.imap_port = result.data.imap_port
      emailForm.imap_encryption = result.data.imap_encryption
    } else {
      emailBound.value = false
      emailConfig.value = null
      showEmailForm.value = true
      emailForm.email_address = ''
      emailForm.smtp_server = ''
      emailForm.smtp_port = 465
      emailForm.smtp_user = ''
      emailForm.smtp_password = ''
      emailForm.smtp_encryption = 'ssl'
      emailForm.imap_server = ''
      emailForm.imap_port = 993
      emailForm.imap_encryption = 'ssl'
    }
  } catch (e) {
    console.error('Failed to load email settings:', e)
  }
}

async function saveEmailSettings() {
  emailError.value = ''
  emailSaving.value = true

  if (!emailForm.email_address || !emailForm.smtp_server || !emailForm.smtp_user) {
    emailError.value = '请填写邮箱地址、SMTP 服务器和用户名'
    emailSaving.value = false
    return
  }

  if (!emailForm.smtp_password) {
    emailError.value = '请填写 SMTP 密码'
    emailSaving.value = false
    return
  }

  try {
    const result = await apiSaveEmailSettings({ ...emailForm })
    if (result.success) {
      toast.success('邮箱绑定成功！测试邮件已发送到您的邮箱。')
      await loadEmailSettings()
    } else {
      emailError.value = result.error || '保存失败'
    }
  } catch (e: any) {
    emailError.value = '保存失败: ' + e.message
  } finally {
    emailSaving.value = false
  }
}

function cancelEmailEdit() {
  showEmailForm.value = false
}

async function handleDeleteEmail() {
  if (!confirm('确定要解除邮箱绑定吗？')) return
  emailDeleting.value = true
  try {
    const result = await apiDeleteEmailSettings()
    if (result.success) {
      await loadEmailSettings()
    } else {
      toast.error(result.error || '删除失败')
    }
  } catch (e: any) {
    toast.error('删除失败: ' + e.message)
  } finally {
    emailDeleting.value = false
  }
}
</script>

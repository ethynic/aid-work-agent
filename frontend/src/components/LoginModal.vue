<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-50 flex items-center justify-center">
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" @click="handleBackdropClick"></div>

      <!-- Modal -->
      <div class="relative bg-white rounded-2xl shadow-2xl w-full max-w-4xl mx-4 overflow-hidden">
        <div class="flex flex-col md:flex-row min-h-[500px]">
          <!-- Left: WeChat QRCode -->
          <div class="w-full md:w-1/2 bg-gradient-to-br from-green-600 to-green-700 p-8 flex flex-col items-center justify-center">
            <h2 class="text-2xl font-bold text-white mb-6">微信扫码登录</h2>

            <!-- QRCode Area -->
            <div class="bg-white rounded-xl p-4 mb-4">
              <div v-if="wxStatus === 'waiting'" class="w-48 h-48 flex items-center justify-center">
                <div v-if="wxQrcodeUrl" class="flex flex-col items-center">
                  <img :src="wxQrcodeUrl" alt="WeChat QRCode" class="w-48 h-48" />
                  <p class="text-sm text-gray-500 mt-2">请使用微信扫码</p>
                </div>
                <div v-else class="text-gray-400">加载中...</div>
              </div>
              <div v-else-if="wxStatus === 'scanned'" class="w-48 h-48 flex flex-col items-center justify-center">
                <svg class="w-16 h-16 text-green-500 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p class="text-sm text-gray-600 mt-2">已扫码，请确认登录</p>
              </div>
              <div v-else-if="wxStatus === 'confirmed'" class="w-48 h-48 flex flex-col items-center justify-center">
                <svg class="w-16 h-16 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
                </svg>
                <p class="text-sm text-gray-600 mt-2">登录成功</p>
              </div>
              <div v-else class="w-48 h-48 flex flex-col items-center justify-center text-red-500">
                <svg class="w-16 h-16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
                <p class="text-sm mt-2">二维码已过期</p>
                <button @click="refreshWxQrcode" class="mt-2 text-sm text-green-600 hover:underline">点击刷新</button>
              </div>
            </div>

            <p class="text-green-100 text-sm">安全快捷的登录方式</p>
          </div>

          <!-- Right: Phone Login -->
          <div class="w-full md:w-1/2 p-8">
            <!-- Tabs -->
            <div class="flex border-b border-slate-200 mb-6">
              <button
                v-for="tab in tabs"
                :key="tab.key"
                @click="currentTab = tab.key"
                :class="[
                  'px-4 py-2 text-sm font-medium transition-colors',
                  currentTab === tab.key
                    ? 'text-cyan-500 border-b-2 border-cyan-500'
                    : 'text-slate-500 hover:text-slate-700'
                ]"
              >
                {{ tab.label }}
              </button>
            </div>

            <!-- Password Login -->
            <div v-if="currentTab === 'password'">
              <div class="space-y-4">
                <div>
                  <label class="block text-sm text-slate-600 mb-1">手机号</label>
                  <input
                    v-model="phoneForm.phone"
                    type="tel"
                    placeholder="请输入手机号"
                    class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                  />
                </div>
                <div>
                  <label class="block text-sm text-slate-600 mb-1">密码</label>
                  <input
                    v-model="phoneForm.password"
                    type="password"
                    placeholder="请输入密码"
                    class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                    @keyup.enter="handlePasswordLogin"
                  />
                </div>
                <button
                  @click="handlePasswordLogin"
                  :disabled="isLoading"
                  class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
                >
                  {{ isLoading ? '登录中...' : '登录' }}
                </button>
              </div>
            </div>

            <!-- Code Login -->
            <div v-if="currentTab === 'code'">
              <div class="space-y-4">
                <div>
                  <label class="block text-sm text-slate-600 mb-1">手机号</label>
                  <input
                    v-model="codeForm.phone"
                    type="tel"
                    placeholder="请输入手机号"
                    class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                  />
                </div>
                <div>
                  <label class="block text-sm text-slate-600 mb-1">验证码</label>
                  <div class="flex gap-2">
                    <input
                      v-model="codeForm.code"
                      type="text"
                      placeholder="请输入验证码"
                      maxlength="6"
                      class="flex-1 px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                      @keyup.enter="handleCodeLogin"
                    />
                    <button
                      @click="handleSendCode"
                      :disabled="codeCooldown > 0 || isSendingCode"
                      class="px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-cyan-500 hover:bg-slate-200 disabled:text-slate-400 transition-colors whitespace-nowrap"
                    >
                      {{ codeCooldown > 0 ? `${codeCooldown}s` : (isSendingCode ? '发送中...' : '获取验证码') }}
                    </button>
                  </div>
                  <p class="text-xs text-slate-400 mt-1">测试环境验证码固定为：888888</p>
                </div>
                <button
                  @click="handleCodeLogin"
                  :disabled="isLoading"
                  class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-600 text-white rounded-lg font-medium transition-colors"
                >
                  {{ isLoading ? '登录中...' : '登录' }}
                </button>
              </div>
            </div>

            <!-- Error Message -->
            <div v-if="errorMessage" class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
              {{ errorMessage }}
            </div>

            <!-- Close Button -->
            <button
              @click="$emit('close')"
              class="absolute top-4 right-4 text-slate-400 hover:text-slate-600 transition-colors"
            >
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { phoneLogin, phoneCodeLogin, sendSmsCode, getWxQrcode, checkWxQrcodeStatus, wxLogin } from '@/api/auth'
import { useAuth } from '@/composables/useAuth'

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
  success: []
}>()

const { setLogin } = useAuth()

const tabs = [
  { key: 'password', label: '密码登录' },
  { key: 'code', label: '验证码登录' }
]

const currentTab = ref('password')
const isLoading = ref(false)
const isSendingCode = ref(false)
const errorMessage = ref('')
const codeCooldown = ref(0)

// Phone Password Form
const phoneForm = ref({
  phone: '',
  password: ''
})

// Phone Code Form
const codeForm = ref({
  phone: '',
  code: ''
})

// WeChat Login
const wxQrcodeUrl = ref('')
const wxSceneStr = ref('')
const wxStatus = ref<'waiting' | 'scanned' | 'confirmed' | 'expired'>('waiting')
let wxPollingInterval: number | null = null

// Cooldown timer for SMS
let cooldownTimer: number | null = null

function startCooldown() {
  codeCooldown.value = 60
  cooldownTimer = window.setInterval(() => {
    codeCooldown.value--
    if (codeCooldown.value <= 0) {
      if (cooldownTimer) clearInterval(cooldownTimer)
    }
  }, 1000)
}

async function handleSendCode() {
  let phone = ''
  if (currentTab.value === 'code') {
    phone = codeForm.value.phone
  }

  if (!phone || phone.length !== 11) {
    errorMessage.value = '请输入正确的手机号'
    return
  }

  isSendingCode.value = true
  errorMessage.value = ''

  try {
    const res = await sendSmsCode(phone)
    if (res.success) {
      startCooldown()
    } else {
      errorMessage.value = res.message || '发送失败'
    }
  } catch (e: any) {
    errorMessage.value = e.message || '发送失败'
  } finally {
    isSendingCode.value = false
  }
}

async function handlePasswordLogin() {
  if (!phoneForm.value.phone || !phoneForm.value.password) {
    errorMessage.value = '请输入手机号和密码'
    return
  }

  isLoading.value = true
  errorMessage.value = ''

  try {
    const res = await phoneLogin(phoneForm.value.phone, phoneForm.value.password)
    if (res.success && res.token && res.user) {
      setLogin(res.token, res.user)
      emit('success')
    } else {
      errorMessage.value = res.message || '登录失败'
    }
  } catch (e: any) {
    errorMessage.value = e.message || '登录失败'
  } finally {
    isLoading.value = false
  }
}

async function handleCodeLogin() {
  if (!codeForm.value.phone || !codeForm.value.code) {
    errorMessage.value = '请输入手机号和验证码'
    return
  }

  isLoading.value = true
  errorMessage.value = ''

  try {
    const res = await phoneCodeLogin(codeForm.value.phone, codeForm.value.code)
    if (res.success && res.token && res.user) {
      setLogin(res.token, res.user)
      emit('success')
    } else {
      errorMessage.value = res.message || '登录失败'
    }
  } catch (e: any) {
    errorMessage.value = e.message || '登录失败'
  } finally {
    isLoading.value = false
  }
}

async function loadWxQrcode() {
  try {
    const res = await getWxQrcode()
    wxQrcodeUrl.value = res.qrcode_url
    wxSceneStr.value = res.scene_str
    wxStatus.value = 'waiting'
    startWxPolling()
  } catch (e) {
    console.error('Failed to load WeChat QRCode:', e)
  }
}

function startWxPolling() {
  if (wxPollingInterval) clearInterval(wxPollingInterval)

  wxPollingInterval = window.setInterval(async () => {
    if (!wxSceneStr.value) return

    try {
      const res = await checkWxQrcodeStatus(wxSceneStr.value)
      wxStatus.value = res.status

      if (res.status === 'confirmed' && res.openid) {
        stopWxPolling()
        // Auto login with WeChat
        const loginRes = await wxLogin(res.openid, res.unionid)
        if (loginRes.success && loginRes.token && loginRes.user) {
          setLogin(loginRes.token, loginRes.user)
          emit('success')
        } else if (loginRes.message === '请绑定手机号以完���登录') {
          // WeChat user needs to bind phone
          errorMessage.value = '请绑定手机号以完成登录'
        }
      } else if (res.status === 'expired') {
        stopWxPolling()
      }
    } catch (e) {
      console.error('Failed to check WeChat status:', e)
    }
  }, 2000)
}

function stopWxPolling() {
  if (wxPollingInterval) {
    clearInterval(wxPollingInterval)
    wxPollingInterval = null
  }
}

function refreshWxQrcode() {
  loadWxQrcode()
}

function handleBackdropClick() {
  // Don't close on backdrop click for login modal
}

watch(() => props.visible, (val) => {
  if (val) {
    errorMessage.value = ''
    loadWxQrcode()
  } else {
    stopWxPolling()
  }
})

onUnmounted(() => {
  stopWxPolling()
  if (cooldownTimer) clearInterval(cooldownTimer)
})
</script>

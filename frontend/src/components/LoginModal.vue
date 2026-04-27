<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-50 flex items-center justify-center">
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" @click="handleBackdropClick"></div>

      <!-- Modal -->
      <div class="relative bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        <div class="p-8">
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

          <!-- Unified Login Form -->
          <div class="space-y-4">
            <!-- Phone - Fixed Position -->
            <div>
              <label class="block text-sm text-slate-600 mb-1">手机号</label>
              <input
                v-model="currentPhone"
                type="tel"
                placeholder="请输入手机号"
                class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
              />
            </div>

            <!-- Password / Code - Dynamic Position -->
            <div v-if="currentTab === 'password'">
              <label class="block text-sm text-slate-600 mb-1">密码</label>
              <input
                v-model="phoneForm.password"
                type="password"
                placeholder="请输入密码"
                class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                @keyup.enter="handlePasswordLogin"
              />
            </div>
            <div v-else>
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
            </div>

            <!-- Hint Text - Dynamic Position (only shown when VITE_SHOW_TEST_HINT=true) -->
            <template v-if="import.meta.env.VITE_SHOW_TEST_HINT === 'true'">
              <p v-if="currentTab === 'password'" class="text-sm text-slate-400">测试环境默认密码为：888888</p>
              <p v-else class="text-sm text-slate-400 mt-1">测试环境验证码固定为：888888</p>
            </template>

            <!-- Login Button - Fixed Position -->
            <button
              @click="currentTab === 'password' ? handlePasswordLogin() : handleCodeLogin()"
              :disabled="isLoading"
              class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
            >
              {{ isLoading ? '登录中...' : '登录' }}
            </button>
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
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
// import { phoneLogin, phoneCodeLogin, sendSmsCode, getWxQrcode, checkWxQrcodeStatus, wxLogin } from '@/api/auth'
import { phoneLogin, phoneCodeLogin, sendSmsCode } from '@/api/auth'
import { useDemoAuth } from '@/composables/useDemoAuth'

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
  success: []
}>()

const { setLogin } = useDemoAuth()

const tabs = [
  { key: 'code', label: '验证码登录' },
  { key: 'password', label: '密码登录' }
]

const currentTab = ref('code')
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

// Unified phone binding
const currentPhone = computed({
  get: () => currentTab.value === 'password' ? phoneForm.value.phone : codeForm.value.phone,
  set: (val) => {
    if (currentTab.value === 'password') {
      phoneForm.value.phone = val
    } else {
      codeForm.value.phone = val
    }
  }
})

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
  const phone = currentPhone.value

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
  if (!currentPhone.value || !phoneForm.value.password) {
    errorMessage.value = '请输入手机号和密码'
    return
  }

  isLoading.value = true
  errorMessage.value = ''

  try {
    const res = await phoneLogin(currentPhone.value, phoneForm.value.password)
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
  if (!currentPhone.value || !codeForm.value.code) {
    errorMessage.value = '请输入手机号和验证码'
    return
  }

  isLoading.value = true
  errorMessage.value = ''

  try {
    const res = await phoneCodeLogin(currentPhone.value, codeForm.value.code)
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

// WeChat Login functions (Hidden temporarily)
// async function loadWxQrcode() {
//   try {
//     const res = await getWxQrcode()
//     wxQrcodeUrl.value = res.qrcode_url
//     wxSceneStr.value = res.scene_str
//     wxStatus.value = 'waiting'
//     startWxPolling()
//   } catch (e) {
//     console.error('Failed to load WeChat QRCode:', e)
//   }
// }

// function startWxPolling() {
//   if (wxPollingInterval) clearInterval(wxPollingInterval)

//   wxPollingInterval = window.setInterval(async () => {
//     if (!wxSceneStr.value) return

//     try {
//       const res = await checkWxQrcodeStatus(wxSceneStr.value)
//       wxStatus.value = res.status

//       if (res.status === 'confirmed' && res.openid) {
//         stopWxPolling()
//         // Auto login with WeChat
//         const loginRes = await wxLogin(res.openid, res.unionid)
//         if (loginRes.success && loginRes.token && loginRes.user) {
//           setLogin(loginRes.token, loginRes.user)
//           emit('success')
//         } else if (loginRes.message === '请绑定手机号以完成登录') {
//           // WeChat user needs to bind phone
//           errorMessage.value = '请绑定手机号以完成登录'
//         }
//       } else if (res.status === 'expired') {
//         stopWxPolling()
//       }
//     } catch (e) {
//       console.error('Failed to check WeChat status:', e)
//     }
//   }, 2000)
// }

// function stopWxPolling() {
//   if (wxPollingInterval) {
//     clearInterval(wxPollingInterval)
//     wxPollingInterval = null
//   }
// }

// function refreshWxQrcode() {
//   loadWxQrcode()
// }

function handleBackdropClick() {
  // Don't close on backdrop click for login modal
}

watch(() => props.visible, (val) => {
  if (val) {
    errorMessage.value = ''
    // loadWxQrcode() // WeChat login temporarily disabled
  } else {
    // stopWxPolling() // WeChat login temporarily disabled
  }
})

onUnmounted(() => {
  // stopWxPolling() // WeChat login temporarily disabled
  if (cooldownTimer) clearInterval(cooldownTimer)
})
</script>

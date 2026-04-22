<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
      <div class="px-8 pt-8 pb-6">
        <h1 class="text-2xl font-bold text-slate-800 text-center mb-1">重置密码</h1>
        <p class="text-sm text-slate-500 text-center mb-8">通过手机号重置登录密码</p>

        <!-- 步骤1：验证手机号和图形验证码 -->
        <div v-if="step === 1" class="space-y-4">
          <div>
            <label class="block text-sm text-slate-600 mb-1">手机号</label>
            <input
              v-model="phone"
              type="tel"
              placeholder="请输入手机号"
              maxlength="11"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
            />
          </div>

          <!-- 图形验证码 -->
          <div>
            <label class="block text-sm text-slate-600 mb-1">图形验证码</label>
            <div class="flex gap-2">
              <input
                v-model="captchaCode"
                type="text"
                placeholder="请输入图形验证码"
                maxlength="4"
                class="flex-1 px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                @keyup.enter="handleSendCode"
              />
              <div
                class="w-24 h-12 bg-slate-200 rounded-lg cursor-pointer flex items-center justify-center select-none overflow-hidden"
                @click="refreshCaptcha"
                title="点击刷新"
              >
                <img v-if="captchaSvg" :src="'data:image/svg+xml;base64,' + captchaSvg" alt="验证码" class="w-full h-full" />
                <span v-else class="text-slate-400 text-sm">加载中</span>
              </div>
            </div>
          </div>

          <button
            @click="handleSendCode"
            :disabled="isSendingCode"
            class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
          >
            {{ isSendingCode ? '发送中...' : '发送短信验证码' }}
          </button>

          <div class="text-center">
            <router-link :to="loginUrl" class="text-sm text-cyan-500 hover:text-cyan-600">
              返回登录
            </router-link>
          </div>
        </div>

        <!-- 步骤2：输入短信验证码和新密码 -->
        <div v-else class="space-y-4">
          <div>
            <label class="block text-sm text-slate-600 mb-1">短信验证码</label>
            <div class="flex gap-2">
              <input
                v-model="smsCode"
                type="text"
                inputmode="numeric"
                pattern="[0-9]*"
                placeholder="请输入短信验证码"
                maxlength="6"
                autocomplete="one-time-code"
                class="flex-1 px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                @keyup.enter="handleResetPassword"
              />
              <button
                @click="resendSmsCode"
                :disabled="cooldown > 0"
                class="px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-cyan-500 hover:bg-slate-200 disabled:text-slate-400 transition-colors whitespace-nowrap"
              >
                {{ cooldown > 0 ? `${cooldown}s` : '重新发送' }}
              </button>
            </div>
          </div>

          <div>
            <label class="block text-sm text-slate-600 mb-1">新密码</label>
            <input
              v-model="newPassword"
              type="password"
              placeholder="请输入新密码"
              autocomplete="new-password"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
              @keyup.enter="handleResetPassword"
            />
          </div>

          <div>
            <label class="block text-sm text-slate-600 mb-1">确认密码</label>
            <input
              v-model="confirmPassword"
              type="password"
              placeholder="请再次输入新密码"
              autocomplete="new-password"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
              @keyup.enter="handleResetPassword"
            />
          </div>

          <!-- 密码规则提示 -->
          <div class="p-3 bg-amber-50 border border-amber-200 rounded-lg">
            <p class="text-sm text-amber-700">{{ passwordMsg }}</p>
          </div>

          <button
            @click="handleResetPassword"
            :disabled="isLoading"
            class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
          >
            {{ isLoading ? '处理中...' : '确认重置' }}
          </button>

          <div class="text-center">
            <a href="#" @click.prevent="step = 1" class="text-sm text-cyan-500 hover:text-cyan-600">
              上一步
            </a>
          </div>
        </div>

        <!-- 成功提示 -->
        <div v-if="step === 'success'" class="text-center py-8">
          <div class="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg class="w-8 h-8 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 class="text-xl font-bold text-slate-800 mb-2">密码重置成功</h2>
          <p class="text-sm text-slate-500 mb-6">密码重置成功，3秒后自动跳转到登录页</p>
          <!-- Toast 提示 -->
          <div class="fixed top-8 left-1/2 -translate-x-1/2 px-6 py-3 bg-green-500 text-white rounded-lg shadow-lg z-50">
            密码重置成功，3秒后自动跳转到登录页
          </div>
        </div>

        <div v-if="errorMessage" class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
          {{ errorMessage }}
        </div>
      </div>

      <div class="px-8 pb-6">
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { getCaptcha, sendResetPasswordCode, resetPassword } from '@/api/auth'

const route = useRoute()
const tenantId = computed(() => route.query.tenant_id as string || '')
const loginUrl = computed(() =>
  tenantId.value ? `/t/${tenantId.value}/login` : '/portal/login'
)

// 默认密码规则提示
const DEFAULT_PASSWORD_MSG = '长度8-50位，必须有字母+数字'

const step = ref<1 | 2 | 'success'>(1)
const phone = ref('')
const captchaCode = ref('')
const captchaId = ref('')
const captchaSvg = ref('')
const smsCode = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordMsg = ref(DEFAULT_PASSWORD_MSG)

const isSendingCode = ref(false)
const isLoading = ref(false)
const errorMessage = ref('')
const cooldown = ref(0)

let cooldownTimer: number | null = null

// 获取图形验证码
async function refreshCaptcha() {
  try {
    const res = await getCaptcha()
    if (res.success) {
      captchaId.value = res.captcha_id || ''
      captchaSvg.value = res.svg_base64 || ''
    }
  } catch (e) {
    console.error('获取图形验证码失败:', e)
  }
}

// 发送短信验证码
async function handleSendCode() {
  if (!phone.value || phone.value.length !== 11) {
    errorMessage.value = '请输入正确的手机号'
    return
  }
  if (!captchaCode.value) {
    errorMessage.value = '请输入图形验证码'
    return
  }

  isSendingCode.value = true
  errorMessage.value = ''

  try {
    const res = await sendResetPasswordCode({
      phone: phone.value,
      captcha_code: captchaCode.value,
      captcha_id: captchaId.value
    })
    if (res.success) {
      step.value = 2
      startCooldown()
    } else {
      errorMessage.value = res.message || '发送失败'
      refreshCaptcha()
      captchaCode.value = ''
    }
  } catch (e: any) {
    errorMessage.value = e.message || '发送失败'
    refreshCaptcha()
    captchaCode.value = ''
  } finally {
    isSendingCode.value = false
  }
}

// 重新发送短信验证码
async function resendSmsCode() {
  if (cooldown.value > 0) return
  captchaCode.value = ''
  await refreshCaptcha()
  step.value = 1
}

// 开始倒计时
function startCooldown() {
  cooldown.value = 60
  cooldownTimer = window.setInterval(() => {
    cooldown.value--
    if (cooldown.value <= 0 && cooldownTimer) {
      clearInterval(cooldownTimer)
    }
  }, 1000)
}

// 重置密码
async function handleResetPassword() {
  if (!smsCode.value) {
    errorMessage.value = '请输入短信验证码'
    return
  }
  if (!newPassword.value) {
    errorMessage.value = '请输入新密码'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    errorMessage.value = '两次输入的密码不一致'
    return
  }

  isLoading.value = true
  errorMessage.value = ''

  try {
    const res = await resetPassword({
      phone: phone.value,
      captcha_code: captchaCode.value,
      captcha_id: captchaId.value,
      sms_code: smsCode.value,
      new_password: newPassword.value
    })
    if (res.success) {
      step.value = 'success'
      // 3秒后自动跳转到登录页
      setTimeout(() => {
        window.location.href = loginUrl.value
      }, 3000)
    } else {
      errorMessage.value = res.message || '重置失败'
    }
  } catch (e: any) {
    errorMessage.value = e.message || '重置失败'
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  refreshCaptcha()
})

onUnmounted(() => {
  if (cooldownTimer) clearInterval(cooldownTimer)
})
</script>
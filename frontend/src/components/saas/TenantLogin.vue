<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
      <div class="px-8 pt-8 pb-6">
        <h1 class="text-2xl font-bold text-slate-800 text-center mb-1">企业管理平台</h1>
        <p class="text-sm text-slate-500 text-center mb-8">管理员登录</p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm text-slate-600 mb-1">手机号</label>
            <input
              v-model="phone"
              type="tel"
              placeholder="请输入管理员手机号"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
            />
          </div>

          <div>
            <label class="block text-sm text-slate-600 mb-1">验证码</label>
            <div class="flex gap-2">
              <input
                v-model="code"
                type="text"
                placeholder="请输入验证码"
                maxlength="6"
                class="flex-1 px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                @keyup.enter="handleLogin"
              />
              <button
                @click="handleSendCode"
                :disabled="cooldown > 0 || isSendingCode"
                class="px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-cyan-500 hover:bg-slate-200 disabled:text-slate-400 transition-colors whitespace-nowrap"
              >
                {{ cooldown > 0 ? `${cooldown}s` : (isSendingCode ? '发送中...' : '获取验证码') }}
              </button>
            </div>
          </div>

          <button
            @click="handleLogin"
            :disabled="isLoading"
            class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
          >
            {{ isLoading ? '登录中...' : '登录' }}
          </button>
        </div>

        <div v-if="errorMessage" class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
          {{ errorMessage }}
        </div>
      </div>

      <div class="px-8 pb-6">
        <p class="text-xs text-slate-400 text-center">测试环境验证码固定为：888888</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { sendAdminSmsCode, adminLogin } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const { setLogin } = useTenantAuth()

const phone = ref('')
const code = ref('')
const isLoading = ref(false)
const isSendingCode = ref(false)
const errorMessage = ref('')
const cooldown = ref(0)

let cooldownTimer: number | null = null

function startCooldown() {
  cooldown.value = 60
  cooldownTimer = window.setInterval(() => {
    cooldown.value--
    if (cooldown.value <= 0 && cooldownTimer) {
      clearInterval(cooldownTimer)
    }
  }, 1000)
}

async function handleSendCode() {
  if (!phone.value || phone.value.length !== 11) {
    errorMessage.value = '请输入正确的手机号'
    return
  }

  isSendingCode.value = true
  errorMessage.value = ''

  try {
    const res = await sendAdminSmsCode(phone.value)
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

async function handleLogin() {
  if (!phone.value || !code.value) {
    errorMessage.value = '请输入手机号和验证码'
    return
  }

  isLoading.value = true
  errorMessage.value = ''

  try {
    const res = await adminLogin(phone.value, code.value)
    if (res.success && res.token && res.admin && res.tenant) {
      setLogin(res.token, res.admin, res.tenant)
      router.push('/portal')
    } else {
      errorMessage.value = res.message || '登录失败'
    }
  } catch (e: any) {
    errorMessage.value = e.message || '登录失败'
  } finally {
    isLoading.value = false
  }
}

onUnmounted(() => {
  if (cooldownTimer) clearInterval(cooldownTimer)
})
</script>

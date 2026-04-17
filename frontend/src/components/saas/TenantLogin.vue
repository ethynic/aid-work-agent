<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
      <div class="px-8 pt-8 pb-6">
        <h1 class="text-2xl font-bold text-slate-800 text-center mb-1">企业管理平台</h1>
        <p class="text-sm text-slate-500 text-center mb-8">管理员登录</p>

        <div class="space-y-4">
          <!-- 账号类型切换 -->
          <div class="flex gap-2 mb-4">
            <button
              @click="loginType = 'phone'"
              :class="[
                'flex-1 py-2 rounded-lg text-sm font-medium transition-colors',
                loginType === 'phone'
                  ? 'bg-cyan-500 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              ]"
            >
              手机号登录
            </button>
            <button
              @click="loginType = 'username'"
              :class="[
                'flex-1 py-2 rounded-lg text-sm font-medium transition-colors',
                loginType === 'username'
                  ? 'bg-cyan-500 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              ]"
            >
              用户名登录
            </button>
          </div>

          <!-- 手机号/用户名输入 -->
          <div>
            <label class="block text-sm text-slate-600 mb-1">{{ loginType === 'phone' ? '手机号' : '用户名' }}</label>
            <input
              v-model="identifier"
              :type="loginType === 'phone' ? 'tel' : 'text'"
              :placeholder="loginType === 'phone' ? '请输入手机号' : '请输入用户名'"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
            />
          </div>

          <!-- 密码输入 -->
          <div>
            <label class="block text-sm text-slate-600 mb-1">密码</label>
            <input
              v-model="password"
              type="password"
              placeholder="请输入密码"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
              @keyup.enter="handleLogin"
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
                @keyup.enter="handleLogin"
              />
              <div
                class="w-24 h-12 bg-slate-200 rounded-lg cursor-pointer flex items-center justify-center text-lg font-bold tracking-wider text-slate-700 select-none"
                @click="refreshCaptcha"
                title="点击刷新"
              >
                {{ captchaDisplay }}
              </div>
            </div>
          </div>

          <button
            @click="handleLogin"
            :disabled="isLoading"
            class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
          >
            {{ isLoading ? '登录中...' : '登录' }}
          </button>

          <!-- 忘记密码链接 -->
          <div class="text-center">
            <router-link to="/portal/reset-password" class="text-sm text-cyan-500 hover:text-cyan-600">
              忘记密码？
            </router-link>
          </div>
        </div>

        <div v-if="errorMessage" class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
          {{ errorMessage }}
        </div>
      </div>

      <div class="px-8 pb-6">
        <p class="text-xs text-slate-400 text-center">测试环境图形验证码固定为：8888（开发模式显示）</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { login, getCaptcha } from '@/api/auth'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const { setLogin } = useTenantAuth()

const loginType = ref<'phone' | 'username'>('phone')
const identifier = ref('')
const password = ref('')
const captchaCode = ref('')
const captchaId = ref('')
const captchaDisplay = ref('????')
const isLoading = ref(false)
const errorMessage = ref('')

// 获取图形验证码
async function refreshCaptcha() {
  try {
    const res = await getCaptcha()
    if (res.success) {
      captchaId.value = res.captcha_id || ''
      // 开发环境显示验证码，生产环境显示????
      if (res.code) {
        captchaDisplay.value = res.code
      } else {
        captchaDisplay.value = '????'
      }
    }
  } catch (e) {
    console.error('获取图形验证码失败:', e)
  }
}

async function handleLogin() {
  if (!identifier.value) {
    errorMessage.value = loginType.value === 'phone' ? '请输入手机号' : '请输入用户名'
    return
  }
  if (!password.value) {
    errorMessage.value = '请输入密码'
    return
  }
  if (!captchaCode.value) {
    errorMessage.value = '请输入图形验证码'
    return
  }

  isLoading.value = true
  errorMessage.value = ''

  try {
    const res = await login({
      identifier: identifier.value,
      password: password.value,
      captcha_code: captchaCode.value,
      captcha_id: captchaId.value
    })
    if (res.success && res.token && res.user) {
      setLogin(res.token, res.user, null)
      router.push('/portal')
    } else {
      errorMessage.value = res.message || '登录失败'
      // 登录失败后刷新验证码
      refreshCaptcha()
      captchaCode.value = ''
    }
  } catch (e: any) {
    errorMessage.value = e.message || '登录失败'
    refreshCaptcha()
    captchaCode.value = ''
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  refreshCaptcha()
})
</script>
<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
      <div class="px-4 md:px-8 pt-6 pb-6">
        <h1 class="text-xl font-bold text-slate-800 text-center mt-4 mb-1">统一登录</h1>

        <!-- 租户状态提示 -->
        <div v-if="tenantStatusMessage" class="text-red-500 text-sm text-center font-medium mb-6 py-2 px-3 bg-red-50 rounded border border-red-200">
          {{ tenantStatusMessage }}
        </div>
        <!-- 租户过期提示 -->
        <div v-if="tenantExpiredMessage" class="text-red-500 text-sm text-center font-medium mb-6 py-2 px-3 bg-red-50 rounded border border-red-200">
          {{ tenantExpiredMessage }}
        </div>
        <!-- 租户即将过期提示 -->
        <div v-else-if="tenantExpiringMessage" class="text-orange-500 text-sm text-center font-medium mb-6 py-2 px-3 bg-orange-50 rounded border border-orange-200">
          {{ tenantExpiringMessage }}
        </div>

        <div class="space-y-3">
          <!-- 租户代码输入 -->
          <div>
            <label class="block text-sm text-slate-600 mb-1">租户代码</label>
            <input
              v-model="tenantCode"
              type="text"
              placeholder="例如：ALIBB"
              maxlength="8"
              autocomplete="off"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
              @input="clearError('tenant_code')"
            />
            <div v-if="fieldErrors.tenant_code" class="text-red-500 text-sm mt-1">
              {{ fieldErrors.tenant_code }}
            </div>
          </div>

          <!-- 手机号或用户名输入 -->
          <div>
            <label class="block text-sm text-slate-600 mb-1">手机号或用户名</label>
            <input
              v-model="identifier"
              type="text"
              placeholder="请输入手机号或用户名"
              autocomplete="username"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
              @input="clearError('identifier')"
            />
            <div v-if="fieldErrors.identifier" class="text-red-500 text-sm mt-1">
              {{ fieldErrors.identifier }}
            </div>
          </div>

          <!-- 密码输入 -->
          <div>
            <label class="block text-sm text-slate-600 mb-1">密码</label>
            <input
              v-model="password"
              type="password"
              placeholder="请输入密码"
              autocomplete="current-password"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
              @keyup.enter="handleLogin"
              @input="clearError('password')"
            />
            <div v-if="fieldErrors.password" class="text-red-500 text-sm mt-1">
              {{ fieldErrors.password }}
            </div>
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
                autocomplete="off"
                class="flex-1 px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
                @keyup.enter="handleLogin"
                @input="clearError('captcha_code')"
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
            <div v-if="fieldErrors.captcha_code" class="text-red-500 text-sm mt-1">
              {{ fieldErrors.captcha_code }}
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
            <a href="#" class="text-sm text-cyan-500 hover:text-cyan-600" @click.prevent="showForgetPassword">
              忘记密码？
            </a>
          </div>
        </div>

        <div v-if="errorMessage" class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
          {{ errorMessage }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { getCaptcha } from '@/api/auth'
import { unifiedLogin } from '@/api/auth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAgent } from '@/composables/useAgent'
import { useSession } from '@/composables/useSession'

const { setLogin } = useTenantAuth()
const toast = useToast()

// 表单数据
const tenantCode = ref('')
const identifier = ref('')
const password = ref('')
const captchaCode = ref('')
const captchaId = ref('')
const captchaSvg = ref('')
const isLoading = ref(false)
const errorMessage = ref('')
const tenantExpiredMessage = ref('')
const tenantExpiringMessage = ref('')
const tenantStatusMessage = ref('')

// 字段级错误
const fieldErrors = ref<Record<string, string>>({
  tenant_code: '',
  identifier: '',
  password: '',
  captcha_code: ''
})

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

// 清除字段错误
function clearError(field: string) {
  if (fieldErrors.value[field]) {
    fieldErrors.value[field] = ''
  }
  errorMessage.value = ''
}

// 显示忘记密码提示
function showForgetPassword() {
  toast.info('请通过租户管理员重置密码', {
    timeout: 5000
  })
}

async function handleLogin() {
  // 重置错误
  errorMessage.value = ''
  fieldErrors.value = {
    tenant_code: '',
    identifier: '',
    password: '',
    captcha_code: ''
  }

  // 基本验证
  if (!tenantCode.value.trim()) {
    fieldErrors.value.tenant_code = '请输入租户代码'
    return
  }

  if (!identifier.value) {
    fieldErrors.value.identifier = '请输入手机号或用户名'
    return
  }

  if (!password.value) {
    fieldErrors.value.password = '请输入密码'
    return
  }

  if (!captchaCode.value) {
    fieldErrors.value.captcha_code = '请输入图形验证码'
    return
  }

  isLoading.value = true

  try {
    const res = await unifiedLogin({
      tenant_code: tenantCode.value.trim().toUpperCase(),
      identifier: identifier.value,
      password: password.value,
      captcha_code: captchaCode.value,
      captcha_id: captchaId.value
    })

    if (res.success && res.token && res.user && res.redirect_url) {
      // 保存租户代码到 localStorage
      localStorage.setItem('last_tenant_code', tenantCode.value.trim().toUpperCase())

      // 设置登录状态
      const tenantInfo = res.tenant_id ? { tenant_id: res.tenant_id, company_name: '', plan: 'free', status: 'active' } : null
      const adminInfo = {
        user_id: res.user.user_id,
        username: res.user.username,
        phone: res.user.phone || '',
        role: (res.user as any).role || 'user'
      }
      if (tenantInfo) {
        setLogin(res.token, adminInfo, tenantInfo)
      } else {
        setLogin(res.token, adminInfo, { tenant_id: '', company_name: '', plan: 'free', status: 'active' })
      }

      // 登录成功后先清空所有缓存（避免同账号多设备时显示旧数据）
      const { clearSessionCache: clearAgentSessionCache, clearSession: clearAgentSession, clearAttachments } = useAgent()
      const { clearSessionCache: clearSessionListCache } = useSession()
      clearAgentSessionCache()
      clearAgentSession()
      clearAttachments()
      clearSessionListCache()

      // 跳转
      window.location.href = res.redirect_url
    } else if (res.errors && res.errors.length > 0) {
      // 处理字段级错误
      res.errors.forEach((error: any) => {
        if (error.field && fieldErrors.value[error.field] !== undefined) {
          fieldErrors.value[error.field] = error.message
        } else {
          errorMessage.value = error.message || '登录失败'
        }
      })
      // 登录失败时自动刷新验证码
      refreshCaptcha()
      captchaCode.value = ''
    } else {
      errorMessage.value = res.message || '登录失败'
      refreshCaptcha()
      captchaCode.value = ''
    }
  } catch (e: any) {
    errorMessage.value = e.message || '网络错误，请稍后重试'
    refreshCaptcha()
    captchaCode.value = ''
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  // 加载上次使用的租户代码
  const lastTenantCode = localStorage.getItem('last_tenant_code')
  if (lastTenantCode) {
    tenantCode.value = lastTenantCode
  }

  // 获取验证码
  refreshCaptcha()
})
</script>
<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
      <div class="px-8 pt-8 pb-6">
        <h1 class="text-2xl font-bold text-slate-800 text-center mb-1">{{ pageTitle.title }}</h1>
        <p class="text-sm text-slate-500 text-center mb-2">{{ pageTitle.subtitle }}</p>

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
              :autocomplete="loginType === 'phone' ? 'tel' : 'username'"
              class="w-full px-4 py-3 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 placeholder-slate-400 focus:outline-none focus:border-cyan-400"
            />
          </div>

          <!-- 密码输入 -->
          <div>
            <label class="block text-sm text-slate-600 mb-1">密码{{ !isPortalRoute ? '（首次登录，没有密码，请点击下方"忘记密码"）' : '' }}</label>
            <input
              v-model="password"
              type="password"
              placeholder="请输入密码"
              autocomplete="current-password"
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
            @click="handleLogin"
            :disabled="isLoading"
            class="w-full py-3 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg font-medium transition-colors"
          >
            {{ isLoading ? '登录中...' : '登录' }}
          </button>

          <!-- 忘记密码链接 - 仅租户前台显示 -->
          <div v-if="!isPortalRoute" class="text-center">
            <router-link :to="resetPasswordUrl" class="text-sm text-cyan-500 hover:text-cyan-600">
              忘记密码？
            </router-link>
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
import { ref, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import { getCaptcha } from '@/api/auth'
import { adminPasswordLogin, getTenantPublicInfo } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()
const { setLogin } = useTenantAuth()
const toast = useToast()

// 从路由参数获取 tenant_id
const tenantId = computed(() => route.params.tenant_id as string)
const isPortalRoute = computed(() => route.path.includes('/portal'))
const resetPasswordUrl = computed(() =>
  tenantId.value ? `/portal/reset-password?tenant_id=${tenantId.value}` : '/portal/reset-password'
)

const tenantName = ref('')

// 登录页标题动态显示
const pageTitle = computed(() => {
  if (isPortalRoute.value) {
    return { title: '爱定义管理后台', subtitle: '平台管理员登录' }
  }
  return { title: tenantName.value || '用户登录', subtitle: '请输入账号信息' }
})

const loginType = ref<'phone' | 'username'>('phone')
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
    const res = await adminPasswordLogin({
      identifier: identifier.value,
      password: password.value,
      captcha_code: captchaCode.value,
      captcha_id: captchaId.value,
      tenant_id: tenantId.value,
      // 平台管理后台登录：要求必须是平台管理员
      ...(isPortalRoute.value ? { required_role: 'platform_admin' } : {})
    })
    if (res.success && res.token && res.user) {
      // 平台管理员的 tenant 可能为 null
      const tenantInfo = res.tenant ? { ...res.tenant, status: res.tenant.status } : null
      if (tenantInfo) {
        setLogin(res.token, res.user, tenantInfo)
      } else {
        setLogin(res.token, res.user, { tenant_id: '', company_name: '', plan: 'free', status: 'active' })
      }
      // 显示到期警告
      if (res.expire_warning) {
        toast.warning(res.expire_warning, {
          timeout: 10000,
          closeOnClick: true,
          pauseOnHover: true,
        })
      }
      // 根据当前路由决定跳转
      if (isPortalRoute.value) {
        router.push('/portal')
      } else {
        router.push(`/t/${tenantId.value}`)
      }
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
  // /t/:tenant_id/login 路径下必须有 tenant_id
  if (!isPortalRoute.value && !tenantId.value) {
    errorMessage.value = 'URL 缺少租户ID'
    return
  }
  refreshCaptcha()

  // 获取租户名称用于标题显示，并检查租户过期状态
  if (tenantId.value) {
    getTenantPublicInfo(tenantId.value).then(res => {
      if (res.success && res.tenant) {
        tenantName.value = res.tenant.company_name
      }
      // 显示租户状态提示
      if (res.tenant?.status && res.tenant.status !== 'active') {
        const statusMap: Record<string, string> = {
          'suspended': '停用',
          'deactivated': '已删除'
        }
        const statusDisplay = res.tenant.status_display || statusMap[res.tenant.status] || '停用'
        tenantStatusMessage.value = `该租户已${statusDisplay}，请联系平台管理员`
      }
      // 显示租户过期提示（不影响用户输入账号密码，登录时后端仍会验证）
      if (res.expire_info?.is_expired && res.expire_info.expire_date) {
        tenantExpiredMessage.value = `该租户已过期（到期日期：${res.expire_info.expire_date}），请联系平台管理员续费`
      } else if (res.expire_info?.show_warning && res.expire_info.expire_date) {
        tenantExpiringMessage.value = `您的租户即将过期（到期日期：${res.expire_info.expire_date}），请联系平台管理员续费`
      }
    }).catch(() => {
      // 获取失败不影响登录，标题保持默认
    })
  }
})
</script>
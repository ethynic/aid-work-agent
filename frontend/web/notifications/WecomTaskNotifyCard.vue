<template>
  <!-- 企微通知渠道行（方案 B，2026-11-14）：无卡片外壳，嵌入父级「通知渠道」卡片；
       手机号识别命中自动绑定，未命中自动弹 OAuth 绑定弹窗 -->
  <div>
    <div class="flex items-center gap-3 py-3.5">
      <div class="w-9 h-9 rounded-[10px] bg-success-50 text-success-700 flex items-center justify-center flex-none">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M8 10h8M8 14h5M21 12a9 9 0 11-18 0 9 9 0 0118 0zM7 17l-4 3 1-4" /></svg>
      </div>
      <div class="flex-1 min-w-0">
        <div class="text-sm font-semibold text-default">企微通知 <BaseBadge v-if="!loading && status?.app_available" :intent="notifyOn ? 'success' : 'neutral'">{{ notifyOn ? '已开启' : bound ? '已绑定' : '未绑定' }}</BaseBadge></div>
        <div class="text-xs text-gray-500 mt-0.5">{{ wecomRowDesc }}</div>
      </div>
      <template v-if="!loading && status?.app_available">
        <button v-if="!bound" class="text-sm text-primary-600 hover:underline cursor-pointer flex-none" :disabled="busy" @click="startBind">绑定企微</button>
        <button v-else class="text-sm text-primary-600 hover:underline cursor-pointer flex-none" :disabled="busy" @click="unbind">解绑</button>
        <BaseSwitch :model-value="notifyOn" :disabled="busy || !bound || !targetActive" aria-label="企微通知开关" @update:model-value="toggleWecom" />
      </template>
    </div>
    <p v-if="!loading && status && !status.app_available" class="text-xs text-gray-500 -mt-1 pb-1">当前租户未配置企业微信通知渠道，需要租户管理员在「连接中心 → 渠道配置」添加企业微信应用通知。</p>
    <p v-else-if="!loading && bound && !targetActive" class="text-xs text-warning-700 -mt-1 pb-1">通知目标未就绪（可能账号已变动或已被撤销），请解绑后重新绑定。</p>
    <p v-if="error" role="alert" class="text-sm text-danger-600 pb-1">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-success-700 pb-1">{{ notice }}</p>
  </div>
  <BaseModal v-model="bindOpen" title="绑定企业微信" size="sm" :show-fullscreen="false">
    <div class="space-y-3 text-sm">
      <p v-if="bindInfo && !inWeCom" class="text-muted">将在新窗口打开企业微信授权页，在企微中扫码确认后本页将自动完成绑定。</p>
      <p v-else-if="bindInfo" class="text-muted">将跳转到企业微信授权页，授权完成后自动返回。</p>
      <p v-if="bindError" role="alert" class="text-danger-600">{{ bindError }}</p>
    </div>
    <template #footer>
      <div class="flex items-center gap-3">
        <BaseButton v-if="inWeCom && bindInfo" intent="primary" @click="openOauth">在企业微信中授权</BaseButton>
        <BaseButton v-else-if="bindInfo" intent="primary" @click="openQrAuth">打开企微授权页</BaseButton>
        <BaseButton intent="secondary" :disabled="busy" @click="refreshStatus">刷新绑定状态</BaseButton>
        <BaseButton intent="ghost" :disabled="busy" @click="closeBind">关闭</BaseButton>
      </div>
    </template>
  </BaseModal>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import { getTenantScopedKey } from '@/api/tenantStorage'
import { notificationsAPI, type WecomBindStatus, type WecomBindUrl, type WecomBindSuggest } from '@/api/notifications'

// 企微任务通知卡片（wecom-app-task-notification-design §4.5）：复用浏览器通知
// 卡片骨架——未绑定走绑定流程（PC 新窗口打开企微扫码授权页 / 手机号预填确认，
// 企微内嵌环境直接跳授权链接），已绑定提供开关与解绑。
// 开关只控制 wecom_app 偏好行；target 由服务端绑定落库时登记（设计 §4.3）。

// OAuth 回调落地页(/notification/wecom-bind)与发起页路径不同，token key 需在发起时
// 存入 localStorage 供落地页取用（仅存 key 名，不存凭证本身）；PC 流程授权页在
// 新窗口打开并重定向回落地页，sessionStorage 按标签页隔离，新窗口读不到。
// 复审 2：上下文按 state 分键——两个标签页各拿不同 state 互不覆盖，回调页只
// 消费自己 state 的键
const bindContextKey = (state: string) => `wecom-bind-context:${state}`
// 企业微信内置浏览器 UA 标识：内嵌环境直接跳授权链接，不展示二维码
const WECOM_UA_PATTERN = /wxwork|wecom/i
const POLL_INTERVAL_MS = 3000
const POLL_MAX_TICKS = 100 // ≈state TTL 5 分钟，超时后停止轮询由用户手动刷新
// 绑定落库成功但通知 target 登记失败（评审回炉 P2-4）：显式警示，不再乐观提示成功
const REGISTER_FAILED_TEXT = '绑定成功，但通知目标登记失败，请稍后在通知设置页重试解绑后重新绑定。'

// 绑定/偏好操作错误码 → 用户提示（其余错误展示通用文案，不暴露原始错误码）
const ERROR_TEXT: Record<string, string> = {
  NOTIFICATION_WECOM_APP_UNAVAILABLE: '当前租户未配置企业微信通知渠道，需要租户管理员在「连接中心 → 渠道配置」添加企业微信应用通知。',
  NOTIFICATION_PROVIDER_UNAVAILABLE: '当前租户未配置企业微信通知渠道，需要租户管理员在「连接中心 → 渠道配置」添加企业微信应用通知。',
  NOTIFICATION_BIND_STATE_INVALID: '绑定链接已过期或无效，请重新发起绑定。',
  NOTIFICATION_BIND_FAILED: '企微授权失败，请重试。',
  NOTIFICATION_BIND_NOT_IN_SCOPE: '当前企微账号不在通知应用可见范围内，请联系管理员。',
  NOTIFICATION_BIND_TAKEN: '该企微账号已被其他账号绑定。',
  NOTIFICATION_BIND_MISMATCH: '企微账号与当前登录用户不匹配。',
  NOTIFICATION_BIND_PHONE_REQUIRED: '当前账号未登记手机号，无法识别企微账号，请通过扫码绑定。',
}

function describe(error: unknown, fallback: string): string {
  const code = (error as { code?: string } | null)?.code
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code]
  const message = error instanceof Error ? error.message : ''
  // request() 失败时 message 即错误码（如 NOTIFICATION_*），不直接暴露给用户
  return message && !message.startsWith('NOTIFICATION_') && !message.startsWith('HTTP_') ? message : fallback
}

const route = useRoute()
const loading = ref(true), busy = ref(false), error = ref(''), notice = ref('')
const status = ref<WecomBindStatus | null>(null)
const suggest = ref<WecomBindSuggest | null>(null)
const bindOpen = ref(false), bindError = ref('')
const bindInfo = ref<WecomBindUrl | null>(null)
const bindSuggestDone = ref(false)

const bound = computed(() => !!status.value?.bound)
const targetActive = computed(() => status.value?.target_state === 'active')
const notifyOn = computed(() => bound.value && targetActive.value && preferenceEnabled.value)
const preferenceEnabled = ref(false)
const inWeCom = computed(() => WECOM_UA_PATTERN.test(navigator.userAgent))
// 行说明（方案 B）：绑定状态融入说明文字；未配置租户时由行下方提示条说明
const wecomRowDesc = computed(() => {
  if (!status.value?.app_available) return '任务完成或失败时通过企业微信应用消息提醒'
  if (bound.value) return `任务完成或失败时通过企业微信应用消息提醒 · 已绑定 ${status.value?.name || ''}`
  return suggest.value?.matched ? '识别到企微账号，正在自动绑定…' : '绑定企微账号后，任务完成或失败时通过企微应用消息提醒'
})
const emit = defineEmits<{ availability: [available: boolean] }>()

async function refresh() {
  loading.value = true; error.value = ''
  try {
    status.value = await notificationsAPI.wecomBindStatus()
    emit('availability', status.value.app_available === true)
    const prefs = await notificationsAPI.preferences()
    preferenceEnabled.value = prefs.preferences.some(p => p.category === 'task' && p.channel === 'wecom_app' && p.enabled)
    // 未绑定时预取手机号预填（只读）；失败静默，不阻塞卡片展示。
    // 手机号命中即自动完成绑定（服务端二次核实仍在 confirm 内）；未命中回落 OAuth。
    if (!bound.value && !bindSuggestDone.value) {
      bindSuggestDone.value = true
      try {
        suggest.value = await notificationsAPI.wecomBindSuggest()
        if (suggest.value?.matched && suggest.value?.userid) {
          try {
            const result = await notificationsAPI.wecomBindConfirm(suggest.value.userid)
            await refresh()
            if (result.target_registered === false) error.value = REGISTER_FAILED_TEXT
            else notice.value = `已通过手机号自动绑定企微账号「${result.name || result.userid}」。`
            return
          } catch (e) { error.value = describe(e, '自动绑定失败，请尝试扫码绑定。') }
        }
        // 手机号未命中（明确 matched:false）→ 自动弹出 OAuth 绑定弹窗
        //（用户口径 2026-11-14）；suggest 接口本身失败时不自动弹，保留手动入口
        if (suggest.value?.matched === false && status.value?.app_available) void startBind()
      } catch { suggest.value = null }
    }
  } catch (e) { error.value = describe(e, '读取企微通知状态失败。') }
  finally { loading.value = false }
}

function rememberBindContext(state: string) {
  // OAuth 回调落地页用发起页的 token 调 confirm（state 绑定发起用户+租户）；
  // tenant 取发起页路由的租户段（/t/:tenant_id/...；portal 全局发起为空），
  // 回调页据此带 X-Tenant-Id（复审 3：平台管理员 subject 租户由该头决定）
  localStorage.setItem(bindContextKey(state), JSON.stringify({
    token_key: getTenantScopedKey('saas_token'),
    return_path: route.fullPath,
    tenant: typeof route.params.tenant_id === 'string' ? route.params.tenant_id : '',
  }))
}

async function startBind() {
  busy.value = true; error.value = ''; bindError.value = ''
  try {
    const info = await notificationsAPI.wecomBindUrl()
    bindInfo.value = info
    rememberBindContext(info.state)
    bindOpen.value = true
    startPolling()
  } catch (e) { error.value = describe(e, '获取绑定链接失败，请稍后重试。') }
  finally { busy.value = false }
}

function openOauth() {
  if (!bindInfo.value) return
  rememberBindContext(bindInfo.value.state)
  window.location.href = bindInfo.value.oauth_url
}

function openQrAuth() {
  // PC 扫码绑定（评审回炉 P1-3）：新窗口打开企微扫码授权页，用户手机扫码
  // 确认后该窗口带 code+state 重定向回落地页（同源 localStorage 有登录态）
  // 完成绑定；本页轮询 status 收起弹窗
  if (!bindInfo.value) return
  rememberBindContext(bindInfo.value.state)
  window.open(bindInfo.value.qr_url, '_blank', 'noopener')
}

// ---------- 扫码等待：轮询绑定状态，授权完成自动收起弹窗 ----------
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollTicks = 0

function startPolling() {
  stopPolling()
  pollTicks = 0
  pollTimer = setInterval(async () => {
    pollTicks += 1
    if (pollTicks > POLL_MAX_TICKS) { stopPolling(); return }
    try {
      const current = await notificationsAPI.wecomBindStatus()
      if (current.bound) {
        stopPolling()
        bindOpen.value = false
        await refresh()
        notice.value = targetActive.value ? '企微绑定成功。' : REGISTER_FAILED_TEXT
      }
    } catch { /* 轮询失败静默，等待下一轮或手动刷新 */ }
  }, POLL_INTERVAL_MS)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function refreshStatus() {
  return refresh().then(() => {
    if (bound.value) {
      bindOpen.value = false
      notice.value = targetActive.value ? '企微绑定成功。' : REGISTER_FAILED_TEXT
    }
    else bindError.value = ''
  })
}

function closeBind() { bindOpen.value = false; stopPolling() }


// 开关切换单一入口：开=启用偏好；关=停用偏好
function toggleWecom(on: boolean) {
  if (on === notifyOn.value) return
  return on ? enable() : disable()
}

async function enable() {
  busy.value = true; error.value = ''
  try {
    await notificationsAPI.wecomSetPreference(true)
    preferenceEnabled.value = true
    notice.value = '企微任务通知已开启。'
  } catch (e) { error.value = describe(e, '开启失败，请重试。') }
  finally { busy.value = false }
}

async function disable() {
  busy.value = true; error.value = ''
  try {
    await notificationsAPI.wecomSetPreference(false)
    preferenceEnabled.value = false
    notice.value = '企微任务通知已关闭。'
  } catch (e) { error.value = describe(e, '关闭失败，请重试。') }
  finally { busy.value = false }
}

async function unbind() {
  busy.value = true; error.value = ''
  try {
    await notificationsAPI.wecomBindUnbind()
    bindSuggestDone.value = false // 解绑后重新预填
    notice.value = '已解绑企微账号。'
    await refresh()
  } catch (e) { error.value = describe(e, '解绑失败，请重试。') }
  finally { busy.value = false }
}

onMounted(() => { void refresh() })
onUnmounted(() => { stopPolling() })
</script>

<template>
  <BaseCard title="企微任务通知">
    <p class="text-sm text-muted mb-4">开启后，本账号新接单任务完成或失败时会通过企业微信应用消息提醒，点击通知可回到原会话。消息仅包含会话名称和任务状态，不含任务内容。</p>
    <p v-if="loading" role="status" class="text-muted">正在检查企微通知状态…</p>
    <template v-else>
      <p v-if="!status?.app_available" class="text-sm text-muted">服务端暂未开放企微任务通知。</p>
      <template v-else>
        <BaseBadge :intent="notifyOn ? 'success' : 'neutral'">{{ notifyOn ? '企微通知已开启' : bound ? '已绑定，通知未开启' : '未绑定' }}</BaseBadge>
        <p class="text-sm text-muted mt-3">{{ stateHint }}</p>
        <!-- 未绑定：扫码授权或手机号预填确认绑定 -->
        <div v-if="!bound" class="mt-4 space-y-2">
          <p v-if="suggest?.matched" class="text-sm text-muted">识别到企微账号：{{ suggest.name }}（{{ suggest.userid }}），确认后即绑定该账号。</p>
          <div class="flex flex-wrap gap-3">
            <BaseButton :disabled="busy" @click="startBind">{{ busy ? '处理中…' : '绑定企微' }}</BaseButton>
            <BaseButton v-if="suggest?.matched && suggest.userid" intent="secondary" :disabled="busy" @click="confirmBind">确认绑定「{{ suggest.name }}」</BaseButton>
          </div>
        </div>
        <!-- 已绑定：开关 + 解绑 -->
        <div v-else class="flex flex-wrap gap-3 mt-5">
          <BaseButton :disabled="busy || notifyOn || !targetActive" @click="enable">{{ busy ? '处理中…' : '开启企微通知' }}</BaseButton>
          <BaseButton intent="secondary" :disabled="busy || !notifyOn" @click="disable">关闭企微通知</BaseButton>
          <BaseButton intent="ghost" :disabled="busy" @click="unbind">解绑</BaseButton>
        </div>
      </template>
      <p v-if="error" role="alert" class="text-sm text-danger-600 mt-4">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm text-success-700 mt-4">{{ notice }}</p>
    </template>
  </BaseCard>
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
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { getTenantScopedKey } from '@/api/tenantStorage'
import { notificationsAPI, type WecomBindStatus, type WecomBindUrl, type WecomBindSuggest } from '@/api/notifications'

// 企微任务通知卡片（wecom-app-task-notification-design §4.5）：复用浏览器通知
// 卡片骨架——未绑定走绑定流程（PC 新窗口打开企微扫码授权页 / 手机号预填确认，
// 企微内嵌环境直接跳授权链接），已绑定提供开关与解绑。
// 开关只控制 wecom_app 偏好行；target 由服务端绑定落库时登记（设计 §4.3）。

// OAuth 回调落地页(/notification/wecom-bind)与发起页路径不同，token key 需在发起时
// 存入 localStorage 供落地页取用（仅存 key 名，不存凭证本身）；PC 流程授权页在
// 新窗口打开并重定向回落地页，sessionStorage 按标签页隔离，新窗口读不到
const BIND_CONTEXT_KEY = 'wecom-bind-context'
// 企业微信内置浏览器 UA 标识：内嵌环境直接跳授权链接，不展示二维码
const WECOM_UA_PATTERN = /wxwork|wecom/i
const POLL_INTERVAL_MS = 3000
const POLL_MAX_TICKS = 100 // ≈state TTL 5 分钟，超时后停止轮询由用户手动刷新
// 绑定落库成功但通知 target 登记失败（评审回炉 P2-4）：显式警示，不再乐观提示成功
const REGISTER_FAILED_TEXT = '绑定成功，但通知目标登记失败，请稍后在通知设置页重试解绑后重新绑定。'

// 绑定/偏好操作错误码 → 用户提示（其余错误展示通用文案，不暴露原始错误码）
const ERROR_TEXT: Record<string, string> = {
  NOTIFICATION_WECOM_APP_UNAVAILABLE: '服务端暂未开放企微任务通知。',
  NOTIFICATION_PROVIDER_UNAVAILABLE: '服务端暂未开放企微任务通知。',
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
const stateHint = computed(() => {
  if (!bound.value) return suggest.value?.matched ? '已识别企微账号，确认绑定即可开启。' : '绑定后即可在企微中接收任务完成提醒。'
  if (!targetActive.value) return '通知目标未就绪（可能账号已变动或已被撤销），请解绑后重新绑定。'
  return notifyOn.value ? '任务完成后将通过企业微信应用消息提醒。' : '开启后新接单任务完成或失败时会提醒。'
})

async function refresh() {
  loading.value = true; error.value = ''
  try {
    status.value = await notificationsAPI.wecomBindStatus()
    const prefs = await notificationsAPI.preferences()
    preferenceEnabled.value = prefs.preferences.some(p => p.category === 'task' && p.channel === 'wecom_app' && p.enabled)
    // 未绑定时预取手机号预填（只读）；失败静默，不阻塞卡片展示
    if (!bound.value && !bindSuggestDone.value) {
      bindSuggestDone.value = true
      try { suggest.value = await notificationsAPI.wecomBindSuggest() } catch { suggest.value = null }
    }
  } catch (e) { error.value = describe(e, '读取企微通知状态失败。') }
  finally { loading.value = false }
}

function rememberBindContext() {
  // OAuth 回调落地页用发起页的 token 调 confirm（state 绑定发起用户）
  localStorage.setItem(BIND_CONTEXT_KEY, JSON.stringify({
    token_key: getTenantScopedKey('saas_token'),
    return_path: route.fullPath,
  }))
}

async function startBind() {
  busy.value = true; error.value = ''; bindError.value = ''
  try {
    bindInfo.value = await notificationsAPI.wecomBindUrl()
    rememberBindContext()
    bindOpen.value = true
    startPolling()
  } catch (e) { error.value = describe(e, '获取绑定链接失败，请稍后重试。') }
  finally { busy.value = false }
}

function openOauth() {
  if (!bindInfo.value) return
  rememberBindContext()
  window.location.href = bindInfo.value.oauth_url
}

function openQrAuth() {
  // PC 扫码绑定（评审回炉 P1-3）：新窗口打开企微扫码授权页，用户手机扫码
  // 确认后该窗口带 code+state 重定向回落地页（同源 localStorage 有登录态）
  // 完成绑定；本页轮询 status 收起弹窗
  if (!bindInfo.value) return
  rememberBindContext()
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

async function confirmBind() {
  if (!suggest.value?.userid) return
  busy.value = true; error.value = ''
  try {
    const result = await notificationsAPI.wecomBindConfirm(suggest.value.userid)
    await refresh()
    // 绑定落库成功但 target 登记失败时显式警示，不乐观提示成功（评审回炉 P2-4）；
    // 先 refresh 再置提示，避免被 refresh 清空
    if (result.target_registered === false) error.value = REGISTER_FAILED_TEXT
    else notice.value = `已绑定企微账号「${result.name || result.userid}」。`
  } catch (e) { error.value = describe(e, '确认绑定失败，请重试。') }
  finally { busy.value = false }
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

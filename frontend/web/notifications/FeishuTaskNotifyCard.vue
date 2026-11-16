<template>
  <!-- 飞书通知渠道行（飞书任务通知接入，2026-11-15）：无卡片外壳，嵌入父级
       「通知渠道」卡片；绑定仅手机号识别命中自动完成（本期无 OAuth 弹窗），
       未命中行下方提示 -->
  <div>
    <div class="flex items-center gap-3 py-3.5 border-t border-gray-100">
      <div class="w-9 h-9 rounded-[10px] bg-primary-50 text-primary-600 flex items-center justify-center flex-none">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
      </div>
      <div class="flex-1 min-w-0">
        <div class="text-sm font-semibold text-default">飞书通知 <BaseBadge v-if="!loading && status?.app_available" :intent="notifyOn ? 'success' : 'neutral'">{{ notifyOn ? '已开启' : bound ? '已绑定' : '未绑定' }}</BaseBadge></div>
        <div class="text-xs text-gray-500 mt-0.5">{{ feishuRowDesc }}</div>
      </div>
      <template v-if="!loading && status?.app_available">
        <button v-if="bound" class="text-sm text-primary-600 hover:underline cursor-pointer flex-none" :disabled="busy" @click="unbind">解绑</button>
        <BaseSwitch :model-value="notifyOn" :disabled="busy || !bound || !targetActive" aria-label="飞书通知开关" @update:model-value="toggleFeishu" />
      </template>
    </div>
    <p v-if="!loading && status && !status.app_available" class="text-xs text-gray-500 -mt-1 pb-1">当前租户未配置飞书通知渠道，需要租户管理员在「连接中心 → 渠道配置」将飞书应用的通知用途设为两用或仅通知。</p>
    <p v-else-if="!loading && suggestMissed" class="text-xs text-gray-500 -mt-1 pb-1">当前账号手机号未在飞书通讯录匹配到成员，请确认账号手机号与应用可见范围后刷新重试。</p>
    <p v-else-if="!loading && bound && !targetActive" class="text-xs text-warning-700 -mt-1 pb-1">通知目标未就绪（可能账号已变动或已被撤销），请解绑后重新识别绑定。</p>
    <p v-if="error" role="alert" class="text-sm text-danger-600 pb-1">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-success-700 pb-1">{{ notice }}</p>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import { notificationsAPI, type FeishuBindStatus, type FeishuBindSuggest } from '@/api/notifications'

// 飞书任务通知行（2026-11-15，对齐企微 WecomTaskNotifyCard 行结构）：
// 本期无 OAuth——未绑定时手机号识别（suggest 只读）命中即自动 confirm 绑定；
// 未命中行下方提示（刷新页面重试）。开关只控制 feishu_app 偏好行；target 由
// 服务端绑定落库时登记。

// 绑定落库成功但通知 target 登记失败（对齐企微评审回炉 P2-4）：显式警示
const REGISTER_FAILED_TEXT = '绑定成功，但通知目标登记失败，请稍后在通知设置页重试解绑后重新绑定。'

// 绑定/偏好操作错误码 → 用户提示（其余错误展示通用文案，不暴露原始错误码）
const ERROR_TEXT: Record<string, string> = {
  NOTIFICATION_FEISHU_APP_UNAVAILABLE: '当前租户未配置飞书通知渠道，需要租户管理员在「连接中心 → 渠道配置」将飞书应用的通知用途设为两用或仅通知。',
  NOTIFICATION_PROVIDER_UNAVAILABLE: '当前租户未配置飞书通知渠道，需要租户管理员在「连接中心 → 渠道配置」将飞书应用的通知用途设为两用或仅通知。',
  NOTIFICATION_FEISHU_BIND_TAKEN: '该飞书账号已被其他账号绑定。',
  NOTIFICATION_FEISHU_BIND_MISMATCH: '飞书账号与当前登录用户不匹配。',
  NOTIFICATION_BIND_PHONE_REQUIRED: '当前账号未登记手机号，无法识别飞书账号，请先在个人信息中登记手机号。',
}

function describe(error: unknown, fallback: string): string {
  const code = (error as { code?: string } | null)?.code
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code]
  const message = error instanceof Error ? error.message : ''
  // request() 失败时 message 即错误码（如 NOTIFICATION_*），不直接暴露给用户
  return message && !message.startsWith('NOTIFICATION_') && !message.startsWith('HTTP_') ? message : fallback
}

const loading = ref(true), busy = ref(false), error = ref(''), notice = ref('')
const status = ref<FeishuBindStatus | null>(null)
const suggest = ref<FeishuBindSuggest | null>(null)
const suggestDone = ref(false)

const bound = computed(() => !!status.value?.bound)
const targetActive = computed(() => status.value?.target_state === 'active')
const notifyOn = computed(() => bound.value && targetActive.value && preferenceEnabled.value)
const preferenceEnabled = ref(false)
// 手机号识别明确未命中（matched:false）→ 行下方提示；suggest 接口失败不提示
const suggestMissed = computed(() => !bound.value && suggest.value?.matched === false)
// 行说明（对齐企微行口径）：绑定状态融入说明文字；未配置租户时由行下方提示条说明
const feishuRowDesc = computed(() => {
  if (!status.value?.app_available) return '任务完成或失败时通过飞书应用消息提醒'
  if (bound.value) return `任务完成或失败时通过飞书应用消息提醒${status.value?.name ? ' · 已绑定 ' + status.value.name : ''}`
  return suggest.value?.matched ? '识别到飞书账号，正在自动绑定…' : '识别飞书账号后，任务完成或失败时通过飞书应用消息提醒'
})
const emit = defineEmits<{ availability: [available: boolean] }>()

async function refresh() {
  loading.value = true; error.value = ''
  try {
    status.value = await notificationsAPI.feishuBindStatus()
    emit('availability', status.value.app_available === true)
    const prefs = await notificationsAPI.preferences()
    preferenceEnabled.value = prefs.preferences.some(p => p.category === 'task' && p.channel === 'feishu_app' && p.enabled)
    // 未绑定时手机号识别（只读）；失败静默，不阻塞行展示。
    // 命中即自动完成绑定（服务端二次核实仍在 confirm 内）；未命中行下方提示。
    if (!bound.value && !suggestDone.value) {
      suggestDone.value = true
      try {
        suggest.value = await notificationsAPI.feishuBindSuggest()
        if (suggest.value?.matched && suggest.value?.open_id) {
          try {
            const result = await notificationsAPI.feishuBindConfirm(suggest.value.open_id)
            await refresh()
            if (result.target_registered === false) error.value = REGISTER_FAILED_TEXT
            else notice.value = '已通过手机号自动绑定飞书账号。'
            return
          } catch (e) { error.value = describe(e, '自动绑定失败，请刷新重试。') }
        }
      } catch (e) {
        // suggest 失败：已映射错误码（如未登记手机号）给出提示；503（租户未
        // 配置渠道）静默——行下方提示条已说明，不重复通用失败文案
        // （用户反馈 2026-11-16）
        suggest.value = null
        if ((e as { status?: number } | null)?.status !== 503) {
          error.value = describe(e, '自动绑定失败，请刷新重试。')
        }
      }
    }
  } catch (e) { error.value = describe(e, '读取飞书通知状态失败。') }
  finally { loading.value = false }
}

// 开关切换单一入口：开=启用偏好；关=停用偏好
function toggleFeishu(on: boolean) {
  if (on === notifyOn.value) return
  return on ? enable() : disable()
}

async function enable() {
  busy.value = true; error.value = ''
  try {
    await notificationsAPI.feishuSetPreference(true)
    preferenceEnabled.value = true
    notice.value = '飞书任务通知已开启。'
  } catch (e) { error.value = describe(e, '开启失败，请重试。') }
  finally { busy.value = false }
}

async function disable() {
  busy.value = true; error.value = ''
  try {
    await notificationsAPI.feishuSetPreference(false)
    preferenceEnabled.value = false
    notice.value = '飞书任务通知已关闭。'
  } catch (e) { error.value = describe(e, '关闭失败，请重试。') }
  finally { busy.value = false }
}

async function unbind() {
  busy.value = true; error.value = ''
  try {
    await notificationsAPI.feishuBindUnbind()
    // 解绑后不再自动重识别（重识别命中会立刻自动重绑，用户无法保持解绑态）；
    // 页面刷新重新挂载后可重新识别绑定
    suggestDone.value = true
    notice.value = '已解绑飞书账号。'
    await refresh()
  } catch (e) { error.value = describe(e, '解绑失败，请重试。') }
  finally { busy.value = false }
}

onMounted(() => { void refresh() })
</script>

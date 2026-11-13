<template>
  <div class="min-h-screen bg-canvas flex items-center justify-center p-4">
    <BaseCard title="企微绑定" class="w-full max-w-lg">
      <p v-if="loading" role="status" class="text-muted">正在确认绑定…</p>
      <template v-else>
        <p :class="ok ? (warn ? 'text-amber-600' : 'text-success-700') : 'text-danger-600'" role="status">{{ message }}</p>
        <div v-if="!loading" class="mt-4 flex flex-wrap items-center gap-2">
          <BaseButton v-if="ok && returnPath" intent="primary" @click="router.push(returnPath)">返回通知设置</BaseButton>
          <BaseButton v-else-if="!ok && !loggedIn" intent="primary" @click="router.push('/')">去登录</BaseButton>
          <BaseButton intent="secondary" @click="router.push('/')">返回首页</BaseButton>
        </div>
      </template>
    </BaseCard>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { credentialGet } from '@/platform/credentialStore'
import { notificationsAPI } from '@/api/notifications'

// 企微 OAuth 回调落地页（wecom-app-task-notification-design §4.1/§4.5）：
// 企微授权后重定向到本页（?code&state），用发起页存于 localStorage 的登录态
// 调 /wecom/bind/callback 完成绑定（PC 流程授权页在新窗口打开并重定向回本页，
// sessionStorage 按标签页隔离读不到，故发起侧存 localStorage 同源共享）。
// 未登录场景不越过认证，提示先登录后重新发起。

// 与 WecomTaskNotifyCard 的发起侧约定一致
const BIND_CONTEXT_KEY = 'wecom-bind-context'

// 回调处理错误码 → 用户提示（与设置卡片同口径）
const ERROR_TEXT: Record<string, string> = {
  NOTIFICATION_WECOM_APP_UNAVAILABLE: '服务端暂未开放企微任务通知。',
  NOTIFICATION_BIND_STATE_INVALID: '绑定链接已过期或无效，请回到「通知设置」重新发起绑定。',
  NOTIFICATION_BIND_FAILED: '企微授权失败，请回到「通知设置」重新发起绑定。',
  NOTIFICATION_BIND_NOT_IN_SCOPE: '当前企微账号不在通知应用可见范围内，请联系管理员。',
  NOTIFICATION_BIND_TAKEN: '该企微账号已被其他账号绑定。',
  NOTIFICATION_BIND_MISMATCH: '企微账号与当前登录用户不匹配。',
}

const route = useRoute(), router = useRouter()
const loading = ref(true), ok = ref(false), loggedIn = ref(true), warn = ref(false)
const message = ref('')
// 发起页设置页路径（/t/.../notifications 或 /portal/notifications），校验后用于返回
const returnPath = ref('')

function describe(error: unknown, fallback: string): string {
  const code = (error as { code?: string } | null)?.code
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code]
  const text = error instanceof Error ? error.message : ''
  return text && !text.startsWith('NOTIFICATION_') && !text.startsWith('HTTP_') ? text : fallback
}

async function resolve(): Promise<void> {
  const code = typeof route.query.code === 'string' ? route.query.code : ''
  const state = typeof route.query.state === 'string' ? route.query.state : ''
  let context: { token_key?: string; return_path?: string } | null = null
  try { context = JSON.parse(localStorage.getItem(BIND_CONTEXT_KEY) || 'null') } catch { context = null }
  // 一次性消费：避免残留旧 return_path / token key 指向
  localStorage.removeItem(BIND_CONTEXT_KEY)
  const path = typeof context?.return_path === 'string' ? context.return_path : ''
  if (/^\/(t|portal)\//.test(path)) returnPath.value = path
  if (!code || !state) {
    message.value = '绑定链接无效，请回到「通知设置」重新发起绑定。'
    loading.value = false
    return
  }
  // 落地页路径不在 /t/ /portal 下，token key 必须取发起页存入的（state 绑定发起用户）
  const token = typeof context?.token_key === 'string' ? credentialGet(context.token_key) : null
  if (!token) {
    loggedIn.value = false
    message.value = '尚未登录。请先登录平台账号，再回到「通知设置」重新发起绑定。'
    loading.value = false
    return
  }
  try {
    const result = await notificationsAPI.wecomBindCallback(code, state, { Authorization: `Bearer ${token}` })
    ok.value = true
    // 绑定落库成功但 target 登记失败时显式警示，不乐观提示成功（评审回炉 P2-4）
    if (result.target_registered === false) {
      warn.value = true
      message.value = '绑定成功，但通知目标登记失败，请稍后在通知设置页重试解绑后重新绑定。'
    } else {
      message.value = `绑定成功（${result.name || result.userid}），可以关闭此页或返回通知设置开启通知。`
    }
  } catch (e) {
    if ((e as { status?: number } | null)?.status === 401) {
      loggedIn.value = false
      message.value = '登录状态已失效。请先登录平台账号，再回到「通知设置」重新发起绑定。'
    } else {
      message.value = describe(e, '绑定失败，请回到「通知设置」重新发起绑定。')
    }
  } finally { loading.value = false }
}

onMounted(() => { void resolve() })
</script>

<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader
      title="微信 Agent"
      :is-logged-in="isLoggedIn"
      :user="admin"
      @toggle-sidebar="toggleSidebar?.()"
      @logout="logout"
    />
    <main class="flex-1 min-h-0 overflow-y-auto px-4 py-6 sm:px-6">
      <div class="max-w-4xl mx-auto space-y-4">
        <RouterLink class="text-sm text-primary-700 underline" :to="{ query: { tab: 'history' } }">返回总结历史</RouterLink>
        <BaseCard>
          <section aria-labelledby="weixin-summary-title" class="space-y-4">
            <div class="space-y-1">
              <h1 id="weixin-summary-title" class="m-0 text-xl font-semibold text-default">微信群聊今日总结</h1>
              <p class="m-0 text-sm leading-6 text-muted">在选定的 Windows 电脑上登录微信并打开目标群聊。读取范围为北京时间今天。</p>
            </div>

            <div class="rounded-lg border border-default px-3 py-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <p class="m-0 min-w-0 break-words text-sm text-muted" role="status">{{ deviceStatus }}</p>
              <div class="flex flex-wrap items-center gap-3">
                <RouterLink class="text-sm text-primary-700 underline" :to="{ name: 'tenant-local-tools', params: { tenant_id: tenantId } }">管理设备</RouterLink>
                <BaseButton type="button" size="sm" intent="ghost" :disabled="loadingDevices" @click="refreshDevices">刷新设备</BaseButton>
              </div>
            </div>

            <form class="space-y-4" @submit.prevent="generate">
              <div class="space-y-1.5">
                <label for="weixin-group-name" class="block text-sm font-medium text-default">群聊全名</label>
                <BaseInput id="weixin-group-name" v-model="targetName" maxlength="128" required :disabled="busy" />
              </div>
              <label class="flex items-start gap-2 text-sm leading-6 text-default">
                <input v-model="confirmed" type="checkbox" class="mt-1.5 shrink-0" :disabled="busy" />
                <span>同意将该群今天的文字消息发送给模型，仅用于生成本次总结。</span>
              </label>
              <BaseButton type="submit" :disabled="!canGenerate">{{ busy ? '正在生成…' : '生成今日总结' }}</BaseButton>
            </form>

            <p v-if="submitError" class="m-0 text-sm text-danger-700" role="alert">{{ submitError }}</p>
          </section>
        </BaseCard>

        <BaseCard v-if="runId">
          <section aria-labelledby="weixin-summary-result" class="space-y-3">
            <h2 id="weixin-summary-result" class="m-0 break-words text-base font-semibold text-default">{{ run?.target_name ? `${run.target_name}的总结` : '总结结果' }}</h2>
            <p v-if="run?.business_date" class="m-0 text-sm text-muted">{{ run.business_date }} · 北京时间</p>
            <div v-if="pollError" class="flex flex-wrap items-center gap-2">
              <p class="m-0 text-sm text-danger-700" role="alert">{{ pollError }}</p>
              <BaseButton type="button" size="sm" intent="secondary" @click="refreshRun">重试查看</BaseButton>
            </div>
            <p v-else-if="!run || activeRun" class="m-0 text-sm text-muted" role="status">
              {{ run?.state === 'queued' ? '正在等待选定设备接单…' : '正在读取群聊并生成总结…' }}
            </p>
            <template v-else-if="run.state === 'succeeded'">
              <p v-if="run.partial" class="m-0 text-sm text-warning-700">只总结了可确认属于今天的部分消息。</p>
              <p v-if="run.summary" class="m-0 whitespace-pre-wrap break-words text-sm leading-7 text-default">{{ run.summary }}</p>
              <p v-else class="m-0 text-sm text-muted">在已读取范围内，没有可以总结的当天文字消息。</p>
              <p v-if="run.summary && run.message_count !== undefined" class="m-0 text-sm text-muted">本次总结使用 {{ run.message_count }} 条文字消息。</p>
            </template>
            <p v-else class="m-0 text-sm text-danger-700" role="alert">{{ runFailure }}</p>
          </section>
        </BaseCard>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onScopeDispose, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { createWeixinSummary, getWeixinSummary, listDevices, type LocalToolDevice, type WeixinSummaryRun } from '@/api/localTools'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const toggleSidebar = inject<() => void>('toggleSidebar')
const { admin, isLoggedIn, logout } = useTenantAuth()

const tenantId = computed(() => String(route.params.tenant_id ?? ''))
const runId = computed(() => typeof route.query.run === 'string' ? route.query.run : '')
const targetName = ref('')
const confirmed = ref(false)
const device = ref<LocalToolDevice>()
const loadingDevices = ref(false)
const deviceError = ref('')
const run = ref<WeixinSummaryRun>()
const submitting = ref(false)
const submitError = ref('')
const pollError = ref('')

const activeStates = new Set(['queued', 'claimed', 'running', 'cancel_requested'])
const activeRun = computed(() => Boolean(run.value && activeStates.has(run.value.state)))
const busy = computed(() => submitting.value || activeRun.value || Boolean(runId.value && !run.value && !pollError.value))
const canGenerate = computed(() => Boolean(
  targetName.value.trim() && confirmed.value && device.value && !loadingDevices.value && !busy.value
))
const deviceStatus = computed(() => {
  if (loadingDevices.value) return '正在检查本地设备…'
  if (deviceError.value) return deviceError.value
  if (!device.value) return '请先在本地工具中选定运行微信的电脑。'
  return `${device.value.name || '已选设备'} · ${device.value.online ? '在线' : '连接待确认'}`
})

const failures: Record<string, string> = {
  UI_CHANGED: '当前微信会话与输入的群名不匹配，或会话已发生变化。请打开目标群后重试。',
  TARGET_NOT_FOUND: '微信 CLI 无法确认当前会话是目标群聊。请核对完整群名。',
  TARGET_AMBIGUOUS: '微信 CLI 无法唯一核对当前会话。请打开目标群后重试。',
  WEIXIN_NOT_FOUND: '请打开选定电脑上的微信并登录。',
  CONTENT_UNAVAILABLE: '本次未能核实消息或日期范围。请确认微信会话可见后重试。',
  SUMMARY_COVERAGE_UNAVAILABLE: '无法核实当天的文字消息范围。请打开目标群聊，确认消息时间可见后重试。',
  NO_CREDIT: '积分余额已耗尽，请联系管理员充值。',
  SUMMARY_FAILED: '模型总结未完成，请稍后重新生成。',
  EXECUTION_UNKNOWN: '本次执行未能确认完成，请检查本地 Runtime 后重试。',
}
const runFailure = computed(() => failures[run.value?.error_code ?? '']
  || '本次总结未完成。请确认微信与 Runtime 正常运行后重试。')

let deviceRequest = 0
let submission: AbortController | undefined
let polling: AbortController | undefined
let pollTimer: ReturnType<typeof setTimeout> | undefined

async function refreshDevices() {
  const request = ++deviceRequest
  loadingDevices.value = true
  deviceError.value = ''
  try {
    const devices = await listDevices()
    if (request === deviceRequest) device.value = devices.find(item => item.selected && item.status === 'active')
  } catch (error) {
    if (request === deviceRequest) {
      device.value = undefined
      deviceError.value = (error as Error).message
    }
  } finally {
    if (request === deviceRequest) loadingDevices.value = false
  }
}

function stopPolling() {
  polling?.abort()
  clearTimeout(pollTimer)
}

async function refreshRun() {
  stopPolling()
  if (!runId.value) return
  const request = new AbortController()
  polling = request
  pollError.value = ''
  try {
    const current = await getWeixinSummary(runId.value, request.signal)
    if (request.signal.aborted) return
    run.value = current
    if (activeStates.has(current.state)) pollTimer = setTimeout(refreshRun, 1500)
  } catch (error) {
    if (!request.signal.aborted) pollError.value = (error as Error).message
  }
}

async function generate() {
  const selectedDevice = device.value
  if (!selectedDevice || !canGenerate.value) return
  const request = new AbortController()
  submission = request
  submitting.value = true
  submitError.value = ''
  try {
    const result = await createWeixinSummary(targetName.value, selectedDevice.device_id, request.signal)
    if (request.signal.aborted) return
    confirmed.value = false
    await router.replace({ query: { ...route.query, run: result.id } })
  } catch (error) {
    if (!request.signal.aborted) submitError.value = (error as Error).message
  } finally {
    if (!request.signal.aborted) submitting.value = false
  }
}

watch([targetName, () => device.value?.device_id], () => { confirmed.value = false })

watch(tenantId, () => {
  submission?.abort()
  submitting.value = false
  targetName.value = ''
  confirmed.value = false
  submitError.value = ''
  device.value = undefined
  void refreshDevices()
}, { immediate: true })

watch([tenantId, runId], () => {
  run.value = undefined
  pollError.value = ''
  void refreshRun()
}, { immediate: true })

onScopeDispose(() => {
  deviceRequest++
  submission?.abort()
  stopPolling()
})
</script>

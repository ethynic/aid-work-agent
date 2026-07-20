<template>
  <article class="mt-3 overflow-hidden rounded-2xl border border-amber-200 bg-amber-50/60 shadow-sm">
    <div class="flex items-start justify-between gap-4 border-b border-amber-200/80 px-4 py-3">
      <div>
        <div class="mb-1 flex items-center gap-2 text-xs font-medium text-amber-700">
          <span class="h-2 w-2 rounded-full bg-amber-500"></span>
          浏览器任务已暂停
        </div>
        <h3 class="text-sm font-semibold text-slate-800">{{ assistance.title }}</h3>
      </div>
      <div class="text-right text-xs text-slate-500">
        <div>剩余时间</div>
        <div class="font-mono font-semibold text-slate-700">{{ countdown }}</div>
      </div>
    </div>

    <div class="space-y-4 p-4">
      <ol class="space-y-2">
        <li v-for="(step, index) in assistance.steps" :key="step" class="flex gap-3 text-sm text-slate-700">
          <span class="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white text-xs font-semibold text-slate-500 ring-1 ring-slate-200">{{ index + 1 }}</span>
          <span>{{ step }}</span>
        </li>
      </ol>

      <div class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
        <span class="font-medium text-slate-700">完成方式：</span>
        {{ assistance.completion_mode === 'confirm_only' ? '完成后请手动确认' : '点击完成后由系统检测页面状态' }}
      </div>

      <BrowserView :run-id="assistance.run_id" :auth-headers="authHeaders" :control-enabled="state === 'controlling'" />

      <div v-if="assistance.missing_conditions?.length" class="rounded-lg border border-amber-200 bg-white px-3 py-2 text-xs text-amber-700">
        页面条件尚未满足，请继续完成页面操作后再试。
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button v-if="state === 'pending'" class="rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700" @click="takeControl">
          开始接管
        </button>
        <button v-if="state === 'controlling'" class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500" @click="complete">
          完成并继续
        </button>
        <button v-if="state === 'pending' || state === 'controlling'" class="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-600 hover:bg-slate-50" @click="extend">
          延长 5 分钟
        </button>
        <button v-if="state === 'pending' || state === 'controlling'" class="ml-auto rounded-lg px-3 py-2 text-sm text-red-600 hover:bg-red-50" @click="cancel">
          取消任务
        </button>
        <span v-if="state === 'resume_queued'" class="text-sm font-medium text-emerald-700">已交还 Agent，正在继续…</span>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { BrowserHumanAssistance } from '@/types'
import { browserApi } from '@/api/agent'
import BrowserView from './BrowserView.vue'

const props = defineProps<{ assistance: BrowserHumanAssistance; authHeaders: Record<string, string> }>()
const emit = defineEmits<{
  (e: 'updated', assistance: BrowserHumanAssistance): void
  (e: 'continuation', events: any[]): void
}>()
const now = ref(Date.now())
const timer = window.setInterval(() => { now.value = Date.now() }, 1000)
let stopped = false
onBeforeUnmount(() => {
  stopped = true
  window.clearInterval(timer)
})
const state = computed(() => props.assistance.state || 'pending')
const countdown = computed(() => {
  const seconds = Math.max(0, Math.floor((new Date(props.assistance.expires_at).getTime() - now.value) / 1000))
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
})

async function takeControl() {
  await browserApi.takeControl(props.assistance.run_id, props.assistance.assistance_id, props.authHeaders)
  emit('updated', { ...props.assistance, state: 'controlling' })
}
async function complete() {
  const result = await browserApi.complete(props.assistance.run_id, props.assistance.assistance_id, props.authHeaders)
  emit('updated', { ...props.assistance, state: result.success ? 'resume_queued' : 'controlling', missing_conditions: result.missing_conditions || [] })
  if (result.success) void pollContinuation()
}
let polling = false
async function pollContinuation() {
  if (polling) return
  polling = true
  let lastSeq = Number(sessionStorage.getItem(`browser-continuation:${props.assistance.continuation_id}`) || 0)
  try {
    for (let attempt = 0; attempt < 900; attempt += 1) {
      await new Promise(resolve => window.setTimeout(resolve, 1000))
      if (stopped) return
      try {
        const result = await browserApi.continuationEvents(props.assistance.continuation_id, lastSeq, props.authHeaders)
        if (result.events?.length) {
          lastSeq = result.last_seq
          sessionStorage.setItem(`browser-continuation:${props.assistance.continuation_id}`, String(lastSeq))
          emit('continuation', result.events)
          if (result.events.some((event: any) =>
            event.type === 'agent_continuation_completed'
            || event.type === 'browser_run_closed'
            || event.type === 'browser_human_required'
          )) return
        }
      } catch {
        // 页面刷新或短时断线后继续按 last_seq 补取。
      }
    }
  } finally {
    polling = false
  }
}
onMounted(() => {
  if (['pending', 'controlling', 'resume_queued'].includes(state.value)) {
    void pollContinuation()
  }
})
async function extend() {
  const result = await browserApi.extend(props.assistance.run_id, props.assistance.assistance_id, props.authHeaders)
  emit('updated', { ...props.assistance, expires_at: result.expires_at })
}
async function cancel() {
  await browserApi.cancel(props.assistance.run_id, props.assistance.assistance_id, props.authHeaders)
  emit('updated', { ...props.assistance, state: 'cancelled' })
}
</script>

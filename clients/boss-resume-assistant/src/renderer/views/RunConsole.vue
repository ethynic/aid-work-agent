<template>
  <div>
    <div class="card">
      <h2>任务控制</h2>
      <div class="control-row">
        <select v-model="selectedJobId" class="select job-select">
          <option :value="null" disabled>选择岗位</option>
          <option v-for="job in jobs" :key="job.id" :value="job.id">{{ job.name }}</option>
        </select>
        <button class="btn" :disabled="!canStart" @click="onStart">开始</button>
        <button class="btn btn-secondary" :disabled="!canPause" @click="onPause">暂停</button>
        <button class="btn" :disabled="!canResume" @click="onResume">恢复</button>
        <button class="btn btn-danger" :disabled="!canStop" @click="onStop">停止</button>
      </div>
      <div v-if="error" class="error-text">{{ error }}</div>
      <div v-if="!connected" class="muted hint">
        尚未连接浏览器。请先在「登录」页启动 Chrome 并完成登录确认。
      </div>
    </div>

    <div class="card">
      <h2>进度</h2>
      <div class="status-line">
        <span v-if="status" :class="stateBadgeClass(status.state)">{{ STATE_LABELS[status.state] }}</span>
        <span v-if="status?.pauseReason" class="error-text">暂停原因：{{ status.pauseReason }}</span>
      </div>
      <div class="stats">
        <div class="stat">
          <div class="stat-value">{{ status?.stats.viewed ?? 0 }}</div>
          <div class="stat-label">已查看</div>
        </div>
        <div class="stat">
          <div class="stat-value ok">{{ status?.stats.qualified ?? 0 }}</div>
          <div class="stat-label">合格</div>
        </div>
        <div class="stat">
          <div class="stat-value fail">{{ status?.stats.rejected ?? 0 }}</div>
          <div class="stat-label">不合格</div>
        </div>
        <div class="stat">
          <div class="stat-value warn">{{ status?.stats.uncertain ?? 0 }}</div>
          <div class="stat-label">待复核</div>
        </div>
        <div class="stat">
          <div class="stat-value">{{ status?.stats.actionsAttempted ?? 0 }}</div>
          <div class="stat-label">已发动作</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>实时事件</h2>
      <div v-if="events.length" class="event-list">
        <div v-for="(e, i) in events" :key="i" class="event-row">
          <span class="muted mono">{{ formatTime(e.at) }}</span>
          <span v-if="e.type === 'state'" class="badge badge-neutral">{{ e.state ? STATE_LABELS[e.state] : '' }}</span>
          <span v-else-if="e.type === 'candidate'" :class="conclusionBadge(e.conclusion)">{{ e.conclusion }}</span>
          <span v-else :class="e.level === 'error' ? 'badge badge-fail' : e.level === 'warn' ? 'badge badge-warn' : 'badge badge-neutral'">日志</span>
          <span class="event-msg">{{ e.message }}</span>
        </div>
      </div>
      <p v-else class="muted">暂无事件。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useSession } from '../composables/useSession'
import { STATE_LABELS, stateBadgeClass } from '../stateLabels'
import type { JobRecord } from '@shared/ipc'

const { status, events, refresh } = useSession()
const jobs = ref<JobRecord[]>([])
const selectedJobId = ref<number | null>(null)
const error = ref('')

const connected = computed(() => status.value?.connected === true)
const isRunning = computed(() => {
  const s = status.value?.state
  return (
    s === 'READING_LIST' || s === 'OPENING_DETAIL' || s === 'CAPTURING_DETAIL' ||
    s === 'OCR_AND_NORMALIZE' || s === 'SCREENING' || s === 'WAITING_REVIEW' ||
    s === 'EXECUTING_ACTION' || s === 'CLOSING_DETAIL' || s === 'CHECKPOINT'
  )
})
const canStart = computed(
  () =>
    connected.value &&
    selectedJobId.value !== null &&
    !isRunning.value &&
    ['CONNECTING_CDP', 'COMPLETED', 'STOPPED'].includes(status.value?.state ?? ''),
)
const canPause = computed(() => isRunning.value)
const canResume = computed(() => status.value?.state === 'PAUSED' && connected.value)
const canStop = computed(() => isRunning.value || status.value?.state === 'PAUSED')

function conclusionBadge(conclusion?: string): string {
  if (conclusion === 'QUALIFIED') return 'badge badge-ok'
  if (conclusion === 'REJECTED') return 'badge badge-fail'
  if (conclusion === 'UNCERTAIN') return 'badge badge-warn'
  return 'badge badge-neutral'
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString()
}

async function guard(fn: () => Promise<unknown>) {
  error.value = ''
  try {
    await fn()
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function onStart() {
  if (selectedJobId.value === null) return
  await guard(() => window.bossResume.session.start(selectedJobId.value as number))
}
async function onPause() {
  await guard(() => window.bossResume.session.pause())
}
async function onResume() {
  await guard(() => window.bossResume.session.resume())
}
async function onStop() {
  await guard(() => window.bossResume.session.stop())
}

onMounted(async () => {
  jobs.value = await window.bossResume.jobs.list()
  if (jobs.value.length && selectedJobId.value === null) {
    selectedJobId.value = jobs.value[0]!.id
  }
})
</script>

<style scoped>
.control-row {
  display: flex;
  gap: 10px;
  align-items: center;
}
.job-select {
  width: 260px;
}
.hint {
  font-size: 12px;
  margin-top: 10px;
}
.error-text {
  color: var(--c-danger);
  font-size: 12px;
  margin-top: 8px;
}
.status-line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.stats {
  display: flex;
  gap: 32px;
}
.stat-value {
  font-size: 24px;
  font-weight: 600;
}
.stat-value.ok {
  color: var(--c-ok-text);
}
.stat-value.fail {
  color: var(--c-fail-text);
}
.stat-value.warn {
  color: var(--c-warn-text);
}
.stat-label {
  font-size: 12px;
  color: var(--c-muted);
  margin-top: 2px;
}
.event-list {
  max-height: 320px;
  overflow-y: auto;
}
.event-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px solid var(--c-border);
}
.event-msg {
  word-break: break-all;
}
</style>

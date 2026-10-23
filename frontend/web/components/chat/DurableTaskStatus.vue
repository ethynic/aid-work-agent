<template>
  <section class="mb-3 rounded-lg border border-default bg-surface p-3 text-sm text-default" aria-label="任务状态">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="font-medium">{{ title }}</p>
      <span v-if="row.execution_mode === 'continuous'" class="text-xs text-muted">持续任务</span>
    </div>
    <p v-if="timer && row.status === 'waiting' && !row.pause_requested && !row.sleep_blocked" class="mt-1 text-muted">
      下次唤醒：<time :datetime="timer.wake_at">{{ deadline }}</time>
    </p>
    <p v-if="timer?.reason" class="mt-1 break-words text-muted">{{ timer.reason }}</p>
    <p v-if="row.sleep_blocked" class="mt-1 text-muted">自动继续暂不可用，原任务已保留。请核对执行条件后继续。</p>
    <p v-if="wait?.kind === 'continuous_guard'" class="mt-1 text-muted">连续执行已达到本段保护上限，请选择继续或停止。</p>
    <p v-if="row.usage" class="mt-1 text-muted">
      本任务累计消耗 {{ credits(row.usage.task_credits) }} 积分 · 本会话累计消耗 {{ credits(row.usage.session_credits) }} 积分
      <span v-if="row.usage.pending">（部分用量待结算）</span>
    </p>
    <p v-if="row.usage?.updated_at" class="mt-1 text-xs text-muted">积分更新于 {{ dateTime(row.usage.updated_at) }}</p>
    <div v-if="!terminal" class="mt-2 flex flex-wrap gap-2">
      <BaseButton v-if="canResume" size="sm" :disabled="disabled" @click="$emit('control', 'resume')">{{ row.status === 'paused' ? '继续任务' : '立即继续' }}</BaseButton>
      <BaseButton v-if="row.status === 'running' || row.status === 'waiting'" size="sm" intent="secondary" :disabled="disabled || row.pause_requested" @click="$emit('control', 'pause')">暂停</BaseButton>
      <BaseButton size="sm" intent="danger" :disabled="disabled" @click="$emit('control', 'cancel')">停止任务</BaseButton>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import BaseButton from '../ui/BaseButton.vue'
import { runnerTerminal, runnerWaitLeaf, type RunnerView } from '@/api/runner'

const props = defineProps<{ row: RunnerView; busy: boolean }>()
defineEmits<{ control: [action: 'pause' | 'resume' | 'cancel'] }>()
const terminal = computed(() => runnerTerminal(props.row.status))
const wait = computed(() => runnerWaitLeaf(props.row.snapshot.waiting))
const timer = computed(() => wait.value?.kind === 'timer' ? wait.value : undefined)
const canResume = computed(() => !props.row.local_recovery && (props.row.status === 'paused' && !wait.value
  || wait.value?.kind === 'continuous_guard' && !!wait.value.wait_id && !!wait.value.target_execution_id && ['waiting','paused'].includes(props.row.status)
  || !!timer.value?.wait_id && !!timer.value.target_execution_id && ['waiting','paused'].includes(props.row.status)))
const disabled = computed(() => props.busy || props.row.cancel_requested || !!props.row.resume_requested)
const title = computed(() => props.row.cancel_requested && !terminal.value ? '正在停止任务'
  : props.row.pause_requested && props.row.status !== 'paused' ? '正在暂停任务'
  : props.row.resume_requested ? '正在恢复任务'
  : props.row.sleep_blocked ? '自动继续已暂停'
  : wait.value?.kind === 'continuous_guard' ? '连续执行已暂停'
  : ({ paused:'任务已暂停', interrupted:'任务已中断，请核对执行记录', completed:'任务已完成',
      failed:'任务执行失败', cancelled:'任务已停止', finalizing:'正在保存任务结果', queued:'任务排队中' } as Record<string,string>)[props.row.status]
    || (timer.value && props.row.status === 'waiting' ? '任务休眠中' : '任务执行中'))
function dateTime(value: string) {
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { hour12:false }) : '时间待确认'
}
const deadline = computed(() => timer.value?.wake_at ? dateTime(timer.value.wake_at) : '等待服务端确认')
function credits(value: number) {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits:4 }) : '待确认'
}
</script>

<template>
  <dl class="space-y-3 text-sm break-words">
    <div><dt class="text-muted">目标</dt><dd class="whitespace-pre-wrap">{{ spec.goal }}</dd></div>
    <div><dt class="text-muted">完成标准</dt><dd v-if="spec.completion_rule.mode === 'rounds'">有效回复达到 {{ spec.completion_rule.rounds_target }} 轮</dd><dd v-else-if="spec.completion_rule.mode === 'judged'"><ul class="list-disc pl-5"><li v-for="criterion in spec.completion_rule.criteria" :key="criterion">{{ criterion }}</li></ul></dd><dd v-else><ul class="list-disc pl-5"><li v-for="field in spec.completion_rule.fields" :key="field.key">{{ field.question }}（{{ field.key }}）：允许 {{ field.allowed_values.join('、') }}；接受 {{ field.accepted_values.join('、') }}</li></ul>必须全部确认</dd></div>
    <div><dt class="text-muted">开场白</dt><dd class="whitespace-pre-wrap">{{ spec.opening_text || '不发送开场白' }}</dd></div>
    <div><dt class="text-muted">授权上限</dt><dd>发送 {{ spec.limits.max_replies }} 条（含开场白） · 决策 {{ spec.limits.max_decisions }} 次 · {{ spec.limits.max_cost_units }} 积分<br />截止 {{ dateLabel(spec.limits.expires_at) }} · 等待对方超时 {{ spec.limits.peer_wait_timeout_seconds }} 秒</dd></div>
    <div><dt class="text-muted">回复策略</dt><dd>{{ spec.reply_policy.style }}<br />允许事实：{{ spec.reply_policy.allowed_facts.join('；') || '无额外事实' }}<br />禁止承诺：{{ spec.reply_policy.forbidden_commitments.join('；') || '无额外条目' }}</dd></div>
    <div><dt class="text-muted">工作时段</dt><dd>{{ spec.work_window ? `UTC ${spec.work_window.start_hour_utc}:00–${spec.work_window.end_hour_utc}:00；工作日 ${spec.work_window.weekdays_utc.map(d => ['周一','周二','周三','周四','周五','周六','周日'][d]).join('、')}` : '不限制时段' }}</dd></div>
  </dl>
</template>
<script setup lang="ts">
import type { TaskSpec } from '@/api/sessionTasks'
import { dateLabel } from './presentation'
defineProps<{ spec: TaskSpec }>()
</script>

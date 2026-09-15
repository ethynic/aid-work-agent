<template>
  <fieldset :disabled="disabled" class="space-y-4">
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <label class="text-sm text-muted">任务目标 *<textarea v-model="form.goal" :class="input()" rows="3" required maxlength="2000" /></label>
      <label class="text-sm text-muted">开场白（可选，最多一次）<textarea v-model="form.opening" :class="input()" rows="3" maxlength="500" /></label>
      <label class="text-sm text-muted">完成方式 *<BaseSelect v-model="form.mode"><option value="rounds">达到回复轮数</option><option value="peer_confirmed">对方确认全部字段</option><option value="judged">按标准判断完成</option></BaseSelect></label>
      <label v-if="form.mode === 'rounds'" class="text-sm text-muted">目标回复轮数 *<BaseInput v-model="form.rounds" type="number" min="1" max="1000" required /></label>
      <label v-if="form.mode === 'judged'" class="text-sm text-muted">完成标准（每行一条，1–10 条）*<textarea v-model="form.criteria" :class="input()" rows="3" required /></label>
    </div>
    <div v-if="form.mode === 'peer_confirmed'" class="space-y-3">
      <p class="text-sm text-muted">所有字段均须由发布后的对方消息明确确认；接受值必须属于允许值。</p>
      <div v-for="(field, index) in form.fields" :key="index" class="grid grid-cols-1 md:grid-cols-4 gap-3 border border-default rounded p-3">
        <label class="text-sm text-muted">字段标识 *<BaseInput v-model="field.key" placeholder="例如 interest" required /></label>
        <label class="text-sm text-muted">确认问题 *<BaseInput v-model="field.question" required maxlength="500" /></label>
        <label class="text-sm text-muted">允许值（逗号分隔）*<BaseInput v-model="field.allowed" required /></label>
        <label class="text-sm text-muted">接受值（逗号分隔）*<BaseInput v-model="field.accepted" required /></label>
        <BaseButton type="button" intent="danger-ghost" :disabled="form.fields.length <= 1" @click="form.fields.splice(index, 1)">删除字段</BaseButton>
      </div>
      <BaseButton type="button" intent="secondary" :disabled="form.fields.length >= 10" @click="form.fields.push({ key: '', question: '', allowed: '', accepted: '' })">添加字段</BaseButton>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <label class="text-sm text-muted">回复风格 *<BaseInput v-model="form.style" required maxlength="200" /></label>
      <label class="text-sm text-muted">允许使用的事实（每行一条）<textarea v-model="form.facts" :class="input()" rows="3" /></label>
      <label class="text-sm text-muted">禁止承诺（每行一条）<textarea v-model="form.forbidden" :class="input()" rows="3" /></label>
      <label v-for="field in limitFields" :key="field.key" class="text-sm text-muted">{{ field.label }} *<BaseInput v-model="form[field.key]" type="number" min="1" :max="field.max" required /></label>
      <label class="text-sm text-muted">截止时间（本地时区）*<BaseInput v-model="form.expires" type="datetime-local" required /></label>
    </div>
    <label class="flex gap-2 text-sm"><input v-model="form.windowEnabled" type="checkbox" />限制工作时段（UTC）</label>
    <div v-if="form.windowEnabled" class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <label class="text-sm text-muted">开始小时（UTC）<BaseInput v-model="form.start" type="number" min="0" max="23" /></label>
      <label class="text-sm text-muted">结束小时（UTC）<BaseInput v-model="form.end" type="number" min="0" max="23" /></label>
      <label class="text-sm text-muted">工作日（0 周一至 6 周日，逗号分隔）<BaseInput v-model="form.days" /></label>
    </div>
  </fieldset>
</template>
<script setup lang="ts">
import { reactive } from 'vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { input } from '@/variants/input'
import type { TaskSpec, CompletionRule } from '@/api/sessionTasks'
defineProps<{ disabled?: boolean }>()
const form = reactive({ goal: '', opening: '', mode: 'rounds', rounds: '3', criteria: '', fields: [{ key: 'confirmed', question: '', allowed: '是,否', accepted: '是' }], style: '专业、简洁，不作未经授权的承诺', facts: '', forbidden: '', replies: '5', decisions: '10', cost: '100', wait: '86400', expires: '', windowEnabled: false, start: '0', end: '23', days: '0,1,2,3,4,5,6' })
const limitFields: { key: 'replies' | 'decisions' | 'cost' | 'wait'; label: string; max: number }[] = [{ key: 'replies', label: '最多发送条数（含开场白）', max: 1000 }, { key: 'decisions', label: '最多决策次数', max: 1000 }, { key: 'cost', label: '费用上限（积分）', max: 10000000 }, { key: 'wait', label: '等待对方超时（秒）', max: 2592000 }]
const lines = (value: string) => value.split('\n').map(x => x.trim()).filter(Boolean)
const values = (value: string) => value.split(/[,，]/).map(x => x.trim()).filter(Boolean)
function getSpec(): TaskSpec {
  const rule: CompletionRule = form.mode === 'rounds' ? { mode: 'rounds', rounds_target: Number(form.rounds) } : form.mode === 'judged' ? { mode: 'judged', criteria: lines(form.criteria) } : { mode: 'peer_confirmed', require_all: true, fields: form.fields.map(f => ({ key: f.key, question: f.question, allowed_values: values(f.allowed), accepted_values: values(f.accepted) })) }
  if (!form.goal.trim() || !form.style.trim()) throw new Error('请填写目标和回复风格')
  if (rule.mode === 'rounds' && rule.rounds_target + (form.opening ? 1 : 0) > Number(form.replies)) throw new Error('目标轮数加开场白不能超过发送上限')
  if (rule.mode === 'judged' && (!rule.criteria.length || rule.criteria.length > 10 || new Set(rule.criteria).size !== rule.criteria.length)) throw new Error('请填写 1–10 条不重复的完成标准')
  if (rule.mode === 'peer_confirmed' && (new Set(rule.fields.map(f => f.key)).size !== rule.fields.length || rule.fields.some(f => !/^[a-z][a-z0-9_]{0,63}$/.test(f.key) || !f.question.trim() || !f.allowed_values.length || !f.accepted_values.length || f.accepted_values.some(v => !f.allowed_values.includes(v))))) throw new Error('确认字段标识、问题及接受值不合法')
  const expires = new Date(form.expires)
  if (!Number.isFinite(expires.getTime()) || expires.getTime() <= Date.now()) throw new Error('截止时间必须晚于当前时间')
  for (const field of limitFields) if (!Number.isFinite(Number(form[field.key])) || Number(form[field.key]) <= 0 || Number(form[field.key]) > field.max || (field.key !== 'cost' && !Number.isInteger(Number(form[field.key])))) throw new Error(`${field.label}不合法`)
  const days = values(form.days).map(Number)
  if (form.windowEnabled && (!days.length || days.some(d => !Number.isInteger(d) || d < 0 || d > 6) || [Number(form.start), Number(form.end)].some(h => !Number.isInteger(h) || h < 0 || h > 23))) throw new Error('工作时段不合法')
  return { goal: form.goal, opening_text: form.opening || null, completion_rule: rule, reply_policy: { style: form.style, allowed_facts: lines(form.facts), forbidden_commitments: lines(form.forbidden) }, limits: { max_replies: Number(form.replies), max_decisions: Number(form.decisions), max_cost_units: Number(form.cost), peer_wait_timeout_seconds: Number(form.wait), expires_at: expires.toISOString() }, work_window: form.windowEnabled ? { start_hour_utc: Number(form.start), end_hour_utc: Number(form.end), weekdays_utc: days } : null }
}
function load(spec: TaskSpec) {
  const rule = spec.completion_rule
  const date = new Date(spec.limits.expires_at)
  Object.assign(form, { goal: spec.goal, opening: spec.opening_text || '', mode: rule.mode, style: spec.reply_policy.style, facts: spec.reply_policy.allowed_facts.join('\n'), forbidden: spec.reply_policy.forbidden_commitments.join('\n'), replies: String(spec.limits.max_replies), decisions: String(spec.limits.max_decisions), cost: String(spec.limits.max_cost_units), wait: String(spec.limits.peer_wait_timeout_seconds), expires: new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16), windowEnabled: !!spec.work_window })
  if (rule.mode === 'rounds') form.rounds = String(rule.rounds_target)
  if (rule.mode === 'judged') form.criteria = rule.criteria.join('\n')
  if (rule.mode === 'peer_confirmed') form.fields = rule.fields.map(f => ({ key: f.key, question: f.question, allowed: f.allowed_values.join(','), accepted: f.accepted_values.join(',') }))
  if (spec.work_window) Object.assign(form, { start: String(spec.work_window.start_hour_utc), end: String(spec.work_window.end_hour_utc), days: spec.work_window.weekdays_utc.join(',') })
}
defineExpose({ getSpec, load, form })
</script>

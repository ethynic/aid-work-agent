<template>
  <div>
    <div class="card">
      <h2>岗位列表</h2>
      <table v-if="jobs.length" class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>名称</th>
            <th>硬规则</th>
            <th>动作上限(会话/日)</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in jobs" :key="job.id">
            <td>{{ job.id }}</td>
            <td>{{ job.name }}</td>
            <td class="mono">{{ summarizeRules(job.hardRules) }}</td>
            <td>{{ job.actionLimitSession ?? '默认' }} / {{ job.actionLimitDay ?? '默认' }}</td>
            <td class="muted">{{ job.createdAt }}</td>
            <td>
              <button class="btn btn-secondary btn-sm" @click="editJob(job)">编辑</button>
              <button class="btn btn-secondary btn-sm danger-text" @click="removeJob(job)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无岗位，请在下方创建。</p>
    </div>

    <div class="card">
      <h2>{{ editingId ? `编辑岗位 #${editingId}` : '新增岗位' }}</h2>
      <div class="grid">
        <div class="form-field">
          <label class="form-label">岗位名称 *</label>
          <input v-model="form.name" class="input" placeholder="如：前端工程师" />
        </div>
        <div class="form-field">
          <label class="form-label">期望城市（逗号分隔，留空不限）</label>
          <input v-model="form.cities" class="input" placeholder="如：北京,上海" />
        </div>
        <div class="form-field">
          <label class="form-label">最低工作年限（年，留空不限）</label>
          <input v-model="form.minYears" class="input" type="number" min="0" placeholder="如：3" />
        </div>
        <div class="form-field">
          <label class="form-label">必备技能（逗号分隔，简历须全部包含）</label>
          <input v-model="form.requiredSkills" class="input" placeholder="如：Vue,TypeScript" />
        </div>
        <div class="form-field">
          <label class="form-label">排除关键词（逗号分隔，命中即不合格）</label>
          <input v-model="form.excludeKeywords" class="input" placeholder="如：外包,实习" />
        </div>
        <div class="form-field">
          <label class="form-label">偏好技能（逗号分隔，供 AI 参考）</label>
          <input v-model="form.preferredSkills" class="input" placeholder="如：Electron,Node.js" />
        </div>
        <div class="form-field">
          <label class="form-label">会话动作上限（留空默认 50）</label>
          <input v-model="form.actionLimitSession" class="input" type="number" min="1" />
        </div>
        <div class="form-field">
          <label class="form-label">每日动作上限（留空默认 100）</label>
          <input v-model="form.actionLimitDay" class="input" type="number" min="1" />
        </div>
      </div>
      <div class="form-field">
        <label class="form-label">问候语模板（当前版本仅保存备忘，动作使用 BOSS 默认招呼语）</label>
        <textarea v-model="form.greetingTemplate" class="textarea" placeholder="您好，看到您的简历……"></textarea>
      </div>
      <div v-if="error" class="error-text">{{ error }}</div>
      <div class="actions">
        <button class="btn" :disabled="busy" @click="save">{{ editingId ? '保存' : '创建' }}</button>
        <button v-if="editingId" class="btn btn-secondary" @click="resetForm">取消编辑</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { JobRecord, JobInput } from '@shared/ipc'

const jobs = ref<JobRecord[]>([])
const editingId = ref<number | null>(null)
const busy = ref(false)
const error = ref('')

const emptyForm = {
  name: '',
  cities: '',
  minYears: '',
  requiredSkills: '',
  excludeKeywords: '',
  preferredSkills: '',
  actionLimitSession: '',
  actionLimitDay: '',
  greetingTemplate: '',
}
const form = ref({ ...emptyForm })

function splitList(v: string): string[] {
  return v
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function summarizeRules(hardRules: string | null): string {
  if (!hardRules) return '-'
  try {
    const cfg = JSON.parse(hardRules) as Record<string, unknown>
    const parts: string[] = []
    if (Array.isArray(cfg['city']) && cfg['city'].length) parts.push(`城市:${(cfg['city'] as string[]).join('/')}`)
    if (typeof cfg['minYears'] === 'number') parts.push(`≥${cfg['minYears']}年`)
    if (Array.isArray(cfg['requiredSkills']) && cfg['requiredSkills'].length) {
      parts.push(`必备:${(cfg['requiredSkills'] as string[]).join('/')}`)
    }
    if (Array.isArray(cfg['excludeKeywords']) && cfg['excludeKeywords'].length) {
      parts.push(`排除:${(cfg['excludeKeywords'] as string[]).join('/')}`)
    }
    return parts.join('；') || '-'
  } catch {
    return '(配置解析失败)'
  }
}

async function load() {
  jobs.value = await window.bossResume.jobs.list()
}

function editJob(job: JobRecord) {
  editingId.value = job.id
  let cfg: Record<string, unknown> = {}
  let prefs: Record<string, unknown> = {}
  try {
    cfg = job.hardRules ? (JSON.parse(job.hardRules) as Record<string, unknown>) : {}
  } catch {
    /* 配置损坏时以空表单编辑，保存即修复 */
  }
  try {
    prefs = job.preferences ? (JSON.parse(job.preferences) as Record<string, unknown>) : {}
  } catch {
    /* 同上 */
  }
  form.value = {
    name: job.name,
    cities: Array.isArray(cfg['city']) ? (cfg['city'] as string[]).join(',') : '',
    minYears: typeof cfg['minYears'] === 'number' ? String(cfg['minYears']) : '',
    requiredSkills: Array.isArray(cfg['requiredSkills']) ? (cfg['requiredSkills'] as string[]).join(',') : '',
    excludeKeywords: Array.isArray(cfg['excludeKeywords']) ? (cfg['excludeKeywords'] as string[]).join(',') : '',
    preferredSkills: Array.isArray(prefs['preferredSkills']) ? (prefs['preferredSkills'] as string[]).join(',') : '',
    actionLimitSession: job.actionLimitSession ? String(job.actionLimitSession) : '',
    actionLimitDay: job.actionLimitDay ? String(job.actionLimitDay) : '',
    greetingTemplate: job.greetingTemplate ?? '',
  }
}

function resetForm() {
  editingId.value = null
  form.value = { ...emptyForm }
  error.value = ''
}

function buildInput(): JobInput {
  const hardRules: Record<string, unknown> = {}
  const cities = splitList(form.value.cities)
  if (cities.length) hardRules['city'] = cities
  if (form.value.minYears.trim()) hardRules['minYears'] = Number(form.value.minYears)
  const required = splitList(form.value.requiredSkills)
  if (required.length) hardRules['requiredSkills'] = required
  const exclude = splitList(form.value.excludeKeywords)
  if (exclude.length) hardRules['excludeKeywords'] = exclude

  const preferences: Record<string, unknown> = {}
  const preferred = splitList(form.value.preferredSkills)
  if (preferred.length) preferences['preferredSkills'] = preferred

  return {
    name: form.value.name.trim(),
    hardRules: Object.keys(hardRules).length ? JSON.stringify(hardRules) : null,
    preferences: Object.keys(preferences).length ? JSON.stringify(preferences) : null,
    greetingTemplate: form.value.greetingTemplate.trim() || null,
    actionLimitSession: form.value.actionLimitSession.trim() ? Number(form.value.actionLimitSession) : null,
    actionLimitDay: form.value.actionLimitDay.trim() ? Number(form.value.actionLimitDay) : null,
  }
}

async function save() {
  error.value = ''
  if (!form.value.name.trim()) {
    error.value = '岗位名称不能为空'
    return
  }
  busy.value = true
  try {
    const input = buildInput()
    if (editingId.value) {
      await window.bossResume.jobs.update(editingId.value, input)
    } else {
      await window.bossResume.jobs.create(input)
    }
    resetForm()
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function removeJob(job: JobRecord) {
  if (!confirm(`确定删除岗位「${job.name}」？相关历史数据保留。`)) return
  error.value = ''
  try {
    await window.bossResume.jobs.remove(job.id)
    if (editingId.value === job.id) resetForm()
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(load)
</script>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0 16px;
}
.actions {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}
.error-text {
  color: var(--c-danger);
  font-size: 12px;
  margin-bottom: 8px;
}
.danger-text {
  color: var(--c-danger);
}
</style>

<template>
  <div>
    <div class="card">
      <h2>复核队列（UNCERTAIN）</h2>
      <div class="toolbar">
        <label class="checkbox-label">
          <input type="checkbox" v-model="includeResolved" @change="load" />
          显示已改判
        </label>
        <button class="btn btn-secondary btn-sm" @click="load">刷新</button>
      </div>
      <p v-if="!items.length" class="muted">暂无待复核候选人。</p>
      <div v-for="item in items" :key="item.evaluationId" class="review-item">
        <div class="review-head">
          <span class="name">{{ item.candidateName ?? '（未知姓名）' }}</span>
          <span class="mono muted">{{ item.fingerprint }}</span>
          <span class="badge badge-warn">{{ item.conclusion }}</span>
          <span class="muted time">{{ item.createdAt }}</span>
          <template v-if="item.override">
            <span class="badge badge-ok">已改判: {{ item.override.overrideConclusion }}</span>
          </template>
        </div>
        <div class="muted reason">评估理由：{{ item.reason ?? '-' }}</div>
        <div v-if="item.override" class="muted reason">
          改判理由：{{ item.override.overrideReason }}（{{ item.override.createdAt }}）
        </div>
        <details class="evidence">
          <summary class="muted">证据明细</summary>
          <pre class="mono evidence-json">{{ formatEvidence(item.evidence) }}</pre>
        </details>
        <div class="override-row">
          <select v-model="overrideForm[item.evaluationId]!.conclusion" class="select override-select">
            <option value="QUALIFIED">改判：合格</option>
            <option value="REJECTED">改判：不合格</option>
            <option value="UNCERTAIN">维持：待复核</option>
          </select>
          <input
            v-model="overrideForm[item.evaluationId]!.reason"
            class="input override-reason"
            placeholder="改判理由（必填）"
          />
          <button class="btn btn-sm" @click="submitOverride(item)">提交改判</button>
        </div>
      </div>
      <div v-if="error" class="error-text">{{ error }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import type { ReviewItem } from '@shared/ipc'

const items = ref<ReviewItem[]>([])
const includeResolved = ref(false)
const error = ref('')
const overrideForm = reactive<Record<number, { conclusion: 'QUALIFIED' | 'REJECTED' | 'UNCERTAIN'; reason: string }>>({})

async function load() {
  error.value = ''
  try {
    items.value = await window.bossResume.review.list({ includeResolved: includeResolved.value })
    for (const item of items.value) {
      if (!overrideForm[item.evaluationId]) {
        overrideForm[item.evaluationId] = { conclusion: 'QUALIFIED', reason: '' }
      }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

function formatEvidence(evidence: string | null): string {
  if (!evidence) return '-'
  try {
    return JSON.stringify(JSON.parse(evidence), null, 2)
  } catch {
    return evidence
  }
}

async function submitOverride(item: ReviewItem) {
  error.value = ''
  const form = overrideForm[item.evaluationId]
  if (!form) return
  if (!form.reason.trim()) {
    error.value = `候选人 ${item.candidateName ?? item.evaluationId}：改判理由不能为空`
    return
  }
  try {
    await window.bossResume.review.override({
      evaluationId: item.evaluationId,
      conclusion: form.conclusion,
      reason: form.reason.trim(),
    })
    form.reason = ''
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--c-muted);
}
.review-item {
  border-top: 1px solid var(--c-border);
  padding: 12px 0;
}
.review-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.name {
  font-weight: 600;
  font-size: 14px;
}
.time {
  font-size: 12px;
}
.reason {
  font-size: 12px;
  margin-top: 6px;
}
.evidence {
  margin-top: 6px;
  font-size: 12px;
}
.evidence-json {
  background: var(--c-bg);
  border-radius: 6px;
  padding: 8px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.override-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  align-items: center;
}
.override-select {
  width: 140px;
}
.override-reason {
  flex: 1;
}
.error-text {
  color: var(--c-danger);
  font-size: 12px;
  margin-top: 8px;
}
</style>

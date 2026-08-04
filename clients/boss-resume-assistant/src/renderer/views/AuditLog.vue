<template>
  <div>
    <div class="card">
      <h2>筛选条件</h2>
      <div class="filters">
        <select v-model="filters.jobId" class="select">
          <option :value="null">全部岗位</option>
          <option v-for="job in jobs" :key="job.id" :value="job.id">{{ job.name }}</option>
        </select>
        <input v-model="filters.dateFrom" class="input" type="date" title="起始日期" />
        <input v-model="filters.dateTo" class="input" type="date" title="截止日期" />
        <input v-model="filters.candidate" class="input" placeholder="候选人姓名/指纹" />
        <select v-model="filters.action" class="select">
          <option value="">全部动作</option>
          <option value="GREET">打招呼</option>
          <option value="REJECT">不合适</option>
        </select>
        <select v-model="filters.status" class="select">
          <option value="">全部状态</option>
          <option value="PLANNED">PLANNED</option>
          <option value="SENT">SENT</option>
          <option value="CONFIRMED">CONFIRMED</option>
          <option value="UNKNOWN">UNKNOWN</option>
          <option value="FAILED">FAILED</option>
        </select>
        <button class="btn" @click="load">查询</button>
      </div>
      <div class="export-row">
        <span class="muted">导出评估+动作（按上方岗位/日期条件）：</span>
        <select v-model="exportConclusion" class="select export-conclusion">
          <option value="">全部结论</option>
          <option value="QUALIFIED">合格</option>
          <option value="REJECTED">不合格</option>
          <option value="UNCERTAIN">待复核</option>
        </select>
        <button class="btn btn-secondary" @click="doExport('csv')">导出 CSV</button>
        <button class="btn btn-secondary" @click="doExport('json')">导出 JSON</button>
        <span v-if="exportMessage" class="muted">{{ exportMessage }}</span>
      </div>
      <div v-if="error" class="error-text">{{ error }}</div>
    </div>

    <div class="card">
      <h2>动作日志（{{ actionRows.length }}）</h2>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>候选人</th>
              <th>动作</th>
              <th>状态</th>
              <th>理由</th>
              <th>错误</th>
              <th>创建时间</th>
              <th>发出时间</th>
              <th>确认时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in actionRows" :key="row.id">
              <td>{{ row.id }}</td>
              <td>{{ row.candidateName ?? row.fingerprint ?? '-' }}</td>
              <td>{{ row.action === 'GREET' ? '打招呼' : row.action === 'REJECT' ? '不合适' : row.action }}</td>
              <td>
                <span :class="statusBadge(row.status)">{{ row.status }}</span>
              </td>
              <td class="cell-clip" :title="row.reason ?? ''">{{ row.reason ?? '-' }}</td>
              <td class="cell-clip" :title="row.error ?? ''">{{ row.error ?? '-' }}</td>
              <td class="muted">{{ row.createdAt }}</td>
              <td class="muted">{{ row.sentAt ?? '-' }}</td>
              <td class="muted">{{ row.confirmedAt ?? '-' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!actionRows.length" class="muted">无记录。</p>
      </div>
    </div>

    <div class="card">
      <h2>CDP 协议审计（{{ cdpRows.length }}）</h2>
      <div class="filters">
        <input v-model="cdpMethod" class="input" placeholder="方法名（如 Page.captureScreenshot）" />
        <select v-model="cdpStatus" class="select">
          <option value="">全部状态</option>
          <option value="OK">OK</option>
          <option value="ERROR">ERROR</option>
          <option value="FORBIDDEN">FORBIDDEN</option>
        </select>
        <button class="btn" @click="loadCdp">查询</button>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>方法</th>
              <th>参数摘要</th>
              <th>耗时(ms)</th>
              <th>状态</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in cdpRows" :key="row.id">
              <td>{{ row.id }}</td>
              <td class="mono">{{ row.method }}</td>
              <td class="mono cell-clip" :title="row.paramsSummary">{{ row.paramsSummary }}</td>
              <td>{{ row.durationMs ?? '-' }}</td>
              <td>
                <span :class="row.status === 'OK' ? 'badge badge-ok' : 'badge badge-fail'">{{ row.status }}</span>
              </td>
              <td class="muted">{{ row.at }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!cdpRows.length" class="muted">无记录。</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { ActionLogRow, CdpAuditRow, JobRecord } from '@shared/ipc'

const jobs = ref<JobRecord[]>([])
const actionRows = ref<ActionLogRow[]>([])
const cdpRows = ref<CdpAuditRow[]>([])
const error = ref('')
const exportMessage = ref('')
const exportConclusion = ref('')
const cdpMethod = ref('')
const cdpStatus = ref('')

const filters = ref({
  jobId: null as number | null,
  dateFrom: '',
  dateTo: '',
  candidate: '',
  action: '',
  status: '',
})

function statusBadge(status: string): string {
  if (status === 'CONFIRMED') return 'badge badge-ok'
  if (status === 'UNKNOWN' || status === 'SENT') return 'badge badge-warn'
  if (status === 'FAILED') return 'badge badge-fail'
  return 'badge badge-neutral'
}

async function load() {
  error.value = ''
  try {
    actionRows.value = await window.bossResume.audit.actions({
      ...(filters.value.jobId !== null ? { jobId: filters.value.jobId } : {}),
      ...(filters.value.dateFrom ? { dateFrom: filters.value.dateFrom } : {}),
      ...(filters.value.dateTo ? { dateTo: filters.value.dateTo } : {}),
      ...(filters.value.candidate.trim() ? { candidate: filters.value.candidate.trim() } : {}),
      ...(filters.value.action ? { action: filters.value.action } : {}),
      ...(filters.value.status ? { status: filters.value.status } : {}),
    })
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function loadCdp() {
  error.value = ''
  try {
    cdpRows.value = await window.bossResume.audit.cdp({
      ...(cdpMethod.value.trim() ? { method: cdpMethod.value.trim() } : {}),
      ...(cdpStatus.value ? { status: cdpStatus.value } : {}),
    })
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function doExport(format: 'csv' | 'json') {
  error.value = ''
  exportMessage.value = ''
  try {
    const result = await window.bossResume.exporter.evaluations({
      format,
      ...(filters.value.jobId !== null ? { jobId: filters.value.jobId } : {}),
      ...(filters.value.dateFrom ? { dateFrom: filters.value.dateFrom } : {}),
      ...(filters.value.dateTo ? { dateTo: filters.value.dateTo } : {}),
      ...(exportConclusion.value ? { conclusion: exportConclusion.value } : {}),
    })
    if (result.cancelled) {
      exportMessage.value = '已取消'
    } else if (!result.ok) {
      error.value = result.error ?? '导出失败'
    } else {
      exportMessage.value = `已导出 ${result.rowCount} 行到 ${result.path}`
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(async () => {
  jobs.value = await window.bossResume.jobs.list()
  await Promise.all([load(), loadCdp()])
})
</script>

<style scoped>
.filters {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.filters .input,
.filters .select {
  width: 180px;
}
.export-row {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  margin-top: 4px;
}
.export-conclusion {
  width: 120px;
}
.error-text {
  color: var(--c-danger);
  font-size: 12px;
  margin-top: 8px;
}
.table-wrap {
  overflow-x: auto;
}
.cell-clip {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

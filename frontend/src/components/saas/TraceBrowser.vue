<template>
  <div class="h-full flex flex-col bg-canvas">
    <div class="flex items-center justify-between px-6 py-4 bg-white border-b border-default">
      <h1 class="text-xl font-bold text-default">追踪查看</h1>
      <div class="flex items-center gap-3">
        <input
          v-model="search"
          @keyup.enter="loadData"
          placeholder="搜索会话ID"
          class="px-3 py-1.5 border border-default rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 w-48"
        />
        <input
          v-model="filterTenantId"
          @keyup.enter="loadData"
          placeholder="租户ID"
          class="px-3 py-1.5 border border-default rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 w-36"
        />
        <input
          v-model="filterUserId"
          @keyup.enter="loadData"
          placeholder="用户ID"
          class="px-3 py-1.5 border border-default rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 w-36"
        />
        <select v-model="filterStatus" @change="loadData"
          class="px-3 py-1.5 border border-default rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500">
          <option value="">全部状态</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="cancelled">已取消</option>
        </select>
        <select v-model="filterSourceType" @change="loadData"
          class="px-3 py-1.5 border border-default rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500">
          <option value="">全部来源</option>
          <option value="chat">Web</option>
          <option value="wecom">企微</option>
          <option value="wecom_kf">企微客服</option>
          <option value="dingtalk">钉钉</option>
          <option value="feishu">飞书</option>
        </select>
        <select v-model="timeRange" @change="loadData"
          class="px-3 py-1.5 border border-default rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500">
          <option value="">全部时间</option>
          <option value="1h">最近1小时</option>
          <option value="24h">最近24小时</option>
          <option value="7d">最近7天</option>
          <option value="30d">最近30天</option>
        </select>
        <button @click="loadData" :disabled="loading"
          class="px-3 py-1.5 bg-primary-600 text-white rounded-lg text-sm hover:bg-primary-700 transition-colors disabled:opacity-50">
          刷新
        </button>
      </div>
    </div>

    <div class="flex-1 overflow-auto p-6">
      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>
      <template v-else>
        <div class="bg-white rounded-xl shadow-sm overflow-hidden">
          <BaseTable
            :columns="columns"
            :data="sessions"
            row-key="session_id"
            :on-row-click="(row: any) => goToSession(row.session_id)"
          >
            <template #seq="{ index }">
              {{ (page - 1) * pageSize + index + 1 }}
            </template>
            <template #session_id="{ row }">
              <span class="text-xs text-default font-mono break-all" :title="row.session_id">
                {{ row.session_id.length > 60 ? row.session_id.slice(0, 60) + '...' : row.session_id }}
              </span>
            </template>
            <template #tenant_id="{ row }">
              <span class="text-xs text-default font-mono break-all" :title="row.tenant_id || ''">
                {{ row.tenant_id || '-' }}
              </span>
            </template>
            <template #user_id="{ row }">
              <span class="text-xs text-default font-mono break-all" :title="row.user_id || ''">
                {{ row.user_id || '-' }}
              </span>
            </template>
            <template #first_input="{ row }">
              <span class="text-sm text-default break-words whitespace-normal">
                {{ row.first_content || row.first_input || '-' }}
              </span>
            </template>
            <template #source_type="{ row }">
              <span :class="sourceBadgeClass(row.source_type)"
                class="inline-block px-1.5 py-0.5 rounded text-xs font-medium">
                {{ sourceLabel(row.source_type) }}
              </span>
            </template>
            <template #trace_count="{ row }">
              <span class="text-xs text-default">{{ row.trace_count }}</span>
            </template>
            <template #total_tokens="{ row }">
              <span class="text-xs text-default">{{ formatTokens(row.total_tokens) }}</span>
            </template>
            <template #error_count="{ row }">
              <span v-if="row.error_count > 0" class="text-danger-600 font-medium">{{ row.error_count }}</span>
              <span v-else class="text-muted">0</span>
            </template>
            <template #last_trace_at="{ row }">
              <span class="text-xs text-muted">{{ formatDateTime(row.last_trace_at) }}</span>
            </template>
            <template #empty>暂无追踪数据</template>
          </BaseTable>

          <div v-if="total > 0" class="px-4 py-3 border-t border-default flex items-center justify-center">
            <BasePagination
              :total="total"
              v-model:current-page="page"
              v-model:page-size="pageSize"
              @change="loadData"
            />
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getTracedSessions, type SessionSummary } from '@/api/monitor'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'

const router = useRouter()

const sessions = ref<SessionSummary[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const filterStatus = ref('')
const filterSourceType = ref('')
const timeRange = ref('')
const search = ref('')
const filterTenantId = ref('')
const filterUserId = ref('')

const columns: TableColumn[] = [
  { key: 'seq', label: '序号', width: '48px', thAlign: 'center', tdAlign: 'center' },
  { key: 'session_id', label: '会话ID', width: '280px', tdAlign: 'left' },
  { key: 'tenant_id', label: '租户', width: '160px', tdAlign: 'left' },
  { key: 'user_id', label: '用户', width: '192px', tdAlign: 'left' },
  { key: 'first_input', label: '首次输入', tdAlign: 'left' },
  { key: 'source_type', label: '来源', width: '96px', tdAlign: 'left' },
  { key: 'trace_count', label: 'Trace数', width: '80px', tdAlign: 'left' },
  { key: 'total_tokens', label: 'Token', width: '96px', tdAlign: 'left' },
  { key: 'error_count', label: '错误', width: '64px', tdAlign: 'left' },
  { key: 'last_trace_at', label: '最后活跃', width: '160px', tdAlign: 'left' },
]

const SOURCE_LABELS: Record<string, string> = {
  chat: 'Web',
  wecom: '企微',
  wecom_kf: '企微客服',
  dingtalk: '钉钉',
  feishu: '飞书',
}

const SOURCE_BADGE_CLASSES: Record<string, string> = {
  chat: 'bg-gray-100 text-gray-700',
  wecom: 'bg-success-100 text-success-700',
  wecom_kf: 'bg-success-100 text-success-700',
  dingtalk: 'bg-primary-100 text-primary-700',
  feishu: 'bg-warning-100 text-warning-700',
}

function sourceLabel(s: string | null): string {
  if (!s) return '-'
  return SOURCE_LABELS[s] || s
}

function sourceBadgeClass(s: string | null): string {
  if (!s) return 'bg-gray-100 text-gray-700'
  return SOURCE_BADGE_CLASSES[s] || 'bg-gray-100 text-gray-700'
}

function goToSession(sessionId: string) {
  router.push(`/portal/monitoring/${sessionId}`)
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}

function formatDateTime(ts: string | null): string {
  if (!ts) return '-'
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadData() {
  loading.value = true
  try {
    const res = await getTracedSessions({
      page: page.value,
      page_size: pageSize.value,
      status: filterStatus.value || undefined,
      source_type: filterSourceType.value || undefined,
      time_range: timeRange.value || undefined,
      search: search.value || undefined,
      tenant_id: filterTenantId.value || undefined,
      user_id: filterUserId.value || undefined,
    })
    if (res.success) {
      sessions.value = res.data
      total.value = res.total
    }
  } catch (e) {
    console.error('Failed to load sessions:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

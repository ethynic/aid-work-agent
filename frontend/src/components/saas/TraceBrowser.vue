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
        <div class="bg-white rounded-xl shadow-sm border border-default overflow-hidden">
          <table v-if="sessions.length > 0" class="w-full">
            <thead class="bg-canvas sticky top-0">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-12">序号</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-56">会话ID</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-48">租户</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-48">用户</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">首次输入</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-24">来源</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-20">Trace数</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-24">Token</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-16">错误</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted w-40">最后活跃</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-default">
              <tr v-for="(s, idx) in sessions" :key="s.session_id"
                class="hover:bg-surface-hover cursor-pointer" @click="goToSession(s.session_id)">
                <td class="px-4 py-2 text-xs text-muted">{{ (page - 1) * pageSize + idx + 1 }}</td>
                <td class="px-4 py-2 text-xs text-default font-mono break-all" :title="s.session_id">{{ s.session_id }}</td>
                <td class="px-4 py-2 text-xs text-default font-mono break-all" :title="s.tenant_id || ''">{{ s.tenant_id || '-' }}</td>
                <td class="px-4 py-2 text-xs text-default font-mono break-all" :title="s.user_id || ''">{{ s.user_id || '-' }}</td>
                <td class="px-4 py-2 text-sm text-default break-words whitespace-normal">{{ s.first_content || s.first_input || '-' }}</td>
                <td class="px-4 py-2 text-sm">
                  <span :class="sourceBadgeClass(s.source_type)"
                    class="inline-block px-1.5 py-0.5 rounded text-xs font-medium">
                    {{ sourceLabel(s.source_type) }}
                  </span>
                </td>
                <td class="px-4 py-2 text-sm text-default">{{ s.trace_count }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ formatTokens(s.total_tokens) }}</td>
                <td class="px-4 py-2 text-sm">
                  <span v-if="s.error_count > 0" class="text-danger-600 font-medium">{{ s.error_count }}</span>
                  <span v-else class="text-muted">0</span>
                </td>
                <td class="px-4 py-2 text-xs text-muted">{{ formatDateTime(s.last_trace_at) }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="text-center py-12 text-muted">暂无追踪数据</div>

          <div v-if="total > 0" class="px-4 py-3 border-t border-default flex items-center justify-center">
            <BasePagination
              :total="total"
              v-model:current-page="page"
              v-model:page-size="pageSize"
              :show-size-changer="false"
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

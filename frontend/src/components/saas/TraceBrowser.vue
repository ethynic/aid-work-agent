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
        <select v-model="filterStatus" @change="loadData"
          class="px-3 py-1.5 border border-default rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500">
          <option value="">全部状态</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="cancelled">已取消</option>
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
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">会话ID</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">首次输入</th>
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
                <td class="px-4 py-2 text-xs text-default font-mono">{{ s.session_id.substring(0, 16) }}...</td>
                <td class="px-4 py-2 text-sm text-default max-w-xs truncate">{{ s.first_input || '-' }}</td>
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

          <div v-if="totalPages > 1" class="px-4 py-3 border-t border-default flex items-center justify-between">
            <span class="text-sm text-muted">共 {{ total }} 个会话</span>
            <div class="flex items-center gap-2">
              <button @click="page--; loadData()" :disabled="page <= 1"
                class="px-2 py-1 text-xs border border-default rounded hover:bg-surface-hover disabled:opacity-50">
                上一页
              </button>
              <span class="text-sm text-default">{{ page }} / {{ totalPages }}</span>
              <button @click="page++; loadData()" :disabled="page >= totalPages"
                class="px-2 py-1 text-xs border border-default rounded hover:bg-surface-hover disabled:opacity-50">
                下一页
              </button>
            </div>
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

const router = useRouter()

const sessions = ref<SessionSummary[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const total = ref(0)
const totalPages = ref(0)
const filterStatus = ref('')
const timeRange = ref('')
const search = ref('')

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
      page_size: pageSize,
      status: filterStatus.value || undefined,
      time_range: timeRange.value || undefined,
      search: search.value || undefined,
    })
    if (res.success) {
      sessions.value = res.data
      total.value = res.total
      totalPages.value = res.total_pages
    }
  } catch (e) {
    console.error('Failed to load sessions:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

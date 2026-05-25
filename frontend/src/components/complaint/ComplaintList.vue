<template>
  <div class="min-h-full bg-[var(--bg-primary)] text-[var(--text-primary)] transition-colors duration-200">
    <!-- Header -->
    <div class="px-6 pt-6 pb-4">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-xl font-bold tracking-tight" style="font-family: 'Noto Sans SC', 'DM Sans', sans-serif;">
            投诉记录
          </h1>
          <p class="text-sm text-[var(--text-tertiary)] mt-0.5">投诉全生命周期管理</p>
        </div>
      </div>

      <!-- Stats Cards -->
      <div class="grid grid-cols-4 gap-4">
        <div
          v-for="stat in statsCards"
          :key="stat.key"
          class="relative overflow-hidden rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 group hover:border-[var(--accent-primary)]/30 transition-colors"
        >
          <div class="flex items-start justify-between">
            <div>
              <p class="text-xs font-medium text-[var(--text-tertiary)] uppercase tracking-wider">{{ stat.label }}</p>
              <p class="text-2xl font-bold mt-1 tabular-nums">{{ stat.value }}</p>
            </div>
            <div class="w-9 h-9 rounded-lg flex items-center justify-center" :class="stat.iconBg">
              <svg class="w-4.5 h-4.5" :class="stat.iconColor" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" :d="stat.iconPath" />
              </svg>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="px-6 pb-4">
      <div class="flex items-center gap-3 p-3 rounded-xl bg-[var(--bg-secondary)] border border-[var(--border-primary)]">
        <select
          v-model="filters.status"
          class="text-sm rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] text-[var(--text-primary)] px-3 py-1.5 focus:ring-1 focus:ring-[var(--accent-primary)] focus:border-[var(--accent-primary)] outline-none"
        >
          <option value="">全部状态</option>
          <option v-for="(label, key) in statusLabels" :key="key" :value="key">{{ label }}</option>
        </select>
        <select
          v-model="filters.category"
          class="text-sm rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] text-[var(--text-primary)] px-3 py-1.5 focus:ring-1 focus:ring-[var(--accent-primary)] focus:border-[var(--accent-primary)] outline-none"
        >
          <option value="">全部分类</option>
          <option v-for="(label, key) in categoryLabels" :key="key" :value="key">{{ label }}</option>
        </select>
        <select
          v-model="filters.urgency"
          class="text-sm rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] text-[var(--text-primary)] px-3 py-1.5 focus:ring-1 focus:ring-[var(--accent-primary)] focus:border-[var(--accent-primary)] outline-none"
        >
          <option value="">全部紧急程度</option>
          <option v-for="(label, key) in urgencyLabels" :key="key" :value="key">{{ label }}</option>
        </select>
        <div class="relative flex-1 max-w-xs">
          <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"/></svg>
          <input
            v-model="filters.keyword"
            type="text"
            placeholder="搜索投诉内容、投诉ID..."
            class="w-full text-sm rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] text-[var(--text-primary)] pl-9 pr-3 py-1.5 focus:ring-1 focus:ring-[var(--accent-primary)] focus:border-[var(--accent-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
            @keyup.enter="loadComplaints"
          />
        </div>
        <div class="flex-1" />
        <button
          @click="loadComplaints"
          class="text-sm px-3 py-1.5 rounded-lg bg-[var(--accent-primary)] text-white hover:brightness-110 transition-all"
        >
          查询
        </button>
        <button
          @click="resetFilters"
          class="text-sm px-3 py-1.5 rounded-lg border border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
        >
          重置
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="px-6 pb-6">
      <div class="rounded-xl border border-[var(--border-primary)] overflow-hidden bg-[var(--bg-secondary)]">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-[var(--border-primary)] bg-[var(--bg-primary)]/50">
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider w-12">序号</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">投诉ID</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">分类</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">紧急程度</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">状态</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">情绪</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">投诉内容</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">交互数</th>
              <th class="text-left px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">创建时间</th>
              <th class="text-right px-4 py-3 font-medium text-[var(--text-tertiary)] text-xs uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="10" class="text-center py-12 text-[var(--text-tertiary)]">
                <div class="flex items-center justify-center gap-2">
                  <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                  加载中...
                </div>
              </td>
            </tr>
            <tr v-else-if="complaints.length === 0">
              <td colspan="10" class="text-center py-12 text-[var(--text-tertiary)]">
                暂无投诉数据
              </td>
            </tr>
            <tr
              v-for="(item, index) in complaints"
              :key="item.complaint_id"
              class="border-b border-[var(--border-primary)] last:border-0 hover:bg-[var(--bg-hover)] transition-colors cursor-pointer"
              @click="openDetail(item)"
            >
              <td class="px-4 py-3 text-[var(--text-tertiary)] tabular-nums">{{ (page - 1) * pageSize + index + 1 }}</td>
              <td class="px-4 py-3 font-mono text-xs text-[var(--text-secondary)]">{{ item.complaint_id }}</td>
              <td class="px-4 py-3">
                <div class="font-medium text-[var(--text-primary)]">{{ categoryLabels[item.category] || item.category }}</div>
                <div v-if="item.sub_category" class="text-xs text-[var(--text-tertiary)] mt-0.5">{{ item.sub_category }}</div>
              </td>
              <td class="px-4 py-3">
                <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="urgencyBadgeClass(item.urgency)">
                  {{ urgencyLabels[item.urgency] || item.urgency }}
                </span>
              </td>
              <td class="px-4 py-3">
                <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="statusBadgeClass(item.status)">
                  {{ statusLabels[item.status] || item.status }}
                </span>
              </td>
              <td class="px-4 py-3">
                <span v-if="item.customer_emotion" class="text-xs" :class="emotionClass(item.customer_emotion)">
                  {{ emotionLabels[item.customer_emotion] || item.customer_emotion }}
                </span>
                <span v-else class="text-[var(--text-tertiary)]">-</span>
              </td>
              <td class="px-4 py-3 text-[var(--text-secondary)] max-w-[200px] truncate">
                {{ item.description_short || item.description }}
              </td>
              <td class="px-4 py-3 text-center tabular-nums text-[var(--text-secondary)]">{{ item.interaction_count || 0 }}</td>
              <td class="px-4 py-3 text-[var(--text-tertiary)] text-xs tabular-nums">{{ formatDate(item.created_at) }}</td>
              <td class="px-4 py-3 text-right" @click.stop>
                <button
                  @click="openDetail(item)"
                  class="p-1.5 rounded-md text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors"
                  title="查看详情"
                >
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="flex items-center justify-between mt-4">
        <p class="text-sm text-[var(--text-tertiary)]">
          共 <span class="font-medium text-[var(--text-secondary)]">{{ total }}</span> 条投诉
        </p>
        <div class="flex items-center gap-1">
          <button
            :disabled="page <= 1"
            @click="page--; loadComplaints()"
            class="px-3 py-1.5 text-sm rounded-lg border border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >上一页</button>
          <template v-for="p in visiblePages" :key="p">
            <button
              v-if="p !== '...'"
              @click="page = Number(p); loadComplaints()"
              class="w-8 h-8 text-sm rounded-lg transition-colors"
              :class="p === page ? 'bg-[var(--accent-primary)] text-white font-medium' : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'"
            >{{ p }}</button>
            <span v-else class="text-[var(--text-tertiary)] px-1">...</span>
          </template>
          <button
            :disabled="page >= totalPages"
            @click="page++; loadComplaints()"
            class="px-3 py-1.5 text-sm rounded-lg border border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >下一页</button>
        </div>
      </div>
    </div>

    <!-- Detail Side Panel -->
    <Teleport to="body">
      <div v-if="detail" class="fixed inset-0 z-50 flex justify-end">
        <div class="absolute inset-0 bg-black/30 backdrop-blur-sm" @click="detail = null" />
        <div class="relative w-full max-w-lg bg-[var(--bg-secondary)] border-l border-[var(--border-primary)] shadow-xl overflow-y-auto">
          <div class="sticky top-0 z-10 bg-[var(--bg-secondary)] border-b border-[var(--border-primary)] px-6 py-4 flex items-center justify-between">
            <h2 class="text-lg font-semibold">投诉详情</h2>
            <button @click="detail = null" class="p-1.5 rounded-md text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-6">
            <!-- Basic Info -->
            <div>
              <h3 class="text-sm font-medium text-[var(--text-tertiary)] mb-3 uppercase tracking-wider">基本信息</h3>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div><span class="text-[var(--text-tertiary)]">投诉ID：</span><span class="font-mono text-xs text-[var(--text-primary)]">{{ detail.complaint_id }}</span></div>
                <div><span class="text-[var(--text-tertiary)]">分类：</span><span class="text-[var(--text-primary)]">{{ categoryLabels[detail.category] || detail.category }}</span></div>
                <div v-if="detail.sub_category"><span class="text-[var(--text-tertiary)]">子分类：</span><span class="text-[var(--text-primary)]">{{ detail.sub_category }}</span></div>
                <div><span class="text-[var(--text-tertiary)]">状态：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="statusBadgeClass(detail.status)">{{ statusLabels[detail.status] || detail.status }}</span>
                </div>
                <div><span class="text-[var(--text-tertiary)]">紧急程度：</span>
                  <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="urgencyBadgeClass(detail.urgency)">{{ urgencyLabels[detail.urgency] || detail.urgency }}</span>
                </div>
                <div v-if="detail.customer_emotion"><span class="text-[var(--text-tertiary)]">客户情绪：</span><span :class="emotionClass(detail.customer_emotion)">{{ emotionLabels[detail.customer_emotion] || detail.customer_emotion }}</span></div>
                <div v-if="detail.order_id"><span class="text-[var(--text-tertiary)]">订单ID：</span><span class="text-[var(--text-primary)]">{{ detail.order_id }}</span></div>
                <div v-if="detail.escalated_to"><span class="text-[var(--text-tertiary)]">升级至：</span><span class="text-[var(--text-primary)]">{{ detail.escalated_to }}</span></div>
                <div><span class="text-[var(--text-tertiary)]">创建时间：</span><span class="text-[var(--text-primary)] tabular-nums">{{ formatDate(detail.created_at) }}</span></div>
                <div v-if="detail.resolved_at"><span class="text-[var(--text-tertiary)]">解决时间：</span><span class="text-[var(--text-primary)] tabular-nums">{{ formatDate(detail.resolved_at) }}</span></div>
              </div>
            </div>

            <!-- Description -->
            <div>
              <h3 class="text-sm font-medium text-[var(--text-tertiary)] mb-3 uppercase tracking-wider">投诉内容</h3>
              <p class="text-sm text-[var(--text-secondary)] bg-[var(--bg-primary)]/50 rounded-lg p-3 border border-[var(--border-primary)]">{{ detail.description }}</p>
            </div>

            <!-- Resolution -->
            <div v-if="detail.resolution">
              <h3 class="text-sm font-medium text-[var(--text-tertiary)] mb-3 uppercase tracking-wider">处理结果</h3>
              <p class="text-sm text-[var(--text-secondary)] bg-emerald-50 dark:bg-emerald-900/10 rounded-lg p-3 border border-emerald-200 dark:border-emerald-800/30">{{ detail.resolution }}</p>
            </div>

            <!-- Interactions -->
            <div>
              <h3 class="text-sm font-medium text-[var(--text-tertiary)] mb-3 uppercase tracking-wider">交互记录 ({{ detail.interactions?.length || 0 }})</h3>
              <div v-if="!detail.interactions?.length" class="text-sm text-[var(--text-tertiary)] py-2">暂无交互记录</div>
              <div v-else class="space-y-3">
                <div
                  v-for="interaction in detail.interactions"
                  :key="interaction.id"
                  class="p-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)]/50"
                >
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-xs font-medium" :class="senderTypeClass(interaction.sender_type)">{{ senderTypeLabels[interaction.sender_type] || interaction.sender_type }}</span>
                    <span class="text-xs text-[var(--text-tertiary)] tabular-nums">{{ formatDate(interaction.created_at) }}</span>
                  </div>
                  <p class="text-sm text-[var(--text-secondary)]">{{ interaction.content }}</p>
                </div>
              </div>
            </div>

            <!-- Followups -->
            <div v-if="detail.followups?.length">
              <h3 class="text-sm font-medium text-[var(--text-tertiary)] mb-3 uppercase tracking-wider">跟进任务</h3>
              <div class="space-y-2">
                <div
                  v-for="followup in detail.followups"
                  :key="followup.id"
                  class="p-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)]/50"
                >
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-sm text-[var(--text-primary)]">{{ followup.action }}</span>
                    <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="followupStatusClass(followup.status)">{{ followupStatusLabel(followup.status) }}</span>
                  </div>
                  <div v-if="followup.assigned_to" class="text-xs text-[var(--text-tertiary)]">负责人: {{ followup.assigned_to }}</div>
                  <div v-if="followup.due_date" class="text-xs text-[var(--text-tertiary)]">截止: {{ formatDate(followup.due_date) }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { complaintAPI, type Complaint } from '@/api/complaint'

// --- Constants ---
const statusLabels: Record<string, string> = {
  open: '待处理', classifying: '分类中', in_progress: '处理中',
  resolved: '已解决', closed: '已关闭', escalated: '已升级',
}
const categoryLabels: Record<string, string> = {
  '产品质量': '产品质量', '服务态度': '服务态度', '物流配送': '物流配送',
  '虚假宣传': '虚假宣传', '售后服务': '售后服务', '价格争议': '价格争议',
  '隐私安全': '隐私安全', '其他': '其他',
}
const urgencyLabels: Record<string, string> = {
  normal: '一般', high: '紧急', urgent: '非常紧急', critical: '危急',
}
const emotionLabels: Record<string, string> = {
  neutral: '平静', dissatisfied: '不满', angry: '愤怒', furious: '极度愤怒',
}
const senderTypeLabels: Record<string, string> = {
  customer: '客户', agent: 'AI助手', system: '系统', supervisor: '主管',
}

// --- State ---
const complaints = ref<Complaint[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const detail = ref<Complaint | null>(null)
const filters = ref({ status: '', category: '', urgency: '', keyword: '' })

// --- Computed ---
const totalPages = computed(() => Math.ceil(total.value / pageSize.value) || 1)
const visiblePages = computed(() => {
  const pages: (number | string)[] = []
  const t = totalPages.value
  const p = page.value
  if (t <= 7) { for (let i = 1; i <= t; i++) pages.push(i) }
  else {
    pages.push(1)
    if (p > 3) pages.push('...')
    for (let i = Math.max(2, p - 1); i <= Math.min(t - 1, p + 1); i++) pages.push(i)
    if (p < t - 2) pages.push('...')
    pages.push(t)
  }
  return pages
})

const statsCards = computed(() => [
  { key: 'total', label: '总投诉', value: total.value, iconBg: 'bg-slate-100 dark:bg-slate-700/50', iconColor: 'text-slate-600 dark:text-slate-400', iconPath: 'M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z' },
  { key: 'open', label: '待处理', value: complaints.value.filter(c => c.status === 'open').length, iconBg: 'bg-blue-100 dark:bg-blue-900/30', iconColor: 'text-blue-600 dark:text-blue-400', iconPath: 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z' },
  { key: 'escalated', label: '已升级', value: complaints.value.filter(c => c.status === 'escalated').length, iconBg: 'bg-red-100 dark:bg-red-900/30', iconColor: 'text-red-600 dark:text-red-400', iconPath: 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z' },
  { key: 'resolved', label: '已解决', value: complaints.value.filter(c => c.status === 'resolved' || c.status === 'closed').length, iconBg: 'bg-emerald-100 dark:bg-emerald-900/30', iconColor: 'text-emerald-600 dark:text-emerald-400', iconPath: 'M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
])

// --- Methods ---
function statusBadgeClass(status: string) {
  const map: Record<string, string> = {
    open: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
    classifying: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
    in_progress: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
    resolved: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
    closed: 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400',
    escalated: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  }
  return map[status] || 'bg-slate-100 text-slate-700'
}

function urgencyBadgeClass(urgency: string) {
  const map: Record<string, string> = {
    normal: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
    high: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
    urgent: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
    critical: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  }
  return map[urgency] || 'bg-slate-100 text-slate-700'
}

function emotionClass(emotion: string) {
  const map: Record<string, string> = {
    neutral: 'text-slate-500', dissatisfied: 'text-amber-600 dark:text-amber-400',
    angry: 'text-red-600 dark:text-red-400', furious: 'text-red-700 dark:text-red-300 font-medium',
  }
  return map[emotion] || 'text-slate-500'
}

function senderTypeClass(type: string) {
  const map: Record<string, string> = {
    customer: 'text-blue-600 dark:text-blue-400', agent: 'text-emerald-600 dark:text-emerald-400',
    system: 'text-slate-500', supervisor: 'text-purple-600 dark:text-purple-400',
  }
  return map[type] || 'text-slate-500'
}

function followupStatusClass(status: string) {
  const map: Record<string, string> = {
    pending: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
    in_progress: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
    done: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
    skipped: 'bg-slate-100 text-slate-500',
  }
  return map[status] || 'bg-slate-100 text-slate-500'
}

function followupStatusLabel(status: string) {
  const map: Record<string, string> = { pending: '待处理', in_progress: '进行中', done: '已完成', skipped: '已跳过' }
  return map[status] || status
}

function formatDate(dateStr?: string | null) {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '-' }
}

async function loadComplaints() {
  loading.value = true
  try {
    const params = {
      ...filters.value,
      page: page.value,
      page_size: pageSize.value,
    }
    console.log('[ComplaintList] request params:', params)
    const data = await complaintAPI.listComplaints(params)
    console.log('[ComplaintList] response data:', data)
    complaints.value = data.items
    total.value = data.total
  } catch (e: any) {
    console.error('加载投诉列表失败:', e)
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.value = { status: '', category: '', urgency: '', keyword: '' }
  page.value = 1
  loadComplaints()
}

async function openDetail(item: Complaint) {
  try {
    detail.value = await complaintAPI.getComplaint(item.complaint_id)
  } catch (e: any) {
    alert(e.message || '加载详情失败')
  }
}

onMounted(() => {
  loadComplaints()
})
</script>

<style scoped>
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f8fafc;
  --bg-hover: #f1f5f9;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-tertiary: #94a3b8;
  --border-primary: #e2e8f0;
  --accent-primary: #2563eb;
}
:root.dark, .dark {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-hover: #334155;
  --text-primary: #f1f5f9;
  --text-secondary: #cbd5e1;
  --text-tertiary: #64748b;
  --border-primary: #334155;
  --accent-primary: #3b82f6;
}
</style>

<template>
  <div class="min-h-full bg-surface text-default transition-colors duration-200">
    <!-- Header -->
    <div class="px-6 pt-6 pb-4">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-xl font-bold tracking-tight" style="font-family: 'Noto Sans SC', 'DM Sans', sans-serif;">
            线索管理
          </h1>
          <p class="text-sm text-muted mt-0.5">销售线索全生命周期管理</p>
        </div>
        <div class="flex items-center gap-3">
          <button
            @click="showImportDialog = true"
            class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border border-default bg-canvas text-muted hover:bg-surface-hover transition-colors"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/></svg>
            导入
          </button>
          <button
            @click="handleExport"
            class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border border-default bg-canvas text-muted hover:bg-surface-hover transition-colors"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12M12 16.5V3"/></svg>
            导出
          </button>
          <button
            @click="openAddModal"
            class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:brightness-110 transition-all shadow-sm"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
            添加线索
          </button>
        </div>
      </div>

      <!-- Stats Cards -->
      <div class="grid grid-cols-4 gap-4">
        <div
          v-for="stat in statsCards"
          :key="stat.key"
          class="relative overflow-hidden rounded-xl border border-default bg-canvas p-4 group hover:border-primary-300 transition-colors"
        >
          <div class="flex items-start justify-between">
            <div>
              <p class="text-xs font-medium text-muted uppercase tracking-wider">{{ stat.label }}</p>
              <p class="text-2xl font-bold mt-1 tabular-nums">
                {{ stat.value }}
              </p>
            </div>
            <div class="w-9 h-9 rounded-lg flex items-center justify-center" :class="stat.iconBg">
              <svg class="w-4.5 h-4.5" :class="stat.iconColor" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" :d="stat.iconPath" />
              </svg>
            </div>
          </div>
          <div v-if="stat.subtext" class="text-xs text-muted mt-2">{{ stat.subtext }}</div>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="px-6 pb-4">
      <div class="flex items-center gap-3 p-3 rounded-xl bg-canvas border border-default">
        <select
          v-model="filters.stage"
          class="text-sm rounded-lg border border-default bg-surface text-default px-3 py-1.5 focus:ring-1 focus:ring-primary-600 focus:border-primary-600 outline-none"
        >
          <option value="">全部阶段</option>
          <option v-for="(label, key) in stageLabels" :key="key" :value="key">{{ label }}</option>
        </select>
        <select
          v-model="filters.status"
          class="text-sm rounded-lg border border-default bg-surface text-default px-3 py-1.5 focus:ring-1 focus:ring-primary-600 focus:border-primary-600 outline-none"
        >
          <option value="">全部状态</option>
          <option value="active">活跃</option>
          <option value="converted">已转化</option>
          <option value="lost">已丢失</option>
        </select>
        <div class="relative flex-1 max-w-xs">
          <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"/></svg>
          <input
            v-model="filters.keyword"
            type="text"
            placeholder="搜索公司名、联系人、电话..."
            class="w-full text-sm rounded-lg border border-default bg-surface text-default pl-9 pr-3 py-1.5 focus:ring-1 focus:ring-primary-600 focus:border-primary-600 outline-none placeholder:text-muted"
            @keyup.enter="loadLeads"
          />
        </div>
        <div class="flex-1" />
        <button
          @click="loadLeads"
          class="text-sm px-3 py-1.5 rounded-lg bg-primary-600 text-white hover:brightness-110 transition-all"
        >
          查询
        </button>
        <button
          @click="resetFilters"
          class="text-sm px-3 py-1.5 rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors"
        >
          重置
        </button>
      </div>
    </div>

    <!-- Table -->
    <div class="px-6 pb-6">
      <div class="rounded-xl border border-default overflow-hidden bg-canvas">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-default bg-surface/50">
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">公司名称</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">联系人</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">电话</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">阶段</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">评分</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">下次跟进</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">创建时间</th>
              <th class="text-right px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-if="loading"
            >
              <td colspan="8" class="text-center py-12 text-muted">
                <div class="flex items-center justify-center gap-2">
                  <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                  加载中...
                </div>
              </td>
            </tr>
            <tr v-else-if="leads.length === 0">
              <td colspan="8" class="text-center py-12 text-muted">
                暂无线索数据
              </td>
            </tr>
            <tr
              v-for="lead in leads"
              :key="lead.lead_id"
              class="border-b border-default last:border-0 hover:bg-surface-hover transition-colors cursor-pointer"
              @click="openDetail(lead)"
            >
              <td class="px-4 py-3">
                <div class="font-medium text-default">{{ lead.company_name || '-' }}</div>
                <div v-if="lead.industry" class="text-xs text-muted mt-0.5">{{ lead.industry }}</div>
              </td>
              <td class="px-4 py-3 text-muted">{{ lead.contact_name || '-' }}</td>
              <td class="px-4 py-3 text-muted tabular-nums">{{ lead.phone || '-' }}</td>
              <td class="px-4 py-3">
                <span
                  class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium"
                  :class="stageBadgeClass(lead.stage)"
                >
                  {{ stageLabels[lead.stage as keyof typeof stageLabels] || lead.stage }}
                </span>
              </td>
              <td class="px-4 py-3">
                <div class="flex items-center gap-1.5">
                  <div class="w-12 h-1.5 rounded-full bg-default overflow-hidden">
                    <div class="h-full rounded-full transition-all" :class="scoreBarClass(lead.score)" :style="{ width: `${Math.min(lead.score, 100)}%` }" />
                  </div>
                  <span class="text-xs text-muted tabular-nums">{{ lead.score }}</span>
                </div>
              </td>
              <td class="px-4 py-3 text-muted text-xs tabular-nums">
                <span v-if="isOverdue(lead.next_followup_at)" class="text-danger-500 font-medium">逾期</span>
                <span v-else>{{ formatDate(lead.next_followup_at) }}</span>
              </td>
              <td class="px-4 py-3 text-muted text-xs tabular-nums">{{ formatDate(lead.created_at) }}</td>
              <td class="px-4 py-3 text-right" @click.stop>
                <div class="flex items-center justify-end gap-1">
                  <button
                    @click="openDetail(lead)"
                    class="p-1.5 rounded-md text-muted hover:text-default hover:bg-surface-hover transition-colors"
                    title="查看详情"
                  >
                    <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                  </button>
                  <button
                    @click="confirmDelete(lead)"
                    class="p-1.5 rounded-md text-muted hover:text-danger-500 hover:bg-danger-50 dark:hover:bg-danger-900/20 transition-colors"
                    title="删除"
                  >
                    <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/></svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="flex items-center justify-between mt-4">
        <p class="text-sm text-muted">
          共 <span class="font-medium text-muted">{{ total }}</span> 条线索
        </p>
        <div class="flex items-center gap-1">
          <button
            :disabled="page <= 1"
            @click="page--; loadLeads()"
            class="px-3 py-1.5 text-sm rounded-lg border border-default text-muted hover:bg-surface-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >上一页</button>
          <template v-for="p in visiblePages" :key="p">
            <button
              v-if="p !== '...'"
              @click="page = Number(p); loadLeads()"
              class="w-8 h-8 text-sm rounded-lg transition-colors"
              :class="p === page ? 'bg-primary-600 text-white font-medium' : 'text-muted hover:bg-surface-hover'"
            >{{ p }}</button>
            <span v-else class="text-muted px-1">...</span>
          </template>
          <button
            :disabled="page >= totalPages"
            @click="page++; loadLeads()"
            class="px-3 py-1.5 text-sm rounded-lg border border-default text-muted hover:bg-surface-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >下一页</button>
        </div>
      </div>
    </div>

    <!-- Add Lead Modal -->
    <Teleport to="body">
      <div v-if="showAddModal" class="fixed inset-0 z-50 flex items-center justify-center">
        <div class="absolute inset-0 bg-black/40 backdrop-blur-sm" @click="showAddModal = false" />
        <div class="relative w-full max-w-lg mx-4 bg-canvas rounded-2xl border border-default shadow-xl overflow-hidden">
          <div class="px-6 py-4 border-b border-default">
            <h2 class="text-lg font-semibold">添加线索</h2>
          </div>
          <div class="p-6 space-y-4 max-h-[70vh] overflow-y-auto">
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-xs font-medium text-muted mb-1">公司名称 *</label>
                <input v-model="newLead.company_name" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
              </div>
              <div>
                <label class="block text-xs font-medium text-muted mb-1">联系人 *</label>
                <input v-model="newLead.contact_name" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
              </div>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-xs font-medium text-muted mb-1">电话</label>
                <input v-model="newLead.phone" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
              </div>
              <div>
                <label class="block text-xs font-medium text-muted mb-1">邮箱</label>
                <input v-model="newLead.email" type="email" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
              </div>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-xs font-medium text-muted mb-1">行业</label>
                <input v-model="newLead.industry" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
              </div>
              <div>
                <label class="block text-xs font-medium text-muted mb-1">地区</label>
                <input v-model="newLead.region" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
              </div>
            </div>
            <div>
              <label class="block text-xs font-medium text-muted mb-1">备注</label>
              <textarea v-model="newLead.description" rows="2" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600 resize-none" />
            </div>
          </div>
          <div class="px-6 py-4 border-t border-default flex justify-end gap-3">
            <button @click="showAddModal = false" class="px-4 py-2 text-sm rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors">取消</button>
            <button @click="handleAddLead" :disabled="submitting" class="px-4 py-2 text-sm rounded-lg bg-primary-600 text-white hover:brightness-110 disabled:opacity-50 transition-all">确认添加</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Detail Side Panel -->
    <Teleport to="body">
      <div v-if="selectedLead" class="fixed inset-0 z-50 flex justify-end">
        <div class="absolute inset-0 bg-black/30 backdrop-blur-sm" @click="selectedLead = null" />
        <div class="relative w-full max-w-lg bg-canvas border-l border-default shadow-xl overflow-y-auto">
          <div class="sticky top-0 z-10 bg-canvas border-b border-default px-6 py-4 flex items-center justify-between">
            <h2 class="text-lg font-semibold">线索详情</h2>
            <button @click="selectedLead = null" class="p-1.5 rounded-md text-muted hover:text-default hover:bg-surface-hover transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-6">
            <!-- Basic Info -->
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">基本信息</h3>
              <div class="grid grid-cols-2 gap-3 text-sm">
                <div><span class="text-muted">公司：</span><span class="text-default font-medium">{{ selectedLead.company_name || '-' }}</span></div>
                <div><span class="text-muted">联系人：</span><span class="text-default">{{ selectedLead.contact_name || '-' }}</span></div>
                <div><span class="text-muted">电话：</span><span class="text-default">{{ selectedLead.phone || '-' }}</span></div>
                <div><span class="text-muted">邮箱：</span><span class="text-default">{{ selectedLead.email || '-' }}</span></div>
                <div><span class="text-muted">行业：</span><span class="text-default">{{ selectedLead.industry || '-' }}</span></div>
                <div><span class="text-muted">地区：</span><span class="text-default">{{ selectedLead.region || '-' }}</span></div>
              </div>
            </div>

            <!-- Stage Pipeline -->
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">当前阶段</h3>
              <div class="flex items-center gap-1">
                <template v-for="(_label, key) in stageLabels" :key="key">
                  <div
                    class="flex-1 h-2 rounded-full transition-colors"
                    :class="getStagePipelineClass(key, selectedLead.stage)"
                  />
                </template>
              </div>
              <div class="flex items-center mt-2">
                <span
                  class="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium"
                  :class="stageBadgeClass(selectedLead.stage)"
                >
                  {{ stageLabels[selectedLead.stage as keyof typeof stageLabels] || selectedLead.stage }}
                </span>
                <span class="ml-2 text-xs text-muted">评分 {{ selectedLead.score }}/100</span>
              </div>
            </div>

            <!-- Stage Actions -->
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">阶段操作</h3>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="(label, key) in stageLabels"
                  :key="key"
                  v-show="key !== selectedLead.stage"
                  @click="changeStage(key)"
                  class="px-3 py-1.5 text-xs rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors"
                >
                  {{ label }}
                </button>
              </div>
            </div>

            <!-- Recent Records -->
            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">跟进记录</h3>
              <div v-if="!selectedLead.recent_records?.length" class="text-sm text-muted py-2">暂无跟进记录</div>
              <div v-else class="space-y-3">
                <div
                  v-for="rec in selectedLead.recent_records"
                  :key="rec.record_id"
                  class="p-3 rounded-lg border border-default bg-surface/50"
                >
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-xs font-medium text-muted">{{ followupTypeLabels[rec.followup_type as keyof typeof followupTypeLabels] || rec.followup_type }}</span>
                    <span class="text-xs text-muted tabular-nums">{{ formatDate(rec.followup_at) }}</span>
                  </div>
                  <p class="text-sm text-muted">{{ rec.content }}</p>
                  <div v-if="rec.outcome" class="mt-1">
                    <span class="text-xs" :class="outcomeClass(rec.outcome)">{{ outcomeLabels[rec.outcome as keyof typeof outcomeLabels] || rec.outcome }}</span>
                  </div>
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
import { followupAPI, type Lead, type DashboardData } from '@/api/followup'

// --- Constants ---
const stageLabels: Record<string, string> = {
  new: '新线索', contacting: '联系中', qualified: '有意向',
  proposal: '方案中', negotiation: '谈判中', won: '成交', lost: '丢失',
}
const stageOrder = ['new', 'contacting', 'qualified', 'proposal', 'negotiation', 'won', 'lost']
const followupTypeLabels: Record<string, string> = {
  phone: '电话', email: '邮件', visit: '拜访', wechat: '微信', ai_call: 'AI外呼', other: '其他',
}
const outcomeLabels: Record<string, string> = {
  positive: '积极', neutral: '中性', negative: '消极', no_response: '未回复',
}

// --- State ---
const leads = ref<Lead[]>([])
const loading = ref(false)
const submitting = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const showAddModal = ref(false)
const showImportDialog = ref(false)
const selectedLead = ref<Lead | null>(null)
const dashboard = ref<DashboardData | null>(null)

const filters = ref({ stage: '', status: '', keyword: '' })
const newLead = ref<Record<string, string>>({ company_name: '', contact_name: '', phone: '', email: '', industry: '', region: '', description: '' })

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

const statsCards = computed(() => {
  const byStage = dashboard.value?.by_stage || {}
  return [
    { key: 'new', label: '新线索', value: byStage['new'] || 0, subtext: '', iconBg: 'bg-info-100 dark:bg-info-900/30', iconColor: 'text-info-600 dark:text-info-400', iconPath: 'M12 4.5v15m0 0l6.75-6.75M12 19.5l-6.75-6.75' },
    { key: 'contacting', label: '联系中', value: byStage['contacting'] || 0, subtext: '', iconBg: 'bg-amber-100 dark:bg-amber-900/30', iconColor: 'text-amber-600 dark:text-amber-400', iconPath: 'M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155' },
    { key: 'won', label: '已成交', value: byStage['won'] || 0, subtext: '', iconBg: 'bg-success-100 dark:bg-success-900/30', iconColor: 'text-success-600 dark:text-success-400', iconPath: 'M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
    { key: 'total', label: '总活跃', value: dashboard.value?.total_active || 0, subtext: `${dashboard.value?.overdue_followups || 0} 条逾期跟进`, iconBg: 'bg-canvas', iconColor: 'text-muted', iconPath: 'M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5m.75-9l3-3 2.148 2.148A12.061 12.061 0 0116.5 7.605' },
  ]
})

// --- Methods ---
function stageBadgeClass(stage: string) {
  const map: Record<string, string> = {
    new: 'bg-info-100 text-info-700 dark:bg-info-900/40 dark:text-info-300',
    contacting: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
    qualified: 'bg-success-100 text-success-700 dark:bg-success-900/40 dark:text-success-300',
    proposal: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
    negotiation: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
    won: 'bg-success-100 text-success-700 dark:bg-success-900/40 dark:text-success-300',
    lost: 'bg-danger-100 text-danger-700 dark:bg-danger-900/40 dark:text-danger-300',
  }
  return map[stage] || 'bg-canvas text-muted'
}

function scoreBarClass(score: number) {
  if (score >= 70) return 'bg-success-500'
  if (score >= 40) return 'bg-amber-500'
  return 'bg-default'
}

function getStagePipelineClass(stageKey: string, currentStage: string) {
  const ci = stageOrder.indexOf(currentStage)
  const ki = stageOrder.indexOf(stageKey)
  if (currentStage === 'lost') return ki <= ci ? 'bg-danger-400' : 'bg-default'
  return ki <= ci ? 'bg-primary-600' : 'bg-default'
}

function outcomeClass(outcome: string) {
  const map: Record<string, string> = {
    positive: 'text-success-600 dark:text-success-400',
    neutral: 'text-muted',
    negative: 'text-danger-500',
    no_response: 'text-muted',
  }
  return map[outcome] || 'text-muted'
}

function isOverdue(dateStr?: string) {
  if (!dateStr) return false
  return new Date(dateStr) < new Date()
}

function formatDate(dateStr?: string | null) {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '-' }
}

async function loadLeads() {
  loading.value = true
  try {
    const data = await followupAPI.listLeads({
      ...filters.value,
      page: page.value,
      page_size: pageSize.value,
    })
    leads.value = data.items
    total.value = data.total
  } catch (e: any) {
    console.error('加载线索失败:', e)
  } finally {
    loading.value = false
  }
}

async function loadDashboard() {
  try {
    dashboard.value = await followupAPI.getDashboard()
  } catch { /* ignore */ }
}

function resetFilters() {
  filters.value = { stage: '', status: '', keyword: '' }
  page.value = 1
  loadLeads()
}

function openAddModal() {
  newLead.value = { company_name: '', contact_name: '', phone: '', email: '', industry: '', region: '', description: '' }
  showAddModal.value = true
}

async function handleAddLead() {
  if (!newLead.value.company_name && !newLead.value.contact_name) {
    alert('请至少填写公司名称或联系人')
    return
  }
  submitting.value = true
  try {
    const userId = localStorage.getItem('userId') || ''
    await followupAPI.createLead({ ...newLead.value, user_id: userId } as any)
    showAddModal.value = false
    loadLeads()
    loadDashboard()
  } catch (e: any) {
    alert(e.message || '添加失败')
  } finally {
    submitting.value = false
  }
}

async function openDetail(lead: Lead) {
  try {
    selectedLead.value = await followupAPI.getLead(lead.lead_id)
  } catch (e: any) {
    alert(e.message || '加载详情失败')
  }
}

async function changeStage(stage: string) {
  if (!selectedLead.value) return
  try {
    await followupAPI.updateStage(selectedLead.value.lead_id, stage)
    selectedLead.value.stage = stage
    loadLeads()
    loadDashboard()
  } catch (e: any) {
    alert(e.message || '变更阶段失败')
  }
}

async function confirmDelete(lead: Lead) {
  if (!confirm(`确定要删除线索「${lead.company_name || lead.contact_name}」吗？`)) return
  try {
    await followupAPI.deleteLead(lead.lead_id)
    loadLeads()
    loadDashboard()
  } catch (e: any) {
    alert(e.message || '删除失败')
  }
}

function handleExport() {
  // TODO: Phase 3 - 完整导出功能
  alert('导出功能开发中')
}

// --- Init ---
onMounted(() => {
  loadLeads()
  loadDashboard()
})
</script>

<template>
  <div class="page-container">
    <!-- Header -->
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">线索管理</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">销售线索全生命周期管理</p>
      </div>
      <div class="page-toolbar-right">
        <BaseButton intent="secondary" @click="showImportDialog = true">导入</BaseButton>
        <BaseButton intent="secondary" @click="handleExport">导出</BaseButton>
        <BaseButton @click="openAddModal">添加线索</BaseButton>
      </div>
    </div>

    <!-- Stats Cards -->
    <div class="grid grid-cols-4 gap-4 mb-4">
      <div
        v-for="stat in statsCards"
        :key="stat.key"
        class="relative overflow-hidden rounded-xl border border-default bg-surface p-4 group hover:border-primary-300 transition-colors"
      >
        <div class="flex items-start justify-between">
          <div>
            <p class="text-xs font-medium text-muted uppercase tracking-wider">{{ stat.label }}</p>
            <p class="text-2xl font-bold mt-1 tabular-nums">{{ stat.value }}</p>
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

    <!-- Filters -->
    <div class="flex items-center gap-3 p-3 rounded-xl bg-surface border border-default mb-4">
      <BaseSelect v-model="filters.stage" size="sm" class="w-32">
        <option value="">全部阶段</option>
        <option v-for="(label, key) in stageLabels" :key="key" :value="key">{{ label }}</option>
      </BaseSelect>
      <BaseSelect v-model="filters.status" size="sm" class="w-28">
        <option value="">全部状态</option>
        <option value="active">活跃</option>
        <option value="converted">已转化</option>
        <option value="lost">已丢失</option>
      </BaseSelect>
      <BaseInput v-model="filters.keyword" placeholder="搜索公司名、联系人、电话..." size="sm" class="w-64" @keyup.enter="handleSearch(filters.keyword)" />
      <div class="flex-1" />
      <BaseButton size="sm" @click="handleSearch(filters.keyword)">查询</BaseButton>
      <BaseButton size="sm" intent="secondary" @click="resetFilters">重置</BaseButton>
    </div>

    <!-- Table -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="leads" row-key="lead_id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #company_name="{ row }">
          <div>
            <div class="font-medium text-default">{{ row.company_name || '-' }}</div>
            <div v-if="row.industry" class="text-xs text-muted mt-0.5">{{ row.industry }}</div>
          </div>
        </template>
        <template #contact_name="{ row }">{{ row.contact_name || '-' }}</template>
        <template #phone="{ row }">{{ row.phone || '-' }}</template>
        <template #stage="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="stageBadgeClass(row.stage)">
            {{ stageLabels[row.stage as keyof typeof stageLabels] || row.stage }}
          </span>
        </template>
        <template #score="{ row }">
          <div class="flex items-center gap-1.5">
            <div class="w-12 h-1.5 rounded-full bg-gray-200 overflow-hidden">
              <div class="h-full rounded-full transition-all" :class="scoreBarClass(row.score)" :style="{ width: `${Math.min(row.score, 100)}%` }" />
            </div>
            <span class="text-xs text-muted tabular-nums">{{ row.score }}</span>
          </div>
        </template>
        <template #next_followup_at="{ row }">
          <span v-if="isOverdue(row.next_followup_at)" class="text-danger-500 font-medium text-xs">逾期</span>
          <span v-else class="text-xs text-muted">{{ formatDate(row.next_followup_at) }}</span>
        </template>
        <template #created_at="{ row }">{{ formatDate(row.created_at) }}</template>
        <template #actions="{ row }">
          <div class="flex items-center justify-end gap-1">
            <BaseButton intent="ghost" size="sm" @click="openDetail(row)">详情</BaseButton>
            <BaseButton intent="danger" size="sm" @click="confirmDelete(row)">删除</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            加载中...
          </div>
          <span v-else>暂无线索数据</span>
        </template>
      </BaseTable>
    </div>

    <!-- Pagination -->
    <BasePagination
      v-if="total > 0"
      :total="total"
      v-model:current-page="currentPage"
      :page-size="pageSize"
    />

    <!-- Add Lead Modal -->
    <BaseModal v-model="showAddModal" title="添加线索" size="lg">
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="text-sm text-muted mb-1 block">公司名称 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="newLead.company_name" placeholder="请输入公司名称" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">联系人 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="newLead.contact_name" placeholder="请输入联系人" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">电话</label>
          <BaseInput v-model="newLead.phone" placeholder="请输入电话" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">邮箱</label>
          <BaseInput v-model="newLead.email" placeholder="请输入邮箱" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">行业</label>
          <BaseInput v-model="newLead.industry" placeholder="请输入行业" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">地区</label>
          <BaseInput v-model="newLead.region" placeholder="请输入地区" />
        </div>
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">备注</label>
        <textarea v-model="newLead.description" rows="2" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600 resize-none" />
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showAddModal = false">取消</BaseButton>
        <BaseButton :disabled="submitting" @click="handleAddLead">{{ submitting ? '添加中...' : '确认添加' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- Detail Side Panel -->
    <Teleport to="body">
      <div v-if="selectedLead" class="fixed inset-0 z-50 flex justify-end">
        <div class="absolute inset-0 bg-black/30 backdrop-blur-sm" @click="selectedLead = null" />
        <div class="relative w-full max-w-lg bg-surface border-l border-default shadow-xl overflow-y-auto">
          <div class="sticky top-0 z-10 bg-surface border-b border-default px-6 py-4 flex items-center justify-between">
            <h2 class="text-lg font-semibold text-default">线索详情</h2>
            <button @click="selectedLead = null" class="p-1.5 rounded-md text-muted hover:text-default hover:bg-surface-hover transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-6">
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

            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">当前阶段</h3>
              <div class="flex items-center gap-1">
                <template v-for="(_label, key) in stageLabels" :key="key">
                  <div class="flex-1 h-2 rounded-full transition-colors" :class="getStagePipelineClass(key, selectedLead.stage)" />
                </template>
              </div>
              <div class="flex items-center mt-2">
                <span class="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium" :class="stageBadgeClass(selectedLead.stage)">
                  {{ stageLabels[selectedLead.stage as keyof typeof stageLabels] || selectedLead.stage }}
                </span>
                <span class="ml-2 text-xs text-muted">评分 {{ selectedLead.score }}/100</span>
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">阶段操作</h3>
              <div class="flex flex-wrap gap-2">
                <BaseButton
                  v-for="(label, key) in stageLabels"
                  :key="key"
                  v-show="key !== selectedLead.stage"
                  intent="secondary"
                  size="sm"
                  @click="changeStage(key)"
                >
                  {{ label }}
                </BaseButton>
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium text-muted mb-3 uppercase tracking-wider">跟进记录</h3>
              <div v-if="!selectedLead.recent_records?.length" class="text-sm text-muted py-2">暂无跟进记录</div>
              <div v-else class="space-y-3">
                <div v-for="rec in selectedLead.recent_records" :key="rec.record_id" class="p-3 rounded-lg border border-default bg-surface/50">
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
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'

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

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'company_name', label: '公司名称', width: '180px' },
  { key: 'contact_name', label: '联系人' },
  { key: 'phone', label: '电话' },
  { key: 'stage', label: '阶段' },
  { key: 'score', label: '评分' },
  { key: 'next_followup_at', label: '下次跟进' },
  { key: 'created_at', label: '创建时间' },
  { key: 'actions', label: '操作', width: '140px' },
]

const leads = ref<Lead[]>([])
const total = ref(0)
const submitting = ref(false)
const showAddModal = ref(false)
const showImportDialog = ref(false)
const selectedLead = ref<Lead | null>(null)
const dashboard = ref<DashboardData | null>(null)

const filters = ref({ stage: '', status: '', keyword: '' })
const newLead = ref<Record<string, string>>({ company_name: '', contact_name: '', phone: '', email: '', industry: '', region: '', description: '' })

const { currentPage, pageSize, loading, seqNumber, handleSearch } =
  usePageContext(async () => { await loadLeads() })

const statsCards = computed(() => {
  const byStage = dashboard.value?.by_stage || {}
  return [
    { key: 'new', label: '新线索', value: byStage['new'] || 0, subtext: '', iconBg: 'bg-info-100', iconColor: 'text-info-600', iconPath: 'M12 4.5v15m0 0l6.75-6.75M12 19.5l-6.75-6.75' },
    { key: 'contacting', label: '联系中', value: byStage['contacting'] || 0, subtext: '', iconBg: 'bg-warning-100', iconColor: 'text-warning-600', iconPath: 'M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155' },
    { key: 'won', label: '已成交', value: byStage['won'] || 0, subtext: '', iconBg: 'bg-success-100', iconColor: 'text-success-600', iconPath: 'M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
    { key: 'total', label: '总活跃', value: dashboard.value?.total_active || 0, subtext: `${dashboard.value?.overdue_followups || 0} 条逾期跟进`, iconBg: 'bg-gray-100', iconColor: 'text-muted', iconPath: 'M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5m.75-9l3-3 2.148 2.148A12.061 12.061 0 0116.5 7.605' },
  ]
})

function stageBadgeClass(stage: string) {
  const map: Record<string, string> = {
    new: 'bg-info-100 text-info-700',
    contacting: 'bg-warning-100 text-warning-700',
    qualified: 'bg-success-100 text-success-700',
    proposal: 'bg-primary-100 text-primary-700',
    negotiation: 'bg-warning-100 text-warning-700',
    won: 'bg-success-100 text-success-700',
    lost: 'bg-danger-100 text-danger-700',
  }
  return map[stage] || 'bg-gray-100 text-muted'
}

function scoreBarClass(score: number) {
  if (score >= 70) return 'bg-success-500'
  if (score >= 40) return 'bg-warning-500'
  return 'bg-gray-300'
}

function getStagePipelineClass(stageKey: string, currentStage: string) {
  const ci = stageOrder.indexOf(currentStage)
  const ki = stageOrder.indexOf(stageKey)
  if (currentStage === 'lost') return ki <= ci ? 'bg-danger-400' : 'bg-gray-200'
  return ki <= ci ? 'bg-primary-600' : 'bg-gray-200'
}

function outcomeClass(outcome: string) {
  const map: Record<string, string> = {
    positive: 'text-success-600',
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
  try {
    const data = await followupAPI.listLeads({
      ...filters.value,
      page: currentPage.value,
      page_size: pageSize.value,
    })
    leads.value = data.items
    total.value = data.total
  } catch (e: any) {
    console.error('加载线索失败:', e)
  }
}

async function loadDashboard() {
  try {
    dashboard.value = await followupAPI.getDashboard()
  } catch { /* ignore */ }
}

function resetFilters() {
  filters.value = { stage: '', status: '', keyword: '' }
  handleSearch('')
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

async function openDetail(lead: any) {
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

async function confirmDelete(lead: any) {
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
  alert('导出功能开发中')
}

onMounted(() => {
  loadLeads()
  loadDashboard()
  pageSize.value = 20
})
</script>

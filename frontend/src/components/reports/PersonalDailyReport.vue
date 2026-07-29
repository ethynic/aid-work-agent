<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="工作日报"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button
          class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover"
          @click="openPushSettings(); closeMenu()"
        >
          推送配置
        </button>
        <button
          class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover"
          @click="refresh(); closeMenu()"
        >
          刷新
        </button>
      </template>
    </AppHeader>

    <div class="flex-1 overflow-y-auto p-6">
      <div class="max-w-5xl mx-auto">
        <!-- 类型切换 + 日期选择 + scope 切换 -->
        <div class="flex items-center gap-3 mb-4">
          <div class="inline-flex rounded-lg border border-default overflow-hidden">
            <button
              v-for="t in REPORT_TYPES"
              :key="t.value"
              :class="[
                'px-4 py-2 text-sm font-medium transition-colors',
                reportType === t.value
                  ? 'bg-primary-600 text-white'
                  : 'bg-surface text-default hover:bg-surface-hover'
              ]"
              @click="switchType(t.value)"
            >
              {{ t.label }}
            </button>
          </div>
          <input
            :value="dateInputValue"
            :type="dateInputType"
            class="px-3 py-2 bg-surface border border-default rounded-lg text-default text-sm focus:outline-none focus:border-primary-400"
            @change="handleDateChange"
          />
          <!-- 个人/团队 切换器：仅租户管理员可见 -->
          <div v-if="canViewTeam" class="inline-flex rounded-lg border border-default overflow-hidden">
            <button
              v-for="s in SCOPES"
              :key="s.value"
              :class="[
                'px-4 py-2 text-sm font-medium transition-colors',
                scope === s.value
                  ? 'bg-primary-600 text-white'
                  : 'bg-surface text-default hover:bg-surface-hover'
              ]"
              @click="switchScope(s.value)"
            >
              {{ s.label }}
            </button>
          </div>
          <button
            class="ml-auto px-3 py-2 text-sm bg-surface border border-default rounded-lg text-default hover:bg-surface-hover"
            :disabled="loading || regenerating"
            @click="handleRegenerate"
          >
            {{ regenerating ? '生成中...' : '重新生成' }}
          </button>
        </div>

        <!-- 提示：报告类消耗积分 -->
        <div class="mb-4 px-4 py-2 rounded-lg bg-warning-50 border border-warning-200 text-warning-700 text-xs">
          <template v-if="isTeamScope">
            生成团队{{ typeLabel }}会消耗少量积分。
          </template>
          <template v-else>
            生成{{ typeLabel }}会消耗少量积分。
          </template>
        </div>

        <!-- 加载状态 -->
        <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

        <!-- 无数据 -->
        <div v-else-if="!report" class="text-center py-12 text-muted">
          {{ emptyStateText }}
        </div>

        <!-- 报告内容 -->
        <div v-else class="space-y-4">
          <!-- 概览指标 -->
          <div class="grid grid-cols-4 gap-3">
            <template v-if="isTeamScope">
              <!-- 团队日报指标 -->
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">活跃员工</div>
                <div class="text-2xl font-bold text-default mt-1">
                  {{ report.metrics?.active_user_count ?? 0 }}/{{ report.metrics?.total_users ?? 0 }}
                </div>
                <div class="text-xs text-muted mt-1">活跃率 {{ formatPercent(report.metrics?.active_rate) }}</div>
              </div>
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">总对话数</div>
                <div class="text-2xl font-bold text-default mt-1">{{ report.metrics?.total_dialog_count ?? 0 }}</div>
              </div>
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">总消耗积分</div>
                <div class="text-2xl font-bold text-default mt-1">{{ formatCredit(report.metrics?.total_credit_cost) }}</div>
              </div>
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">总节省时间</div>
                <div class="text-2xl font-bold text-success-600 mt-1">
                  {{ report.metrics?.total_saved_minutes ?? 0 }} <span class="text-sm font-normal">分钟</span>
                </div>
              </div>
            </template>
            <template v-else>
              <!-- 个人日报指标 -->
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">{{ typeLabel }}对话数</div>
                <div class="text-2xl font-bold text-default mt-1">{{ report.metrics?.dialog_count ?? 0 }}</div>
              </div>
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">消耗积分</div>
                <div class="text-2xl font-bold text-default mt-1">{{ formatCredit(report.metrics?.credit_cost) }}</div>
              </div>
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">预估节省时间</div>
                <div class="text-2xl font-bold text-success-600 mt-1">{{ report.metrics?.saved_minutes ?? 0 }} <span class="text-sm font-normal">分钟</span></div>
              </div>
              <div class="bg-white rounded-xl border border-default p-4">
                <div class="text-xs text-muted">数字员工数</div>
                <div class="text-2xl font-bold text-default mt-1">{{ Object.keys(report.metrics?.subagent_distribution ?? {}).length }}</div>
              </div>
            </template>
          </div>

          <!-- 团队日报截断提示 -->
          <div
            v-if="isTeamScope && report?.metrics?.input_truncated"
            class="px-4 py-2 rounded-lg bg-warning-50 border border-warning-200 text-warning-700 text-xs"
          >
            因数据量较大（{{ report.metrics.input_member_count }} 个成员 / {{ report.metrics.input_dialog_count }} 条对话进入 AI 摘要），仅基于部分代表性对话生成，统计指标已完整聚合。
          </div>

          <!-- 摘要正文 -->
          <div class="bg-white rounded-xl border border-default p-6">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-lg font-semibold text-default">{{ scopeLabel }}{{ typeLabel }}工作摘要</h3>
              <span v-if="report.cached === false" class="text-xs text-primary-600">新生成</span>
            </div>
            <div class="prose prose-sm max-w-none text-default whitespace-pre-wrap">{{ report.summary_text }}</div>
          </div>

          <!-- 个人日报：数字员工使用 -->
          <div
            v-if="!isTeamScope && report.metrics?.subagent_distribution && Object.keys(report.metrics.subagent_distribution).length"
            class="bg-white rounded-xl border border-default p-6"
          >
            <h3 class="text-lg font-semibold text-default mb-3">数字员工使用</h3>
            <div class="space-y-2">
              <div
                v-for="(count, name) in report.metrics.subagent_distribution"
                :key="String(name)"
                class="flex items-center gap-3"
              >
                <div class="w-40 text-sm text-default truncate">{{ name }}</div>
                <div class="flex-1 bg-surface-hover rounded h-2 overflow-hidden">
                  <div
                    class="bg-primary-500 h-2"
                    :style="{ width: barWidth(count as number, report.metrics.subagent_distribution) }"
                  ></div>
                </div>
                <div class="w-12 text-right text-sm text-muted">{{ count }}</div>
              </div>
            </div>
          </div>

          <!-- 个人日报：工具使用 Top -->
          <div
            v-if="!isTeamScope && report.metrics?.tool_distribution && Object.keys(report.metrics.tool_distribution).length"
            class="bg-white rounded-xl border border-default p-6"
          >
            <h3 class="text-lg font-semibold text-default mb-3">工具使用</h3>
            <div class="flex flex-wrap gap-2">
              <span
                v-for="(count, name) in report.metrics.tool_distribution"
                :key="String(name)"
                class="inline-flex items-center gap-1 px-3 py-1 rounded-full bg-surface-hover text-xs text-default"
              >
                {{ name }} <span class="text-muted">×{{ count }}</span>
              </span>
            </div>
          </div>

          <!-- 团队日报：活跃员工排行 -->
          <div
            v-if="isTeamScope && teamUserStatsSorted.length"
            class="bg-white rounded-xl border border-default p-6"
          >
            <h3 class="text-lg font-semibold text-default mb-3">活跃员工排行</h3>
            <div class="table-scroll-wrapper">
              <BaseTable :columns="teamUserColumns" :data="teamUserStatsSorted" row-key="user_id">
                <template #seq="{ index }">
                  {{ index + 1 }}
                </template>
                <template #user="{ row }">
                  <div class="flex flex-col gap-0.5 py-1">
                    <div class="text-sm text-default truncate">{{ row.username || row.user_id }}</div>
                    <div v-if="row.phone || row.nickname" class="text-xs text-muted truncate">
                      <span v-if="row.phone">{{ row.phone }}</span>
                      <span v-if="row.phone && row.nickname" class="mx-1">·</span>
                      <span v-if="row.nickname">{{ row.nickname }}</span>
                    </div>
                  </div>
                </template>
                <template #actions="{ row }">
                  <span class="text-xs text-muted">{{ getUsageLevel(row.dialog_count) }}</span>
                </template>
                <template #empty>暂无活跃员工</template>
              </BaseTable>
            </div>
          </div>

          <!-- 渠道分布 -->
          <div
            v-if="report.metrics?.source_distribution && Object.keys(report.metrics.source_distribution).length"
            class="bg-white rounded-xl border border-default p-6"
          >
            <h3 class="text-lg font-semibold text-default mb-3">渠道分布</h3>
            <div class="flex flex-wrap gap-2">
              <span
                v-for="(count, src) in report.metrics.source_distribution"
                :key="String(src)"
                class="inline-flex items-center gap-1 px-3 py-1 rounded-full bg-info-50 text-info-700 text-xs"
              >
                {{ getChatRecordSourceTypeInfo(String(src)).label }} <span class="text-info-500">×{{ count }}</span>
              </span>
            </div>
          </div>

          <!-- 元数据 -->
          <div class="text-xs text-muted px-2">
            报告 ID: {{ report.report_id }} · 生成模型: {{ report.model || '-' }} ·
            消耗积分: {{ formatCredit(report.credit_cost) }} ·
            生成时间: {{ report.generated_at || '-' }}
          </div>
        </div>
      </div>
    </div>

    <!-- 推送配置弹窗 -->
    <ReportPushSettings
      v-if="showPushSettings"
      @close="showPushSettings = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, inject, onMounted } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import ReportPushSettings from './ReportPushSettings.vue'
import {
  getPersonalToday,
  getPersonalByDate,
  regeneratePersonal,
  getTeamToday,
  getTeamByDate,
  regenerateTeam,
  type PersonalReport,
  type TeamReport,
  type ReportType,
} from '@/api/workReports'
import { getChatRecordSourceTypeInfo } from '@/api/enums'
import { formatCredit } from '@/utils/formatCredit'
import { useTenantAuth } from '@/composables/useTenantAuth'

const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone,
  } : null
})

const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
function handleLogout() {
  tenantLogout()
}

const REPORT_TYPES: Array<{ value: ReportType; label: string }> = [
  { value: 'daily', label: '日报' },
  { value: 'weekly', label: '周报' },
  { value: 'monthly', label: '月报' },
]

const SCOPES: Array<{ value: 'personal' | 'team'; label: string }> = [
  { value: 'personal', label: '个人' },
  { value: 'team', label: '团队' },
]

const reportType = ref<ReportType>('daily')
const scope = ref<'personal' | 'team'>('personal')
const selectedDate = ref(new Date().toISOString().slice(0, 10))
const personalReport = ref<PersonalReport | null>(null)
const teamReport = ref<TeamReport | null>(null)
const loading = ref(false)
const regenerating = ref(false)
const showPushSettings = ref(false)

const todayStr = new Date().toISOString().slice(0, 10)

// ============== 日期选择器：根据 reportType 切换为 date/week/month ==============

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function formatDateYMD(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

// YYYY-MM-DD -> YYYY-Www（ISO 周算法）
function dateToWeekValue(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00`)
  const day = d.getDay() || 7
  // 调整到当周周四：ISO 周年以周四所在年为准
  const thursday = new Date(d)
  thursday.setDate(d.getDate() - day + 4)
  const year = thursday.getFullYear()
  const firstThursday = new Date(year, 0, 4)
  const ftDay = firstThursday.getDay() || 7
  const firstThursdayMonday = new Date(firstThursday)
  firstThursdayMonday.setDate(firstThursday.getDate() - (ftDay - 1))
  const diffDays = Math.round((thursday.getTime() - firstThursdayMonday.getTime()) / 86400000)
  const week = Math.floor(diffDays / 7) + 1
  return `${year}-W${pad2(week)}`
}

// YYYY-Www -> YYYY-MM-DD（所在周的周一）
function weekValueToMonday(weekStr: string): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(weekStr)
  if (!m) return formatDateYMD(new Date())
  const year = parseInt(m[1], 10)
  const week = parseInt(m[2], 10)
  // Jan 4 一定在第 1 周
  const jan4 = new Date(year, 0, 4)
  const jan4Day = jan4.getDay() || 7
  const week1Monday = new Date(jan4)
  week1Monday.setDate(jan4.getDate() - (jan4Day - 1))
  const monday = new Date(week1Monday)
  monday.setDate(week1Monday.getDate() + (week - 1) * 7)
  return formatDateYMD(monday)
}

const dateInputType = computed(() => {
  if (reportType.value === 'weekly') return 'week'
  if (reportType.value === 'monthly') return 'month'
  return 'date'
})

const dateInputValue = computed(() => {
  if (reportType.value === 'weekly') return dateToWeekValue(selectedDate.value)
  if (reportType.value === 'monthly') return selectedDate.value.slice(0, 7)
  return selectedDate.value
})

function handleDateChange(e: Event) {
  const v = (e.target as HTMLInputElement).value
  if (!v) return
  if (reportType.value === 'weekly') {
    selectedDate.value = weekValueToMonday(v)
  } else if (reportType.value === 'monthly') {
    selectedDate.value = `${v}-01`
  } else {
    selectedDate.value = v
  }
  loadReport()
}

const typeLabel = computed(() => {
  return REPORT_TYPES.find((t) => t.value === reportType.value)?.label ?? '日报'
})
const scopeLabel = computed(() => {
  return SCOPES.find((s) => s.value === scope.value)?.label ?? ''
})
const isTeamScope = computed(() => scope.value === 'team')

// 统一 report 对象：根据 scope 返回对应类型
// 注意：模板中按 isTeamScope 区分访问字段，这里用 any 简化联合类型判断
const report = computed<any>(() => {
  return isTeamScope.value ? teamReport.value : personalReport.value
})

// 仅租户管理员/平台管理员可查看团队日报
const canViewTeam = computed(() => {
  const role = tenantAdmin.value?.role
  return role === 'tenant_admin' || role === 'platform_admin'
})

// 空状态文案
const emptyStateText = computed(() => {
  if (isTeamScope.value) {
    return `暂无团队${typeLabel.value}，点击「重新生成」创建。`
  }
  return selectedDate.value === todayStr
    ? '今日暂无对话记录，无法生成日报。'
    : '该日期暂无日报，可点击「重新生成」尝试生成。'
})

// 团队日报活跃员工排行：按 dialog_count DESC 排序
const teamUserStatsSorted = computed(() => {
  const stats = (teamReport.value?.metrics?.user_stats) || []
  return [...stats].sort((a, b) => b.dialog_count - a.dialog_count)
})

const teamUserColumns = [
  { key: 'seq', label: '序号', width: '60px' },
  {
    key: 'user',
    label: '用户',
    width: '240px',
    tooltip: (row: Record<string, any>) => {
      const parts = [row.username || row.user_id, row.phone, row.nickname].filter(Boolean)
      return parts.join(' · ')
    },
  },
  { key: 'dialog_count', label: '对话数', width: '120px' },
  { key: 'credit_cost', label: '消耗积分', width: '140px' },
  { key: 'saved_minutes', label: '节省时间(分)', width: '140px' },
  { key: 'actions', label: '使用强度', width: '120px' },
]

function getUsageLevel(dialogCount: number): string {
  if (dialogCount >= 50) return '高频'
  if (dialogCount >= 20) return '活跃'
  if (dialogCount >= 5) return '常规'
  return '少量'
}

function barWidth(count: number, dist: Record<string, number>): string {
  const max = Math.max(...Object.values(dist), 1)
  return `${Math.round((count / max) * 100)}%`
}

function formatPercent(rate: number | undefined): string {
  if (rate === undefined || rate === null) return '0%'
  return `${(rate * 100).toFixed(1)}%`
}

function openPushSettings() {
  showPushSettings.value = true
}

function switchType(t: ReportType) {
  reportType.value = t
  // 切换类型时，把 selectedDate 对齐到当前周期起点
  // - daily: today
  // - weekly: today 所在周的周一
  // - monthly: today 所在月的 1 号
  const today = new Date()
  if (t === 'weekly') {
    const day = today.getDay() || 7
    today.setDate(today.getDate() - (day - 1))
    selectedDate.value = formatDateYMD(today)
  } else if (t === 'monthly') {
    selectedDate.value = `${today.getFullYear()}-${pad2(today.getMonth() + 1)}-01`
  } else {
    selectedDate.value = formatDateYMD(today)
  }
  loadReport()
}

function switchScope(s: 'personal' | 'team') {
  if (scope.value === s) return
  scope.value = s
  loadReport()
}

async function loadReport() {
  loading.value = true
  try {
    if (isTeamScope.value) {
      // 团队日报：today 和指定日期都只查缓存，不自动生成
      const isToday = selectedDate.value === todayStr
      const res = isToday
        ? await getTeamToday(reportType.value)
        : await getTeamByDate(selectedDate.value, reportType.value)
      teamReport.value = res.data
    } else {
      // 个人日报：today 自动生成，指定日期仅查缓存
      const isToday = selectedDate.value === todayStr
      const res = isToday
        ? await getPersonalToday(reportType.value)
        : await getPersonalByDate(selectedDate.value, reportType.value)
      personalReport.value = res.data
    }
  } catch (e) {
    console.error('加载日报失败:', e)
    if (isTeamScope.value) {
      teamReport.value = null
    } else {
      personalReport.value = null
    }
  } finally {
    loading.value = false
  }
}

async function handleRegenerate() {
  const tip = isTeamScope.value
    ? `确定重新生成团队${typeLabel.value}？将消耗少量积分。`
    : `确定重新生成${typeLabel.value}？将消耗少量积分。`
  if (!confirm(tip)) return
  regenerating.value = true
  try {
    if (isTeamScope.value) {
      const res = await regenerateTeam(selectedDate.value, reportType.value)
      teamReport.value = res.data
    } else {
      const res = await regeneratePersonal(selectedDate.value, reportType.value)
      personalReport.value = res.data
    }
  } catch (e) {
    alert(e instanceof Error ? e.message : '重新生成失败')
  } finally {
    regenerating.value = false
  }
}

function refresh() {
  loadReport()
}

onMounted(() => {
  loadReport()
})
</script>

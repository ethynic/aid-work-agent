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
        <!-- 类型切换 + 日期选择 -->
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
            v-model="selectedDate"
            type="date"
            class="px-3 py-2 bg-surface border border-default rounded-lg text-default text-sm focus:outline-none focus:border-primary-400"
            @change="loadReport"
          />
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
          生成日报/周报/月报会消耗少量积分（每次约 2-5 积分，由小模型 deepseek-v4-flash 生成）。可在「积分用量」页面查看报告类消耗明细。
        </div>

        <!-- 加载状态 -->
        <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

        <!-- 无数据 -->
        <div v-else-if="!report" class="text-center py-12 text-muted">
          {{ selectedDate === todayStr ? '今日暂无对话记录，无法生成日报。' : '该日期暂无日报，可点击「重新生成」尝试生成。' }}
        </div>

        <!-- 报告内容 -->
        <div v-else class="space-y-4">
          <!-- 概览指标 -->
          <div class="grid grid-cols-4 gap-3">
            <div class="bg-white rounded-xl border border-default p-4">
              <div class="text-xs text-muted">{{ typeLabel }}对话数</div>
              <div class="text-2xl font-bold text-default mt-1">{{ report.metrics?.dialog_count ?? 0 }}</div>
            </div>
            <div class="bg-white rounded-xl border border-default p-4">
              <div class="text-xs text-muted">消耗积分</div>
              <div class="text-2xl font-bold text-default mt-1">{{ report.metrics?.credit_cost ?? 0 }}</div>
            </div>
            <div class="bg-white rounded-xl border border-default p-4">
              <div class="text-xs text-muted">预估节省时间</div>
              <div class="text-2xl font-bold text-success-600 mt-1">{{ report.metrics?.saved_minutes ?? 0 }} <span class="text-sm font-normal">分钟</span></div>
            </div>
            <div class="bg-white rounded-xl border border-default p-4">
              <div class="text-xs text-muted">数字员工数</div>
              <div class="text-2xl font-bold text-default mt-1">{{ Object.keys(report.metrics?.subagent_distribution ?? {}).length }}</div>
            </div>
          </div>

          <!-- 摘要正文 -->
          <div class="bg-white rounded-xl border border-default p-6">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-lg font-semibold text-default">{{ typeLabel }}工作摘要</h3>
              <span v-if="report.cached === false" class="text-xs text-primary-600">新生成</span>
            </div>
            <div class="prose prose-sm max-w-none text-default whitespace-pre-wrap">{{ report.summary_text }}</div>
          </div>

          <!-- 数字员工使用 -->
          <div v-if="report.metrics?.subagent_distribution && Object.keys(report.metrics.subagent_distribution).length" class="bg-white rounded-xl border border-default p-6">
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

          <!-- 工具使用 Top -->
          <div v-if="report.metrics?.tool_distribution && Object.keys(report.metrics.tool_distribution).length" class="bg-white rounded-xl border border-default p-6">
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

          <!-- 渠道分布 -->
          <div v-if="report.metrics?.source_distribution && Object.keys(report.metrics.source_distribution).length" class="bg-white rounded-xl border border-default p-6">
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
            消耗积分: {{ report.credit_cost ?? 0 }} ·
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
import ReportPushSettings from './ReportPushSettings.vue'
import {
  getPersonalToday,
  getPersonalByDate,
  regeneratePersonal,
  type PersonalReport,
  type ReportType,
} from '@/api/workReports'
import { getChatRecordSourceTypeInfo } from '@/api/enums'
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

const reportType = ref<ReportType>('daily')
const selectedDate = ref(new Date().toISOString().slice(0, 10))
const report = ref<PersonalReport | null>(null)
const loading = ref(false)
const regenerating = ref(false)
const showPushSettings = ref(false)

const todayStr = new Date().toISOString().slice(0, 10)
const typeLabel = computed(() => {
  return REPORT_TYPES.find((t) => t.value === reportType.value)?.label ?? '日报'
})

function barWidth(count: number, dist: Record<string, number>): string {
  const max = Math.max(...Object.values(dist), 1)
  return `${Math.round((count / max) * 100)}%`
}

function openPushSettings() {
  showPushSettings.value = true
}

function switchType(t: ReportType) {
  reportType.value = t
  loadReport()
}

async function loadReport() {
  loading.value = true
  try {
    const isToday = selectedDate.value === todayStr
    const res = isToday
      ? await getPersonalToday(reportType.value)
      : await getPersonalByDate(selectedDate.value, reportType.value)
    report.value = res.data
  } catch (e) {
    console.error('加载日报失败:', e)
    report.value = null
  } finally {
    loading.value = false
  }
}

async function handleRegenerate() {
  if (!confirm(`确定重新生成${typeLabel.value}？将消耗少量积分。`)) return
  regenerating.value = true
  try {
    const res = await regeneratePersonal(selectedDate.value, reportType.value)
    report.value = res.data
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

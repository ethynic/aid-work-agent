<template>
  <div class="h-full flex flex-col bg-canvas">
    <!-- 标题栏 -->
    <div class="flex items-center justify-between px-6 py-4 bg-white border-b border-default shrink-0">
      <h1 class="text-xl font-bold text-default">系统健康</h1>
      <div class="flex items-center gap-3">
        <span v-if="lastUpdated" class="text-xs text-muted">
          采集 {{ formatTs(overview?.snapshot?.collected_at) }} · 更新 {{ lastUpdated }}
        </span>
        <BaseButton size="sm" intent="secondary" @click="togglePolling">
          {{ polling ? '暂停刷新' : '恢复刷新' }}
        </BaseButton>
        <BaseButton size="sm" intent="secondary" @click="loadAll">刷新</BaseButton>
      </div>
    </div>

    <!-- 内容区 -->
    <div class="flex-1 overflow-y-auto p-4 md:p-6 space-y-4">
      <!-- 采集器状态横幅（非正常态优先展示） -->
      <div v-if="collectorBanner" :class="['rounded-lg px-4 py-3 text-sm border', collectorBanner.cls]">
        {{ collectorBanner.text }}
      </div>

      <!-- AI 研判横幅 -->
      <div v-if="assessmentBanner"
        :class="['rounded-lg px-4 py-4 border', assessmentBanner.cls]">
        <div class="flex items-start gap-3">
          <span :class="['shrink-0 text-xs font-medium px-2 py-0.5 rounded', assessmentBanner.tagCls]">
            {{ overview?.assessment?.source === 'llm' ? 'AI 研判' : '规则判定' }}
          </span>
          <div class="min-w-0 flex-1">
            <div class="text-sm font-medium text-default">{{ overview?.assessment?.summary }}</div>
            <div v-if="overview?.assessment?.suggestion" class="text-xs text-muted mt-1">
              建议：{{ overview?.assessment?.suggestion }}
            </div>
            <div class="flex flex-wrap gap-2 mt-2">
              <BaseBadge v-for="item in overview?.assessment?.items || []" :key="item.group"
                :intent="levelIntent(item.status)">
                {{ groupLabel(item.group) }} · {{ levelLabel(item.status) }}
              </BaseBadge>
            </div>
          </div>
        </div>
      </div>

      <!-- 状态卡区 -->
      <div :class="isMobile ? 'grid grid-cols-1 gap-4' : 'grid grid-cols-4 gap-4'">
        <!-- Runner 负载卡 -->
        <div class="bg-white rounded-lg border border-default p-4">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-semibold text-default">Runner 负载</h2>
            <BaseBadge v-if="runnerStallLevel" :intent="levelIntent(runnerStallLevel)">
              {{ runnerStallLevel === 'critical' ? '调度停摆' : '调度观察' }}
            </BaseBadge>
          </div>
          <div class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted">运行中 / 收尾</span>
              <span class="text-default font-medium">
                {{ runner?.running ?? '-' }} / {{ runner?.finalizing ?? '-' }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">排队</span>
              <span :class="levelCls(valueLevel('runner.queued', runner?.queued))" class="font-medium">
                {{ runner?.queued ?? '-' }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">最老任务运行</span>
              <span :class="levelCls(valueLevel('runner.oldest_running_seconds', runner?.oldest_running_seconds))" class="font-medium">
                {{ fmtDuration(runner?.oldest_running_seconds) }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">最老排队等待</span>
              <span :class="levelCls(valueLevel('runner.oldest_queued_seconds', runner?.oldest_queued_seconds))" class="font-medium">
                {{ fmtDuration(runner?.oldest_queued_seconds) }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">今日等待 P50 / P95</span>
              <span :class="levelCls(valueLevel('runner.wait_p95_seconds', runner?.wait_p95_seconds))" class="font-medium">
                {{ fmtDuration(runner?.wait_p50_seconds) }} / {{ fmtDuration(runner?.wait_p95_seconds) }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">今日重试占比 (attempt&gt;1)</span>
              <span class="text-default">{{ fmtPercent(runner?.retried_share_today) }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">活跃告警</span>
              <span :class="levelCls(valueLevel('runner.active_alerts', runner?.active_alerts))" class="font-medium">
                {{ runner?.active_alerts ?? '-' }}
              </span>
            </div>
          </div>
        </div>

        <!-- 系统资源卡 -->
        <div class="bg-white rounded-lg border border-default p-4">
          <h2 class="text-sm font-semibold text-default mb-3">系统资源</h2>
          <div v-if="!system?.available" class="text-sm text-muted">psutil 不可用</div>
          <div v-else class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted">CPU</span>
              <span :class="levelCls(valueLevel('system.cpu_percent', system?.cpu_percent))" class="font-medium">
                {{ system?.cpu_percent ?? '-' }}%
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">Load (1m)</span>
              <span :class="levelCls(loadLevel)" class="font-medium">{{ system?.load_1 ?? '-' }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">内存</span>
              <span :class="levelCls(valueLevel('system.mem_used_percent', system?.mem_used_percent))" class="font-medium">
                {{ system?.mem_used_gb ?? '-' }} / {{ system?.mem_total_gb ?? '-' }} GB
                ({{ system?.mem_used_percent ?? '-' }}%)
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">磁盘 IO 读 / 写</span>
              <span class="text-default">
                {{ system?.disk_read_mb_per_s ?? '-' }} / {{ system?.disk_write_mb_per_s ?? '-' }} MB/s
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">storage 分区</span>
              <span :class="levelCls(valueLevel('system.disk_used_percent_storage', system?.disk_used_percent_storage))" class="font-medium">
                {{ system?.disk_used_percent_storage ?? '-' }}%
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">log 分区</span>
              <span :class="levelCls(valueLevel('system.disk_used_percent_log', system?.disk_used_percent_log))" class="font-medium">
                {{ system?.disk_used_percent_log ?? '-' }}%
              </span>
            </div>
          </div>
        </div>

        <!-- 业务活跃卡 -->
        <div class="bg-white rounded-lg border border-default p-4">
          <h2 class="text-sm font-semibold text-default mb-3">业务活跃</h2>
          <div class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted">在线会话（5 分钟）</span>
              <span class="text-default font-medium">{{ business?.online_sessions_5m ?? '-' }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">今日请求量</span>
              <span class="text-default font-medium">{{ business?.requests_today ?? '-' }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">今日错误率</span>
              <span :class="levelCls(valueLevel('business.error_rate_today', business?.error_rate_today))" class="font-medium">
                {{ fmtPercent(business?.error_rate_today) }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">任务耗时 P50 / P95</span>
              <span class="text-default">{{ fmtMs(business?.duration_p50_ms) }} / {{ fmtMs(business?.duration_p95_ms) }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">错误日志（1 小时）</span>
              <span :class="levelCls(valueLevel('business.errors_last_hour', business?.errors_last_hour))" class="font-medium">
                {{ business?.errors_last_hour ?? '-' }}
              </span>
            </div>
          </div>
        </div>

        <!-- 中间件与告警卡 -->
        <div class="bg-white rounded-lg border border-default p-4">
          <h2 class="text-sm font-semibold text-default mb-3">中间件</h2>
          <div class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted">DB 连接数</span>
              <span :class="levelCls(dbConnLevel)" class="font-medium">
                {{ middleware?.db_connections ?? '-' }}
                <template v-if="middleware?.db_max_connections"> / {{ middleware?.db_max_connections }}</template>
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">DB 查询延迟 (SELECT 1)</span>
              <span :class="levelCls(valueLevel('middleware.db_select1_ms', middleware?.db_select1_ms))" class="font-medium">
                {{ middleware?.db_select1_ms ?? '-' }} ms
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">Redis</span>
              <BaseBadge :intent="middleware?.redis_available ? 'success' : 'danger'">
                {{ middleware?.redis_available ? '正常' : '不可用/降级' }}
              </BaseBadge>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">模型 Key 并发</span>
              <span :class="levelCls(keyPoolLevel)" class="font-medium">
                {{ middleware?.key_pool_active ?? '-' }}
                <template v-if="middleware?.key_pool_max"> / {{ middleware?.key_pool_max }}</template>
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">模型错误率（今日）</span>
              <span :class="levelCls(valueLevel('model_error_rate_today', business?.error_rate_today))" class="font-medium">
                {{ fmtPercent(business?.error_rate_today) }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 运行中任务 -->
      <div>
        <h2 class="text-sm font-semibold text-default mb-2">
          运行中任务（{{ runningTasks.length }}）
        </h2>
        <div v-if="runningTasks.length === 0" class="text-sm text-muted py-4 text-center bg-white rounded-lg border border-default">
          暂无运行中任务
        </div>
        <!-- PC：表格 -->
        <BaseTable v-else-if="!isMobile" :columns="runningColumns" :data="runningTasks" row-key="runner_id" table-fixed>
          <template #elapsed_seconds="{ row }">
            <span :class="row.elapsed_seconds >= 1800 ? 'text-danger-600 font-medium' : 'text-default'">
              {{ fmtDuration(row.elapsed_seconds) }}
            </span>
          </template>
        </BaseTable>
        <!-- 手机端：卡片列表 -->
        <div v-else class="space-y-2">
          <div v-for="task in runningTasks" :key="task.runner_id"
            class="bg-white rounded-lg border border-default p-3 space-y-1.5 text-sm">
            <div class="flex items-center justify-between">
              <span class="font-mono text-xs text-muted break-all">{{ task.runner_id }}</span>
              <BaseBadge :intent="task.elapsed_seconds >= 1800 ? 'danger' : 'info'">
                {{ task.status }}
              </BaseBadge>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">租户</span>
              <span class="text-default">{{ task.tenant_name || task.tenant_id || '-' }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">数字员工</span>
              <span class="text-default">{{ task.profile_id }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">已运行</span>
              <span :class="task.elapsed_seconds >= 1800 ? 'text-danger-600 font-medium' : 'text-default'">
                {{ fmtDuration(task.elapsed_seconds) }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">attempt</span>
              <span class="text-default">{{ task.attempt }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 排队任务 -->
      <div>
        <h2 class="text-sm font-semibold text-default mb-2">
          排队任务（{{ queuedTasks.length }}）
        </h2>
        <div v-if="queuedTasks.length === 0" class="text-sm text-muted py-4 text-center bg-white rounded-lg border border-default">
          暂无排队任务
        </div>
        <BaseTable v-else-if="!isMobile" :columns="queuedColumns" :data="queuedTasks" row-key="runner_id" table-fixed>
          <template #elapsed_seconds="{ row }">
            <span :class="row.elapsed_seconds >= 600 ? 'text-danger-600 font-medium' : row.elapsed_seconds >= 120 ? 'text-warning-600 font-medium' : 'text-default'">
              {{ fmtDuration(row.elapsed_seconds) }}
            </span>
          </template>
        </BaseTable>
        <div v-else class="space-y-2">
          <div v-for="task in queuedTasks" :key="task.runner_id"
            class="bg-white rounded-lg border border-default p-3 space-y-1.5 text-sm">
            <div class="flex items-center justify-between">
              <span class="font-mono text-xs text-muted break-all">{{ task.runner_id }}</span>
              <BaseBadge :intent="task.elapsed_seconds >= 600 ? 'danger' : task.elapsed_seconds >= 120 ? 'warning' : 'neutral'">
                已等 {{ fmtDuration(task.elapsed_seconds) }}
              </BaseBadge>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">租户</span>
              <span class="text-default">{{ task.tenant_name || task.tenant_id || '-' }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">数字员工</span>
              <span class="text-default">{{ task.profile_id }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">attempt</span>
              <span class="text-default">{{ task.attempt }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 系统健康监控页（管理后台默认首页，/portal/system-health）
 * 设计文档：docs/system/system-health-monitor-design.md
 *
 * - 15s 轮询可暂停；请求失败静默重试不弹错（设计 §2）
 * - 红黄高亮由前端硬阈值驱动（与后端 assessment.py THRESHOLDS 同源镜像，
 *   改阈值需两处同步）；AI 研判横幅只解释数据，不隐藏数据
 * - 数据量小（运行中/排队任务有限），全量渲染，不分页（设计 §6.2）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import { useMobile } from '@/composables/useMobile'
import {
  getMonitorOverview,
  getMonitorRunners,
  type HealthLevel,
  type MonitorOverview,
  type RunnerTaskItem,
} from '@/api/adminMonitor'

const { isMobile } = useMobile()

const overview = ref<MonitorOverview | null>(null)
const runningTasks = ref<RunnerTaskItem[]>([])
const queuedTasks = ref<RunnerTaskItem[]>([])
const lastUpdated = ref('')
const polling = ref(true)

let pollTimer: number | undefined
let runnersTimer: number | undefined

const runner = computed(() => overview.value?.snapshot?.runner)
const system = computed(() => overview.value?.snapshot?.system)
const business = computed(() => overview.value?.snapshot?.business)
const middleware = computed(() => overview.value?.snapshot?.middleware)

// ==================== 前端硬阈值（镜像 src/services/system_health/assessment.py THRESHOLDS） ====================
const THRESHOLDS: Record<string, [number, number]> = {
  'runner.queued': [2, 5],
  'runner.oldest_running_seconds': [600, 1800],
  'runner.oldest_queued_seconds': [120, 600],
  'runner.wait_p95_seconds': [120, 600],
  'runner.active_alerts': [1, 3],
  'system.cpu_percent': [80, 95],
  'system.mem_used_percent': [80, 92],
  'system.disk_used_percent_storage': [80, 92],
  'system.disk_used_percent_log': [80, 92],
  'middleware.db_select1_ms': [200, 1000],
  'business.error_rate_today': [0.05, 0.15],
  'business.errors_last_hour': [20, 100],
}

function valueLevel(key: string, value: number | null | undefined): HealthLevel {
  if (value == null) return 'normal'
  const [watch, critical] = THRESHOLDS[key] ?? [Infinity, Infinity]
  if (value >= critical) return 'critical'
  if (value >= watch) return 'watch'
  return 'normal'
}

const runnerStallLevel = computed<HealthLevel>(() =>
  runner.value?.runner_stalled ? 'critical' : 'normal')

const loadLevel = computed<HealthLevel>(() => {
  const load = system.value?.load_1
  const cpuCount = system.value?.cpu_count
  if (load == null || !cpuCount) return 'normal'
  if (load >= cpuCount * 2) return 'critical'
  if (load >= cpuCount) return 'watch'
  return 'normal'
})

const dbConnLevel = computed<HealthLevel>(() => {
  const conns = middleware.value?.db_connections
  const max = middleware.value?.db_max_connections
  if (conns == null || !max) return 'normal'
  const ratio = conns / max
  if (ratio >= 0.95) return 'critical'
  if (ratio >= 0.8) return 'watch'
  return 'normal'
})

const keyPoolLevel = computed<HealthLevel>(() => {
  const active = middleware.value?.key_pool_active
  const max = middleware.value?.key_pool_max
  if (active == null || !max) return 'normal'
  const ratio = active / max
  if (ratio >= 1.0) return 'critical'
  if (ratio >= 0.8) return 'watch'
  return 'normal'
})

// ==================== 横幅与样式映射 ====================

const collectorBanner = computed(() => {
  const status = overview.value?.status
  if (status === 'storage_unavailable') {
    return { text: '监控存储不可用（Redis）——快照无法读取，恢复后自动回到正常', cls: 'bg-warning-50 border-warning-200 text-warning-800' }
  }
  if (status === 'collector_down') {
    return { text: '采集中断——心跳超过 60s 未更新（未部署后台采集容器的环境恒为此状态）', cls: 'bg-warning-50 border-warning-200 text-warning-800' }
  }
  return null
})

const assessmentBanner = computed(() => {
  const a = overview.value?.assessment
  if (!a) return null
  if (a.overall === 'normal' && collectorBanner.value) return null
  const cls = a.overall === 'critical'
    ? 'bg-danger-50 border-danger-200'
    : a.overall === 'watch'
      ? 'bg-warning-50 border-warning-200'
      : 'bg-success-50 border-success-200'
  const tagCls = a.overall === 'critical'
    ? 'bg-danger-600 text-white'
    : a.overall === 'watch'
      ? 'bg-warning-600 text-white'
      : 'bg-success-600 text-white'
  return { cls, tagCls }
})

function levelIntent(level: HealthLevel) {
  if (level === 'critical') return 'danger' as const
  if (level === 'watch') return 'warning' as const
  return 'success' as const
}

function levelCls(level: HealthLevel) {
  if (level === 'critical') return 'text-danger-600'
  if (level === 'watch') return 'text-warning-600'
  return 'text-default'
}

function levelLabel(level: HealthLevel) {
  if (level === 'critical') return '异常'
  if (level === 'watch') return '观察'
  return '正常'
}

function groupLabel(group: string) {
  const map: Record<string, string> = {
    runner: 'Runner', system: '系统资源', middleware: '中间件', business: '业务面',
  }
  return map[group] || group
}

// ==================== 表格列定义 ====================

const runningColumns: TableColumn[] = [
  { key: 'runner_id', label: 'Runner ID', width: '260px' },
  { key: 'tenant_name', label: '租户', tooltip: (row) => row.tenant_name || row.tenant_id || '-' },
  { key: 'profile_id', label: '数字员工' },
  { key: 'status', label: '状态', width: '110px' },
  { key: 'attempt', label: 'attempt', width: '80px' },
  { key: 'elapsed_seconds', label: '已运行' },
]

const queuedColumns: TableColumn[] = [
  { key: 'runner_id', label: 'Runner ID', width: '260px' },
  { key: 'tenant_name', label: '租户', tooltip: (row) => row.tenant_name || row.tenant_id || '-' },
  { key: 'profile_id', label: '数字员工' },
  { key: 'attempt', label: 'attempt', width: '80px' },
  { key: 'elapsed_seconds', label: '已等待' },
]

// ==================== 格式化 ====================

function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  if (m < 60) return s ? `${m}分${s}秒` : `${m}分`
  const h = Math.floor(m / 60)
  return `${h}时${m % 60}分`
}

function fmtPercent(value: number | null | undefined): string {
  if (value == null) return '-'
  return `${(value * 100).toFixed(1)}%`
}

function fmtMs(value: number | null | undefined): string {
  if (value == null) return '-'
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`
  return `${value}ms`
}

function formatTs(ts: string | undefined): string {
  if (!ts) return '-'
  const d = new Date(ts)
  return isNaN(d.getTime()) ? ts : d.toLocaleTimeString('zh-CN', { hour12: false })
}

// ==================== 数据加载与轮询 ====================

async function loadOverview() {
  try {
    const res = await getMonitorOverview()
    if (res.success !== false) overview.value = res
  } catch {
    // 静默重试（设计 §2：请求失败不弹错误）
  }
}

async function loadRunners() {
  try {
    const res = await getMonitorRunners()
    if (res.success) {
      runningTasks.value = res.running || []
      queuedTasks.value = (res.queued || []).slice().sort((a, b) => b.elapsed_seconds - a.elapsed_seconds)
    }
  } catch {
    // 静默重试
  }
}

function loadAll() {
  lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  loadOverview()
  loadRunners()
}

function togglePolling() {
  polling.value = !polling.value
  if (polling.value) startPolling()
  else stopPolling()
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(loadOverview, 15000)
  runnersTimer = window.setInterval(loadRunners, 15000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = undefined }
  if (runnersTimer) { clearInterval(runnersTimer); runnersTimer = undefined }
}

onMounted(() => {
  loadAll()
  startPolling()
})

onUnmounted(() => stopPolling())
</script>

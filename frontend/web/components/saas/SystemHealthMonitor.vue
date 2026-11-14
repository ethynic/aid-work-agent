<template>
  <div class="h-full flex flex-col bg-canvas">
    <!-- 标题栏 -->
    <div class="flex items-center justify-between px-6 py-4 bg-white border-b border-default shrink-0">
      <h1 class="text-xl font-bold text-default">系统健康</h1>
      <div class="flex items-center gap-3">
        <span v-if="lastUpdated" class="text-xs text-muted">
          采集 {{ formatTs(overview?.snapshot?.collected_at) }} · 更新 {{ lastUpdated }}
        </span>
        <BaseButton v-if="hasAnomaly" size="sm" intent="primary" @click="copyAnomalyInfo">复制异常信息</BaseButton>
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
          <BaseBadge :class="['shrink-0', levelBadgeCls(overallLevel)]" :intent="levelIntent(overallLevel)">
            {{ levelLabel(overallLevel) }}
          </BaseBadge>
          <div class="min-w-0 flex-1">
            <div class="text-sm font-medium text-default">{{ overview?.assessment?.summary }}</div>
            <div v-if="overview?.assessment?.suggestion" class="text-xs text-muted mt-1">
              建议：{{ overview?.assessment?.suggestion }}
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
            <BaseBadge :class="levelBadgeCls(runnerGroupLevel)" :intent="levelIntent(runnerGroupLevel)">
              {{ levelLabel(runnerGroupLevel) }}
            </BaseBadge>
          </div>
          <div class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted">执行体</span>
              <span class="text-default font-medium" :title="workerSlotTitle">
                {{ workerOccupied ?? '-' }} / {{ workerCapacity ?? '-' }}
                <span v-if="workerCapacity != null" class="text-muted text-xs">({{ slotConcurrency }} * {{ runner?.worker_replicas ?? '-' }})</span>
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">重工具服务</span>
              <span class="text-default font-medium" :title="heavyToolsTitle">
                {{ heavyToolsLabel }}
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
              <span :title="retryShareTitle" class="text-default">
                {{ fmtPercent(runner?.retried_share_today) }}{{ retryShareSuffix }}
              </span>
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
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-semibold text-default">系统资源</h2>
            <BaseBadge :class="levelBadgeCls(systemGroupLevel)" :intent="levelIntent(systemGroupLevel)">
              {{ levelLabel(systemGroupLevel) }}
            </BaseBadge>
          </div>
          <div v-if="!system?.available" class="text-sm text-muted">psutil 不可用</div>
          <div v-else class="space-y-2 text-sm">
            <div class="flex justify-between">
              <span class="text-muted">CPU</span>
              <span :class="levelCls(valueLevel('system.cpu_percent', system?.cpu_percent))" class="font-medium">
                {{ system?.cpu_percent ?? '-' }}%
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">系统负载（1 分钟）</span>
              <span :class="levelCls(loadLevel)" class="font-medium"
                title="平均排队使用 CPU 的任务数；等于核数即满载，超出核数说明有积压">
                {{ system?.load_1 ?? '-' }}
              </span>
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
              <span class="text-muted">网络 IO 收 / 发</span>
              <span :class="levelCls(netLoadLevel)"
                :title="system?.net_bandwidth_mbps ? `公网带宽 ${system.net_bandwidth_mbps} Mbps，观察 ≥80% / 警告 ≥95%` : undefined">
                {{ system?.net_recv_mb_per_s ?? '-' }} / {{ system?.net_sent_mb_per_s ?? '-' }} MB/s
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
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-semibold text-default">业务活跃</h2>
            <BaseBadge :class="levelBadgeCls(businessGroupLevel)" :intent="levelIntent(businessGroupLevel)">
              {{ levelLabel(businessGroupLevel) }}
            </BaseBadge>
          </div>
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
              <span class="text-muted">今日对话失败率</span>
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
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-semibold text-default">中间件</h2>
            <BaseBadge :class="levelBadgeCls(middlewareGroupLevel)" :intent="levelIntent(middlewareGroupLevel)">
              {{ levelLabel(middlewareGroupLevel) }}
            </BaseBadge>
          </div>
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
              <span :class="middleware?.redis_available ? 'text-default font-medium' : 'text-danger-600 font-medium'">
                {{ middleware?.redis_available ? '正常' : '不可用/降级' }}
              </span>
            </div>
            <div class="flex justify-between">
              <span class="text-muted">模型 Key 并发</span>
              <span :class="levelCls(keyPoolLevel)" class="font-medium">
                {{ middleware?.key_pool_active ?? '-' }}
                <template v-if="middleware?.key_pool_max"> / {{ middleware?.key_pool_max }}</template>
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
import { useToast } from 'vue-toastification'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import { useMobile } from '@/composables/useMobile'
import { resolveEnvLabel } from '@/utils/envLabel'
import { formatDateTimeWithSeconds } from '@/utils/date'
import {
  getMonitorOverview,
  getMonitorRunners,
  type HealthLevel,
  type MonitorOverview,
  type RunnerTaskItem,
} from '@/api/adminMonitor'

const { isMobile } = useMobile()
const toast = useToast()

const overview = ref<MonitorOverview | null>(null)
const runningTasks = ref<RunnerTaskItem[]>([])
const queuedTasks = ref<RunnerTaskItem[]>([])
const lastUpdated = ref('')
const polling = ref(true)

let pollTimer: number | undefined
let runnersTimer: number | undefined

const runner = computed(() => overview.value?.snapshot?.runner)
const system = computed(() => overview.value?.snapshot?.system)
// 重试占比样本不足（<10）时后端置 null，展示原始计数帮助解读
const retryShareSuffix = computed(() => {
  const r = runner.value
  if (!r) return ''
  if (r.retried_share_today == null) return `（样本不足，重试 ${r.runner_retried_today ?? 0}/${r.runner_started_today ?? 0}）`
  return `（${r.runner_retried_today ?? 0}/${r.runner_started_today ?? 0}）`
})
const retryShareTitle = computed(() => 'attempt>1 的任务数 / 今日已启动任务数；启动数不足 10 时不计算占比')

// 池路由退役（2026-11-13 重工具服务化 §9）：单一 runner-worker 服务全量领取，
// 执行体容量 = 副本数 × 每副本并发（AGENT_RUNNER_SLOT_CONCURRENCY，未配置=4，
// 与 settings/compose 默认一致）；重负载经代理远程派发到重工具服务。
const slotConcurrency = computed(() => runner.value?.light_concurrency ?? 4)
const workerOccupied = computed(() =>
  (runner.value?.running ?? 0) + (runner.value?.finalizing ?? 0))
const workerCapacity = computed(() => {
  const replicas = runner.value?.worker_replicas
  if (replicas == null) return null
  return replicas * slotConcurrency.value
})
const heavyTools = computed(() => runner.value?.heavy_tools)
const heavyToolsLabel = computed(() => {
  const h = heavyTools.value
  if (!h) return '-'
  if (h.mode !== 'remote') {
    return `本地模式（${h.replicas ?? '-'}×${h.concurrency ?? '-'}）`
  }
  const status = h.available == null ? '未知' : (h.available ? '正常' : '不可用')
  const running = h.running_jobs != null ? `，执行中 ${h.running_jobs}` : ''
  const busy = h.busy ? '，繁忙' : ''
  return `${status}${running}${busy}`
})
const workerSlotTitle = computed(() => {
  const replicas = runner.value?.worker_replicas ?? '-'
  const c = slotConcurrency.value
  return `占用/容量为运行+收尾合并计数；容量 = 每副本并发 ${c} × worker 副本 ${replicas}（AGENT_RUNNER_SLOT_CONCURRENCY × AGENT_RUNNER_WORKER_SLOTS，部署配置口径）；全部 worker 等价全量领取，重工具经代理远程派发`
})
const heavyToolsTitle = computed(() => {
  const h = heavyTools.value
  if (!h) return '重工具服务未采集'
  return `mode=${h.mode}，副本 ${h.replicas ?? '-'} × 并发 ${h.concurrency ?? '-'}（HEAVY_TOOLS_REPLICAS × HEAVY_TOOLS_CONCURRENCY，部署配置口径）；remote 模式探测 /internal/heavy-tools/health，local 模式重负载在轻容器本地执行`
})
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
  runner.value?.runner_stalled ? 'error' : 'normal')

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

// 网络带宽占满：收/发取较大者对比 PUBLIC_BANDWIDTH_MBPS 基准（未配置则不研判）
const netLoadLevel = computed<HealthLevel>(() => {
  const s = system.value
  const bw = s?.net_bandwidth_mbps
  const recv = s?.net_recv_mb_per_s
  const sent = s?.net_sent_mb_per_s
  if (!bw || bw <= 0 || recv == null || sent == null) return 'normal'
  const ratio = Math.max(recv, sent) * 8 / bw
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

// ==================== 总状态与分组状态（四级：normal/watch/critical/error） ====================
// error 仅用于基础设施硬故障（Redis 不可用、调度停摆、采集中断），与后端 assessment.py 同源

const LEVEL_RANK: Record<HealthLevel, number> = { normal: 0, watch: 1, critical: 2, error: 3 }

function worstLevel(levels: HealthLevel[]): HealthLevel {
  return levels.reduce((worst, l) => (LEVEL_RANK[l] > LEVEL_RANK[worst] ? l : worst), 'normal' as HealthLevel)
}

const runnerGroupLevel = computed<HealthLevel>(() => {
  if (runner.value?.runner_stalled) return 'error'
  return worstLevel([
    valueLevel('runner.queued', runner.value?.queued),
    valueLevel('runner.oldest_running_seconds', runner.value?.oldest_running_seconds),
    valueLevel('runner.oldest_queued_seconds', runner.value?.oldest_queued_seconds),
    valueLevel('runner.wait_p95_seconds', runner.value?.wait_p95_seconds),
    valueLevel('runner.active_alerts', runner.value?.active_alerts),
  ])
})

const systemGroupLevel = computed<HealthLevel>(() => {
  if (!system.value?.available) return 'watch'
  return worstLevel([
    valueLevel('system.cpu_percent', system.value?.cpu_percent),
    loadLevel.value,
    valueLevel('system.mem_used_percent', system.value?.mem_used_percent),
    valueLevel('system.disk_used_percent_storage', system.value?.disk_used_percent_storage),
    valueLevel('system.disk_used_percent_log', system.value?.disk_used_percent_log),
    netLoadLevel.value,
  ])
})

const middlewareGroupLevel = computed<HealthLevel>(() => {
  if (middleware.value?.redis_available === false) return 'error'
  return worstLevel([
    dbConnLevel.value,
    valueLevel('middleware.db_select1_ms', middleware.value?.db_select1_ms),
    keyPoolLevel.value,
  ])
})

const businessGroupLevel = computed<HealthLevel>(() =>
  worstLevel([
    valueLevel('business.error_rate_today', business.value?.error_rate_today),
    valueLevel('business.errors_last_hour', business.value?.errors_last_hour),
  ]))

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
  const cls = a.overall === 'critical' || a.overall === 'error'
    ? 'bg-danger-50 border-danger-200'
    : a.overall === 'watch'
      ? 'bg-warning-50 border-warning-200'
      : 'bg-success-50 border-success-200'
  const tagCls = a.overall === 'critical' || a.overall === 'error'
    ? 'bg-danger-600 text-white'
    : a.overall === 'watch'
      ? 'bg-warning-600 text-white'
      : 'bg-success-600 text-white'
  return { cls, tagCls }
})

// 总状态：采集中断视为 error；AI 研判缺失时按前端分组状态兜底
const overallLevel = computed<HealthLevel>(() => {
  if (collectorBanner.value) return 'error'
  const a = overview.value?.assessment
  if (a) return a.overall
  return worstLevel([runnerGroupLevel.value, systemGroupLevel.value,
    middlewareGroupLevel.value, businessGroupLevel.value])
})

function levelIntent(level: HealthLevel) {
  if (level === 'critical' || level === 'error') return 'danger' as const
  if (level === 'watch') return 'warning' as const
  return 'success' as const
}

// 错误态在红色基础上加粗，与警告（仅红色）拉开视觉层级
function levelBadgeCls(level: HealthLevel) {
  return level === 'error' ? 'font-bold' : ''
}

function levelCls(level: HealthLevel) {
  if (level === 'critical') return 'text-danger-600'
  if (level === 'watch') return 'text-warning-600'
  return 'text-default'
}

function levelLabel(level: HealthLevel) {
  if (level === 'error') return '错误'
  if (level === 'critical') return '警告'
  if (level === 'watch') return '观察'
  return '正常'
}

function groupLabel(group: string) {
  const map: Record<string, string> = {
    runner: 'Runner', system: '系统资源', middleware: '中间件', business: '业务面',
  }
  return map[group] || group
}

// ==================== 复制异常信息 ====================
// 阈值项的取值来源、显示名与单位格式化（键与 THRESHOLDS 同源镜像）
const METRIC_META: Record<string, { label: string; get: () => number | null | undefined; fmt: (v: number) => string }> = {
  'runner.queued': { label: 'Runner 排队任务', get: () => runner.value?.queued, fmt: (v) => `${v} 个` },
  'runner.oldest_running_seconds': { label: '最老任务运行', get: () => runner.value?.oldest_running_seconds, fmt: fmtDuration },
  'runner.oldest_queued_seconds': { label: '最老排队等待', get: () => runner.value?.oldest_queued_seconds, fmt: fmtDuration },
  'runner.wait_p95_seconds': { label: '今日等待 P95', get: () => runner.value?.wait_p95_seconds, fmt: fmtDuration },
  'runner.active_alerts': { label: 'Runner 活跃告警', get: () => runner.value?.active_alerts, fmt: (v) => `${v} 条` },
  'system.cpu_percent': { label: 'CPU 使用率', get: () => system.value?.cpu_percent, fmt: (v) => `${v}%` },
  'system.mem_used_percent': { label: '内存使用率', get: () => system.value?.mem_used_percent, fmt: (v) => `${v}%` },
  'system.disk_used_percent_storage': { label: 'storage 分区使用率', get: () => system.value?.disk_used_percent_storage, fmt: (v) => `${v}%` },
  'system.disk_used_percent_log': { label: 'log 分区使用率', get: () => system.value?.disk_used_percent_log, fmt: (v) => `${v}%` },
  'middleware.db_select1_ms': { label: 'DB 查询延迟 (SELECT 1)', get: () => middleware.value?.db_select1_ms, fmt: (v) => `${v}ms` },
  'business.error_rate_today': { label: '今日对话失败率', get: () => business.value?.error_rate_today, fmt: fmtPercent },
  'business.errors_last_hour': { label: '错误日志（1 小时）', get: () => business.value?.errors_last_hour, fmt: (v) => `${v} 条` },
}

const hasAnomaly = computed(() => {
  if (collectorBanner.value) return true
  if (LEVEL_RANK[overallLevel.value] > 0) return true
  // 兜底：后端研判缺失或滞后时，按前端阈值判断
  if (middleware.value?.redis_available === false) return true
  if (runnerStallLevel.value !== 'normal' || loadLevel.value !== 'normal'
    || dbConnLevel.value !== 'normal' || keyPoolLevel.value !== 'normal'
    || netLoadLevel.value !== 'normal') return true
  return Object.entries(METRIC_META).some(([key, meta]) => {
    const value = meta.get()
    return value != null && valueLevel(key, value) !== 'normal'
  })
})

function collectAnomalyLines(): string[] {
  const lines: string[] = []
  if (runner.value?.runner_stalled) lines.push('- [错误] Runner 调度停摆（runner_stalled=true）')
  if (middleware.value?.redis_available === false) lines.push('- [错误] Redis 不可用/降级（redis_available=false）')
  for (const [key, meta] of Object.entries(METRIC_META)) {
    const value = meta.get()
    const level = valueLevel(key, value)
    if (level === 'normal' || value == null) continue
    const [watch, critical] = THRESHOLDS[key]
    lines.push(`- [${levelLabel(level)}] ${meta.label}: ${meta.fmt(value)}（观察 ≥${meta.fmt(watch)} / 警告 ≥${meta.fmt(critical)}）`)
  }
  if (loadLevel.value !== 'normal') {
    const load = system.value?.load_1
    const cpuCount = system.value?.cpu_count
    if (load != null && cpuCount) {
      lines.push(`- [${levelLabel(loadLevel.value)}] 系统负载 (1m): ${load}（CPU 核数 ${cpuCount}，观察 ≥${cpuCount} / 警告 ≥${cpuCount * 2}）`)
    }
  }
  if (dbConnLevel.value !== 'normal') {
    const conns = middleware.value?.db_connections
    const max = middleware.value?.db_max_connections
    if (conns != null && max) {
      lines.push(`- [${levelLabel(dbConnLevel.value)}] DB 连接数: ${conns}/${max}（观察 ≥80% / 警告 ≥95%）`)
    }
  }
  if (keyPoolLevel.value !== 'normal') {
    const active = middleware.value?.key_pool_active
    const max = middleware.value?.key_pool_max
    if (active != null && max) {
      lines.push(`- [${levelLabel(keyPoolLevel.value)}] 模型 Key 并发: ${active}/${max}（观察 ≥80% / 警告 ≥100%）`)
    }
  }
  if (netLoadLevel.value !== 'normal') {
    const s = system.value
    if (s?.net_bandwidth_mbps) {
      lines.push(`- [${levelLabel(netLoadLevel.value)}] 网络 IO 收/发: ${s.net_recv_mb_per_s}/${s.net_sent_mb_per_s} MB/s`
        + `（公网带宽 ${s.net_bandwidth_mbps} Mbps，观察 ≥80% / 警告 ≥95%）`)
    }
  }
  return lines
}

function buildAnomalyText(): string {
  const parts: string[] = [`${resolveEnvLabel()}系统健康异常：`]
  const collectedAt = overview.value?.snapshot?.collected_at || overview.value?.heartbeat?.collected_at
  parts.push(collectedAt
    ? `时间: 采集 ${formatDateTimeWithSeconds(collectedAt)}（页面更新 ${lastUpdated.value || '-'}）`
    : `时间: 采集时间未知（页面更新 ${lastUpdated.value || '-'}）`)
  const lines = collectAnomalyLines()
  if (lines.length) parts.push(`异常指标:\n${lines.join('\n')}`)
  const a = overview.value?.assessment
  if (a) {
    parts.push(`${a.source === 'llm' ? 'AI 研判' : '规则判定'}: ${a.summary}`)
    if (a.suggestion) parts.push(`建议: ${a.suggestion}`)
    const items = (a.items || []).map((i) => `${groupLabel(i.group)}·${levelLabel(i.status)}`).join(' / ')
    if (items) parts.push(`分组状态: ${items}`)
  }
  if (collectorBanner.value) parts.push(collectorBanner.value.text)
  return parts.join('\n')
}

function copyAnomalyInfo() {
  navigator.clipboard.writeText(buildAnomalyText()).then(() => {
    toast.success('已复制到剪贴板')
  }).catch(() => {
    toast.error('复制失败')
  })
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

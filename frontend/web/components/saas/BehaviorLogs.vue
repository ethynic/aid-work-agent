<template>
  <div class="page-container">
    <!-- 工具栏 -->
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">行为日志</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">
          {{ isPlatform ? '全局用户行为审计（可按租户筛选）' : '本企业用户行为审计记录' }}
        </p>
      </div>
      <div class="page-toolbar-right">
        <!-- 平台视角：租户 ID 筛选 -->
        <BaseInput
          v-if="isPlatform"
          v-model="filterTenantId"
          size="sm"
          placeholder="租户 ID"
          class="w-40"
          @keyup.enter="handleSearch('')"
        />
        <!-- 行为类型 -->
        <BaseSelect v-model="filterAction" size="sm" class="w-32" @update:model-value="handleSearch('')">
          <option value="">全部行为</option>
          <option v-for="(info, value) in BehaviorActionMap" :key="value" :value="value">
            {{ info.label }}
          </option>
        </BaseSelect>
        <!-- 资源类型 -->
        <BaseSelect v-model="filterResourceType" size="sm" class="w-32" @update:model-value="handleSearch('')">
          <option value="">全部资源</option>
          <option v-for="(info, value) in BehaviorResourceTypeMap" :key="value" :value="value">
            {{ info.label }}
          </option>
        </BaseSelect>
        <!-- 结果 -->
        <BaseSelect v-model="filterSuccess" size="sm" class="w-24" @update:model-value="handleSearch('')">
          <option value="">全部结果</option>
          <option value="true">成功</option>
          <option value="false">失败</option>
        </BaseSelect>
        <!-- 时间范围 -->
        <BaseSelect v-model="filterTimeRange" size="sm" class="w-28" @update:model-value="handleSearch('')">
          <option value="">全部时间</option>
          <option value="today">今天</option>
          <option value="7d">近 7 天</option>
          <option value="30d">近 30 天</option>
        </BaseSelect>
        <!-- 关键字 -->
        <BaseInput
          v-model="filterKeyword"
          size="sm"
          placeholder="用户ID/资源名称/资源ID"
          class="w-56"
          @keyup.enter="handleSearch('')"
        />
        <BaseButton size="sm" @click="handleSearch('')">搜索</BaseButton>
        <BaseButton size="sm" intent="secondary" @click="refresh">刷新</BaseButton>
      </div>
    </div>

    <!-- 表格 -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="logs" row-key="id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
        <template #user_id="{ row }">
          <div class="flex items-center gap-1.5">
            <span>{{ row.user_id || '-' }}</span>
            <BaseBadge
              v-if="row.user_role"
              size="sm"
              :intent="roleToIntent(row.user_role)"
            >{{ roleLabel(row.user_role) }}</BaseBadge>
          </div>
        </template>
        <template #action="{ row }">
          <BaseBadge
            size="sm"
            :intent="colorToBadgeIntent(actionInfo(row.action).color)"
          >{{ actionInfo(row.action).label }}</BaseBadge>
        </template>
        <template #resource="{ row }">
          <span class="text-muted">{{ resourceTypeInfo(row.resource_type) }}</span>
          <span v-if="row.resource_name" class="ml-1">{{ row.resource_name }}</span>
          <span v-else-if="row.resource_id" class="ml-1 font-mono text-xs text-muted">{{ row.resource_id }}</span>
        </template>
        <template #entry="{ row }">
          <BaseBadge
            v-if="row.entry"
            size="sm"
            :intent="colorToBadgeIntent(entryInfo(row.entry).color)"
          >{{ entryInfo(row.entry).label }}</BaseBadge>
          <span v-else>-</span>
        </template>
        <template #success="{ row }">
          <BaseBadge size="sm" :intent="row.success ? 'success' : 'danger'">
            {{ row.success ? '成功' : '失败' }}
          </BaseBadge>
        </template>
        <template #actions="{ row }">
          <div class="flex items-center justify-center gap-1">
            <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="showDetail(row)">详情</BaseButton>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            加载中...
          </div>
          <span v-else>暂无行为日志</span>
        </template>
      </BaseTable>
    </div>

    <!-- 分页器（必须监听 @change，否则翻页不拉数据） -->
    <BasePagination
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      @change="loadData"
    />

    <!-- 详情 Modal -->
    <BaseModal v-model="showModal" :title="`行为日志详情 #${selectedLog?.id ?? ''}`" size="lg" mode="view">
      <template v-if="selectedLog">
        <!-- 渠道回调来源提示（设计文档 §5.6：渠道事件无用户侧 IP/UA，避免误读） -->
        <div
          v-if="selectedLog.entry === 'channel'"
          class="mb-3 p-2 rounded bg-warning-50 border border-warning-200 text-xs text-warning-700"
        >
          渠道回调来源：本条事件由渠道侧回调触发，client_ip / user_agent / 设备字段为空属正常现象，不代表用户实际设备。
        </div>

        <div class="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span class="text-muted">时间：</span>
            <span class="text-default tabular-nums">{{ formatDateTime(selectedLog.created_at) }}</span>
          </div>
          <div>
            <span class="text-muted">结果：</span>
            <BaseBadge size="sm" :intent="selectedLog.success ? 'success' : 'danger'">
              {{ selectedLog.success ? '成功' : '失败' }}
            </BaseBadge>
          </div>
          <div>
            <span class="text-muted">用户：</span>
            <span class="text-default">{{ selectedLog.user_id || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">角色：</span>
            <span class="text-default">{{ roleLabel(selectedLog.user_role) }}</span>
          </div>
          <div>
            <span class="text-muted">行为：</span>
            <span class="text-default">{{ actionInfo(selectedLog.action).label }}</span>
          </div>
          <div>
            <span class="text-muted">入口：</span>
            <span class="text-default">{{ selectedLog.entry ? entryInfo(selectedLog.entry).label : '-' }}</span>
          </div>
          <div>
            <span class="text-muted">租户：</span>
            <span class="text-default">{{ selectedLog.tenant_id || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">资源类型：</span>
            <span class="text-default">{{ resourceTypeInfo(selectedLog.resource_type) }}</span>
          </div>
          <div>
            <span class="text-muted">资源 ID：</span>
            <span class="text-default font-mono text-xs">{{ selectedLog.resource_id || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">资源名称：</span>
            <span class="text-default">{{ selectedLog.resource_name || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">客户端 IP：</span>
            <span class="text-default font-mono text-xs">{{ selectedLog.client_ip || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">设备：</span>
            <span class="text-default">
              {{ deviceTypeLabel(selectedLog.device_type) }}
              <span v-if="selectedLog.device_info" class="text-muted text-xs">（{{ selectedLog.device_info }}）</span>
            </span>
          </div>
          <div v-if="selectedLog.login_method">
            <span class="text-muted">登录方式：</span>
            <span class="text-default">{{ selectedLog.login_method }}</span>
          </div>
          <div v-if="selectedLog.http_method || selectedLog.path">
            <span class="text-muted">请求：</span>
            <span class="text-default font-mono text-xs">
              {{ selectedLog.http_method || '-' }} {{ selectedLog.path || '-' }}
            </span>
          </div>
          <div v-if="selectedLog.token_id">
            <span class="text-muted">Token 指纹：</span>
            <span class="text-default font-mono text-xs">{{ selectedLog.token_id }}</span>
          </div>
          <div v-if="selectedLog.request_id">
            <span class="text-muted">Trace ID：</span>
            <span class="text-default font-mono text-xs">{{ selectedLog.request_id }}</span>
          </div>
          <div v-if="selectedLog.channel">
            <span class="text-muted">渠道：</span>
            <span class="text-default">{{ selectedLog.channel }}</span>
          </div>
          <div v-if="selectedLog.channel_user_id">
            <span class="text-muted">渠道用户：</span>
            <span class="text-default font-mono text-xs">{{ selectedLog.channel_user_id }}</span>
          </div>
        </div>

        <div v-if="selectedLog.error_msg" class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-2 uppercase tracking-wider">失败原因</h3>
          <div class="p-3 bg-danger-50 border border-danger-200 rounded-lg text-sm text-danger-700 whitespace-pre-wrap">
            {{ selectedLog.error_msg }}
          </div>
        </div>

        <div v-if="detailText" class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-2 uppercase tracking-wider">变更摘要</h3>
          <div class="p-3 bg-gray-900 border border-gray-700 rounded-lg text-xs text-success-400 overflow-auto max-h-64 whitespace-pre font-mono">
            {{ detailText }}
          </div>
        </div>

        <div v-if="selectedLog.user_agent" class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-2 uppercase tracking-wider">User-Agent</h3>
          <div class="p-3 bg-surface-hover rounded-lg text-xs text-muted break-all">
            {{ selectedLog.user_agent }}
          </div>
        </div>
      </template>
      <template #footer>
        <BaseButton size="sm" intent="secondary" @click="showModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { usePageContext } from '@/composables/usePageContext'
import {
  listBehaviorLogs,
  listBehaviorLogsPlatform,
  type BehaviorLogItem
} from '@/api/behavior-logs'
import {
  BehaviorActionMap,
  BehaviorResourceTypeMap,
  BehaviorEntryMap,
  colorToBadgeIntent
} from '@/api/enums'

/**
 * 双视角组件：
 * - scope=platform：调 /api/saas/behavior-logs（全局 + 租户 ID 筛选），平台后台 /portal 使用
 * - scope=tenant：调 /api/admin/behavior-logs（本租户），租户前台 /t/:tenant_id 使用
 */
const props = withDefaults(defineProps<{
  scope?: 'platform' | 'tenant'
}>(), {
  scope: 'tenant'
})

const isPlatform = computed(() => props.scope === 'platform')

const toast = useToast()

// ------- Columns -------
const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'created_at', label: '时间', width: '170px' },
  { key: 'user_id', label: '用户', width: '180px' },
  { key: 'action', label: '行为', width: '100px', thAlign: 'center' as const },
  {
    key: 'resource',
    label: '资源',
    minWidth: '220px',
    tooltip: (row: Record<string, any>) => {
      const type = resourceTypeInfo(row.resource_type)
      const name = row.resource_name || row.resource_id || ''
      return name ? `${type}：${name}` : type
    }
  },
  { key: 'entry', label: '入口', width: '90px', thAlign: 'center' as const },
  { key: 'success', label: '结果', width: '80px', thAlign: 'center' as const },
  { key: 'actions', label: '操作', width: '80px', thAlign: 'center' as const },
]

// ------- State -------
const filterTenantId = ref('')
const filterAction = ref('')
const filterResourceType = ref('')
const filterSuccess = ref('')
const filterTimeRange = ref('')
const filterKeyword = ref('')
const logs = ref<BehaviorLogItem[]>([])
const total = ref(0)
const showModal = ref(false)
const selectedLog = ref<BehaviorLogItem | null>(null)

// ------- Page Context -------
async function loadData() {
  try {
    const params = {
      action: filterAction.value || undefined,
      resource_type: filterResourceType.value || undefined,
      success: filterSuccess.value || undefined,
      keyword: filterKeyword.value || undefined,
      time_range: filterTimeRange.value || undefined,
      tenant_id: isPlatform.value ? (filterTenantId.value || undefined) : undefined,
      page: currentPage.value,
      page_size: pageSize.value,
    }
    const response = isPlatform.value
      ? await listBehaviorLogsPlatform(params)
      : await listBehaviorLogs(params)
    if (response.success) {
      logs.value = response.data
      total.value = response.total
    } else {
      toast.error(response.message || '加载数据失败')
      logs.value = []
      total.value = 0
    }
  } catch (error: any) {
    // 前端日志：加载行为日志失败
    console.error('前端日志：加载行为日志失败:', error)
    toast.error(error.message || '加载数据失败')
    logs.value = []
    total.value = 0
  }
}

const { currentPage, pageSize, loading, seqNumber, handleSearch, refresh } =
  usePageContext(async () => { await loadData() })
pageSize.value = 20

// ------- Detail -------
function showDetail(row: Record<string, any>) {
  // BaseTable slot 的 row 为 Record<string, any>，此处行结构即 BehaviorLogItem
  selectedLog.value = row as unknown as BehaviorLogItem
  showModal.value = true
}

const detailText = computed(() => {
  const detail = selectedLog.value?.detail
  if (!detail || (typeof detail === 'object' && Object.keys(detail).length === 0)) return ''
  try {
    return typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
})

// ------- Label helpers -------
function actionInfo(action: string): { label: string; color: string } {
  return (BehaviorActionMap as Record<string, { label: string; color: string }>)[action]
    ?? { label: action, color: 'gray' }
}

function resourceTypeInfo(type: string | null): string {
  if (!type) return '-'
  return (BehaviorResourceTypeMap as Record<string, { label: string }>)[type]?.label ?? type
}

function entryInfo(entry: string): { label: string; color: string } {
  return (BehaviorEntryMap as Record<string, { label: string; color: string }>)[entry]
    ?? { label: entry, color: 'gray' }
}

function roleLabel(role: string | null): string {
  switch (role) {
    case 'platform_admin': return '平台管理员'
    case 'tenant_admin': return '租户管理员'
    case 'user': return '用户'
    default: return role || '-'
  }
}

function roleToIntent(role: string | null): 'danger' | 'primary' | 'neutral' {
  switch (role) {
    case 'platform_admin': return 'danger'
    case 'tenant_admin': return 'primary'
    default: return 'neutral'
  }
}

function deviceTypeLabel(type: string | null): string {
  switch (type) {
    case 'pc': return 'PC'
    case 'mobile': return '移动端'
    case 'tablet': return '平板'
    case 'unknown': return '未知'
    default: return type || '-'
  }
}

// ------- Formatting -------
function formatDateTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  try {
    // 后端返回本地时间字符串（无时区后缀），直接解析，禁止拼 Z
    const date = new Date(dateStr)
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  } catch {
    return dateStr
  }
}

// ------- Init -------
onMounted(() => { refresh() })
</script>

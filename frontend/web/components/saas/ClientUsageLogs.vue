<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      title="客户端运行日志"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <div class="flex-1 min-h-0 flex flex-col p-6">
      <!-- 筛选区 -->
      <div class="page-toolbar flex-wrap">
        <div class="flex items-center gap-2 flex-wrap">
          <div class="flex flex-col">
            <label class="form-label">租户ID</label>
            <BaseInput v-model="filters.tenant_id" placeholder="租户ID" size="sm" class="w-48" @keyup.enter="onSearch" />
          </div>
          <div class="flex flex-col">
            <label class="form-label">状态</label>
            <BaseSelect v-model="filters.status" size="sm" class="w-32">
              <option value="">全部</option>
              <option value="error">错误</option>
              <option value="warning">告警</option>
              <option value="failed">失败</option>
              <option value="not_found">未找到</option>
              <option value="inconclusive">不确定</option>
              <option value="success">成功</option>
            </BaseSelect>
          </div>
          <div class="flex flex-col">
            <label class="form-label">阶段</label>
            <BaseInput v-model="filters.stage" placeholder="如 run_complete" size="sm" class="w-44" @keyup.enter="onSearch" />
          </div>
          <div class="flex flex-col">
            <label class="form-label">开始日期</label>
            <BaseInput v-model="filters.date_from" type="date" size="sm" class="w-40" />
          </div>
          <div class="flex flex-col">
            <label class="form-label">结束日期</label>
            <BaseInput v-model="filters.date_to" type="date" size="sm" class="w-40" />
          </div>
          <div class="flex items-end gap-2">
            <BaseButton size="sm" @click="onSearch">搜索</BaseButton>
            <BaseButton size="sm" intent="secondary" @click="onReset">重置</BaseButton>
            <BaseButton size="sm" intent="danger-ghost" @click="onRecentErrors">🔍 近24h错误</BaseButton>
          </div>
        </div>
        <BaseButton size="sm" intent="secondary" @click="loadData(currentPage)">刷新</BaseButton>
      </div>

      <div v-if="recentErrorsMode" class="text-xs text-warning-700 bg-warning-50 border border-warning-200 rounded px-3 py-2 mb-2">
        正在显示近 24 小时的错误/告警（跨租户）。<button class="underline" @click="exitRecentErrors">返回完整列表</button>
      </div>

      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

      <template v-else>
        <div class="table-scroll-wrapper flex-1 min-h-0">
          <BaseTable :columns="columns" :data="items" row-key="id">
            <template #seq="{ index }">{{ seqNumber(index) }}</template>
            <template #created_at="{ row }">
              <span class="text-sm text-muted whitespace-nowrap">{{ formatDateTime(row.created_at) }}</span>
            </template>
            <template #tenant_name="{ row }">
              <span class="text-sm text-default" :title="row.tenant_name || row.tenant_id">{{ row.tenant_name || row.tenant_id }}</span>
            </template>
            <template #association_name="{ row }">
              <span class="text-sm text-default">{{ row.association_name || '-' }}</span>
            </template>
            <template #stage="{ row }">
              <span class="text-xs text-muted" :title="row.stage">{{ row.stage || '-' }}</span>
            </template>
            <template #command="{ row }">
              <span class="text-xs text-default" :title="detailCommand(row)">{{ truncate(detailCommand(row), 24) }}</span>
            </template>
            <template #status="{ row }">
              <BaseBadge :intent="statusIntent(row.status)">{{ statusLabel(row.status) }}</BaseBadge>
            </template>
            <template #message="{ row }">
              <span class="text-sm text-default" :title="extractMessage(row)">{{ truncate(extractMessage(row), 60) }}</span>
            </template>
            <template #credit_cost="{ row }">
              <span class="text-sm" :class="row.credit_cost > 0 ? 'text-primary-600 font-medium' : 'text-muted'">
                {{ Number(row.credit_cost || 0) > 0 ? Number(row.credit_cost).toFixed(2) : '-' }}
              </span>
            </template>
            <template #actions="{ row }">
              <BaseButton size="sm" intent="ghost" @click="openDetail(row)">详情</BaseButton>
            </template>
            <template #empty>暂无日志记录</template>
          </BaseTable>
        </div>

        <BasePagination
          v-if="!recentErrorsMode"
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :show-size-changer="true"
          @change="onPageChange"
        />
      </template>
    </div>

    <!-- 详情弹窗 -->
    <BaseModal v-model="detailModal" title="日志详情" size="lg">
      <div v-if="detailItem" class="space-y-3 text-sm">
        <div class="grid grid-cols-2 gap-3">
          <div><span class="text-muted">时间：</span>{{ formatDateTime(detailItem.created_at) }}</div>
          <div><span class="text-muted">状态：</span><BaseBadge :intent="statusIntent(detailItem.status)">{{ statusLabel(detailItem.status) }}</BaseBadge></div>
          <div><span class="text-muted">租户：</span>{{ detailItem.tenant_name || detailItem.tenant_id }}</div>
          <div><span class="text-muted">协会：</span>{{ detailItem.association_name || '-' }}</div>
          <div><span class="text-muted">阶段：</span>{{ detailItem.stage || '-' }}</div>
          <div><span class="text-muted">积分：</span>{{ Number(detailItem.credit_cost || 0) > 0 ? Number(detailItem.credit_cost).toFixed(2) : '-' }}</div>
          <div><span class="text-muted">错误码：</span>{{ detailItem.error_code || '-' }}</div>
          <div><span class="text-muted">session：</span>{{ detailItem.session_id || '-' }}</div>
          <div><span class="text-muted">模型：</span>{{ detailItem.model || '-' }}</div>
          <div><span class="text-muted">binding：</span>{{ detailItem.binding_id }}</div>
        </div>
        <div>
          <div class="text-muted mb-1">消息</div>
          <div class="bg-gray-50 border border-default rounded p-2 break-all">{{ extractMessage(detailItem) }}</div>
        </div>
        <div v-if="detailCommand(detailItem) !== '-'">
          <div class="text-muted mb-1">命令参数</div>
          <div class="bg-gray-50 border border-default rounded p-2 break-all text-xs">
            <div class="text-default">{{ detailCommand(detailItem) }}</div>
            <pre v-if="detailArguments(detailItem)" class="whitespace-pre-wrap mt-1 text-muted">{{ detailArguments(detailItem) }}</pre>
          </div>
        </div>
        <div>
          <div class="text-muted mb-1">detail</div>
          <pre class="bg-gray-50 border border-default rounded p-2 text-xs overflow-auto max-h-60">{{ formatDetail(detailItem.detail) }}</pre>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="detailModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { listClientUsageLogs, getRecentClientErrors, type ClientUsageLogItem } from '@/api/clientUsageLogs'

const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
async function handleLogout() {
  await tenantLogout()
  router.push('/portal/login')
}

// 筛选条件（v-model 需可变，故用 reactive 字段对象）
const filters = ref({
  tenant_id: '',
  status: '',
  stage: '',
  date_from: '',
  date_to: '',
})

const items = ref<ClientUsageLogItem[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const loading = ref(true)
const recentErrorsMode = ref(false)

const detailModal = ref(false)
const detailItem = ref<ClientUsageLogItem | null>(null)

const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'created_at', label: '时间', width: '150px' },
  { key: 'tenant_name', label: '租户', width: '130px' },
  { key: 'association_name', label: '协会', width: '150px' },
  { key: 'stage', label: '阶段', width: '130px' },
  { key: 'command', label: '命令', width: '170px' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'message', label: '消息', width: '' },
  { key: 'credit_cost', label: '积分', width: '90px' },
  { key: 'actions', label: '操作', width: '90px' },
]

function seqNumber(index: number): number {
  return (currentPage.value - 1) * pageSize.value + index + 1
}

function onPageChange(page: number, size: number) {
  currentPage.value = page
  pageSize.value = size
  loadData(page)
}

function onSearch() {
  currentPage.value = 1
  recentErrorsMode.value = false
  loadData(1)
}

function onReset() {
  filters.value = { tenant_id: '', status: '', stage: '', date_from: '', date_to: '' }
  currentPage.value = 1
  recentErrorsMode.value = false
  loadData(1)
}

async function onRecentErrors() {
  loading.value = true
  recentErrorsMode.value = true
  try {
    const res = await getRecentClientErrors({ hours: 24, limit: 100, tenant_id: filters.value.tenant_id || undefined })
    if (res.success) {
      items.value = (res.items || []) as ClientUsageLogItem[]
      total.value = items.value.length
    } else {
      toast.error(res.message || '加载近期错误失败')
      items.value = []
    }
  } catch (e: any) {
    toast.error(e.message || '加载近期错误失败')
    items.value = []
  } finally {
    loading.value = false
  }
}

function exitRecentErrors() {
  recentErrorsMode.value = false
  loadData(currentPage.value)
}

async function loadData(page: number = 1) {
  loading.value = true
  recentErrorsMode.value = false
  try {
    const res = await listClientUsageLogs({
      tenant_id: filters.value.tenant_id || undefined,
      status: filters.value.status || undefined,
      stage: filters.value.stage || undefined,
      date_from: filters.value.date_from || undefined,
      date_to: filters.value.date_to || undefined,
      page,
      page_size: pageSize.value,
    })
    if (res.success) {
      items.value = (res.items || []) as ClientUsageLogItem[]
      total.value = res.total || 0
    } else {
      toast.error(res.message || '加载日志失败')
      items.value = []
      total.value = 0
    }
  } catch (e: any) {
    toast.error(e.message || '加载日志失败')
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function openDetail(row: any) {
  detailItem.value = row as ClientUsageLogItem
  detailModal.value = true
}

// ============== 工具函数 ==============

function extractMessage(row: any): string {
  const d = row.detail
  if (d && typeof d === 'object' && d.message) return String(d.message)
  if (row.error_code) return row.error_code
  return '-'
}

// 命令名（boss 工具计费行 detail.command；与 model 列同值，语义化展示）
function detailCommand(row: any): string {
  const d = row?.detail
  if (d && typeof d === 'object' && d.command) return String(d.command)
  return '-'
}

// 参数摘要（boss 工具计费行 detail.arguments：JSON 或 {_truncated: 文本}）
function detailArguments(row: any): string {
  const d = row?.detail
  if (!d || typeof d !== 'object' || d.arguments == null) return ''
  try {
    return JSON.stringify(d.arguments, null, 2)
  } catch {
    return String(d.arguments)
  }
}

function formatDetail(d: any): string {
  if (d == null) return '-'
  if (typeof d === 'string') return d
  try {
    return JSON.stringify(d, null, 2)
  } catch {
    return String(d)
  }
}

function truncate(s: string, n: number): string {
  if (!s) return '-'
  return s.length > n ? s.slice(0, n) + '…' : s
}

function formatDateTime(datetime: string): string {
  if (!datetime) return ''
  const date = new Date(datetime)
  if (isNaN(date.getTime())) return datetime
  return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function statusLabel(s?: string | null): string {
  const map: Record<string, string> = {
    error: '错误', warning: '告警', failed: '失败',
    not_found: '未找到', inconclusive: '不确定', aborted: '中止', success: '成功',
  }
  if (!s) return '-'
  return map[s] || s
}

function statusIntent(s?: string | null): 'danger' | 'warning' | 'success' | 'neutral' {
  if (s === 'error' || s === 'failed') return 'danger'
  if (s === 'warning') return 'warning'
  if (s === 'success') return 'success'
  if (s === 'not_found' || s === 'inconclusive' || s === 'aborted') return 'neutral'
  return 'neutral'
}

onMounted(() => loadData(1))
</script>

<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      title="公众号内容（跨租户）"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <div class="flex-1 min-h-0 overflow-y-auto p-6 space-y-6">
      <!-- 筛选区 -->
      <div class="page-toolbar flex-wrap">
        <div class="flex items-center gap-2 flex-wrap">
          <div class="flex flex-col">
            <label class="form-label">租户ID</label>
            <BaseInput v-model="tenantFilter" placeholder="留空查看全部租户" size="sm" class="w-56" @keyup.enter="loadAll()" />
          </div>
          <div class="flex items-end gap-2">
            <BaseButton size="sm" @click="loadAll()">搜索</BaseButton>
            <BaseButton size="sm" intent="secondary" @click="onReset">重置</BaseButton>
          </div>
        </div>
        <BaseButton size="sm" intent="ghost" @click="loadAll()">刷新</BaseButton>
      </div>

      <!-- ==================== 运行记录（跨租户） ==================== -->
      <div class="bg-surface rounded-lg border border-default p-5">
        <h2 class="text-base font-medium text-default mb-3">同步运行记录</h2>
        <div v-if="runsLoading" class="text-center py-8 text-muted text-sm">加载中...</div>
        <BaseTable v-else :columns="runColumns" :data="runs" row-key="id">
          <template #tenant_id="{ row }">
            <span class="text-xs font-mono">{{ row.tenant_id || '-' }}</span>
          </template>
          <template #trigger_type="{ row }">{{ triggerLabel(row.trigger_type) }}</template>
          <template #status="{ row }">
            <BaseBadge size="sm" :intent="runStatusIntent(row.status)">{{ runStatusLabel(row.status) }}</BaseBadge>
          </template>
          <template #counts="{ row }">
            <span class="text-xs text-muted">
              共 {{ row.total_count ?? '-' }} · 失败 {{ row.failed_count ?? 0 }}
            </span>
          </template>
          <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
          <template #completed_at="{ row }">{{ formatTime(row.completed_at) }}</template>
        </BaseTable>
        <p v-if="!runsLoading && runs.length === 0" class="text-center text-sm text-muted py-6">暂无运行记录</p>
      </div>

      <!-- ==================== 文章列表（跨租户） ==================== -->
      <div class="bg-surface rounded-lg border border-default p-5">
        <h2 class="text-base font-medium text-default mb-3">文章列表</h2>
        <div v-if="articlesLoading" class="text-center py-8 text-muted text-sm">加载中...</div>
        <BaseTable v-else :columns="articleColumns" :data="articles" row-key="id">
          <template #tenant_id="{ row }">
            <span class="text-xs font-mono">{{ row.tenant_id || '-' }}</span>
          </template>
          <template #title="{ row }">
            <span class="text-default">{{ row.title || truncate(row.original_url || row.external_id, 40) }}</span>
          </template>
          <template #status="{ row }">
            <BaseBadge size="sm" :intent="articleStatusIntent(row.status)">{{ row.status }}</BaseBadge>
          </template>
          <template #processing_status="{ row }">
            <BaseBadge size="sm" :intent="processingIntent(row.processing_status)">{{ row.processing_status || '-' }}</BaseBadge>
          </template>
          <template #error_message="{ row }">
            <span class="text-xs text-danger-600">{{ row.error_message || '-' }}</span>
          </template>
          <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
        </BaseTable>
        <p v-if="!articlesLoading && articles.length === 0" class="text-center text-sm text-muted py-6">暂无文章</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import {
  portalGetRuns,
  portalGetArticles,
  type WechatMpRun,
  type WechatMpArticle
} from '@/api/wechatMp'

const router = useRouter()
const toast = useToast()

const { admin: portalAdmin, isLoggedIn, logout } = useTenantAuth()
const effectiveIsLoggedIn = computed(() => isLoggedIn.value)
const effectiveUser = computed(() => {
  return portalAdmin.value ? {
    user_id: portalAdmin.value.user_id,
    username: portalAdmin.value.username,
    phone: portalAdmin.value.phone
  } : null
})

const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const localSidebarCollapsed = ref(false)
const isSidebarCollapsed = computed({
  get: () => sidebarCollapsed?.value ?? localSidebarCollapsed.value,
  set: (val: boolean) => {
    if (sidebarCollapsed) {
      sidebarCollapsed.value = val
    } else {
      localSidebarCollapsed.value = val
    }
  }
})

function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  } else {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }
}

async function handleLogout() {
  await logout()
  router.push('/portal/login')
}

// ==================== 数据加载 ====================

const tenantFilter = ref('')
const runsLoading = ref(false)
const runs = ref<WechatMpRun[]>([])
const articlesLoading = ref(false)
const articles = ref<WechatMpArticle[]>([])

const runColumns: TableColumn[] = [
  { key: 'id', label: 'Run ID', width: '90px' },
  { key: 'tenant_id', label: '租户', width: '180px' },
  { key: 'trigger_type', label: '触发方式', width: '100px' },
  { key: 'status', label: '状态', width: '110px' },
  { key: 'counts', label: '计数' },
  { key: 'created_at', label: '创建时间', width: '160px' },
  { key: 'completed_at', label: '完成时间', width: '160px' }
]

const articleColumns: TableColumn[] = [
  { key: 'tenant_id', label: '租户', width: '180px' },
  { key: 'title', label: '标题' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'processing_status', label: '处理状态', width: '110px' },
  { key: 'error_message', label: '失败原因' },
  { key: 'created_at', label: '创建时间', width: '160px' }
]

function currentTenantId(): string | undefined {
  return tenantFilter.value.trim() || undefined
}

async function loadAll() {
  await Promise.all([loadRuns(), loadArticles()])
}

async function loadRuns() {
  runsLoading.value = true
  try {
    const res = await portalGetRuns({ tenant_id: currentTenantId(), limit: 50 })
    runs.value = res.runs || []
  } catch (e) {
    toast.warning(e instanceof Error ? e.message : '获取运行记录失败')
  } finally {
    runsLoading.value = false
  }
}

async function loadArticles() {
  articlesLoading.value = true
  try {
    const res = await portalGetArticles({ tenant_id: currentTenantId(), limit: 50 })
    articles.value = res.articles || []
  } catch (e) {
    toast.warning(e instanceof Error ? e.message : '获取文章列表失败')
  } finally {
    articlesLoading.value = false
  }
}

function onReset() {
  tenantFilter.value = ''
  loadAll()
}

// ==================== 展示辅助 ====================

function truncate(text: string, max = 40): string {
  if (!text) return ''
  return text.length > max ? text.slice(0, max) + '...' : text
}

function formatTime(value: string | null): string {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 19)
}

const RUN_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  success: '成功',
  partial_failed: '部分失败',
  failed: '失败',
  skipped_no_credit: '余额不足跳过',
  interrupted: '已中断'
}

function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] || status
}

function runStatusIntent(status: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  switch (status) {
    case 'success': return 'success'
    case 'running': return 'primary'
    case 'queued': return 'info'
    case 'partial_failed':
    case 'skipped_no_credit': return 'warning'
    case 'failed':
    case 'interrupted': return 'danger'
    default: return 'neutral'
  }
}

const TRIGGER_LABELS: Record<string, string> = {
  manual: '手动导入',
  callback: '群发回调',
  retry: '重试',
  recheck: '存活复核',
  scheduled: '定时同步',
  agent: '智能体'
}

function triggerLabel(trigger: string): string {
  return TRIGGER_LABELS[trigger] || trigger
}

function articleStatusIntent(status: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  switch (status) {
    case 'active': return 'success'
    case 'deleted': return 'danger'
    case 'unconfirmed':
    case 'missing': return 'warning'
    default: return 'neutral'
  }
}

function processingIntent(status: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  switch (status) {
    case 'success': return 'success'
    case 'pending': return 'info'
    case 'deferred': return 'warning'
    case 'sync_failed': return 'danger'
    default: return 'neutral'
  }
}

onMounted(loadAll)
</script>

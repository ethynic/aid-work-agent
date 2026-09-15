<template>
  <div class="page-container bg-canvas">
    <AppHeader
      title="公众号内容"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <div class="page-content p-6 space-y-6">
      <!-- 未配置公众号回调时的空态引导（复用渠道配置页完成的配置能力，此处不重复实现表单） -->
      <div v-if="!hasWechatMpChannel" class="bg-info-50 border border-info-200 rounded-lg p-4 text-sm text-info-800">
        <p class="font-medium mb-1">尚未配置公众号</p>
        <p class="text-info-700">
          在「渠道配置」中添加公众号并完成服务器配置后，群发文章可自动入库；
          手动粘贴导入不依赖回调配置，可随时使用。
        </p>
        <BaseButton size="sm" intent="secondary" class="mt-2" @click="goChannelConfig">
          前往渠道配置
        </BaseButton>
      </div>

      <!-- ==================== 粘贴导入 ==================== -->
      <div class="bg-surface rounded-lg border border-default p-5">
        <h2 class="text-base font-medium text-default mb-1">粘贴文章链接导入</h2>
        <p class="text-xs text-muted mb-3">
          每行一个（或用空格/逗号分隔）mp.weixin.qq.com 文章链接，单次最多 50 条。
          提交后加入队列，同租户按顺序串行处理，可在下方运行记录中查看进度。
        </p>
        <textarea
          v-model="importText"
          rows="4"
          class="w-full rounded-lg border border-default bg-canvas px-3 py-2 text-sm text-default focus:outline-none focus:border-primary-400"
          :placeholder="'https://mp.weixin.qq.com/s/xxxx\nhttps://mp.weixin.qq.com/s?__biz=...'"
        ></textarea>
        <div class="flex items-center justify-between mt-2">
          <span class="text-xs text-muted">
            已识别 {{ parsedValidUrls.length }} 条有效链接<template v-if="parsedInvalidCount > 0">
              ，{{ parsedInvalidCount }} 条无效将被拒收</template>
          </span>
          <div class="flex items-center gap-2">
            <BaseButton intent="ghost" size="sm" @click="clearImport">清空</BaseButton>
            <BaseButton :disabled="parsedValidUrls.length === 0 || importing" @click="handleImport">
              {{ importing ? '提交中...' : '导入' }}
            </BaseButton>
          </div>
        </div>

        <!-- 导入结果 -->
        <div v-if="importResult" class="mt-3 rounded-lg p-3 text-sm" :class="importResult.accepted > 0 ? 'bg-success-50 border border-success-200 text-success-800' : 'bg-warning-50 border border-warning-200 text-warning-800'">
          <p class="font-medium">{{ importResult.message }}</p>
          <ul v-if="importResult.rejected.length" class="mt-2 space-y-1 text-xs">
            <li v-for="(r, i) in importResult.rejected" :key="'rj' + i" class="text-danger-700">
              {{ truncate(r.url) }} — {{ r.reason }}
            </li>
          </ul>
          <ul v-if="importResult.duplicates.length" class="mt-1 space-y-1 text-xs">
            <li v-for="(d, i) in importResult.duplicates" :key="'dp' + i" class="text-warning-700">
              {{ truncate(d.url) }} — {{ d.reason }}
            </li>
          </ul>
        </div>
        <p v-if="importError" class="mt-3 text-sm text-danger-600">{{ importError }}</p>
      </div>

      <!-- ==================== 运行记录 ==================== -->
      <div class="bg-surface rounded-lg border border-default p-5">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-base font-medium text-default">运行记录</h2>
          <BaseButton intent="ghost" size="sm" @click="loadRuns()">刷新</BaseButton>
        </div>
        <div v-if="runsLoading" class="text-center py-8 text-muted text-sm">加载中...</div>
        <BaseTable
          v-else
          :columns="runColumns"
          :data="runs"
          row-key="id"
          :on-row-click="toggleRunDetail"
        >
          <template #status="{ row }">
            <BaseBadge :intent="runStatusIntent(row.status)">{{ runStatusLabel(row.status) }}</BaseBadge>
          </template>
          <template #trigger_type="{ row }">{{ triggerLabel(row.trigger_type) }}</template>
          <template #counts="{ row }">
            <span class="text-xs text-muted">
              共 {{ row.total_count ?? '-' }} · 新增 {{ row.new_count ?? 0 }} · 更新 {{ row.updated_count ?? 0 }}
              · 跳过 {{ row.skipped_count ?? 0 }} · 失败 {{ row.failed_count ?? 0 }}
            </span>
          </template>
          <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
          <template #completed_at="{ row }">{{ formatTime(row.completed_at) }}</template>
          <template #expand="{ row }">
            <span class="text-primary-600 text-xs">{{ expandedRunId === row.id ? '收起明细 ▲' : '展开明细 ▼' }}</span>
          </template>
        </BaseTable>
        <p v-if="!runsLoading && runs.length === 0" class="text-center text-sm text-muted py-6">
          暂无运行记录，粘贴文章链接或等待群发回调后可见
        </p>

        <!-- 展开的 items 明细 -->
        <div v-if="expandedRunId" class="mt-3 rounded-lg border border-default bg-canvas p-3">
          <div v-if="itemsLoading" class="text-center py-4 text-muted text-sm">加载明细中...</div>
          <template v-else>
            <p v-if="runItems.length === 0" class="text-center text-xs text-muted py-4">无明细</p>
            <table v-else class="w-full text-xs">
              <thead>
                <tr class="text-muted text-left">
                  <th class="py-1.5 pr-3 font-normal">文章行</th>
                  <th class="py-1.5 pr-3 font-normal">动作</th>
                  <th class="py-1.5 pr-3 font-normal">状态</th>
                  <th class="py-1.5 pr-3 font-normal">计费</th>
                  <th class="py-1.5 font-normal">失败原因</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in runItems" :key="item.id" class="border-t border-default">
                  <td class="py-1.5 pr-3 text-default">#{{ item.article_row_id }}</td>
                  <td class="py-1.5 pr-3 text-default">{{ actionLabel(item.action) }}</td>
                  <td class="py-1.5 pr-3">
                    <BaseBadge size="sm" :intent="itemStatusIntent(item.status)">{{ itemStatusLabel(item.status) }}</BaseBadge>
                  </td>
                  <td class="py-1.5 pr-3 text-muted">{{ item.billing_status || '-' }}</td>
                  <td class="py-1.5 text-danger-600">{{ item.error_message || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </div>
      </div>

      <!-- ==================== 文章列表 ==================== -->
      <div class="bg-surface rounded-lg border border-default p-5">
        <div class="page-toolbar flex-wrap mb-3">
          <div class="flex items-center gap-2 flex-wrap">
            <h2 class="text-base font-medium text-default">文章列表</h2>
            <BaseSelect v-model="articleStatusFilter" size="sm" class="w-32" @change="loadArticles()">
              <option value="">全部状态</option>
              <option value="active">active</option>
              <option value="deleted">deleted</option>
              <option value="alias">alias</option>
              <option value="unconfirmed">unconfirmed</option>
            </BaseSelect>
            <BaseSelect v-model="articleProcessingFilter" size="sm" class="w-36" @change="loadArticles()">
              <option value="">全部处理状态</option>
              <option value="pending">pending</option>
              <option value="success">success</option>
              <option value="sync_failed">sync_failed</option>
              <option value="deferred">deferred</option>
            </BaseSelect>
          </div>
          <BaseButton intent="ghost" size="sm" @click="loadArticles()">刷新</BaseButton>
        </div>
        <div v-if="articlesLoading" class="text-center py-8 text-muted text-sm">加载中...</div>
        <BaseTable
          v-else
          :columns="articleColumns"
          :data="articles"
          row-key="id"
        >
          <template #title="{ row }">
            <span class="text-default">{{ row.title || truncate(row.original_url || row.external_id, 40) }}</span>
          </template>
          <template #status="{ row }">
            <BaseBadge size="sm" :intent="articleStatusIntent(row.status)">{{ row.status }}</BaseBadge>
          </template>
          <template #processing_status="{ row }">
            <BaseBadge size="sm" :intent="processingIntent(row.processing_status)">{{ row.processing_status || '-' }}</BaseBadge>
          </template>
          <template #last_synced_at="{ row }">{{ formatTime(row.last_synced_at) }}</template>
          <template #error_message="{ row }">
            <span class="text-xs text-danger-600">{{ row.error_message || '-' }}</span>
          </template>
          <template #ops="{ row }">
            <div class="flex items-center gap-1">
              <BaseButton
                v-if="row.processing_status === 'failed' || row.processing_status === 'sync_failed'"
                intent="ghost"
                size="sm"
                @click.stop="handleRetry(row as WechatMpArticle)"
              >重试</BaseButton>
              <BaseButton
                v-if="row.status === 'active'"
                intent="ghost"
                size="sm"
                @click.stop="handleRecheck(row as WechatMpArticle)"
              >复核</BaseButton>
            </div>
          </template>
        </BaseTable>
        <p v-if="!articlesLoading && articles.length === 0" class="text-center text-sm text-muted py-6">
          暂无文章，先在上方粘贴文章链接导入
        </p>
        <div v-if="articles.length < articlesTotal" class="flex justify-center mt-3">
          <BaseButton intent="secondary" size="sm" :disabled="articlesLoading" @click="loadMoreArticles">
            加载更多（{{ articles.length }}/{{ articlesTotal }}）
          </BaseButton>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import { listChannels } from '@/api/saasTenant'
import {
  importUrls,
  getRuns,
  getRun,
  getArticles,
  retryArticle,
  recheckArticle,
  type ImportUrlsResponse,
  type WechatMpRun,
  type WechatMpRunItem,
  type WechatMpArticle,
  type EnqueueResponse
} from '@/api/wechatMp'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const tenantId = computed(() => route.params.tenant_id as string)

const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
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
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}

function goChannelConfig() {
  router.push(`/t/${tenantId.value}/channels`)
}

// ==================== 公众号配置存在性（空态引导） ====================

const hasWechatMpChannel = ref(true)

async function checkChannelConfig() {
  try {
    const res = await listChannels()
    hasWechatMpChannel.value = (res.channels || []).some(ch => ch.channel_type === 'wechat_mp')
  } catch {
    // 查询失败不阻塞页面，保持默认（不显示引导）
    hasWechatMpChannel.value = true
  }
}

// ==================== 粘贴导入 ====================

const PAGE_LIMIT = 50

const importText = ref('')
const importing = ref(false)
const importResult = ref<ImportUrlsResponse | null>(null)
const importError = ref('')

const MP_HOST = 'mp.weixin.qq.com'

function normalizeForCheck(raw: string): string {
  const trimmed = raw.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return `https://${trimmed}`
}

/** 前端预校验：与后端 normalize_url 口径一致——/s/{token} 短链与 /s?__biz=..&mid=..&idx=..&sn=.. 长链。 */
function isValidMpArticleUrl(raw: string): boolean {
  try {
    const u = new URL(normalizeForCheck(raw))
    if (u.hostname !== MP_HOST) return false
    if (u.pathname.startsWith('/s/')) return true
    if (u.pathname === '/s') {
      const params = new URLSearchParams(u.search)
      return ['__biz', 'mid', 'idx', 'sn'].every(k => !!params.get(k))
    }
    return false
  } catch {
    return false
  }
}

const parsedValidUrls = computed<string[]>(() => {
  const tokens = importText.value.split(/[\s,;，；]+/).map(t => t.trim()).filter(Boolean)
  const seen = new Set<string>()
  const result: string[] = []
  for (const t of tokens) {
    if (!isValidMpArticleUrl(t)) continue
    const key = normalizeForCheck(t).toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    result.push(t)
    if (result.length >= PAGE_LIMIT) break
  }
  return result
})

const parsedInvalidCount = computed(() => {
  const tokens = importText.value.split(/[\s,;，；]+/).map(t => t.trim()).filter(Boolean)
  return tokens.filter(t => !isValidMpArticleUrl(t)).length
})

function clearImport() {
  importText.value = ''
  importResult.value = null
  importError.value = ''
}

async function handleImport() {
  if (parsedValidUrls.value.length === 0) return
  importing.value = true
  importError.value = ''
  importResult.value = null
  try {
    const res = await importUrls(parsedValidUrls.value)
    importResult.value = res
    importText.value = ''
    toast.success(`已受理 ${res.accepted} 条链接`)
    await Promise.all([loadRuns(), loadArticles()])
  } catch (e) {
    importError.value = e instanceof Error ? e.message : '导入失败，请稍后重试'
  } finally {
    importing.value = false
  }
}

// ==================== 运行记录 ====================

const runsLoading = ref(false)
const runs = ref<WechatMpRun[]>([])
const runsTotal = ref(0)

const expandedRunId = ref<number | null>(null)
const itemsLoading = ref(false)
const runItems = ref<WechatMpRunItem[]>([])

const runColumns: TableColumn[] = [
  { key: 'id', label: 'Run ID', width: '90px' },
  { key: 'trigger_type', label: '触发方式', width: '100px' },
  { key: 'status', label: '状态', width: '110px' },
  { key: 'counts', label: '计数' },
  { key: 'created_at', label: '创建时间', width: '160px' },
  { key: 'completed_at', label: '完成时间', width: '160px' },
  { key: 'expand', label: '', width: '110px' }
]

async function loadRuns() {
  runsLoading.value = true
  try {
    const res = await getRuns({ limit: 20 })
    runs.value = res.runs || []
    runsTotal.value = res.total
    if (expandedRunId.value && !runs.value.some(r => r.id === expandedRunId.value)) {
      expandedRunId.value = null
      runItems.value = []
    }
  } catch (e) {
    toast.warning(e instanceof Error ? e.message : '获取运行记录失败')
  } finally {
    runsLoading.value = false
  }
}

async function toggleRunDetail(row: Record<string, any>) {
  const run = row as WechatMpRun
  if (expandedRunId.value === run.id) {
    expandedRunId.value = null
    runItems.value = []
    return
  }
  expandedRunId.value = run.id
  itemsLoading.value = true
  runItems.value = []
  try {
    const res = await getRun(run.id)
    runItems.value = res.items || []
  } catch (e) {
    toast.warning(e instanceof Error ? e.message : '获取运行明细失败')
    expandedRunId.value = null
  } finally {
    itemsLoading.value = false
  }
}

// ==================== 文章列表 ====================

const articlesLoading = ref(false)
const articles = ref<WechatMpArticle[]>([])
const articlesTotal = ref(0)
const articleStatusFilter = ref('')
const articleProcessingFilter = ref('')

const articleColumns: TableColumn[] = [
  { key: 'title', label: '标题' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'processing_status', label: '处理状态', width: '110px' },
  { key: 'last_synced_at', label: '最近同步', width: '160px' },
  { key: 'error_message', label: '失败原因' },
  { key: 'ops', label: '操作', width: '140px' }
]

async function loadArticles(offset = 0) {
  articlesLoading.value = true
  try {
    const res = await getArticles({
      status: articleStatusFilter.value || undefined,
      processing_status: articleProcessingFilter.value || undefined,
      limit: 20,
      offset
    })
    if (offset > 0) {
      articles.value = articles.value.concat(res.articles || [])
    } else {
      articles.value = res.articles || []
    }
    articlesTotal.value = res.total
  } catch (e) {
    toast.warning(e instanceof Error ? e.message : '获取文章列表失败')
  } finally {
    articlesLoading.value = false
  }
}

async function loadMoreArticles() {
  await loadArticles(articles.value.length)
}

async function handleRetry(row: WechatMpArticle) {
  try {
    const res: EnqueueResponse = await retryArticle(row.id)
    toast.success(res.message || '已重新入队')
    await Promise.all([loadRuns(), loadArticles()])
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '重试失败')
  }
}

async function handleRecheck(row: WechatMpArticle) {
  try {
    const res: EnqueueResponse = await recheckArticle(row.id)
    toast.success(res.message || '复核任务已入队')
    await loadRuns()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '发起复核失败')
  }
}

// ==================== 展示辅助 ====================

function truncate(text: string, max = 60): string {
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

const ITEM_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  running: '处理中',
  success: '成功',
  failed: '失败',
  skipped: '已跳过',
  deferred: '待后续处理',
  interrupted: '已中断'
}

function itemStatusLabel(status: string | null): string {
  return ITEM_STATUS_LABELS[status || ''] || status || '-'
}

function itemStatusIntent(status: string | null): 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  switch (status) {
    case 'success': return 'success'
    case 'running': return 'primary'
    case 'pending': return 'info'
    case 'skipped':
    case 'deferred': return 'warning'
    case 'failed':
    case 'interrupted': return 'danger'
    default: return 'neutral'
  }
}

const ACTION_LABELS: Record<string, string> = {
  new: '新增入库',
  update: '更新',
  delete: '删除',
  restore: '恢复',
  check: '存活复核'
}

function actionLabel(action: string | null): string {
  return ACTION_LABELS[action || ''] || action || '默认'
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

onMounted(async () => {
  await Promise.all([checkChannelConfig(), loadRuns(), loadArticles()])
})
</script>

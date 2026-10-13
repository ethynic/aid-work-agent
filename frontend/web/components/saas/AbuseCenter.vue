<template>
  <div class="page-container bg-canvas" v-bind="$attrs">
    <AppHeader
      title="防滥用中心"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="page-content p-6 gap-4 overflow-y-auto">
      <!-- 租户默认策略 -->
      <BaseCard title="租户默认策略">
        <p class="text-xs text-muted mb-3">
          本租户未单独配置的用户与渠道，按此默认策略评估（user 级 > channel 级 > 本默认 > 平台默认）。
        </p>
        <AbusePolicyEditor scope-type="tenant" @saved="onPolicySaved" />
      </BaseCard>

      <!-- Tab 页签 -->
      <div class="border-b border-default">
        <div class="flex gap-6">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            type="button"
            :class="['pb-2 text-sm font-medium border-b-2 transition-colors', activeTab === tab.key ? 'text-primary-600 border-primary-600' : 'text-muted border-transparent hover:text-default hover:border-hover']"
            @click="switchTab(tab.key)"
          >{{ tab.label }}</button>
        </div>
      </div>

      <!-- 用量监控 -->
      <div v-show="activeTab === 'usage'" class="space-y-4">
        <div class="flex items-center gap-2">
          <BaseSelect v-model="usageWindow" size="sm" class="w-40" @change="loadUsageSummary">
            <option value="hour">近 1 小时</option>
            <option value="day">近 1 天</option>
          </BaseSelect>
          <span class="text-xs text-muted">按窗口内会话积分消耗排序（Top 20）</span>
        </div>

        <div v-if="usageLoading" class="text-center py-12 text-muted">加载中...</div>
        <template v-else-if="usageItems.length > 0">
          <div class="table-scroll-wrapper">
            <BaseTable :columns="usageColumns" :data="usageItems" row-key="user_id">
              <template #index="{ index }">{{ index + 1 }}</template>
              <template #username="{ row }">
                {{ row.nickname || row.username || '-' }}
                <span v-if="row.channel_source" class="ml-1 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-info-100 text-info-700">
                  {{ getUserSourceInfo(row.channel_source).label }}
                </span>
              </template>
              <template #total_credits="{ row }">{{ formatCredits(row.total_credits) }}</template>
              <template #user_status="{ row }">
                <BaseBadge :intent="row.user_status === 'suspended' ? 'danger' : 'success'">
                  {{ row.user_status === 'suspended' ? '已停用' : '正常' }}
                </BaseBadge>
              </template>
              <template #actions="{ row }">
                <div class="flex gap-2">
                  <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openDetail(row)">查看明细</BaseButton>
                  <BaseButton
                    v-if="row.user_status !== 'suspended'"
                    intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs"
                    @click="handleToggleStatus(row, 'suspended')"
                  >停用</BaseButton>
                  <BaseButton
                    v-else intent="ghost" size="sm" class="whitespace-nowrap text-xs"
                    @click="handleToggleStatus(row, 'active')"
                  >启用</BaseButton>
                </div>
              </template>
            </BaseTable>
          </div>

          <!-- 单用户明细弹框 -->
          <BaseModal
            v-model="showDetailModal"
            :title="detailUser ? `${detailUser.nickname || detailUser.username || detailUser.user_id} 的请求明细` : '请求明细'"
            size="lgx"
          >
            <div class="text-xs text-muted mb-2">窗口合计 {{ formatCredits(detailTotalCredits) }} 积分</div>
            <div v-if="detailLoading" class="text-center py-6 text-muted text-sm">加载中...</div>
            <template v-else-if="detailItems.length > 0">
              <div class="table-scroll-wrapper">
                <BaseTable :columns="detailColumns" :data="detailItems" row-key="id" table-fixed>
                  <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
                  <template #user_message="{ row }">
                    <span class="block truncate">{{ row.user_message }}</span>
                  </template>
                  <template #credit_cost="{ row }">{{ formatCredits(row.credit_cost) }}</template>
                </BaseTable>
              </div>
            </template>
            <div v-else class="text-center py-6 text-muted text-sm">窗口内暂无会话消耗记录</div>
            <template #footer>
              <BaseButton intent="secondary" @click="showDetailModal = false">关闭</BaseButton>
            </template>
          </BaseModal>
        </template>
        <div v-else class="text-center py-12 text-muted">窗口内暂无会话积分消耗</div>
      </div>

      <!-- 禁用记录 -->
      <div v-show="activeTab === 'suspended'" class="space-y-4">
        <div class="flex items-center gap-2">
          <BaseSelect v-model="suspendedSource" size="sm" class="w-40" @change="loadSuspendedUsers">
            <option value="all">全部来源</option>
            <option value="channel">仅渠道用户</option>
            <option value="internal">仅内部用户</option>
          </BaseSelect>
          <span class="text-xs text-muted">当前处于停用状态的用户</span>
        </div>

        <div v-if="suspendedLoading" class="text-center py-12 text-muted">加载中...</div>
        <template v-else-if="suspendedUsers.length > 0">
          <div class="table-scroll-wrapper">
            <BaseTable :columns="suspendedColumns" :data="pagedSuspended" row-key="user_id">
              <template #index="{ index }">{{ (suspendedPage - 1) * pageSize + index + 1 }}</template>
              <template #username="{ row }">{{ row.nickname || row.username || '-' }}</template>
              <template #phone="{ row }">{{ row.phone || '-' }}</template>
              <template #role="{ row }">{{ row.role === 'tenant_admin' ? '租户管理员' : '普通用户' }}</template>
              <template #source="{ row }">
                <span v-if="row.source" class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-info-100 text-info-700">
                  {{ getUserSourceInfo(row.source).label }}
                </span>
                <span v-else class="text-xs text-muted">内部</span>
              </template>
              <template #actions="{ row }">
                <div class="flex gap-2">
                  <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="goUsageDetail(row)">查看消耗</BaseButton>
                  <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleToggleStatus(row, 'active')">启用</BaseButton>
                </div>
              </template>
            </BaseTable>
          </div>
          <BasePagination
            :total="suspendedTotal"
            v-model:currentPage="suspendedPage"
            :page-size="pageSize"
            @change="loadSuspendedUsers"
          />
        </template>
        <div v-else class="text-center py-12 text-muted">暂无停用用户</div>
      </div>

      <!-- 触发记录 -->
      <div v-show="activeTab === 'events'" class="space-y-4">
        <div v-if="eventsLoading" class="text-center py-12 text-muted">加载中...</div>
        <template v-else-if="events.length > 0">
          <div class="table-scroll-wrapper">
            <BaseTable :columns="eventColumns" :data="events" row-key="id" table-fixed>
              <template #index="{ index }">{{ (eventsPage - 1) * pageSize + index + 1 }}</template>
              <template #rule_id="{ row }">{{ ruleNames[row.rule_id] || row.rule_id }}</template>
              <template #action="{ row }">
                <BaseBadge :intent="row.action === 'hard_blocked' ? 'danger' : row.action === 'warned' ? 'warning' : 'info'">
                  {{ actionLabel(row.action) }}
                </BaseBadge>
              </template>
              <template #scope_type="{ row }">{{ scopeLabel(row.scope_type, row) }}</template>
              <template #detail="{ row }">
                <span class="block truncate text-xs">{{ formatEventDetail(row.detail) }}</span>
              </template>
              <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
            </BaseTable>
          </div>
          <BasePagination
            :total="eventsTotal"
            v-model:currentPage="eventsPage"
            :page-size="pageSize"
            @change="loadEvents"
          />
        </template>
        <div v-else class="text-center py-12 text-muted">暂无触发记录（观察模式下达到阈值即产生记录）</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'

defineOptions({ inheritAttrs: false })

import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import AbusePolicyEditor from '@/components/saas/AbusePolicyEditor.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { getUserSourceInfo } from '@/api/enums'
import {
  listAbuseEvents,
  getAbuseUsageSummary,
  getAbuseUsageDetail,
  listSuspendedUsers,
  updateUserStatus,
  type AbuseUsageSummary,
  type AbuseUsageDetail,
  type AbuseSuspendedUser,
  type AbuseEvent,
} from '@/api/abuse'

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
    if (sidebarCollapsed) sidebarCollapsed.value = val
    else localSidebarCollapsed.value = val
  }
})

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
  else isSidebarCollapsed.value = !isSidebarCollapsed.value
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}

// ============== Tab 管理 ==============

type TabKey = 'usage' | 'suspended' | 'events'
const tabs = [
  { key: 'usage' as TabKey, label: '用量监控' },
  { key: 'suspended' as TabKey, label: '禁用记录' },
  { key: 'events' as TabKey, label: '触发记录' },
]
const activeTab = ref<TabKey>('usage')
const loadedTabs = ref<Set<TabKey>>(new Set(['usage']))

function switchTab(key: TabKey) {
  activeTab.value = key
  if (!loadedTabs.value.has(key)) {
    loadedTabs.value.add(key)
    if (key === 'usage') loadUsageSummary()
    else if (key === 'suspended') loadSuspendedUsers()
    else if (key === 'events') loadEvents()
  }
}

function onPolicySaved() {
  toast.success('租户默认策略已保存')
}

// ============== 用量监控 ==============

const pageSize = 20
const usageWindow = ref<'hour' | 'day'>('day')
const usageLoading = ref(false)
const usageItems = ref<AbuseUsageSummary[]>([])
const detailUser = ref<AbuseUsageSummary | null>(null)
const showDetailModal = ref(false)
const detailLoading = ref(false)
const detailItems = ref<AbuseUsageDetail[]>([])
const detailTotalCredits = ref(0)

const usageColumns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'username', label: '用户' },
  { key: 'total_credits', label: '积分消耗', width: '110px' },
  { key: 'request_count', label: '请求数', width: '90px' },
  { key: 'last_request_at', label: '最近请求', width: '170px' },
  { key: 'user_status', label: '状态', width: '90px' },
  { key: 'actions', label: '操作', width: '160px', thAlign: 'center' as const },
]

const detailColumns = [
  { key: 'created_at', label: '时间', width: '170px' },
  { key: 'user_message', label: '用户消息' },
  { key: 'credit_cost', label: '积分', width: '90px' },
  { key: 'source_type', label: '来源', width: '140px' },
]

async function loadUsageSummary() {
  usageLoading.value = true
  detailUser.value = null
  showDetailModal.value = false
  try {
    const res = await getAbuseUsageSummary(usageWindow.value)
    usageItems.value = res.items || []
  } catch (e: any) {
    toast.error(e.message || '获取用量汇总失败')
  } finally {
    usageLoading.value = false
  }
}

// 禁用记录「查看消耗」跳转时复用此函数打开明细弹框
async function openDetail(row: any) {
  detailUser.value = row
  showDetailModal.value = true
  detailLoading.value = true
  try {
    const res = await getAbuseUsageDetail(usageWindow.value, row.user_id)
    detailItems.value = res.items || []
    detailTotalCredits.value = res.total_credits || 0
  } catch (e: any) {
    toast.error(e.message || '获取用量明细失败')
  } finally {
    detailLoading.value = false
  }
}

async function handleToggleStatus(row: any, status: 'active' | 'suspended') {
  const tip = status === 'suspended'
    ? '确定停用该用户吗？停用后其 Web 与渠道侧均无法继续使用。'
    : '确定启用该用户吗？'
  if (!confirm(tip)) return
  try {
    await updateUserStatus(row.user_id, status)
    toast.success(status === 'suspended' ? '用户已停用' : '用户已启用')
    await loadUsageSummary()
    loadedTabs.value.delete('suspended')
    suspendedPage.value = 1
  } catch (e: any) {
    toast.error(e.message || '更新用户状态失败')
  }
}

// ============== 禁用记录 ==============

const suspendedSource = ref<'all' | 'channel' | 'internal'>('all')
const suspendedLoading = ref(false)
const suspendedUsers = ref<AbuseSuspendedUser[]>([])
const suspendedTotal = ref(0)
const suspendedPage = ref(1)

const suspendedColumns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'username', label: '用户' },
  { key: 'phone', label: '手机号', width: '130px' },
  { key: 'role', label: '角色', width: '110px' },
  { key: 'source', label: '来源', width: '120px' },
  { key: 'created_at', label: '创建时间', width: '170px' },
  { key: 'actions', label: '操作', width: '160px', thAlign: 'center' as const },
]

const pagedSuspended = computed(() => suspendedUsers.value)

async function loadSuspendedUsers() {
  suspendedLoading.value = true
  try {
    const res = await listSuspendedUsers({
      status: 'suspended',
      source: suspendedSource.value,
      page: suspendedPage.value,
      page_size: pageSize,
    })
    suspendedUsers.value = res.users || []
    suspendedTotal.value = res.total || 0
  } catch (e: any) {
    toast.error(e.message || '获取禁用记录失败')
  } finally {
    suspendedLoading.value = false
  }
}

function goUsageDetail(row: any) {
  const summary: AbuseUsageSummary = {
    user_id: row.user_id,
    username: row.username || '',
    nickname: row.nickname || '',
    channel_source: row.source,
    user_status: row.status,
    total_credits: 0,
    request_count: 0,
    last_request_at: '',
  }
  activeTab.value = 'usage'
  loadedTabs.value.add('usage')
  void openDetail(summary)
}

// ============== 触发记录 ==============

const eventsLoading = ref(false)
const events = ref<AbuseEvent[]>([])
const eventsTotal = ref(0)
const eventsPage = ref(1)

const eventColumns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'created_at', label: '时间', width: '170px' },
  { key: 'rule_id', label: '规则', width: '130px' },
  { key: 'action', label: '动作', width: '110px' },
  { key: 'scope_type', label: '作用域', width: '130px' },
  { key: 'user_id', label: '用户', width: '150px' },
  { key: 'detail', label: '详情' },
]

const ruleNames = ref<Record<string, string>>({})

async function loadRuleNames() {
  try {
    const { getAbusePresets } = await import('@/api/abuse')
    const res = await getAbusePresets()
    const map: Record<string, string> = {}
    for (const r of res.rules || []) map[r.rule_id] = r.name
    ruleNames.value = map
  } catch {
    // 名称映射失败时回退显示 rule_id
  }
}

const actionMap: Record<string, string> = {
  warned: '预警',
  hard_blocked: '硬限拦截',
  soft_notified: '软提醒',
}
function actionLabel(action: string): string {
  return actionMap[action] || action
}

function scopeLabel(scopeType: string, row: any): string {
  if (scopeType === 'tenant') return '租户默认'
  if (scopeType === 'user') return '用户'
  if (scopeType === 'channel') return `渠道${row.channel_type ? `（${row.channel_type}）` : ''}`
  return scopeType
}

function formatEventDetail(detail: Record<string, any> | null): string {
  if (!detail) return '-'
  const parts: string[] = []
  if (detail.level) parts.push(String(detail.level))
  if (detail.period) parts.push(`窗口=${detail.period === 'day' ? '近1天' : '近1小时'}`)
  if (detail.actual_credits != null) parts.push(`消耗=${detail.actual_credits}`)
  if (detail.warn_threshold != null) parts.push(`预警阈值=${detail.warn_threshold}`)
  if (detail.hard_threshold != null) parts.push(`硬限阈值=${detail.hard_threshold}`)
  return parts.join('，') || JSON.stringify(detail)
}

async function loadEvents() {
  eventsLoading.value = true
  try {
    const res = await listAbuseEvents({ page: eventsPage.value, page_size: pageSize })
    events.value = res.items || []
    eventsTotal.value = res.total || 0
  } catch (e: any) {
    toast.error(e.message || '获取触发记录失败')
  } finally {
    eventsLoading.value = false
  }
}

// ============== 工具 ==============

function formatCredits(v: number): string {
  const n = Number(v) || 0
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

function formatTime(t: string): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  loadRuleNames()
  // 站内信深链：?focus_user=xxx&window=hour|day -> 加载后直接展开该用户明细弹框
  const focusUser = route.query.focus_user as string
  const focusWindow = route.query.window as string
  if (focusWindow === 'hour' || focusWindow === 'day') usageWindow.value = focusWindow
  await loadUsageSummary()
  if (focusUser) {
    const row = usageItems.value.find(i => i.user_id === focusUser)
    if (row) {
      await openDetail(row)
    } else {
      // 汇总列表中无此用户（如窗口刚切换），仍直接打开明细弹框
      await openDetail({
        user_id: focusUser,
        username: '',
        nickname: '',
        channel_source: null,
        user_status: 'active',
        total_credits: 0,
        request_count: 0,
        last_request_at: '',
      })
    }
  }
})
</script>

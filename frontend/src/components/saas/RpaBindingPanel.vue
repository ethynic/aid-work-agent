<template>
  <div class="page-container bg-canvas">
    <AppHeader
      title="RPA 绑定管理"
      :is-logged-in="isLoggedIn"
      :user="admin"
      @toggle-sidebar="handleToggleSidebar"
    />

    <div class="page-content p-6">
      <!-- 过滤区 -->
      <div class="page-toolbar">
        <div class="page-toolbar-left">
          <BaseSelect v-model="statusFilter" size="sm" class="w-44" @update:model-value="onFilterChange">
            <option value="">全部状态</option>
            <option value="needs_review_only">需关注（除正常外全部）</option>
            <option value="active">正常</option>
            <option value="disabled">已停用</option>
          </BaseSelect>
          <BaseInput
            v-model="tenantKeyword"
            size="sm"
            placeholder="搜索租户ID/租户名"
            class="w-60"
            @keyup.enter="onFilterChange"
          />
          <BaseInput
            v-model="clientKeyword"
            size="sm"
            placeholder="搜索客户端名/client_id/账号名"
            class="w-72"
            @keyup.enter="onFilterChange"
          />
          <BaseButton size="sm" @click="onFilterChange">搜索</BaseButton>
        </div>
        <div class="page-toolbar-right">
          <BaseButton intent="secondary" size="sm" @click="loadData">刷新</BaseButton>
          <BaseButton size="sm" @click="openCreateDialog">+ 新增绑定</BaseButton>
        </div>
      </div>

      <!-- 表格 -->
      <div v-if="loading && allClients.length === 0" class="text-center py-12 text-muted">加载中...</div>
      <div v-else class="table-scroll-wrapper flex-1 min-h-0">
        <BaseTable :columns="columns" :data="displayedClients" row-key="client_id">
          <template #seq="{ index }">{{ seqNumber(index) }}</template>
          <template #tenant="{ row }">
            <div class="text-sm font-medium text-default">{{ (row as any).tenant_name || row.tenant_id || '-' }}</div>
            <div class="text-xs text-muted font-mono">{{ row.tenant_id || '-' }}</div>
          </template>
          <template #client="{ row }">
            <div class="text-sm text-default">{{ row.client_name || '-' }}</div>
            <div class="text-xs text-muted font-mono">{{ row.client_id || '-' }}</div>
          </template>
          <template #account_count="{ row }">
            <span v-if="row.account_count > 0" class="inline-flex items-center gap-1">
              <BaseBadge :intent="row.account_count > 1 ? 'info' : 'neutral'">{{ row.account_count }} 个账号</BaseBadge>
              <span v-if="row.last_account_name" class="text-xs text-muted">（{{ row.last_account_name }}<span v-if="row.account_count > 1"> 等</span>）</span>
            </span>
            <span v-else class="text-xs text-muted">尚未接入</span>
          </template>
          <template #agent_base_url="{ row }">
            <div v-if="!row.agent_base_url" class="text-xs text-muted">未填写</div>
            <div v-else-if="isPlaceholderUrl(row.agent_base_url)" class="text-xs text-warning-700">
              ⚠ {{ row.agent_base_url }}
            </div>
            <div v-else class="text-xs text-info-700 font-mono break-all">{{ row.agent_base_url }}</div>
          </template>
          <template #client_status="{ row }">
            <BaseBadge :intent="clientStatusIntent(row.client_status)">{{ clientStatusLabel(row.client_status) }}</BaseBadge>
          </template>
          <template #connection_status="{ row }">
            <BaseBadge :intent="connectionBadge(row.last_heartbeat_at).intent">
              {{ connectionBadge(row.last_heartbeat_at).label }}
            </BaseBadge>
          </template>
          <template #last_heartbeat_at="{ row }">{{ formatTime(row.last_heartbeat_at) }}</template>
          <template #actions="{ row }">
            <div class="flex justify-center gap-1 whitespace-nowrap">
              <BaseButton
                v-if="canPause(row.client_status)"
                intent="danger-ghost" size="sm"
                :disabled="pausing || resuming"
                @click="onPause(row as RpaClientRow)"
              >暂停</BaseButton>
              <BaseButton
                v-if="canResume(row.client_status)"
                intent="ghost" size="sm"
                :disabled="pausing || resuming"
                @click="onResume(row as RpaClientRow)"
              >恢复</BaseButton>
              <BaseButton
                intent="ghost" size="sm"
                :disabled="rotating"
                @click="onRotate(row as RpaClientRow)"
              >轮换密钥</BaseButton>
              <BaseButton intent="ghost" size="sm" @click="openDetail(row)">详情</BaseButton>
            </div>
          </template>
          <template #empty>暂无客户端数据</template>
        </BaseTable>
      </div>

      <BasePagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="filteredClients.length"
        :show-size-changer="true"
      />
    </div>

    <!-- 详情弹框 -->
    <BaseModal v-model="showDetail" title="客户端详情" size="lg">
      <div v-if="current" class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">租户ID</label>
            <div class="text-sm text-default font-mono">{{ current.tenant_id || '-' }}</div>
          </div>
          <div>
            <label class="form-label">租户名</label>
            <div class="text-sm text-default">{{ (current as any).tenant_name || '-' }}</div>
          </div>
          <div>
            <label class="form-label">客户端ID</label>
            <div class="text-sm text-default font-mono">{{ current.client_id || '-' }}</div>
          </div>
          <div>
            <label class="form-label">客户端名称</label>
            <div class="text-sm text-default">{{ current.client_name || '-' }}</div>
          </div>
          <div>
            <label class="form-label">客户端状态</label>
            <BaseBadge :intent="clientStatusIntent(current.client_status)">{{ clientStatusLabel(current.client_status) }}</BaseBadge>
          </div>
          <div>
            <label class="form-label">连接状态</label>
            <BaseBadge :intent="connectionBadge(current.last_heartbeat_at).intent">
              {{ connectionBadge(current.last_heartbeat_at).label }}
            </BaseBadge>
          </div>
          <div>
            <label class="form-label">最近心跳</label>
            <div class="text-sm text-default">{{ formatTime(current.last_heartbeat_at) }}</div>
          </div>
          <div>
            <label class="form-label">最小版本</label>
            <div class="text-sm text-default font-mono">{{ current.min_version || '-' }}</div>
          </div>
          <div>
            <label class="form-label">账号数</label>
            <div class="text-sm text-default">{{ current.account_count ?? 0 }}</div>
          </div>
          <div>
            <label class="form-label">绑定数</label>
            <div class="text-sm text-default">{{ current.binding_count ?? 0 }}</div>
          </div>
          <div>
            <label class="form-label">创建时间</label>
            <div class="text-sm text-default">{{ formatTime(current.created_at) }}</div>
          </div>
          <div>
            <label class="form-label">更新时间</label>
            <div class="text-sm text-default">{{ formatTime(current.updated_at) }}</div>
          </div>
        </div>

        <div>
          <label class="form-label">agent_base_url（客户端回填的生产服务地址）</label>
          <BaseInput v-model="agentBaseUrlDraft" placeholder="https://agent.example.com" />
          <div v-if="isPlaceholderUrl(agentBaseUrlDraft)" class="mt-1 text-xs text-warning-700">
            ⚠ 未修改占位地址，生产环境请填写真实 URL（勿用 localhost）
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showDetail = false">取消</BaseButton>
        <BaseButton :disabled="savingUrl" @click="onSaveAgentUrl">{{ savingUrl ? '保存中...' : '保存 agent_base_url' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- 新增绑定表单弹框（平台管理员代管理：选目标租户 + 绑定名称 + 关联数字员工） -->
    <BaseModal
      v-model="showCreateDialog"
      title="新增 RPA 绑定"
      size="md"
      :close-on-overlay="false"
    >
      <div class="space-y-4">
        <div class="bg-info-50 border border-info-200 text-info-800 rounded-lg p-3 text-xs">
          平台管理员代管理：将为此租户创建一个 RPA 客户端，注册成功后会返回 <b>client_secret</b>（仅显示一次）。
        </div>

        <div>
          <label class="form-label">目标租户 <span class="form-required">*</span></label>
          <BaseSelect v-model="createForm.tenantId" :state="createError.tenantId ? 'error' : 'default'">
            <option value="">请选择租户</option>
            <option v-for="t in tenantOptions" :key="t.tenant_id" :value="t.tenant_id">
              {{ t.company_name }} ({{ t.tenant_id }})
            </option>
          </BaseSelect>
          <div v-if="createError.tenantId" class="mt-1 text-xs text-danger-500">{{ createError.tenantId }}</div>
          <div v-else-if="tenantOptions.length === 0 && tenantsLoaded" class="mt-1 text-xs text-muted">
            无可用租户
          </div>
        </div>

        <div>
          <label class="form-label">绑定名称 <span class="form-required">*</span></label>
          <BaseInput
            v-model="createForm.name"
            placeholder="如：销售一组-客户机01"
            :state="createError.name ? 'error' : 'default'"
          />
          <div v-if="createError.name" class="mt-1 text-xs text-danger-500">{{ createError.name }}</div>
        </div>

        <div>
          <label class="form-label">关联数字员工（可选）</label>
          <BaseSelect v-model="createForm.subagentType" :disabled="!createForm.tenantId">
            <option value="">不关联</option>
            <option v-for="s in availableSubagents" :key="s" :value="s">{{ s }}</option>
          </BaseSelect>
          <div v-if="createForm.tenantId && availableSubagents.length === 0 && subagentsLoaded" class="mt-1 text-xs text-muted">
            该租户暂无已订阅数字员工
          </div>
          <div v-if="!createForm.tenantId" class="mt-1 text-xs text-muted">请先选择目标租户</div>
        </div>

        <div class="text-xs text-muted">
          agent_base_url 创建时留空，可在客户端详情中补填。
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showCreateDialog = false">取消</BaseButton>
        <BaseButton :disabled="creating" @click="handleCreate">{{ creating ? '创建中...' : '创建' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- 密钥一次性展示弹框（关闭按钮默认禁用，勾选「已保存」后才能关） -->
    <BaseModal
      v-model="showSecretDialog"
      title="客户端密钥（仅显示一次，关闭后不可再次获取）"
      size="lg"
      :close-on-overlay="false"
    >
      <div v-if="secretInfo" class="space-y-4">
        <div class="bg-warning-50 border border-warning-200 text-warning-800 rounded-lg p-3 text-sm">
          ⚠️ <b>client_secret 仅本次显示一次</b>，关闭后服务端无法找回（仅存加密哈希）。请立即复制并妥善保存。
        </div>

        <!-- client_id -->
        <div>
          <label class="form-label">client_id</label>
          <div class="flex items-center gap-2">
            <code class="flex-1 px-3 py-2 bg-canvas rounded font-mono text-sm break-all">{{ secretInfo.clientId }}</code>
            <BaseButton intent="ghost" size="sm" @click="copyText(secretInfo.clientId)">复制</BaseButton>
          </div>
        </div>

        <!-- client_secret（明文，仅此一次） -->
        <div>
          <label class="form-label">client_secret（明文，仅此一次）</label>
          <div class="flex items-center gap-2">
            <code class="flex-1 px-3 py-2 bg-canvas rounded font-mono text-sm break-all">{{ secretInfo.clientSecret }}</code>
            <BaseButton intent="ghost" size="sm" @click="copyText(secretInfo.clientSecret)">复制</BaseButton>
          </div>
        </div>

        <!-- 一键复制的 yaml 配置片段（client_secret 用 DPAPI 占位，绝不塞明文） -->
        <div>
          <label class="form-label">客户端配置片段（appsettings.yaml，可一键复制）</label>
          <div class="relative">
            <pre class="text-xs bg-canvas rounded-lg p-3 overflow-auto max-h-[200px] whitespace-pre-wrap break-all border border-default">{{ secretInfo.yamlSnippet }}</pre>
            <BaseButton intent="ghost" size="sm" class="absolute top-2 right-2" @click="copyText(secretInfo.yamlSnippet)">复制</BaseButton>
          </div>
          <div class="mt-1 text-xs text-muted">
            注：yaml 中 <code>client_secret_ref</code> 使用 DPAPI 占位，真实 secret 请用客户端的 DPAPI 写入工具落盘，<b>切勿</b>把上面明文 secret 直接贴到 yaml。
          </div>
        </div>

        <!-- agent_base_url 占位 -->
        <div>
          <label class="form-label">agent_base_url（占位，请改为真实生产地址）</label>
          <div class="flex items-center gap-2">
            <code class="flex-1 px-3 py-2 bg-canvas rounded font-mono text-sm break-all">{{ PLACEHOLDER_URL }}</code>
            <BaseButton intent="ghost" size="sm" @click="copyText(PLACEHOLDER_URL)">复制</BaseButton>
          </div>
          <div class="mt-1 text-xs text-warning-700">⚠ 未修改占位地址，生产环境请填写真实 URL（勿用 localhost）</div>
        </div>

        <!-- tenant_id（自动填） -->
        <div>
          <label class="form-label">tenant_id</label>
          <div class="flex items-center gap-2">
            <code class="flex-1 px-3 py-2 bg-canvas rounded font-mono text-sm break-all">{{ secretInfo.tenantId || '-' }}</code>
            <BaseButton v-if="secretInfo.tenantId" intent="ghost" size="sm" @click="copyText(secretInfo.tenantId)">复制</BaseButton>
          </div>
        </div>

        <!-- 勾选已保存密钥（解锁关闭按钮） -->
        <label class="flex items-center gap-2 text-sm text-default">
          <input
            v-model="secretAcknowledged"
            type="checkbox"
            class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
          />
          我已保存密钥，关闭后不可再次获取
        </label>
      </div>
      <template #footer>
        <BaseButton :disabled="!secretAcknowledged" @click="closeSecretDialog">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject, watch } from 'vue'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { usePageContext } from '@/composables/usePageContext'
import {
  listAllBindings, updateClientAgentBaseUrl, registerClient,
  rotateClientSecret, pauseClient, resumeClient,
  type RpaClientRow, type RegisterClientResult,
} from '@/api/wecomPersonalRpa'
import { listTenants, getAvailableSubagents } from '@/api/saasTenant'

const toast = useToast()
const { isLoggedIn, admin } = useTenantAuth()

const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

// ==================== 占位 URL 检测 ====================
const PLACEHOLDER_URL = 'https://agent.example.com'
function isPlaceholderUrl(url: string | null | undefined): boolean {
  if (!url) return false
  const normalized = String(url).trim().replace(/\/+$/, '')
  return normalized === PLACEHOLDER_URL || normalized.startsWith('http://localhost') || normalized.startsWith('https://localhost')
}

// ==================== 状态映射（client 级） ====================
function clientStatusLabel(s: string): string {
  if (s === 'active') return '正常'
  if (s === 'disabled') return '已停用'
  return s || '-'
}
function clientStatusIntent(s: string): 'success' | 'neutral' | 'warning' | 'danger' | 'info' | 'primary' {
  if (s === 'active') return 'success'
  if (s === 'disabled') return 'neutral'
  return 'warning'
}
function canPause(s: string): boolean {
  // 仅 active 客户端可暂停（→ disabled，可通过「恢复」回到 active）
  return s === 'active'
}
function canResume(s: string): boolean {
  return s === 'disabled'
}

// ==================== 连接状态徽章（基于 last_heartbeat_at + 60s 阈值） ====================
const HEARTBEAT_THRESHOLD_MS = 60 * 1000
interface BadgeInfo { intent: 'success' | 'danger' | 'neutral'; label: string }
function connectionBadge(lastHeartbeat: string | null | undefined): BadgeInfo {
  if (!lastHeartbeat) {
    return { intent: 'neutral', label: '未连接' }
  }
  const t = new Date(lastHeartbeat).getTime()
  if (Number.isNaN(t)) {
    return { intent: 'neutral', label: '未连接' }
  }
  // 后端返回本地时间字符串（无 Z 后缀），按本地时区解析
  const diff = Date.now() - t
  if (diff <= HEARTBEAT_THRESHOLD_MS) {
    return { intent: 'success', label: '在线' }
  }
  return { intent: 'danger', label: '离线' }
}

function formatTime(t: string | null | undefined): string {
  if (!t) return '-'
  return String(t).replace('T', ' ').slice(0, 16)
}

// ==================== 数据加载 ====================
const allClients = ref<RpaClientRow[]>([])
const statusFilter = ref('') // 默认「全部」（显示所有 client，包括未连接的）
const tenantKeyword = ref('')
const clientKeyword = ref('')

const { currentPage: page, pageSize, seqNumber, refresh, loading } = usePageContext(async () => {
  try {
    const statusParam = statusFilter.value || undefined
    allClients.value = await listAllBindings(statusParam ? { status: statusParam } : {})
  } catch (e: any) {
    toast.error(e?.message || '获取客户端列表失败')
    allClients.value = []
  }
})

const filteredClients = computed(() => {
  let arr = allClients.value
  const tk = tenantKeyword.value.trim().toLowerCase()
  if (tk) {
    arr = arr.filter(c =>
      (c.tenant_id || '').toLowerCase().includes(tk) ||
      ((c as any).tenant_name || '').toLowerCase().includes(tk),
    )
  }
  const ck = clientKeyword.value.trim().toLowerCase()
  if (ck) {
    arr = arr.filter(c =>
      (c.client_id || '').toLowerCase().includes(ck) ||
      (c.client_name || '').toLowerCase().includes(ck) ||
      (c.last_account_name || '').toLowerCase().includes(ck),
    )
  }
  return arr
})

const displayedClients = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredClients.value.slice(start, start + pageSize.value)
})

function onFilterChange() {
  page.value = 1
  refresh()
}

async function loadData() {
  await refresh()
}

// ==================== 客户端暂停 / 恢复 ====================
const pausing = ref(false)
const resuming = ref(false)

async function onPause(row: RpaClientRow) {
  if (pausing.value || resuming.value) return
  if (!confirm(`确定暂停客户端「${row.client_name || row.client_id}」？暂停后该客户端下的所有账号将不再被自动处理。`)) {
    return
  }
  pausing.value = true
  try {
    await pauseClient(row.client_id, { tenantId: row.tenant_id })
    toast.success('已暂停')
    await refresh()
  } catch (e: any) {
    toast.error(e?.message || '暂停失败')
  } finally {
    pausing.value = false
  }
}

async function onResume(row: RpaClientRow) {
  if (pausing.value || resuming.value) return
  resuming.value = true
  try {
    await resumeClient(row.client_id, { tenantId: row.tenant_id })
    toast.success('已恢复')
    await refresh()
  } catch (e: any) {
    toast.error(e?.message || '恢复失败')
  } finally {
    resuming.value = false
  }
}

// ==================== 轮换密钥（平台管理员代管理） ====================
const rotating = ref(false)

// ==================== 详情弹框 + agent_base_url 编辑 ====================
const showDetail = ref(false)
const current = ref<RpaClientRow | null>(null)
const agentBaseUrlDraft = ref('')
const savingUrl = ref(false)

function openDetail(row: any) {
  current.value = row as RpaClientRow
  agentBaseUrlDraft.value = row.agent_base_url || ''
  showDetail.value = true
}

async function onSaveAgentUrl() {
  if (!current.value?.client_id) {
    toast.error('未找到客户端，无法更新 agent_base_url')
    return
  }
  savingUrl.value = true
  try {
    const v = agentBaseUrlDraft.value.trim()
    await updateClientAgentBaseUrl(current.value.client_id, v || null)
    toast.success('agent_base_url 已更新')
    // 同步当前行
    if (current.value) current.value.agent_base_url = v || null
    await refresh()
  } catch (e: any) {
    toast.error(e?.message || '保存失败')
  } finally {
    savingUrl.value = false
  }
}

// ==================== 列定义 ====================
const columns: TableColumn[] = [
  { key: 'seq', label: '序号', width: '60px', thAlign: 'center' },
  { key: 'tenant', label: '租户', minWidth: '180px' },
  { key: 'client', label: '客户端', minWidth: '160px' },
  { key: 'account_count', label: '账号数', width: '140px', thAlign: 'center' },
  { key: 'agent_base_url', label: 'agent_base_url', minWidth: '220px' },
  { key: 'client_status', label: '状态', width: '90px', thAlign: 'center' },
  { key: 'connection_status', label: '连接状态', width: '100px', thAlign: 'center' },
  { key: 'last_heartbeat_at', label: '最近心跳', width: '150px' },
  { key: 'actions', label: '操作', width: '280px', thAlign: 'center' },
]

// ==================== 新增绑定（平台管理员代管理） ====================
const showCreateDialog = ref(false)
const creating = ref(false)
const createForm = ref<{ tenantId: string; name: string; subagentType: string }>({
  tenantId: '',
  name: '',
  subagentType: '',
})
const createError = ref<{ tenantId?: string; name?: string }>({})

// 租户列表（仅 active 状态可选）
const tenantOptions = ref<Array<{ tenant_id: string; company_name: string; status?: string }>>([])
const tenantsLoaded = ref(false)

// 当前选中租户的可用数字员工列表（按订阅过滤）
const availableSubagents = ref<string[]>([])
const subagentsLoaded = ref(false)

async function loadTenantOptions() {
  try {
    // 拉一大页避免分页，租户数量通常不多；后端 list_tenants 默认 page_size=20
    const res = await listTenants({ page: 1, page_size: 500 })
    const list = (res.tenants || []) as Array<{ tenant_id: string; company_name: string; status?: string }>
    // 仅展示 active 租户，避免给已停用租户创建绑定
    tenantOptions.value = list.filter(t => !t.status || t.status === 'active')
  } catch (e: any) {
    toast.error(e?.message || '获取租户列表失败')
    tenantOptions.value = []
  } finally {
    tenantsLoaded.value = true
  }
}

async function loadAvailableSubagents(tenantId: string) {
  if (!tenantId) {
    availableSubagents.value = []
    subagentsLoaded.value = true
    return
  }
  try {
    // 平台后台路径 /portal/* 下 getSaasAuthHeader 不会自动注入 X-Tenant-Id，必须显式传
    const res = await getAvailableSubagents({ tenantId })
    availableSubagents.value = res.subagents || []
  } catch (e: any) {
    // 不阻断表单（订阅接口失败时只是下拉为空）
    console.warn('前端日志：加载可用数字员工失败', e?.message)
    availableSubagents.value = []
  } finally {
    subagentsLoaded.value = true
  }
}

// 选中租户变化时重新加载该租户的可用数字员工，并清空已选 subagent
watch(() => createForm.value.tenantId, async (newTid) => {
  createForm.value.subagentType = ''
  subagentsLoaded.value = false
  if (newTid) {
    await loadAvailableSubagents(newTid)
  } else {
    availableSubagents.value = []
  }
})

function openCreateDialog() {
  createForm.value = { tenantId: '', name: '', subagentType: '' }
  createError.value = {}
  showCreateDialog.value = true
  if (tenantOptions.value.length === 0) {
    loadTenantOptions()
  }
}

function validateCreate(): boolean {
  const err: { tenantId?: string; name?: string } = {}
  if (!createForm.value.tenantId.trim()) err.tenantId = '请选择目标租户'
  if (!createForm.value.name.trim()) err.name = '请填写绑定名称'
  createError.value = err
  return Object.keys(err).length === 0
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success('已复制')
  } catch {
    toast.error('复制失败，请手动选择复制')
  }
}

function buildYamlSnippet(clientId: string, tenantId: string): string {
  // 安全约束：yaml 中绝不塞明文 client_secret，必须用 DPAPI 占位（用户用客户端落盘工具写入真实值）
  return [
    'WeComPersonalRpa:',
    `  client_id: "${clientId}"`,
    `  # DPAPI 引用占位 - 真实 secret 由客户端 DPAPI 写入工具落盘，请勿在此填明文`,
    '  client_secret_ref: "dpapi:Client.WeComPersonalRpa:client_secret"',
    `  tenant_id: "${tenantId}"`,
    `  agent_base_url: "${PLACEHOLDER_URL}"  # 占位地址，请改为真实生产服务端 URL`,
  ].join('\n')
}

// ==================== 密钥一次性展示 ====================
const showSecretDialog = ref(false)
const secretAcknowledged = ref(false)
const secretInfo = ref<{
  clientId: string
  clientSecret: string
  tenantId: string
  yamlSnippet: string
} | null>(null)

async function handleCreate() {
  if (!validateCreate()) {
    // 必填校验失败：toast + 红色错误提示（对齐 detail-page-convention）
    const firstErr = createError.value.tenantId || createError.value.name
    if (firstErr) toast.error(firstErr)
    return
  }
  // 二次确认（防误点，因为创建后 secret 只显示一次）
  if (!confirm('确认为该租户创建 RPA 客户端？密钥将仅显示一次。')) return

  creating.value = true
  try {
    const tid = createForm.value.tenantId.trim()
    const result: RegisterClientResult = await registerClient(
      {
        name: createForm.value.name.trim(),
        min_version: '1.0.0',
        subagent_type: createForm.value.subagentType.trim() || undefined,
      },
      { tenantId: tid }, // 平台后台代管理：显式传 X-Tenant-Id
    )
    // 关闭表单，弹出密钥展示
    showCreateDialog.value = false
    secretInfo.value = {
      clientId: result.client_id,
      clientSecret: result.client_secret,
      tenantId: tid,
      yamlSnippet: buildYamlSnippet(result.client_id, tid),
    }
    secretAcknowledged.value = false
    showSecretDialog.value = true
    toast.success('客户端创建成功，请立即保存密钥')
  } catch (e: any) {
    toast.error(e?.message || '创建失败')
  } finally {
    creating.value = false
  }
}

function closeSecretDialog() {
  if (!secretAcknowledged.value) return
  showSecretDialog.value = false
  secretInfo.value = null
  secretAcknowledged.value = false
  // 关闭后刷新列表：刚创建的 client 现在应该能看到了（数据源以 clients 表为基准）
  refresh()
}

// 轮换密钥：confirm → 调 rotateClientSecret（带 X-Tenant-Id 代管理）→ 复用密钥展示对话框
async function onRotate(row: RpaClientRow) {
  if (!row?.client_id) {
    toast.error('未找到客户端，无法轮换密钥')
    return
  }
  const clientName = row.client_name || row.client_id
  if (!confirm(`确定轮换客户端「${clientName}」的密钥？旧密钥立即失效，客户端需用新密钥重新签名。`)) {
    return
  }
  rotating.value = true
  try {
    // 平台后台代管理：显式传 X-Tenant-Id（/portal/* 不会自动注入）
    const result = await rotateClientSecret(row.client_id, { tenantId: row.tenant_id })
    // 复用密钥展示对话框：填入新 secret + 重算 yaml 片段（client_id 不变，仅 secret 变）
    secretInfo.value = {
      clientId: result.client_id,
      clientSecret: result.client_secret,
      tenantId: row.tenant_id || '',
      yamlSnippet: buildYamlSnippet(result.client_id, row.tenant_id || ''),
    }
    secretAcknowledged.value = false
    showSecretDialog.value = true
    toast.success('密钥已轮换，请立即保存新密钥')
  } catch (e: any) {
    toast.error(e?.message || '轮换密钥失败')
  } finally {
    rotating.value = false
  }
}

// ==================== 权限校验 ====================
onMounted(async () => {
  if (admin.value?.role !== 'platform_admin') {
    toast.error('仅平台管理员可访问此页面')
    return
  }
  // 并行加载客户端列表和租户列表（租户列表用于「新增绑定」下拉）
  await Promise.all([loadData(), loadTenantOptions()])
})
</script>

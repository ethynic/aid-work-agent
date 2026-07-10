<template>
  <div class="page-container bg-canvas">
    <AppHeader
      title="企业微信个人账号 RPA"
      :is-logged-in="isLoggedIn"
      :user="admin"
      @toggle-sidebar="handleToggleSidebar"
    />

    <div class="page-content p-6">
      <!-- Tab 条 -->
      <div class="mb-4 border-b border-default">
        <div class="flex gap-6">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            :class="[
              'pb-2 text-sm font-medium border-b-2 transition-colors',
              activeTab === tab.key
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover',
            ]"
            @click="switchTab(tab.key)"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>

      <!-- ========== Tab 1: 客户端 ========== -->
      <div v-show="activeTab === 'clients'" class="flex flex-col h-full">
        <div class="page-toolbar">
          <div class="page-toolbar-left">
            <BaseInput
              v-model="clientKeyword"
              size="sm"
              placeholder="搜索名称或 client_id"
                class="w-80"
            />
          </div>
          <div class="page-toolbar-right">
            <BaseButton intent="secondary" @click="pauseAll">暂停全部</BaseButton>
            <BaseButton intent="secondary" @click="resumeAll">恢复全部</BaseButton>
            <BaseButton @click="openRegisterDialog">注册客户端</BaseButton>
          </div>
        </div>

        <div v-if="loadingClients && allClients.length === 0" class="text-center py-12 text-muted">加载中...</div>
        <div v-else class="table-scroll-wrapper flex-1 min-h-0">
          <BaseTable :columns="clientColumns" :data="displayedClients" row-key="client_id">
            <template #seq="{ index }">{{ clientSeq(index) }}</template>
            <template #name="{ row }">
              <div class="font-medium text-default">{{ row.name }}</div>
              <div class="text-xs text-muted font-mono">{{ row.client_id }}</div>
            </template>
            <template #status="{ row }">
              <BaseBadge :intent="statusMeta(WecomRpaClientStatusMap, row.status).intent">
                {{ statusMeta(WecomRpaClientStatusMap, row.status).label }}
              </BaseBadge>
            </template>
            <template #last_seen_at="{ row }">{{ formatTime(row.last_seen_at) }}</template>
            <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
            <template #actions="{ row }">
              <BaseButton intent="ghost" size="sm" class="whitespace-nowrap" @click="openAccountsDialog(row)">查看账号</BaseButton>
              <BaseButton intent="ghost" size="sm" class="whitespace-nowrap" @click="handleRotate(row)">轮换密钥</BaseButton>
            </template>
            <template #empty>暂无客户端，点击「注册客户端」创建</template>
          </BaseTable>
        </div>
        <BasePagination
          v-model:current-page="clientPage"
          v-model:page-size="clientPageSize"
          :total="filteredClients.length"
          :show-size-changer="true"
        />
      </div>

      <!-- ========== Tab 2: 会话绑定 ========== -->
      <div v-show="activeTab === 'bindings'" class="flex flex-col h-full">
        <div class="page-toolbar">
          <div class="page-toolbar-left">
            <BaseSelect v-model="bindingStatusFilter" size="sm" class="w-40">
              <option value="">全部状态</option>
              <option v-for="opt in bindingStatusOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </BaseSelect>
            <BaseInput
              v-model="bindingKeyword"
              size="sm"
              placeholder="搜索会话名称或搜索键"
              class="w-72"
            />
          </div>
          <div class="page-toolbar-right" />
        </div>

        <div v-if="loadingBindings && allBindings.length === 0" class="text-center py-12 text-muted">加载中...</div>
        <div v-else class="table-scroll-wrapper flex-1 min-h-0">
          <BaseTable :columns="bindingColumns" :data="displayedBindings" row-key="binding_id">
            <template #seq="{ index }">{{ bindingSeq(index) }}</template>
            <template #conversation_type="{ row }">
              <span class="text-xs">{{ row.conversation_type === 'group' ? '群聊' : '单聊' }}</span>
            </template>
            <template #status="{ row }">
              <BaseBadge :intent="statusMeta(WecomRpaBindingStatusMap, row.status).intent">
                {{ statusMeta(WecomRpaBindingStatusMap, row.status).label }}
              </BaseBadge>
            </template>
            <template #last_verified_at="{ row }">{{ formatTime(row.last_verified_at) }}</template>
            <template #actions="{ row }">
              <BaseButton
                intent="ghost" size="sm" class="whitespace-nowrap"
                @click="handleEditBindingName(row)"
              >搜索名</BaseButton>
              <BaseButton
                v-if="row.status === 'needs_review' || row.status === 'pending'"
                intent="ghost" size="sm" class="whitespace-nowrap"
                @click="handleConfirmBinding(row)"
              >确认</BaseButton>
              <BaseButton
                v-if="row.status === 'active'"
                intent="ghost" size="sm" class="whitespace-nowrap"
                @click="toggleBinding(row)"
              >暂停</BaseButton>
              <BaseButton
                v-if="row.status === 'paused'"
                intent="ghost" size="sm" class="whitespace-nowrap"
                @click="toggleBinding(row)"
              >恢复</BaseButton>
            </template>
            <template #empty>暂无会话绑定</template>
          </BaseTable>
        </div>
        <BasePagination
          v-model:current-page="bindingPage"
          v-model:page-size="bindingPageSize"
          :total="filteredBindings.length"
          :show-size-changer="true"
        />
      </div>

      <!-- ========== Tab 3: 审计日志 ========== -->
      <div v-show="activeTab === 'audit'" class="flex flex-col h-full">
        <div class="page-toolbar">
          <div class="page-toolbar-left">
            <BaseSelect v-model="auditCategory" size="sm" class="w-40">
              <option v-for="opt in auditCategoryOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </BaseSelect>
            <BaseInput v-model="auditClientId" size="sm" placeholder="client_id" class="w-56" />
            <BaseInput v-model="auditAccountId" size="sm" placeholder="account_id" class="w-48" />
            <BaseInput v-model="auditActionId" size="sm" placeholder="action_id" class="w-48" />
          </div>
          <div class="page-toolbar-right">
            <BaseButton @click="loadAudit">查询</BaseButton>
          </div>
        </div>

        <div v-if="loadingAudit && allAudit.length === 0" class="text-center py-12 text-muted">加载中...</div>
        <div v-else class="table-scroll-wrapper flex-1 min-h-0">
          <BaseTable :columns="auditColumns" :data="displayedAudit" row-key="audit_id">
            <template #seq="{ index }">{{ auditSeq(index) }}</template>
            <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
            <template #category="{ row }">
              <span class="text-xs">{{ auditCategoryLabel(row.category) }}</span>
            </template>
            <template #client_id="{ row }">
              <span class="text-xs font-mono text-muted">{{ row.client_id || '-' }}</span>
            </template>
            <template #account_id="{ row }">
              <span class="text-xs font-mono text-muted">{{ row.account_id || '-' }}</span>
            </template>
            <template #actions="{ row }">
              <BaseButton intent="ghost" size="sm" @click="openAuditDetail(row)">详情</BaseButton>
            </template>
            <template #empty>暂无审计日志</template>
          </BaseTable>
        </div>
        <BasePagination
          v-model:current-page="auditPage"
          v-model:page-size="auditPageSize"
          :total="allAudit.length"
          :show-size-changer="true"
        />
      </div>

      <!-- ========== Tab 4: 监控 ========== -->
      <div v-show="activeTab === 'monitor'" class="flex flex-col h-full">
        <div class="page-toolbar">
          <div class="page-toolbar-left">
            <BaseSelect v-model="monitorWindowHours" size="sm" class="w-32" @update:model-value="loadMonitor">
              <option value="1">近 1 小时</option>
              <option value="24">近 24 小时</option>
              <option value="168">近 7 天</option>
            </BaseSelect>
            <span v-if="metrics" class="text-xs text-muted">生成于 {{ formatTime(metrics.generated_at) }}</span>
          </div>
          <div class="page-toolbar-right">
            <BaseButton intent="secondary" @click="loadMonitor">刷新</BaseButton>
          </div>
        </div>

        <div v-if="loadingMonitor && !metrics" class="text-center py-12 text-muted">加载中...</div>
        <div v-else-if="metrics" class="flex-1 overflow-y-auto space-y-6 mt-2">
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div class="bg-surface rounded-lg border border-default p-4">
              <div class="text-sm text-muted">客户端在线率</div>
              <div class="text-2xl font-bold text-default mt-1">{{ pct(metrics.clients.online_rate) }}</div>
              <div class="text-xs text-muted mt-1">{{ metrics.clients.online }} / {{ metrics.clients.total }} 在线</div>
            </div>
            <div class="bg-surface rounded-lg border border-default p-4">
              <div class="text-sm text-muted">账号在线率</div>
              <div class="text-2xl font-bold text-default mt-1">{{ pct(metrics.accounts.online_rate) }}</div>
              <div class="text-xs text-muted mt-1">{{ metrics.accounts.online }} / {{ metrics.accounts.total }} 在线</div>
            </div>
            <div class="bg-surface rounded-lg border border-default p-4">
              <div class="text-sm text-muted">Action 成功率</div>
              <div class="text-2xl font-bold text-default mt-1">{{ pct(metrics.actions.success_rate) }}</div>
              <div class="text-xs text-muted mt-1">成功 {{ metrics.actions.succeeded }} / 失败 {{ metrics.actions.failed }}</div>
            </div>
            <div class="bg-surface rounded-lg border border-default p-4">
              <div class="text-sm text-muted">待复核绑定</div>
              <div class="text-2xl font-bold text-default mt-1">{{ metrics.bindings.needs_review }}</div>
              <div class="text-xs text-muted mt-1">needs_review</div>
            </div>
          </div>

          <div class="bg-surface rounded-lg border border-default p-4">
            <div class="text-sm font-medium text-default mb-3">近 {{ metrics.window_hours }} 小时活动</div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              <div><span class="text-muted">入站消息：</span><span class="text-default font-medium">{{ metrics.audit_counts.inbound_message || 0 }}</span></div>
              <div><span class="text-muted">智能体回复：</span><span class="text-default font-medium">{{ metrics.audit_counts.agent_reply || 0 }}</span></div>
              <div><span class="text-muted">暂停/恢复：</span><span class="text-default font-medium">{{ metrics.audit_counts.pause_resume || 0 }}</span></div>
              <div><span class="text-muted">动作回执：</span><span class="text-default font-medium">{{ metrics.audit_counts.action_result || 0 }}</span></div>
            </div>
          </div>

          <div class="bg-surface rounded-lg border border-default p-4">
            <div class="text-sm font-medium text-default mb-3">活跃告警（{{ alerts.length }}）</div>
            <div v-if="alerts.length === 0" class="text-center py-6">
              <div class="text-2xl text-success-600 mb-1">✓</div>
              <div class="text-sm text-muted">无活跃告警</div>
            </div>
            <div v-else class="table-scroll-wrapper">
              <BaseTable :columns="alertColumns" :data="alerts" row-key="_key">
                <template #rule="{ row }">{{ alertRuleLabel[row.rule] || row.rule }}</template>
                <template #severity="{ row }">
                  <BaseBadge :intent="alertSeverityIntent(row.severity)">{{ alertSeverityLabel(row.severity) }}</BaseBadge>
                </template>
                <template #empty>无活跃告警</template>
              </BaseTable>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 注册客户端 Modal -->
    <BaseModal v-model="showRegisterDialog" title="注册客户端" size="md">
      <div class="space-y-4">
        <div>
          <label class="form-label">客户端名称 <span class="form-required">*</span></label>
          <BaseInput v-model="registerForm.name" placeholder="如：销售一组-RPA 客户端" />
        </div>
        <div>
          <label class="form-label">最低客户端版本</label>
          <BaseInput v-model="registerForm.min_version" placeholder="1.0.0" />
        </div>
        <div>
          <label class="form-label">关联数字员工（可选）</label>
          <BaseInput v-model="registerForm.subagent_type" placeholder="如：trade-specialist" />
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showRegisterDialog = false">取消</BaseButton>
        <BaseButton :disabled="registering" @click="handleRegister">{{ registering ? '处理中...' : '注册' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- 一次性密钥展示 Modal -->
    <BaseModal v-model="showSecretDialog" title="客户端密钥（仅显示一次）" size="md" :close-on-overlay="false">
      <div v-if="secretInfo" class="space-y-4">
        <div class="bg-warning-50 border border-warning-200 text-warning-800 rounded-lg p-3 text-sm">
          ⚠️ 此密钥仅显示一次，关闭后无法找回，请立即复制并妥善保存。
        </div>
        <div>
          <label class="form-label">client_id</label>
          <div class="flex items-center gap-2">
            <code class="flex-1 px-3 py-2 bg-canvas rounded font-mono text-sm break-all">{{ secretInfo.client_id }}</code>
            <BaseButton intent="ghost" size="sm" @click="copyText(secretInfo.client_id)">复制</BaseButton>
          </div>
        </div>
        <div>
          <label class="form-label">client_secret</label>
          <div class="flex items-center gap-2">
            <code class="flex-1 px-3 py-2 bg-canvas rounded font-mono text-sm break-all">{{ secretInfo.client_secret }}</code>
            <BaseButton intent="ghost" size="sm" @click="copyText(secretInfo.client_secret)">复制</BaseButton>
          </div>
        </div>
        <label class="flex items-center gap-2 text-sm text-default">
          <input v-model="secretAcknowledged" type="checkbox" class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
          我已复制保存密钥
        </label>
      </div>
      <template #footer>
        <BaseButton :disabled="!secretAcknowledged" @click="closeSecretDialog">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- 账号列表 Modal -->
    <BaseModal v-model="showAccountsDialog" :title="`账号列表${currentClient ? ' - ' + currentClient.name : ''}`" size="lg">
      <div v-if="loadingAccounts" class="text-center py-8 text-muted">加载中...</div>
      <div v-else-if="currentAccounts.length === 0" class="text-center py-8 text-muted">该客户端暂无账号</div>
      <div v-else class="table-scroll-wrapper">
        <BaseTable :columns="accountColumns" :data="currentAccounts" row-key="account_id">
          <template #status="{ row }">
            <BaseBadge :intent="statusMeta(WecomRpaAccountStatusMap, row.status).intent">
              {{ statusMeta(WecomRpaAccountStatusMap, row.status).label }}
            </BaseBadge>
          </template>
          <template #paused_reason="{ row }">
            <span class="text-xs text-muted">{{ row.paused_reason || '-' }}</span>
          </template>
          <template #last_login_at="{ row }">{{ formatTime(row.last_login_at) }}</template>
          <template #actions="{ row }">
            <BaseButton
              v-if="row.status === 'paused'"
              intent="ghost" size="sm" @click="toggleAccount(row)"
            >恢复</BaseButton>
            <BaseButton
              v-else
              intent="ghost" size="sm" @click="toggleAccount(row)"
            >暂停</BaseButton>
          </template>
        </BaseTable>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showAccountsDialog = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- 审计详情 Modal -->
    <BaseModal v-model="showAuditDetail" title="审计详情" size="md">
      <pre v-if="auditDetail" class="text-xs bg-canvas rounded-lg p-3 overflow-auto max-h-[60vh] whitespace-pre-wrap break-all">{{ JSON.stringify(auditDetail.payload, null, 2) }}</pre>
      <template #footer>
        <BaseButton intent="secondary" @click="showAuditDetail = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject, watch } from 'vue'
import { useToast } from 'vue-toastification'
import { useTenantAuth } from '@/composables/useTenantAuth'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import {
  listClients, registerClient, listClientAccounts, rotateClientSecret,
  listBindings, confirmBinding, updateBinding, pause, resume, listAudit,
  getMetrics, getAlerts,
  type RpaClientSummary, type RpaAccount, type RpaBinding, type RpaAudit,
  type RpaMetrics, type RpaAlert,
} from '@/api/wecomPersonalRpa'
import {
  WecomRpaClientStatusMap, WecomRpaAccountStatusMap, WecomRpaBindingStatusMap,
  colorToBadgeIntent,
} from '@/api/enums'
import { useRpaPauseResume } from '@/composables/useRpaPauseResume'

const toast = useToast()
const { isLoggedIn, admin } = useTenantAuth()

const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

// ==================== 通用工具 ====================
function paginate<T>(arr: T[], page: number, size: number): T[] {
  const start = (page - 1) * size
  return arr.slice(start, start + size)
}
function statusMeta(map: Record<string, { label: string; color: string }>, status: string) {
  const m = map[status]
  return { label: m?.label ?? status, intent: colorToBadgeIntent(m?.color ?? 'gray') }
}
function formatTime(t: string | null | undefined): string {
  if (!t) return '-'
  // 后端 TIMESTAMP 返回本地时间字符串（无 Z 后缀），直接截取到分钟
  return String(t).replace('T', ' ').slice(0, 16)
}
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success('已复制')
  } catch {
    toast.error('复制失败，请手动选择复制')
  }
}

// ==================== Tab 切换 ====================
const tabs = [
  { key: 'clients', label: '客户端' },
  { key: 'bindings', label: '会话绑定' },
  { key: 'audit', label: '审计日志' },
  { key: 'monitor', label: '监控' },
]
const activeTab = ref('clients')
const loadedTabs = ref(new Set<string>())

async function switchTab(key: string) {
  activeTab.value = key
  if (!loadedTabs.value.has(key)) {
    loadedTabs.value.add(key)
    if (key === 'bindings') await loadBindings()
    else if (key === 'audit') await loadAudit()
    else if (key === 'monitor') await loadMonitor()
  }
}

// ==================== Tab 1: 客户端 ====================
const allClients = ref<RpaClientSummary[]>([])
const loadingClients = ref(false)
const clientKeyword = ref('')
const clientPage = ref(1)
const clientPageSize = ref(10)

const filteredClients = computed(() => {
  const kw = clientKeyword.value.trim().toLowerCase()
  if (!kw) return allClients.value
  return allClients.value.filter(c =>
    (c.name || '').toLowerCase().includes(kw) || c.client_id.toLowerCase().includes(kw),
  )
})
const displayedClients = computed(() => paginate(filteredClients.value, clientPage.value, clientPageSize.value))
watch(clientKeyword, () => { clientPage.value = 1 })
function clientSeq(i: number) { return (clientPage.value - 1) * clientPageSize.value + i + 1 }

const clientColumns: TableColumn[] = [
  { key: 'seq', label: '序号', width: '60px', thAlign: 'center' },
  { key: 'name', label: '名称', minWidth: '200px' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'min_version', label: '最低版本', width: '100px' },
  { key: 'accounts_count', label: '账号数', width: '80px', thAlign: 'center' },
  { key: 'last_seen_at', label: '最后活跃', width: '150px' },
  { key: 'created_at', label: '创建时间', width: '150px' },
  { key: 'actions', label: '操作', width: '200px', thAlign: 'center' },
]

async function loadClients() {
  loadingClients.value = true
  try {
    allClients.value = await listClients()
  } catch (e: any) {
    toast.error(e.message || '获取客户端列表失败')
  } finally {
    loadingClients.value = false
  }
}

// 注册客户端
const showRegisterDialog = ref(false)
const registering = ref(false)
const registerForm = ref({ name: '', min_version: '1.0.0', subagent_type: '' })

function openRegisterDialog() {
  registerForm.value = { name: '', min_version: '1.0.0', subagent_type: '' }
  showRegisterDialog.value = true
}

async function handleRegister() {
  if (!registerForm.value.name.trim()) {
    toast.error('请填写客户端名称')
    return
  }
  registering.value = true
  try {
    const res = await registerClient({
      name: registerForm.value.name.trim(),
      min_version: registerForm.value.min_version || '1.0.0',
      subagent_type: registerForm.value.subagent_type.trim() || undefined,
    })
    showRegisterDialog.value = false
    // 展示一次性密钥
    secretInfo.value = { client_id: res.client_id, client_secret: res.client_secret }
    secretAcknowledged.value = false
    showSecretDialog.value = true
    toast.success('客户端注册成功')
    await loadClients()
  } catch (e: any) {
    toast.error(e.message || '注册客户端失败')
  } finally {
    registering.value = false
  }
}

// 一次性密钥展示
const showSecretDialog = ref(false)
const secretInfo = ref<{ client_id: string; client_secret: string } | null>(null)
const secretAcknowledged = ref(false)

function closeSecretDialog() {
  if (!secretAcknowledged.value) return
  showSecretDialog.value = false
  secretInfo.value = null
  secretAcknowledged.value = false
}

// 轮换密钥
async function handleRotate(client: any) {
  if (!confirm(`确定轮换客户端「${client.name}」的密钥？旧密钥立即失效，客户端需用新密钥重新签名。`)) return
  try {
    const res = await rotateClientSecret(client.client_id)
    secretInfo.value = { client_id: res.client_id, client_secret: res.client_secret }
    secretAcknowledged.value = false
    showSecretDialog.value = true
    toast.success('密钥已轮换')
  } catch (e: any) {
    toast.error(e.message || '轮换密钥失败')
  }
}

// 账号列表 Modal
const showAccountsDialog = ref(false)
const currentClient = ref<RpaClientSummary | null>(null)
const currentAccounts = ref<RpaAccount[]>([])
const loadingAccounts = ref(false)

const accountColumns: TableColumn[] = [
  { key: 'display_name', label: '账号', minWidth: '180px' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'paused_reason', label: '暂停原因', minWidth: '160px' },
  { key: 'last_login_at', label: '最后登录', width: '150px' },
  { key: 'actions', label: '操作', width: '100px', thAlign: 'center' },
]

async function openAccountsDialog(client: any) {
  currentClient.value = client
  showAccountsDialog.value = true
  await loadAccounts(client.client_id)
}

async function loadAccounts(clientId: string) {
  loadingAccounts.value = true
  try {
    currentAccounts.value = await listClientAccounts(clientId)
  } catch (e: any) {
    toast.error(e.message || '获取账号列表失败')
  } finally {
    loadingAccounts.value = false
  }
}

async function toggleAccount(acc: any) {
  const isPaused = acc.status === 'paused'
  // 复用共享 composable（带 confirm + loading 防重复点击 + toast）
  const ok = isPaused
    ? await resumeAccountRef(acc.account_id)
    : await pauseAccountRef(acc.account_id)
  if (ok) {
    if (currentClient.value) await loadAccounts(currentClient.value.client_id)
    await loadClients()
  }
}

// 共享 pause/resume composable（供账号 Modal 复用，binding/tenant 级仍走内联 pause/resume）
const {
  pauseAccount: pauseAccountRef,
  resumeAccount: resumeAccountRef,
} = useRpaPauseResume({})

// 租户级暂停/恢复全部账号
async function pauseAll() {
  if (!confirm('确定暂停全部账号？暂停期间该租户所有 RPA 账号都不会收发消息。')) return
  try {
    await pause({ scope: 'tenant' })
    toast.success('已暂停全部账号')
    await loadClients()
    if (currentClient.value) await loadAccounts(currentClient.value.client_id)
  } catch (e: any) {
    toast.error(e.message || '暂停失败')
  }
}

async function resumeAll() {
  try {
    await resume({ scope: 'tenant' })
    toast.success('已恢复全部账号')
    await loadClients()
    if (currentClient.value) await loadAccounts(currentClient.value.client_id)
  } catch (e: any) {
    toast.error(e.message || '恢复失败')
  }
}

// ==================== Tab 2: 会话绑定 ====================
const allBindings = ref<RpaBinding[]>([])
const loadingBindings = ref(false)
const bindingStatusFilter = ref('')
const bindingKeyword = ref('')
const bindingPage = ref(1)
const bindingPageSize = ref(10)

const bindingStatusOptions = [
  { value: 'pending', label: '待确认' },
  { value: 'active', label: '正常' },
  { value: 'paused', label: '已暂停' },
  { value: 'invalid', label: '已失效' },
  { value: 'needs_review', label: '待复核' },
]

const filteredBindings = computed(() => {
  let arr = allBindings.value
  if (bindingStatusFilter.value) arr = arr.filter(b => b.status === bindingStatusFilter.value)
  const kw = bindingKeyword.value.trim().toLowerCase()
  if (kw) {
    arr = arr.filter(b =>
      (b.display_name || '').toLowerCase().includes(kw) ||
      (b.search_key || '').toLowerCase().includes(kw),
    )
  }
  return arr
})
const displayedBindings = computed(() => paginate(filteredBindings.value, bindingPage.value, bindingPageSize.value))
watch([bindingStatusFilter, bindingKeyword], () => { bindingPage.value = 1 })
function bindingSeq(i: number) { return (bindingPage.value - 1) * bindingPageSize.value + i + 1 }

const bindingColumns: TableColumn[] = [
  { key: 'seq', label: '序号', width: '60px', thAlign: 'center' },
  { key: 'display_name', label: '会话名称', minWidth: '160px' },
  { key: 'search_key', label: '搜索键', minWidth: '140px' },
  { key: 'conversation_type', label: '类型', width: '80px' },
  { key: 'stable_id', label: 'stable_id', width: '160px' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'last_verified_at', label: '最后核实', width: '150px' },
  { key: 'actions', label: '操作', width: '110px', thAlign: 'center' },
]

async function loadBindings() {
  loadingBindings.value = true
  try {
    allBindings.value = await listBindings()
  } catch (e: any) {
    toast.error(e.message || '获取绑定列表失败')
  } finally {
    loadingBindings.value = false
  }
}

async function handleConfirmBinding(b: any) {
  try {
    await confirmBinding(b.binding_id)
    toast.success('已确认，绑定转为正常')
    await loadBindings()
  } catch (e: any) {
    toast.error(e.message || '确认失败')
  }
}

async function handleEditBindingName(b: any) {
  const value = window.prompt('请输入企微会话显示名（例如：陆伟@微信；客户端将搜索“陆伟”）', b.display_name || '')
  if (value === null) return
  if (!value.trim()) {
    toast.error('会话显示名不能为空')
    return
  }
  try {
    await updateBinding(b.binding_id, { display_name: value.trim() })
    toast.success('会话搜索名已更新')
    await loadBindings()
  } catch (e: any) {
    toast.error(e.message || '更新失败')
  }
}

async function toggleBinding(b: any) {
  const isPaused = b.status === 'paused'
  try {
    if (isPaused) {
      await resume({ scope: 'conversation', conversation_id: b.binding_id })
      toast.success('已恢复')
    } else {
      await pause({ scope: 'conversation', conversation_id: b.binding_id })
      toast.success('已暂停')
    }
    await loadBindings()
  } catch (e: any) {
    toast.error(e.message || '操作失败')
  }
}

// ==================== Tab 3: 审计日志 ====================
const allAudit = ref<RpaAudit[]>([])
const loadingAudit = ref(false)
const auditCategory = ref('')
const auditClientId = ref('')
const auditAccountId = ref('')
const auditActionId = ref('')
const auditPage = ref(1)
const auditPageSize = ref(20)

const auditCategoryOptions = [
  { value: '', label: '全部类别' },
  { value: 'client_register', label: '客户端注册' },
  { value: 'secret_rotate', label: '密钥轮换' },
  { value: 'binding_confirm', label: '绑定确认' },
  { value: 'pause_resume', label: '暂停/恢复' },
  { value: 'inbound_message', label: '入站消息' },
  { value: 'agent_reply', label: '智能体回复' },
  { value: 'action_result', label: '动作回执' },
]

const auditCategoryMap: Record<string, string> = {
  client_register: '客户端注册',
  secret_rotate: '密钥轮换',
  binding_confirm: '绑定确认',
  pause_resume: '暂停/恢复',
  inbound_message: '入站消息',
  agent_reply: '智能体回复',
  action_result: '动作回执',
}
function auditCategoryLabel(c: string): string {
  return auditCategoryMap[c] || c
}

const displayedAudit = computed(() => paginate(allAudit.value, auditPage.value, auditPageSize.value))
function auditSeq(i: number) { return (auditPage.value - 1) * auditPageSize.value + i + 1 }

const auditColumns: TableColumn[] = [
  { key: 'seq', label: '序号', width: '60px', thAlign: 'center' },
  { key: 'created_at', label: '时间', width: '150px' },
  { key: 'category', label: '类别', width: '120px' },
  { key: 'client_id', label: 'client_id', width: '180px' },
  { key: 'account_id', label: 'account_id', width: '150px' },
  { key: 'action_id', label: 'action_id', width: '150px' },
  { key: 'actions', label: '详情', width: '80px', thAlign: 'center' },
]

async function loadAudit() {
  loadingAudit.value = true
  try {
    allAudit.value = await listAudit({
      category: auditCategory.value || undefined,
      client_id: auditClientId.value.trim() || undefined,
      account_id: auditAccountId.value.trim() || undefined,
      action_id: auditActionId.value.trim() || undefined,
      limit: 200,
    })
    auditPage.value = 1
  } catch (e: any) {
    toast.error(e.message || '获取审计日志失败')
  } finally {
    loadingAudit.value = false
  }
}

const showAuditDetail = ref(false)
const auditDetail = ref<RpaAudit | null>(null)
function openAuditDetail(row: any) {
  auditDetail.value = row
  showAuditDetail.value = true
}

// ==================== Tab 4: 监控 ====================
const metrics = ref<RpaMetrics | null>(null)
type AlertRow = RpaAlert & { _key: string }
const alerts = ref<AlertRow[]>([])
const loadingMonitor = ref(false)
const monitorWindowHours = ref('24')

const alertColumns: TableColumn[] = [
  { key: 'rule', label: '规则', width: '140px' },
  { key: 'severity', label: '级别', width: '90px' },
  { key: 'entity_name', label: '对象', width: '160px' },
  { key: 'message', label: '说明', minWidth: '240px' },
]

const alertRuleLabel: Record<string, string> = {
  client_offline: '客户端离线',
  account_not_logged_in: '账号未登录',
  consecutive_action_failures: '连续动作失败',
  needs_review_backlog: '待复核积压',
}

function alertSeverityIntent(sev: string): 'danger' | 'warning' | 'neutral' {
  if (sev === 'danger') return 'danger'
  if (sev === 'warning') return 'warning'
  return 'neutral'
}
function alertSeverityLabel(sev: string): string {
  if (sev === 'danger') return '严重'
  if (sev === 'warning') return '警告'
  return '提示'
}
function pct(rate: number): string {
  return (rate * 100).toFixed(1) + '%'
}

async function loadMonitor() {
  loadingMonitor.value = true
  try {
    const [m, a] = await Promise.all([getMetrics(Number(monitorWindowHours.value) || 24), getAlerts()])
    metrics.value = m
    alerts.value = (a || []).map((x, i) => ({ ...x, _key: String(i) }))
  } catch (e: any) {
    toast.error(e.message || '获取监控数据失败')
  } finally {
    loadingMonitor.value = false
  }
}

// ==================== 初始化 ====================
onMounted(async () => {
  loadedTabs.value.add('clients')
  await loadClients()
})
</script>

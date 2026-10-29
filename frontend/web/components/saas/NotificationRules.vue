<template>
  <div class="h-full flex flex-col bg-gray-50">
    <AppHeader
      title="通知规则"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button @click="goToChat(); closeMenu()">返回对话</button>
      </template>
    </AppHeader>

    <div class="flex-1 overflow-y-auto p-6">
      <div class="max-w-4xl space-y-6">
        <!-- 默认外发渠道（租户级目标：未命中规则级目标时按此投递） -->
        <NotificationChannelSettings />

        <!-- 通知规则 -->
        <div class="bg-white rounded-xl shadow-sm border border-default p-6">
          <div class="flex items-start justify-between mb-4 gap-4">
            <div>
              <h2 class="text-base font-semibold text-default">通知规则</h2>
              <p class="text-xs text-muted mt-1 leading-relaxed">
                规则决定各事件的站内信接收人与外发投递；同一事件命中多条规则时各自独立通知。预置规则（系统）可停用与调整，不可删除。
              </p>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              <button
                v-if="queryFiltered"
                @click="clearQueryFilter"
                class="text-xs text-primary-600 hover:text-primary-700 underline"
              >清除筛选</button>
              <BaseButton size="md" @click="openCreate">新增规则</BaseButton>
            </div>
          </div>

          <div v-if="loading" class="text-center py-8 text-muted text-sm">加载中...</div>

          <template v-else>
            <BaseTable :columns="columns" :data="filteredRules" row-key="rule_id">
              <template #event_type="{ row }">{{ eventLabel(row.event_type) }}</template>
              <template #conditions="{ row }">{{ conditionLabel(row as NotificationRule) }}</template>
              <template #recipient_mode="{ row }">{{ recipientLabel(row.recipient_mode) }}</template>
              <template #active_targets="{ row }">
                {{ row.active_targets > 0 ? `${row.active_targets} 个规则级` : '' }}
                <span v-if="row.include_default_target" class="text-muted">
                  {{ row.active_targets > 0 ? ' + 默认渠道' : '默认渠道' }}
                </span>
                <span v-if="row.active_targets === 0 && !row.include_default_target" class="text-muted">仅站内信</span>
              </template>
              <template #enabled="{ row }">
                <BaseBadge :intent="row.enabled ? 'success' : 'neutral'">
                  {{ row.enabled ? '启用中' : '已停用' }}
                </BaseBadge>
              </template>
              <template #actions="{ row }">
                <div class="flex gap-1 justify-center">
                  <BaseButton size="sm" intent="ghost" class="whitespace-nowrap text-xs" @click="openEdit(row as NotificationRule)">编辑</BaseButton>
                  <BaseButton
                    v-if="!row.is_system"
                    size="sm" intent="danger-ghost" class="whitespace-nowrap text-xs"
                    @click="handleDelete(row as NotificationRule)"
                  >删除</BaseButton>
                </div>
              </template>
            </BaseTable>
          </template>
        </div>
      </div>
    </div>

    <!-- 规则编辑弹框 -->
    <BaseModal v-model="showModal" :title="modalTitle" size="lg">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm text-default mb-1">规则名称 <span class="text-danger-500">*</span></label>
            <BaseInput
              v-model="form.name"
              :state="form.name && form.name.trim().length <= 64 ? 'default' : 'error'"
              :disabled="editing?.is_system"
              placeholder="如：留资通知-华东组"
            />
            <p v-if="editing?.is_system" class="text-xs text-muted mt-1">预置规则名称不可修改</p>
          </div>
          <div>
            <label class="block text-sm text-default mb-1">事件类型 <span class="text-danger-500">*</span></label>
            <BaseSelect v-model="form.event_type" :options="eventTypeOptions" :disabled="!!editing" />
            <p v-if="editing" class="text-xs text-muted mt-1">事件类型创建后不可修改</p>
          </div>
        </div>

        <!-- 匹配条件（仅留资事件支持渠道/客服账号两层） -->
        <div v-if="form.event_type === 'lead.captured'">
          <label class="block text-sm text-default mb-1">匹配范围</label>
          <div class="grid grid-cols-2 gap-2">
            <button
              type="button"
              @click="form.scope = 'channel'; form.kf_config_ids = []"
              :class="[
                'px-3 py-2 rounded-lg border text-sm transition-colors text-left',
                form.scope === 'channel'
                  ? 'border-primary-500 bg-primary-50 text-primary-700 font-medium'
                  : 'border-gray-200 text-gray-600 hover:border-gray-300'
              ]"
            >
              渠道层（不限账号）
              <span class="block text-xs font-normal mt-0.5">任一客服账号的留资都命中</span>
            </button>
            <button
              type="button"
              @click="form.scope = 'kf_config'"
              :class="[
                'px-3 py-2 rounded-lg border text-sm transition-colors text-left',
                form.scope === 'kf_config'
                  ? 'border-primary-500 bg-primary-50 text-primary-700 font-medium'
                  : 'border-gray-200 text-gray-600 hover:border-gray-300'
              ]"
            >
              客服账号层
              <span class="block text-xs font-normal mt-0.5">仅所选客服账号的留资命中</span>
            </button>
          </div>
          <div v-if="form.scope === 'kf_config'" class="mt-3">
            <label class="block text-sm text-default mb-1">
              客服账号（来自渠道配置的微信客服）<span class="text-danger-500">*</span>
            </label>
            <div v-if="kfConfigOptions.length === 0" class="text-xs text-muted py-2">
              暂无微信客服渠道配置，请先到「渠道接入」添加
            </div>
            <div v-else class="space-y-1 max-h-40 overflow-y-auto border border-default rounded-lg p-2">
              <label
                v-for="opt in kfConfigOptions" :key="opt.value"
                class="flex items-center gap-2 text-sm text-default cursor-pointer py-1"
              >
                <input
                  type="checkbox"
                  :value="opt.value"
                  v-model="form.kf_config_ids"
                  class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
                />
                {{ opt.label }}
              </label>
            </div>
          </div>
        </div>
        <div v-else-if="form.event_type" class="text-xs text-muted">
          {{ eventLabel(form.event_type) }} 为租户级事件，无需匹配条件
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm text-default mb-1">站内信接收人</label>
            <BaseSelect v-model="form.recipient_mode" :options="recipientOptions" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">外发</label>
            <label class="flex items-center gap-2 text-sm text-default h-10 cursor-pointer">
              <input
                type="checkbox"
                v-model="form.include_default_target"
                class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
              />
              叠加默认外发渠道（上方「通知渠道」配置）
            </label>
          </div>
        </div>

        <div>
          <label class="block text-sm text-default mb-1">启用状态</label>
          <label class="flex items-center gap-2 text-sm text-default cursor-pointer">
            <input
              type="checkbox"
              v-model="form.enabled"
              class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
            />
            启用该规则（停用后不匹配、不通知）
          </label>
        </div>

        <!-- 规则级外发目标（编辑已有规则时） -->
        <div v-if="editing" class="border-t border-default pt-4">
          <div class="flex items-center justify-between mb-2">
            <label class="block text-sm text-default">规则级外发目标</label>
            <BaseButton v-if="!addingTarget" size="sm" intent="secondary" @click="addingTarget = true">添加目标</BaseButton>
          </div>
          <p class="text-xs text-muted mb-3">
            仅该规则命中的事件投递到这些目标；同一地址已被其他规则或默认渠道使用时会提示冲突。
          </p>

          <div v-if="ruleTargets.length === 0 && !addingTarget" class="text-xs text-muted py-2">
            暂无规则级目标{{ form.include_default_target ? '，外发走默认渠道' : '' }}
          </div>
          <div v-for="t in ruleTargets" :key="t.target_id"
            class="flex items-center justify-between py-2 border-b border-default last:border-b-0 gap-2">
            <div class="min-w-0 text-sm text-default">
              <span class="font-medium">{{ providerLabel(t.provider) }}</span>
              <span v-if="t.configuration_display" class="text-xs text-muted ml-2 break-all">{{ t.configuration_display }}</span>
            </div>
            <div class="flex gap-2 flex-shrink-0">
              <BaseButton size="sm" intent="ghost" :disabled="testingId === t.target_id" @click="handleTargetTest(t)">
                {{ testingId === t.target_id ? '发送中...' : '测试' }}
              </BaseButton>
              <BaseButton size="sm" intent="danger-ghost" @click="handleTargetRevoke(t)">停用</BaseButton>
            </div>
          </div>

          <div v-if="addingTarget" class="mt-3 space-y-3 bg-gray-50 rounded-lg p-3">
            <div class="grid grid-cols-2 gap-2">
              <BaseSelect v-model="newTarget.provider" :options="providerOptions" />
              <BaseInput v-model="newTarget.configValue"
                :placeholder="newTarget.provider === 'smtp' ? 'name@example.com' : 'https://...'" />
            </div>
            <p v-if="targetMessage" class="text-xs"
              :class="targetMessageType === 'success' ? 'text-success-600' : 'text-danger-600'">{{ targetMessage }}</p>
            <div class="flex justify-end gap-2">
              <BaseButton size="sm" intent="secondary" @click="cancelAddingTarget">取消</BaseButton>
              <BaseButton size="sm" :disabled="savingTarget" @click="handleTargetSave">
                {{ savingTarget ? '保存中...' : '保存目标' }}
              </BaseButton>
            </div>
          </div>
        </div>

        <div v-if="message" class="p-3 rounded-lg text-sm"
          :class="messageType === 'success' ? 'bg-success-50 border border-success-200 text-success-600' : 'bg-danger-50 border border-danger-200 text-danger-600'">
          {{ message }}
        </div>
      </div>

      <template #footer>
        <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
        <BaseButton :disabled="saving" @click="handleSave">{{ saving ? '保存中...' : '保存' }}</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { inject } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import NotificationChannelSettings from '@/components/notifications/NotificationChannelSettings.vue'
import {
  notificationsAPI,
  type NotificationRule,
  type TenantTarget,
} from '@/api/notifications'
import { listChannels } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'

const EVENT_OPTIONS = [
  { value: 'lead.captured', label: '客户留资' },
  { value: 'billing.credit_low', label: '积分提醒' },
  { value: 'billing.tenant_expiry', label: '到期提醒' },
  { value: 'abuse.rule_triggered', label: '滥用告警' },
]

const RECIPIENT_OPTIONS = [
  { value: 'kf_assignee', label: '线索归属员工' },
  { value: 'tenant_admins', label: '租户管理员' },
]

const PROVIDER_OPTIONS = [
  { value: 'feishu_bot', label: '飞书群机器人' },
  { value: 'dingtalk_bot', label: '钉钉群机器人' },
  { value: 'wecom_bot', label: '企微群机器人' },
  { value: 'smtp', label: '邮件' },
]

const columns: TableColumn[] = [
  { key: 'name', label: '规则名称' },
  { key: 'event_type', label: '事件', width: '110px' },
  { key: 'conditions', label: '匹配范围' },
  { key: 'recipient_mode', label: '站内信接收人', width: '120px' },
  { key: 'active_targets', label: '外发', width: '140px' },
  { key: 'enabled', label: '状态', width: '90px' },
  { key: 'actions', label: '操作', width: '120px', thAlign: 'center' },
]

const route = useRoute()
const router = useRouter()
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

function handleLogout() {
  if (tenantLogout) tenantLogout()
}

function goToChat() {
  router.push(`/t/${route.params.tenant_id}`)
}

const loading = ref(true)
const saving = ref(false)
const rules = ref<NotificationRule[]>([])
const allTargets = ref<TenantTarget[]>([])
const kfConfigOptions = ref<{ value: string; label: string }[]>([])

const showModal = ref(false)
const editing = ref<NotificationRule | null>(null)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const testingId = ref('')
const addingTarget = ref(false)
const savingTarget = ref(false)
const targetMessage = ref('')
const targetMessageType = ref<'success' | 'error'>('success')

const form = reactive({
  name: '',
  event_type: 'lead.captured',
  scope: 'channel' as 'channel' | 'kf_config',
  kf_config_ids: [] as string[],
  recipient_mode: 'kf_assignee' as 'kf_assignee' | 'tenant_admins',
  include_default_target: false,
  enabled: true,
})
const newTarget = reactive({ provider: 'feishu_bot', configValue: '' })

const eventTypeOptions = EVENT_OPTIONS
const recipientOptions = RECIPIENT_OPTIONS
const providerOptions = PROVIDER_OPTIONS

const queryFiltered = computed(() =>
  !!(route.query.event_type || route.query.scope || route.query.kf_config))

const filteredRules = computed(() => {
  let list = rules.value
  const et = route.query.event_type as string | undefined
  const scope = route.query.scope as string | undefined
  const kf = route.query.kf_config as string | undefined
  if (et) list = list.filter((r) => r.event_type === et)
  if (scope) list = list.filter((r) => r.conditions?.scope === scope)
  if (kf) list = list.filter((r) => (r.conditions?.kf_config_ids || []).includes(kf))
  return list
})

const ruleTargets = computed(() => {
  if (!editing.value) return []
  return allTargets.value.filter(
    (t) => t.rule_id === editing.value!.rule_id && t.state === 'active')
})

const modalTitle = computed(() =>
  editing.value ? `编辑规则 - ${editing.value.name}` : '新增规则')

function eventLabel(eventType: string): string {
  return EVENT_OPTIONS.find((e) => e.value === eventType)?.label || eventType
}

function recipientLabel(mode: string): string {
  return RECIPIENT_OPTIONS.find((r) => r.value === mode)?.label || mode
}

function providerLabel(provider: string): string {
  return PROVIDER_OPTIONS.find((p) => p.value === provider)?.label || provider
}

function conditionLabel(rule: NotificationRule): string {
  const c = rule.conditions || {}
  if (rule.event_type !== 'lead.captured') return '全部（租户级）'
  if (c.scope === 'kf_config' && (c.kf_config_ids || []).length > 0) {
    const names = c.kf_config_ids!.map(
      (id) => kfConfigOptions.value.find((o) => o.value === id)?.label || id)
    return `客服账号：${names.join('、')}`
  }
  return '全部客服账号'
}

function showError(e: any, fallback = '操作失败') {
  const code = e?.code || e?.message
  const map: Record<string, string> = {
    NOTIFICATION_RULE_EXISTS: '已存在同名同事件的规则',
    NOTIFICATION_RULE_SYSTEM: '预置规则不支持该操作',
    NOTIFICATION_RULE_EVENT_UNSUPPORTED: '该事件不支持配置规则',
    NOTIFICATION_RULE_CONDITIONS_INVALID: '匹配条件不合法',
    NOTIFICATION_KF_CONFIG_INVALID: '所选客服账号不存在或不属于当前租户',
    NOTIFICATION_RULE_NAME_INVALID: '规则名称不合法（1-64 字）',
    NOTIFICATION_RULE_NOT_FOUND: '规则不存在或已被删除',
    NOTIFICATION_RULE_NOTHING_TO_UPDATE: '没有需要保存的修改',
    NOTIFICATION_RULE_RECIPIENT_INVALID: '接收人模式不合法',
    NOTIFICATION_TARGET_OWNED: '该地址已被其他规则或默认渠道使用，请换地址或先停用原目标',
    NOTIFICATION_PROVIDER_UNAVAILABLE: '渠道暂不可用，请稍后重试',
    NOTIFICATION_BINDING_CHANGED: '配置已被其他操作修改，请刷新后重试',
    NOTIFICATION_TEST_RATE_LIMITED: '发送过于频繁，请 1 分钟后再试',
    NOTIFICATION_ENDPOINT_FORBIDDEN: '地址不合法：需为 https 的官方渠道域名',
    NOTIFICATION_INVALID_EMAIL: '邮箱格式不正确',
    NOTIFICATION_TENANT_DISABLED: '当前租户通知功能未开放',
    NOTIFICATION_DISABLED: '通知功能未开启',
  }
  const text = map[code] || `${fallback}（${code || e?.message || '未知错误'}）`
  if (addingTarget.value) {
    targetMessage.value = text
    targetMessageType.value = 'error'
  } else {
    message.value = text
    messageType.value = 'error'
  }
}

async function loadData() {
  loading.value = true
  try {
    const [rulesRes, targetsRes] = await Promise.all([
      notificationsAPI.listRules().catch(() => null),
      notificationsAPI.listTenantTargets().catch(() => null),
    ])
    if (rulesRes) rules.value = rulesRes.rules
    if (targetsRes) allTargets.value = targetsRes.targets
  } finally {
    loading.value = false
  }
}

async function loadKfConfigs() {
  try {
    const res = await listChannels()
    kfConfigOptions.value = (res.channels || [])
      .filter((c: any) => c.channel_type === 'wecom_kf')
      .map((c: any) => ({ value: c.config_id, label: c.name || c.config_id }))
  } catch {
    // 渠道列表加载失败不阻断页面，客服账号层选项显示为空并提示
  }
}

function resetForm() {
  form.name = ''
  form.event_type = 'lead.captured'
  form.scope = 'channel'
  form.kf_config_ids = []
  form.recipient_mode = 'kf_assignee'
  form.include_default_target = false
  form.enabled = true
}

function openCreate() {
  editing.value = null
  resetForm()
  // 深链预填：?event_type=lead.captured&scope=channel 或 &kf_config=<config_id>
  const et = route.query.event_type as string | undefined
  if (et && EVENT_OPTIONS.some((e) => e.value === et)) form.event_type = et
  const kf = route.query.kf_config as string | undefined
  if (kf) {
    form.event_type = form.event_type || 'lead.captured'
    form.scope = 'kf_config'
    form.kf_config_ids = [kf]
  } else if ((route.query.scope as string) === 'channel') {
    form.scope = 'channel'
  }
  if (form.event_type === 'lead.captured') form.recipient_mode = 'kf_assignee'
  else form.recipient_mode = 'tenant_admins'
  message.value = ''
  showModal.value = true
}

function openEdit(rule: NotificationRule) {
  editing.value = rule
  form.name = rule.name
  form.event_type = rule.event_type
  const c = rule.conditions || {}
  form.scope = c.scope === 'kf_config' ? 'kf_config' : 'channel'
  form.kf_config_ids = [...(c.kf_config_ids || [])]
  form.recipient_mode = rule.recipient_mode
  form.include_default_target = rule.include_default_target
  form.enabled = rule.enabled
  message.value = ''
  addingTarget.value = false
  newTarget.provider = 'feishu_bot'
  newTarget.configValue = ''
  showModal.value = true
}

function buildConditions(): NotificationRule['conditions'] {
  if (form.event_type !== 'lead.captured') return {}
  if (form.scope === 'kf_config') {
    return { scope: 'kf_config', kf_config_ids: [...form.kf_config_ids] }
  }
  return { scope: 'channel' }
}

async function handleSave() {
  message.value = ''
  if (!form.name.trim()) {
    message.value = '请填写规则名称'
    messageType.value = 'error'
    return
  }
  if (form.event_type === 'lead.captured' && form.scope === 'kf_config' && form.kf_config_ids.length === 0) {
    message.value = '请至少选择一个客服账号'
    messageType.value = 'error'
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      const body: Record<string, unknown> = {
        enabled: form.enabled,
        conditions: buildConditions(),
        recipient_mode: form.recipient_mode,
        include_default_target: form.include_default_target,
      }
      if (!editing.value.is_system) body.name = form.name.trim()
      await notificationsAPI.updateRule(editing.value.rule_id, body)
    } else {
      await notificationsAPI.createRule({
        event_type: form.event_type,
        name: form.name.trim(),
        conditions: buildConditions(),
        recipient_mode: form.recipient_mode,
        include_default_target: form.include_default_target,
        enabled: form.enabled,
      })
    }
    showModal.value = false
    await loadData()
  } catch (e) {
    showError(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleDelete(rule: NotificationRule) {
  if (!confirm(`确定删除规则「${rule.name}」？其规则级外发目标将一并停用。`)) return
  try {
    await notificationsAPI.deleteRule(rule.rule_id)
    await loadData()
  } catch (e) {
    showError(e, '删除失败')
  }
}

function cancelAddingTarget() {
  addingTarget.value = false
  newTarget.provider = 'feishu_bot'
  newTarget.configValue = ''
  targetMessage.value = ''
}

const PROVIDER_CHANNELS: Record<string, { channel: string; configKey: string }> = {
  feishu_bot: { channel: 'im_webhook', configKey: 'webhook_url' },
  dingtalk_bot: { channel: 'im_webhook', configKey: 'webhook_url' },
  wecom_bot: { channel: 'im_webhook', configKey: 'webhook_url' },
  smtp: { channel: 'email', configKey: 'email_address' },
}

async function handleTargetSave() {
  if (!editing.value || !newTarget.configValue.trim()) return
  const p = PROVIDER_CHANNELS[newTarget.provider]
  if (!p) return
  savingTarget.value = true
  targetMessage.value = ''
  try {
    await notificationsAPI.registerTenantTarget({
      channel: p.channel,
      provider: newTarget.provider,
      configuration: { [p.configKey]: newTarget.configValue.trim() },
      rule_id: editing.value.rule_id,
    })
    cancelAddingTarget()
    const targetsRes = await notificationsAPI.listTenantTargets()
    allTargets.value = targetsRes.targets
    targetMessage.value = '目标已添加'
    targetMessageType.value = 'success'
  } catch (e) {
    showError(e, '保存目标失败')
  } finally {
    savingTarget.value = false
  }
}

async function handleTargetRevoke(t: TenantTarget) {
  if (!confirm('确定停用该外发目标？')) return
  try {
    await notificationsAPI.revokeTenantTarget(t.target_id, t.binding_version)
    const targetsRes = await notificationsAPI.listTenantTargets()
    allTargets.value = targetsRes.targets
  } catch (e) {
    showError(e, '停用失败')
  }
}

async function handleTargetTest(t: TenantTarget) {
  testingId.value = t.target_id
  targetMessage.value = ''
  try {
    await notificationsAPI.tenantTest({
      target_id: t.target_id,
      binding_version: t.binding_version,
    })
    targetMessage.value = '测试通知已发送，请到对应渠道查收'
    targetMessageType.value = 'success'
  } catch (e) {
    showError(e, '测试发送失败')
  } finally {
    testingId.value = ''
  }
}

function clearQueryFilter() {
  router.replace({ query: {} })
}

onMounted(async () => {
  await Promise.all([loadData(), loadKfConfigs()])
})
</script>

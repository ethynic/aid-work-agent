<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader title="微信 Agent" :is-logged-in="isLoggedIn" :user="admin" @toggle-sidebar="toggleSidebar?.()" @logout="logout" />
    <main class="flex-1 min-h-0 overflow-y-auto px-4 py-6 sm:px-6">
      <div class="mx-auto max-w-6xl space-y-4">
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-default">
          <nav aria-label="微信 Agent 功能" class="flex flex-wrap gap-3">
            <BaseButton v-for="item in workspaces" :key="item.id" type="button" intent="ghost"
              class="-mb-px rounded-none border-b-2"
              :class="workspace === item.id ? 'border-primary-700 text-primary-700 font-semibold' : 'border-transparent'"
              :aria-pressed="workspace === item.id" :aria-controls="`weixin-workspace-${item.id}`"
              @click="showWorkspace(item.id)">{{ item.label }}</BaseButton>
          </nav>
          <div class="flex flex-wrap items-center gap-3 text-sm">
            <span class="text-muted" role="status">{{ deviceStatus }}</span>
            <RouterLink class="text-primary-700 underline" :to="{ name: 'tenant-local-tools', params: { tenant_id: tenantId } }">管理设备</RouterLink>
            <BaseButton type="button" intent="ghost" :disabled="loadingDevice" @click="refreshDevice">刷新</BaseButton>
          </div>
        </div>

        <div v-show="workspace === 'groups'" id="weixin-workspace-groups" class="grid items-start gap-4 md:grid-cols-[minmax(14rem,17rem)_minmax(0,1fr)]">
          <BaseCard>
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h2 class="m-0 text-base font-semibold text-default">跟踪群聊</h2>
              <BaseButton type="button" intent="secondary" @click="editGroup()">添加群聊</BaseButton>
            </div>
            <p v-if="groupsError" class="mt-3 text-sm text-danger-700" role="alert">{{ groupsError }}</p>
            <p v-else-if="!groupsReady" class="mt-3 text-sm text-muted">正在加载群聊…</p>
            <p v-else-if="!activeGroups.length" class="mt-3 text-sm text-muted">添加微信会话列表中显示的完整群名。</p>
            <template v-else>
              <label class="mt-4 flex items-center gap-2 text-sm text-muted">
                <input type="checkbox" :checked="allSelected" :indeterminate="selected.length > 0 && !allSelected" @change="toggleAll">
                全选 · 已选 {{ selected.length }} 群
              </label>
              <ul class="m-0 mt-2 list-none divide-y divide-default p-0">
                <li v-for="group in activeGroups" :key="group.id" class="flex flex-wrap items-center gap-x-2 py-2">
                  <label class="flex min-w-0 flex-1 items-start gap-2 text-sm text-default">
                    <input v-model="selected" class="mt-1 shrink-0" type="checkbox" :value="group.id">
                    <span class="break-all">{{ group.name }}</span>
                  </label>
                  <BaseButton type="button" intent="ghost" :aria-label="`编辑 ${group.name}`" @click="editGroup(group)">编辑</BaseButton>
                  <BaseButton type="button" intent="danger-ghost" :aria-label="`移除 ${group.name}`" @click="confirmRemove(group)">移除</BaseButton>
                </li>
              </ul>
            </template>
          </BaseCard>

          <div class="min-w-0 space-y-4">
            <nav aria-label="微信 Agent 操作" class="flex flex-wrap gap-2">
              <BaseButton v-for="item in tabs" :key="item.id" type="button" :intent="tab === item.id ? 'primary' : 'secondary'"
                :aria-pressed="tab === item.id" @click="changeTab(item.id)">{{ item.label }}</BaseButton>
            </nav>

            <BaseCard v-if="tab === 'generate'">
              <form class="space-y-4" @submit.prevent="generate">
                <h2 class="m-0 text-lg font-semibold text-default">生成总结</h2>
                <fieldset class="space-y-2">
                  <legend class="text-sm font-medium text-default">总结时间范围</legend>
                  <div class="flex flex-wrap gap-2">
                    <BaseButton v-for="item in rangeOptions" :key="item.id" type="button" :intent="preset === item.id ? 'primary' : 'secondary'"
                      :aria-pressed="preset === item.id" @click="choosePreset(item.id)">{{ item.label }}</BaseButton>
                  </div>
                </fieldset>
                <div v-if="preset === 'custom'" class="grid gap-3 sm:grid-cols-2">
                  <label for="weixin-start" class="space-y-1 text-sm text-default">
                    <span>开始时间</span>
                    <BaseInput id="weixin-start" v-model="customStart" type="datetime-local" required />
                  </label>
                  <label for="weixin-end" class="space-y-1 text-sm text-default">
                    <span>结束时间</span>
                    <BaseInput id="weixin-end" v-model="customEnd" type="datetime-local" required />
                  </label>
                </div>
                <div class="space-y-1 rounded-lg border border-default px-3 py-2 text-sm text-muted">
                  <p class="m-0">{{ rangeLabel }} · 北京时间</p>
                  <p class="m-0">{{ device?.name || (device ? '选定的设备' : '未选择设备') }} · 已选 {{ selected.length }} 群</p>
                </div>
                <p class="m-0 text-sm text-muted">点击后，所选时段内能核实的文字将用于生成总结。</p>
                <div class="flex flex-wrap items-center gap-3">
                  <BaseButton size="md" type="submit" :disabled="!canGenerate">{{ submitting ? '正在提交…' : '生成总结' }}</BaseButton>
                  <RouterLink v-if="showDailyLink" class="text-sm text-primary-700 underline" :to="{ query: { mode: 'daily' } }">使用兼容版今日总结</RouterLink>
                </div>
                <p v-if="submitError" class="m-0 text-sm text-danger-700" role="alert">{{ submitError }}</p>
              </form>
            </BaseCard>

            <WeixinSchedules v-else-if="tab === 'schedule'" :groups="activeGroups" :device="device" />
            <BaseCard v-else-if="!batchId">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h2 id="weixin-history-title" tabindex="-1" class="m-0 text-lg font-semibold text-default">总结历史</h2>
                <label class="flex items-center gap-2 text-sm text-muted">
                  筛选群聊
                  <BaseSelect v-model="historyGroup" class="max-w-56" aria-label="筛选群聊">
                    <option value="">全部群聊</option>
                    <option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }}{{ group.deleted_at ? '（已移除）' : '' }}</option>
                  </BaseSelect>
                </label>
              </div>
              <p v-if="historyError" class="my-3 text-sm text-danger-700" role="alert">{{ historyError }}</p>
              <p v-else-if="historyLoading && !history.length" class="my-3 text-sm text-muted">正在加载历史…</p>
              <p v-else-if="!history.length" class="my-3 text-sm text-muted">暂无总结记录。</p>
              <ul v-if="history.length" class="m-0 mt-2 list-none divide-y divide-default p-0">
                <li v-for="item in history" :key="item.id">
                  <button type="button" class="flex w-full flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-3 text-left text-sm text-default hover:bg-surface-hover"
                    :id="`weixin-history-${item.id}`" :aria-current="item.id === historySelection ? 'true' : undefined" @click="openHistory(item)">
                    <span class="min-w-0">
                      <strong class="block break-words">{{ item.title || (item.retry_batch_id ? '单群重试' : '手动总结') }}</strong>
                      <span class="text-muted">{{ formatBeijing(item.created_at, true) }} · {{ item.source === 'schedule' ? '定时' : '手动' }}</span>
                    </span>
                    <span class="text-muted">{{ item.groups.length }} 群 · {{ batchLabel(item.state) }}</span>
                  </button>
                </li>
              </ul>
              <BaseButton v-if="hasMore" type="button" intent="secondary" :disabled="historyLoading" @click="refreshHistory(true)">加载更多</BaseButton>
            </BaseCard>

            <BaseCard v-if="batchId" aria-labelledby="weixin-batch-title">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <h2 ref="batchHeading" id="weixin-batch-title" tabindex="-1" class="m-0 break-words text-lg font-semibold text-default">{{ batch?.title || '本次执行' }}</h2>
                <BaseButton v-if="tab === 'history'" type="button" intent="secondary" @click="backToHistory">返回总结历史</BaseButton>
              </div>
              <div v-if="batchError" class="mt-3 flex flex-wrap items-center gap-2">
                <p class="m-0 text-sm text-danger-700" role="alert">{{ batchError }}</p>
                <BaseButton type="button" intent="secondary" @click="refreshBatch">重试查看</BaseButton>
              </div>
              <p v-else-if="!batch" class="mt-3 text-sm text-muted" role="status">正在加载结果…</p>
              <template v-if="batch">
                <p class="mt-1 text-sm text-muted">{{ formatBeijing(batch.start_at) }} 至 {{ formatBeijing(batch.end_at) }} · 北京时间</p>
                <p class="text-sm text-muted" role="status">
                  {{ batchLabel(batch.state) }} · {{ batch.groups.length }} 群
                  <span v-if="batch.notification_status"> · {{ notificationLabels[batch.notification_status] }}</span>
                </p>
                <ul class="m-0 list-none space-y-3 p-0">
                  <li v-for="group in batch.groups" :key="group.group_id" class="min-w-0 rounded-lg border border-default">
                    <header class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-t-lg bg-canvas px-3 py-2.5">
                      <div class="flex min-w-0 items-start gap-2">
                        <BaseBadge intent="info" size="sm" class="shrink-0">微信群</BaseBadge>
                        <h3 class="m-0 min-w-0 break-all text-sm font-medium text-default">{{ group.name }}</h3>
                      </div>
                      <span class="text-xs text-muted">{{ groupLabel(group) }}</span>
                    </header>
                    <div v-if="group.summary || group.message_count !== undefined || canRetry(group)" class="min-w-0 space-y-2 border-t border-default p-3">
                      <WeixinSummaryMarkdown v-if="group.summary" :source="group.summary" />
                      <p v-if="group.message_count !== undefined" class="m-0 text-sm text-muted">
                        {{ group.message_count ? `使用 ${group.message_count} 条文字消息` : '此时段未读到文字消息' }}
                        {{ group.partial ? ' · 仅包含能核实的部分文字，实际覆盖起止时间无法确认' : '' }}
                      </p>
                      <div v-if="canRetry(group)" class="flex flex-wrap items-center gap-2">
                        <div class="min-w-0 space-y-1">
                          <p class="m-0 text-sm text-danger-700">{{ reason(group.error_code) }}</p>
                          <p class="m-0 text-sm text-muted">重试前，请在原任务设备的微信中打开并核对目标群。</p>
                        </div>
                        <BaseButton type="button" intent="secondary" :disabled="Boolean(retrying)" @click="retry(group)">
                          {{ retrying === group.group_id ? '正在提交…' : '仅重试此群' }}
                        </BaseButton>
                      </div>
                    </div>
                  </li>
                </ul>
                <p v-if="retryError" class="mt-3 text-sm text-danger-700" role="alert">{{ retryError }}</p>
                <RouterLink v-if="batch.retry_batch_id" class="mt-3 inline-block text-sm text-primary-700 underline"
                  :to="{ query: { tab: 'history', batch: batch.retry_batch_id } }">查看原批次</RouterLink>
              </template>
            </BaseCard>
          </div>
        </div>
        <WeixinFriendRequests v-if="friendsVisited" v-show="workspace === 'friends'" id="weixin-workspace-friends"
          :device="device" :loading-device="loadingDevice" />
      </div>
    </main>

    <BaseModal v-model="groupDialog" :title="removing ? '移除跟踪群' : editing ? '编辑群聊' : '添加群聊'" size="sm" accessible :show-fullscreen="false">
      <form class="space-y-3" @submit.prevent="saveGroupDialog">
        <p v-if="removing" class="break-words text-sm text-default">移除「{{ removing.name }}」？已有总结仍可在历史中查看。</p>
        <label v-else for="weixin-group-config" class="block space-y-1 text-sm text-default">
          <span>群聊全名</span>
          <BaseInput id="weixin-group-config" v-model="draftName" required maxlength="128" placeholder="输入微信会话列表显示的完整名称" />
        </label>
        <p v-if="groupDialogError" class="text-sm text-danger-700" role="alert">{{ groupDialogError }}</p>
        <div class="flex justify-end gap-2">
          <BaseButton type="button" intent="secondary" @click="groupDialog = false">取消</BaseButton>
          <BaseButton type="submit" :intent="removing ? 'danger' : 'primary'" :disabled="savingGroup || (!removing && !draftName.trim())">
            {{ removing ? '确认移除' : '保存' }}
          </BaseButton>
        </div>
      </form>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onScopeDispose, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { listDevices, type LocalToolDevice } from '@/api/localTools'
import {
  getBatch, listBatches, listGroups, presets, removeGroup, retryGroup, saveGroup, startBatch, WeixinAgentError,
  type Batch, type Group, type GroupResult, type HistoryBatch, type Preset,
} from '@/api/weixinAgent'
import AppHeader from '@/components/AppHeader.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import WeixinFriendRequests from './WeixinFriendRequests.vue'
import WeixinSchedules from './WeixinSchedules.vue'
import WeixinSummaryMarkdown from './WeixinSummaryMarkdown.vue'
import { formatBeijing } from './beijingTime'

const route = useRoute()
const router = useRouter()
const tenantId = computed(() => String(route.params.tenant_id ?? ''))
const toggleSidebar = inject<() => void>('toggleSidebar')
const { admin, isLoggedIn, logout } = useTenantAuth()
const workspaces = [{ id: 'groups', label: '群聊总结' }, { id: 'friends', label: '批量加好友' }] as const
type Workspace = typeof workspaces[number]['id']
const workspace = ref<Workspace>('groups')
const friendsVisited = ref(false)
function showWorkspace(id: Workspace) {
  if (id === 'friends') friendsVisited.value = true
  workspace.value = id
}
const tabs = [{ id: 'generate', label: '生成总结' }, { id: 'schedule', label: '定时总结' }, { id: 'history', label: '总结历史' }] as const
type Tab = typeof tabs[number]['id']
const tab = computed<Tab>(() => route.query.tab === 'schedule' ? 'schedule' : route.query.tab === 'history' ? 'history' : 'generate')
const batchId = computed(() => typeof route.query.batch === 'string' ? route.query.batch : '')
watch([tab, batchId], () => { workspace.value = 'groups' })
function changeTab(id: Tab) {
  if (id !== tab.value) void router.push({ query: { tab: id, ...(id === 'history' && batchId.value ? { batch: batchId.value } : {}) } })
}
function openBatch(id: string, selectedTab: Tab) {
  return router.push({ query: { tab: selectedTab, batch: id } })
}
const groups = ref<Group[]>([])
const activeGroups = computed(() => groups.value.filter(group => !group.deleted_at))
const groupsError = ref('')
const groupsReady = ref(false)
const selected = ref<string[]>([])
const allSelected = computed(() => !!activeGroups.value.length && selected.value.length === activeGroups.value.length)
const toggleAll = () => { selected.value = allSelected.value ? [] : activeGroups.value.map(group => group.id) }
const groupDialog = ref(false)
const groupDialogError = ref('')
const editing = ref<Group>()
const removing = ref<Group>()
const draftName = ref('')
const savingGroup = ref(false)
function editGroup(group?: Group) {
  removing.value = undefined
  editing.value = group
  draftName.value = group?.name ?? ''
  groupDialogError.value = ''
  groupDialog.value = true
}
function confirmRemove(group: Group) {
  editGroup()
  removing.value = group
}
async function loadGroups() {
  groupsError.value = ''
  try {
    groups.value = await listGroups()
    const current = new Set(activeGroups.value.map(group => group.id))
    selected.value = selected.value.filter(id => current.has(id))
  } catch (error) { groupsError.value = (error as Error).message }
  finally { groupsReady.value = true }
}
async function saveGroupDialog() {
  if (savingGroup.value) return
  savingGroup.value = true
  groupDialogError.value = ''
  try {
    if (removing.value) await removeGroup(removing.value.id)
    else {
      const group = await saveGroup(draftName.value, editing.value?.id)
      if (!editing.value) selected.value = [...selected.value, group.id]
    }
    groupDialog.value = false
    await loadGroups()
  } catch (error) { groupDialogError.value = (error as Error).message }
  finally { savingGroup.value = false }
}

const device = ref<LocalToolDevice>()
const deviceError = ref('')
const loadingDevice = ref(false)
let deviceRequest = 0
async function refreshDevice() {
  const request = ++deviceRequest
  loadingDevice.value = true
  deviceError.value = ''
  try {
    const items = await listDevices()
    if (request === deviceRequest) device.value = items.find(item => item.selected && item.status === 'active')
  } catch (error) {
    if (request === deviceRequest) {
      device.value = undefined
      deviceError.value = (error as Error).message
    }
  } finally { if (request === deviceRequest) loadingDevice.value = false }
}
const deviceStatus = computed(() => loadingDevice.value ? '检查设备中…' : deviceError.value ||
  (device.value ? `${device.value.name || '选定的设备'} · ${device.value.online ? '在线' : '连接待确认'}` : '请先选定运行微信的 Windows 设备'))

const rangeOptions: ReadonlyArray<{ id: Preset | 'custom'; label: string }> = [...presets, { id: 'custom', label: '自定义' }]
const preset = ref<Preset | 'custom'>('today')
const customStart = ref('')
const customEnd = ref('')
const now = ref(Date.now())
const interval = setInterval(() => { now.value = Date.now() }, 15_000)
function choosePreset(value: Preset | 'custom') {
  now.value = Date.now()
  preset.value = value
}
const day = 86_400_000
const offset = 8 * 3_600_000
const bounds = computed(() => {
  if (preset.value === 'custom') return [Date.parse(customStart.value + '+08:00'), Date.parse(customEnd.value + '+08:00')]
  const start = preset.value === 'today' ? Math.floor((now.value + offset) / day) * day - offset
    : now.value - ({ '24h': 1, '3d': 3, '7d': 7 }[preset.value]) * day
  return [start, now.value]
})
const boundsValid = computed(() => bounds.value[0] < bounds.value[1] &&
  (preset.value !== 'custom' || (bounds.value[0] >= now.value - 7 * day && bounds.value[1] <= now.value)))
const rangeLabel = computed(() => boundsValid.value
  ? `${formatBeijing(bounds.value[0])} 至 ${formatBeijing(bounds.value[1])}`
  : '请选择最近 168 小时内的开始和结束时间')
const submitting = ref(false)
const submitError = ref('')
const showDailyLink = ref(false)
const canGenerate = computed(() => !!device.value && !loadingDevice.value && !!selected.value.length && boundsValid.value && !submitting.value)
async function generate() {
  if (!canGenerate.value || !device.value) return
  submitting.value = true
  submitError.value = ''
  showDailyLink.value = false
  try {
    const result = await startBatch({
      device_id: device.value.device_id, group_ids: [...selected.value], preset: preset.value,
      ...(preset.value === 'custom' ? { start_at: new Date(bounds.value[0]).toISOString(), end_at: new Date(bounds.value[1]).toISOString() } : {}),
    })
    await openBatch(result.id, 'generate')
  } catch (error) {
    showDailyLink.value = error instanceof WeixinAgentError && error.code === 'WEIXIN_RANGE_PLUGIN_UNAVAILABLE'
    submitError.value = (error as Error).message
  } finally { submitting.value = false }
}

const batch = ref<Batch>()
const batchError = ref('')
let poll: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
const activeStates = new Set(['queued', 'claimed', 'running', 'cancel_requested'])
function stopPolling() { poll?.abort(); clearTimeout(timer) }
async function refreshBatch() {
  stopPolling()
  if (!batchId.value) return
  const current = new AbortController()
  poll = current
  batchError.value = ''
  try {
    const result = await getBatch(batchId.value, current.signal)
    if (current.signal.aborted) return
    batch.value = result
    if (activeStates.has(result.state) || result.notification_status === 'queued') {
      timer = setTimeout(refreshBatch, activeStates.has(result.state) ? 1500 : 5000)
    } else if (tab.value === 'history' && history.value.some(item => item.id === result.id && item.state !== result.state)) {
      void refreshHistory()
    }
  } catch (error) {
    if (!current.signal.aborted) batchError.value = (error as Error).message
  }
}
const retrying = ref('')
const retryError = ref('')
const canRetry = (group: GroupResult) => !activeStates.has(group.state) && group.state !== 'succeeded'
async function retry(group: GroupResult) {
  if (retrying.value || !canRetry(group) || !batch.value) return
  retrying.value = group.group_id
  retryError.value = ''
  try {
    const result = await retryGroup(batch.value.id, group.group_id)
    await openBatch(result.id, tab.value)
    if (tab.value === 'history') void refreshHistory()
  } catch (error) { retryError.value = (error as Error).message }
  finally { retrying.value = '' }
}
function batchLabel(state: Batch['state']) {
  return { queued: '等待接单', running: '执行中', succeeded: '已完成', partial: '部分完成', failed: '失败' }[state]
}
function groupLabel(group: GroupResult) {
  if (group.state === 'queued') return '等待接单'
  if (activeStates.has(group.state)) return '读取并总结中'
  if (group.state !== 'succeeded') return '失败'
  return group.partial ? '部分读取' : group.message_count ? '已保存' : '无文字'
}
const notificationLabels = { queued: '通知待处理', sent: '通知已发送', failed: '通知投递失败', skipped: '通知按规则跳过' }
function reason(code?: string) {
  const labels: Record<string, string> = {
    UI_CHANGED: '微信会话身份已变化，请先打开这个群聊。',
    TARGET_NOT_FOUND: '未能找到可核实的目标群聊，请打开群聊并核对名称。',
    TARGET_AMBIGUOUS: '无法唯一确认同名目标。',
    WEIXIN_NOT_FOUND: '请在任务设备上打开微信并登录。',
    CONTENT_UNAVAILABLE: '微信消息或日期无法核实，请检查会话可见性。',
    SUMMARY_COVERAGE_UNAVAILABLE: '无法确认所选时段内的文字消息。',
    SUMMARY_FAILED: '模型总结未完成，请稍后重试。',
    DEVICE_NOT_SELECTED: '原任务设备已解绑，请检查本地工具。',
    DEVICE_OFFLINE: '原任务设备离线，请检查 Runtime。',
    WEIXIN_RANGE_PLUGIN_UNAVAILABLE: '原任务设备暂不支持这个总结工具。',
    NO_CREDIT: '积分用尽，请联系管理员充值。',
    RESULT_TIMEOUT: '设备未在有效期内完成，请检查 Runtime 后重试。',
    LOCAL_DEVICE_NOT_READY: '任务设备有待处理的操作，请检查 Runtime 后重试。',
  }
  return labels[code ?? ''] || '未能确认执行完成，请检查微信与 Runtime 后重试。'
}

const history = ref<HistoryBatch[]>([])
const historySelection = ref('')
const batchHeading = ref<HTMLElement>()
function openHistory(item: HistoryBatch) {
  historySelection.value = item.id
  return 'legacy' in item ? router.push({ query: { run: item.id } }) : openBatch(item.id, 'history')
}
async function backToHistory() {
  if (!historySelection.value) historySelection.value = batchId.value
  await router.push({ query: { tab: 'history' } })
  await nextTick()
  const previous = document.getElementById(`weixin-history-${historySelection.value}`)
  const target = previous || document.getElementById('weixin-history-title')
  target?.focus()
}
const historyGroup = ref('')
const historyLoading = ref(false)
const historyError = ref('')
const hasMore = ref(false)
let historySeq = 0
async function refreshHistory(append = false) {
  const seq = ++historySeq
  historyLoading.value = true
  historyError.value = ''
  const offset = append ? history.value.length : 0
  try {
    const response = await listBatches(historyGroup.value, offset)
    if (seq !== historySeq) return
    history.value = append ? [...history.value, ...response.batches] : response.batches
    hasMore.value = response.has_more
  } catch (error) { if (seq === historySeq) historyError.value = (error as Error).message }
  finally { if (seq === historySeq) historyLoading.value = false }
}
watch([tab, historyGroup], () => {
  if (tab.value === 'history') {
    history.value = []
    void refreshHistory()
  }
}, { immediate: true })
watch(batchId, async (id) => {
  batch.value = undefined
  retryError.value = ''
  void refreshBatch()
  if (id && tab.value === 'history') {
    await nextTick()
    batchHeading.value?.focus()
  }
}, { immediate: true })
void loadGroups()
void refreshDevice()
onScopeDispose(() => { stopPolling(); clearInterval(interval); ++deviceRequest; ++historySeq })
</script>

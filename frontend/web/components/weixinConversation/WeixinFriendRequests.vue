<template>
  <div class="min-w-0 space-y-4">
    <nav aria-label="好友申请操作" class="flex flex-wrap gap-2">
      <BaseButton type="button" :aria-pressed="view === 'import'" :intent="view === 'import' ? 'primary' : 'secondary'"
        @click="view = 'import'">导入清单</BaseButton>
      <BaseButton type="button" :aria-pressed="view === 'history'" :intent="view === 'history' ? 'primary' : 'secondary'"
        @click="showHistory">申请记录</BaseButton>
    </nav>

    <div v-show="view === 'import'" class="space-y-3">
      <BaseCard>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="m-0 text-base font-semibold text-default">好友清单</h2>
            <p v-if="draft" class="mt-1 break-all text-sm text-muted">{{ draft.filename }} · {{ draft.contacts.length }} 条</p>
          </div>
          <input ref="fileInput" type="file" accept=".xlsx,.csv" hidden @change="uploadFile">
          <BaseButton type="button" intent="secondary" :disabled="uploading || submitting" @click="fileInput?.click()">
            {{ uploading ? '正在读取…' : '上传清单' }}
          </BaseButton>
        </div>
        <p class="mb-0 mt-2 text-xs text-muted">支持 Excel（.xlsx，第一张工作表）或 UTF-8 CSV，最多 200 条。必填列「手机号或微信号」请用文本格式，「称呼」「验证语」选填。</p>
        <p v-if="exclusions.length" class="mb-0 mt-3 rounded-lg bg-warning-50 px-3 py-2 text-sm text-warning-800" role="status">
          已自动排除 {{ excludedCount }} 条（{{ exclusions.join('、') }}）
        </p>
        <p v-if="uploadError" class="mb-0 mt-2 text-sm text-danger-700" role="alert">{{ uploadError }}</p>
      </BaseCard>

      <BaseCard v-if="draft && eligible.length">
        <h2 class="m-0 text-base font-semibold text-default">待提交联系人</h2>
        <p class="mb-3 mt-1 text-sm text-muted" aria-live="polite">
          {{ submitted ? '此清单已处理，可在申请记录中回看。' : `已选 ${selectedCount} / ${eligible.length} 位，可取消个别联系人` }}
        </p>
        <BaseTable :columns="previewColumns" :data="eligible">
          <template #selected="{ row }">
            <input v-model="row.selected" type="checkbox" :disabled="submitting || submitted"
              :aria-label="`提交 ${row.name || row.identifier}`">
          </template>
          <template #name="{ row }"><span class="break-all">{{ row.name || '—' }}</span></template>
          <template #identifier="{ row }"><span class="break-all">{{ row.identifier }}</span></template>
        </BaseTable>
        <form class="mt-4 flex flex-wrap items-end justify-between gap-3" @submit.prevent="submit">
          <label for="weixin-friend-verification" class="block max-w-md space-y-1 text-sm">
            <span>默认验证语 <span class="text-muted">选填</span></span>
            <BaseInput id="weixin-friend-verification" v-model="verifyMessage" maxlength="60"
              :disabled="submitting || submitted" placeholder="例如：你好，我是研讨会的许遥" />
            <span class="block text-xs text-muted">清单里的验证语优先，两处都留空时使用微信预填的内容。</span>
          </label>
          <BaseButton type="submit" :disabled="!canSubmit">
            {{ submitting ? '正在提交…' : submitted ? '此清单已处理' : `提交 ${selectedCount} 条好友申请` }}
          </BaseButton>
        </form>
        <p v-if="!device?.online && !loadingDevice" class="mb-0 mt-2 text-sm text-muted">请选定在线的微信运行设备。</p>
        <p v-if="submitError" class="mb-0 mt-2 text-sm text-danger-700" role="alert">{{ submitError }}</p>
      </BaseCard>
      <BaseCard v-else-if="draft">
        <p class="m-0 text-sm text-muted">此清单没有可提交的联系人。</p>
      </BaseCard>
    </div>

    <BaseCard v-if="view === 'history' && !detailId">
      <h2 ref="historyHeading" tabindex="-1" class="m-0 text-base font-semibold text-default">申请记录</h2>
      <p v-if="historyLoading && !history.length" class="text-sm text-muted" role="status">正在加载记录…</p>
      <p v-else-if="!historyError && !history.length" class="text-sm text-muted">暂无好友申请记录。</p>
      <ul v-if="history.length" class="m-0 mt-3 list-none divide-y divide-default p-0">
        <li v-for="item in history" :key="item.id" class="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
          <div class="min-w-0">
            <h3 class="m-0 break-all text-sm font-semibold text-default">{{ item.filename }}</h3>
            <p class="m-0 mt-1 text-muted">{{ formatBeijing(item.created_at) }} · {{ item.total }} 条 · {{ countLine(item) }}</p>
          </div>
          <BaseButton type="button" intent="secondary" :aria-label="`查看 ${item.filename} 的申请记录`"
            @click="openBatch(item.id)">查看详情</BaseButton>
        </li>
      </ul>
      <p v-if="historyError" class="text-sm text-danger-700" role="alert">{{ historyError }}</p>
      <BaseButton v-if="historyError" type="button" intent="secondary" :disabled="historyLoading" @click="loadHistory()">重新加载</BaseButton>
      <BaseButton v-else-if="hasMore" type="button" intent="secondary" :disabled="historyLoading" @click="loadHistory(true)">加载更多</BaseButton>
    </BaseCard>

    <BaseCard v-if="view === 'history' && detailId" class="min-w-0">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 ref="detailHeading" tabindex="-1" class="m-0 break-all text-base font-semibold text-default">{{ detail?.filename || '申请详情' }}</h2>
          <p v-if="detail" class="m-0 mt-1 text-sm text-muted">{{ formatBeijing(detail.created_at) }} · {{ detail.total }} 条 · {{ detail.state === 'running' ? '正在逐条处理' : '处理结束' }} · 北京时间</p>
        </div>
        <BaseButton type="button" intent="secondary" @click="backToHistory">返回申请记录</BaseButton>
      </div>
      <p v-if="detailLoading && !detail" class="text-sm text-muted" role="status">正在加载详情…</p>
      <div v-if="detailError" class="flex flex-wrap items-center gap-2">
        <p class="text-sm text-danger-700" role="alert">{{ detailError }}</p>
        <BaseButton type="button" intent="secondary" @click="refreshDetail">重新加载</BaseButton>
      </div>
      <template v-if="detail">
        <div class="my-3 flex flex-wrap items-center justify-between gap-2">
          <p class="m-0 text-sm text-muted" aria-live="polite">{{ countLine(detail) }}</p>
          <label class="flex items-center gap-2 text-sm text-muted">提交状态
            <BaseSelect v-model="filter" class="w-36" size="sm">
              <option value="">全部状态</option>
              <option v-for="(label, status) in statusLabels" :key="status" :value="status">{{ label }}</option>
            </BaseSelect>
          </label>
        </div>
        <BaseTable :columns="resultColumns" :data="filteredResults">
          <template #name="{ row }"><span class="break-all">{{ row.name || '—' }}</span></template>
          <template #identifier="{ row }"><span class="break-all">{{ row.identifier || '—' }}</span></template>
          <template #status="{ row }">
            <BaseBadge size="sm" :intent="badgeIntent(row.status)">{{ statusLabels[row.status as FriendStatus] }}</BaseBadge>
            <p v-if="row.reason" class="m-0 mt-1 text-xs text-muted">{{ reasonText(row.status, row.reason) }}</p>
          </template>
          <template #submitted_at="{ row }">{{ row.submitted_at ? formatBeijing(row.submitted_at, true) : '—' }}</template>
          <template #empty>此状态下没有联系人。</template>
        </BaseTable>
      </template>
    </BaseCard>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref } from 'vue'
import {
  getFriendBatch, listFriendBatches, previewFriends, startFriendBatch, type FriendBatch, type FriendBatchSummary,
  type FriendPreview, type FriendStatus,
} from '@/api/weixinAgent'
import type { LocalToolDevice } from '@/api/localTools'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import { generateUuid } from '@/utils/uuid'
import { formatBeijing } from './beijingTime'

const props = defineProps<{ device?: LocalToolDevice; loadingDevice: boolean }>()
const statusLabels: Record<FriendStatus, string> = {
  request_sent: '已提交', unknown: '待核对', failed: '未提交', friend_added: '已直接添加',
  already_requested: '已有申请', already_friend: '已是好友', skipped: '已跳过', queued: '等待执行', running: '处理中',
}
const reasonLabels: Record<string, string> = {
  DUPLICATE: '清单内重复', MISSING_IDENTIFIER: '缺少手机号或微信号', INVALID_IDENTIFIER: '手机号或微信号格式无效',
  INVALID_VERIFICATION: '验证语格式无效', PREVIOUS_REQUEST: '已有申请记录', PREVIOUS_FRIEND: '已经是好友',
  PREVIOUS_UNKNOWN: '上一批次待核对', REQUEST_IN_PROGRESS: '已有任务在处理', NOT_SELECTED: '本次未选中',
  TARGET_NOT_FOUND: '微信未找到目标', NOT_LOGGED_IN: '微信尚未登录', WEIXIN_NOT_FOUND: '运行设备未打开微信',
  DESKTOP_NOT_INTERACTIVE: '运行设备桌面不可交互', RESULT_TIMEOUT: '运行设备未在有效期内完成',
  LOCAL_DEVICE_PLUGIN_CHANGED: '运行设备的微信插件已变化',
}
function reasonText(status: FriendStatus, code: string) {
  return status === 'unknown' ? '未收到可确认的回执，请在微信中核对申请状态。' : reasonLabels[code] || '本次申请未提交。'
}
function badgeIntent(status: FriendStatus) {
  if (status === 'request_sent' || status === 'friend_added') return 'success'
  if (status === 'unknown') return 'warning'
  if (status === 'failed') return 'danger'
  if (status === 'queued' || status === 'running') return 'info'
  return 'neutral'
}
function countLine(item: FriendBatchSummary) {
  return `已提交 ${item.counts.request_sent || 0} 条 · 待核对 ${item.counts.unknown || 0} 条 · 未提交 ${item.counts.failed || 0} 条`
}
const previewColumns = [{ key: 'selected', label: '提交', width: '64px' }, { key: 'name', label: '联系人' },
  { key: 'identifier', label: '手机号或微信号' }]
const resultColumns = [{ key: 'name', label: '联系人' }, { key: 'identifier', label: '手机号或微信号' },
  { key: 'status', label: '执行结果' }, { key: 'submitted_at', label: '提交时间' }]

const view = ref<'import' | 'history'>('import')
const fileInput = ref<HTMLInputElement>()
const draft = ref<FriendPreview>()
const verifyMessage = ref('')
const uploading = ref(false)
const uploadError = ref('')
const submitting = ref(false)
const submitted = ref(false)
const submitError = ref('')
let lastRequest: { key: string; id: string } | undefined
const eligible = computed(() => draft.value?.contacts.filter(row => !row.exclusion) || [])
const excludedCount = computed(() => (draft.value?.contacts.length || 0) - eligible.value.length)
const selectedCount = computed(() => eligible.value.filter(row => row.selected).length)
const exclusions = computed(() => {
  const counts = new Map<string, number>()
  for (const row of draft.value?.contacts || []) {
    if (row.exclusion) counts.set(row.exclusion, (counts.get(row.exclusion) || 0) + 1)
  }
  return [...counts].map(([reason, count]) => `${reasonLabels[reason] || '不可提交'} ${count} 条`)
})
const canSubmit = computed(() => Boolean(props.device?.online && !props.loadingDevice &&
  !uploading.value && !submitting.value && !submitted.value && selectedCount.value))

async function uploadFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || uploading.value || submitting.value) return
  uploading.value = true
  uploadError.value = submitError.value = ''
  draft.value = undefined
  verifyMessage.value = ''
  submitted.value = false
  lastRequest = undefined
  try {
    const preview = await previewFriends(file)
    draft.value = { ...preview, contacts: preview.contacts.map(row => ({ ...row, selected: !row.exclusion })) }
  } catch (error) { uploadError.value = (error as Error).message }
  finally { uploading.value = false }
}
async function submit() {
  if (!canSubmit.value || !props.device || !draft.value) return
  submitting.value = true
  submitError.value = ''
  try {
    const payload = {
      device_id: props.device.device_id, filename: draft.value.filename, verify_message: verifyMessage.value.trim(),
      contacts: draft.value.contacts.map(({ name, identifier, verify_message, selected }) =>
        ({ name, identifier, verify_message, selected })),
    }
    const key = JSON.stringify(payload)
    if (lastRequest?.key !== key) lastRequest = { key, id: generateUuid() }
    const result = await startFriendBatch({ request_id: lastRequest.id, ...payload })
    submitted.value = true
    await openBatch(result.id, result)
  } catch (error) { submitError.value = (error as Error).message }
  finally { submitting.value = false }
}

const history = ref<FriendBatchSummary[]>([])
const historyLoading = ref(false)
const historyError = ref('')
const hasMore = ref(false)
const detailId = ref('')
const detail = ref<FriendBatch>()
const detailError = ref('')
const detailLoading = ref(false)
const detailHeading = ref<HTMLElement>()
const historyHeading = ref<HTMLElement>()
const filter = ref('')
const filteredResults = computed(() => (detail.value?.contacts || []).filter(row => !filter.value || row.status === filter.value))
let poll: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
function stopPolling() { poll?.abort(); clearTimeout(timer); detailLoading.value = false }
async function refreshDetail() {
  stopPolling()
  if (!detailId.value) return
  const current = new AbortController()
  poll = current
  detailError.value = ''
  detailLoading.value = true
  try {
    const result = await getFriendBatch(detailId.value, current.signal)
    if (current.signal.aborted) return
    detail.value = result
    if (result.state === 'running') timer = setTimeout(refreshDetail, 1500)
  } catch (error) {
    if (!current.signal.aborted) detailError.value = (error as Error).message
  } finally { if (!current.signal.aborted) detailLoading.value = false }
}
async function loadHistory(append = false) {
  if (historyLoading.value) return
  historyLoading.value = true
  historyError.value = ''
  try {
    const result = await listFriendBatches(append ? history.value.length : 0)
    history.value = append ? [...history.value, ...result.batches] : result.batches
    hasMore.value = result.has_more
  } catch (error) { historyError.value = (error as Error).message }
  finally { historyLoading.value = false }
}
function showHistory() {
  view.value = 'history'
  if (!detailId.value) void loadHistory()
}
async function openBatch(id: string, initial?: FriendBatch) {
  stopPolling()
  view.value = 'history'
  detailId.value = id
  detail.value = initial
  detailError.value = filter.value = ''
  if (!initial || initial.state === 'running') void refreshDetail()
  await nextTick()
  detailHeading.value?.focus()
}
async function backToHistory() {
  stopPolling()
  detailId.value = ''
  void loadHistory()
  await nextTick()
  historyHeading.value?.focus()
}
onScopeDispose(stopPolling)
</script>

<template>
  <!-- 群绑定选择器：设备 → 异步搜索候选 → 选择 → 创建绑定 → 核验状态（P3-A2） -->
  <BaseModal
    :model-value="modelValue"
    :title="mode === 'select' ? '选择目标群绑定' : '群绑定管理'"
    size="lg"
    @update:model-value="v => emit('update:modelValue', v)"
  >
    <div class="space-y-5">
      <!-- 既有绑定列表 -->
      <section>
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-sm font-semibold text-default">既有绑定</h3>
          <BaseButton size="sm" intent="ghost" :disabled="loadingBindings" @click="loadBindings">刷新</BaseButton>
        </div>
        <div v-if="loadingBindings && !bindings.length" class="text-sm text-muted py-3">加载中...</div>
        <div v-else-if="bindingsError" class="text-sm text-danger-600 py-3">
          {{ bindingsError }}
          <BaseButton size="sm" intent="secondary" @click="loadBindings">重试</BaseButton>
        </div>
        <div v-else-if="!bindings.length" class="empty-state">
          <div class="empty-state-icon">#</div>
          <p class="text-sm text-muted">暂无群绑定，请先通过下方搜索创建</p>
        </div>
        <div v-else class="table-scroll-wrapper">
          <BaseTable :columns="bindingColumns" :data="bindings" row-key="id">
            <template #label="{ row }">
              <span class="text-sm font-medium text-default" :title="row.label">{{ row.label }}</span>
            </template>
            <template #device="{ row }">{{ deviceName(row.device_id) }}</template>
            <template #state="{ row }">
              <BaseBadge :intent="GROUP_BINDING_STATE_BADGE[row.state as GroupBindingState]">
                {{ GROUP_BINDING_STATE_LABELS[row.state as GroupBindingState] }}
              </BaseBadge>
            </template>
            <template #verified_at="{ row }">{{ formatDateTime(row.verified_at) }}</template>
            <template #actions="{ row }">
              <div class="flex items-center justify-center gap-1 whitespace-nowrap">
                <BaseButton
                  v-if="mode === 'select'"
                  size="sm"
                  intent="ghost"
                  :disabled="row.state !== 'complete'"
                  :title="row.state !== 'complete' ? '仅已核验（complete）绑定可用于发送' : ''"
                  @click="chooseBinding(row as GroupBindingItem)"
                >选用</BaseButton>
                <BaseButton
                  v-if="row.state === 'pending'"
                  size="sm"
                  intent="ghost"
                  :disabled="verifyingIds.has(row.id)"
                  @click="handleVerify(row as GroupBindingItem)"
                >{{ verifyingIds.has(row.id) ? '核验中...' : '核验' }}</BaseButton>
                <span
                  v-if="row.identity_evidence_ref"
                  class="text-xs text-muted"
                  :title="`证据引用：${row.identity_evidence_ref}`"
                >🔍</span>
              </div>
            </template>
            <template #empty>暂无群绑定</template>
          </BaseTable>
        </div>
      </section>

      <!-- 搜索新群 -->
      <section class="rounded-lg border border-default p-3 space-y-3">
        <h3 class="text-sm font-semibold text-default">搜索并绑定新群</h3>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <label class="form-label">设备 <span class="form-required">*</span></label>
            <BaseSelect v-model="selectedDeviceId" :disabled="searching">
              <option value="" disabled>请选择设备</option>
              <option v-for="d in devices" :key="d.device_id" :value="d.device_id">
                {{ deviceOptionLabel(d) }}
              </option>
            </BaseSelect>
          </div>
          <div>
            <label class="form-label">群名称关键字 <span class="form-required">*</span></label>
            <BaseInput
              v-model="keyword"
              placeholder="输入完整群名称（精确匹配）"
              :disabled="searching"
              @keyup.enter="startSearch"
            />
          </div>
          <div class="flex items-end gap-2">
            <BaseButton size="md" :disabled="!canSearch || searching" @click="startSearch">
              {{ searching ? '搜索中...' : '搜索' }}
            </BaseButton>
            <BaseButton
              size="md"
              intent="secondary"
              :disabled="!selectedDeviceId || preflighting"
              :title="selectedDeviceId ? '对设备执行微信环境预检（只读）' : '请先选择设备'"
              @click="handlePreflight"
            >{{ preflighting ? '预检中...' : '设备预检' }}</BaseButton>
          </div>
        </div>

        <!-- 预检结果 -->
        <div v-if="preflightResult" class="rounded-lg bg-canvas border border-default p-3 text-xs text-default">
          <div class="font-medium mb-1">设备预检结果</div>
          <div class="break-all whitespace-pre-wrap">{{ preflightSummary }}</div>
        </div>
        <div v-else-if="preflightError" class="text-xs text-warning-600">{{ preflightError }}</div>

        <!-- 搜索状态 / 候选 -->
        <div v-if="searchStatus === 'pending' || searchStatus === 'running'" class="text-sm text-muted">
          正在设备上搜索群（异步任务，每 2 秒查询一次）...
        </div>
        <div v-else-if="searchStatus === 'failed'" class="text-sm text-danger-600">
          搜索失败：{{ searchError || '设备未返回结果' }}，可重试
        </div>
        <template v-else-if="searchStatus === 'succeeded'">
          <div class="text-xs text-muted">
            候选 {{ candidates.length }} 个<span v-if="searchTruncated">（结果被截断，请用更精确的关键字）</span>，请选择目标群
          </div>
          <div v-if="!candidates.length" class="text-sm text-warning-600">
            未搜索到匹配的群，请确认群名称与设备上登录的账号
          </div>
          <div v-else class="table-scroll-wrapper">
            <BaseTable :columns="candidateColumns" :data="candidates" row-key="target_ref">
              <template #title="{ row }">
                <span class="text-sm text-default" :title="String(row.title)">{{ row.title }}</span>
              </template>
              <template #actions="{ row }">
                <div class="text-center">
                  <BaseButton
                    size="sm"
                    intent="ghost"
                    :disabled="creating"
                    @click="selectCandidate(row as GroupSearchCandidate)"
                  >选择</BaseButton>
                </div>
              </template>
              <template #empty>无候选</template>
            </BaseTable>
          </div>
        </template>

        <!-- 创建绑定表单 -->
        <div v-if="selectedCandidate" class="rounded-lg bg-canvas border border-default p-3 space-y-2">
          <div class="text-sm text-default">
            已选群：<span class="font-medium">{{ selectedCandidate.title }}</span>
          </div>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label class="form-label">绑定标签 <span class="form-required">*</span></label>
              <BaseInput v-model="bindingLabel" placeholder="绑定显示名（默认群名，核验按精确标题比对）" :disabled="creating" />
            </div>
            <div class="flex items-end">
              <BaseButton :disabled="!canCreateBinding || creating" @click="handleCreateBinding">
                {{ creating ? '创建中...' : '创建绑定（待核验）' }}
              </BaseButton>
            </div>
          </div>
        </div>
      </section>
    </div>

    <template #footer>
      <BaseButton intent="secondary" @click="emit('update:modelValue', false)">关闭</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
/**
 * GroupBindingPicker：绑定选择 + 管理（P3-A2，R54②）。
 * - 搜索经 POST /group-searches（202 异步）→ GET /group-searches/{id} 2 秒轮询至终态；
 * - 候选（target_ref）→ POST /group-bindings（pending_verification）→ verify 核验；
 * - VERIFY_FAILED（设备离线/超时/0 命中）提示可重试，绑定保持待核验；
 * - select 模式仅允许选用 complete 绑定（发送侧硬门禁）。
 * 轮询取消：弹框关闭 / 组件卸载即停止（poll 代际 token 失效）。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  WX_ERROR_CODES,
  WeixinApiError,
  createGroupBinding,
  createGroupSearch,
  getGroupSearch,
  listDevices,
  listGroupBindings,
  preflightDevice,
  verifyGroupBinding,
  type DeviceItem,
  type GroupBindingItem,
  type GroupBindingState,
  type GroupSearchCandidate,
} from '@/api/weixinMarketing'
import {
  GROUP_BINDING_STATE_BADGE,
  GROUP_BINDING_STATE_LABELS,
  formatDateTime,
} from './weixinDisplay'

const props = defineProps<{
  modelValue: boolean
  /** select：为自动化选择目标群（仅 complete 可选用）；manage：纯管理 */
  mode?: 'select' | 'manage'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'selected': [binding: GroupBindingItem]
}>()

const toast = useToast()

// ---------- 设备 ----------
const devices = ref<DeviceItem[]>([])
const devicesError = ref('')
const selectedDeviceId = ref('')

async function loadDevices() {
  devicesError.value = ''
  try {
    const res = await listDevices()
    devices.value = res.items
    if (!selectedDeviceId.value && res.items.length) {
      const preferred = res.items.find(d => d.online && d.weixin.available) || res.items[0]
      selectedDeviceId.value = preferred.device_id
    }
  } catch (e) {
    devicesError.value = e instanceof WeixinApiError ? e.message : '加载设备列表失败'
  }
}

function deviceName(deviceId: string): string {
  const d = devices.value.find(x => x.device_id === deviceId)
  return d?.name || deviceId.slice(0, 8)
}

function deviceOptionLabel(d: DeviceItem): string {
  const flags: string[] = []
  if (!d.online) flags.push('离线')
  if (!d.weixin.available) flags.push('无微信能力')
  return `${d.name || d.device_id.slice(0, 8)}${flags.length ? `（${flags.join('、')}）` : ''}`
}

// ---------- 预检 ----------
const preflighting = ref(false)
const preflightResult = ref<Record<string, unknown> | null>(null)
const preflightError = ref('')

const preflightSummary = computed(() => {
  if (!preflightResult.value) return ''
  try {
    return JSON.stringify(preflightResult.value, null, 2)
  } catch {
    return String(preflightResult.value)
  }
})

async function handlePreflight() {
  if (!selectedDeviceId.value || preflighting.value) return
  preflighting.value = true
  preflightResult.value = null
  preflightError.value = ''
  try {
    const res = await preflightDevice(selectedDeviceId.value)
    preflightResult.value = res.environment
  } catch (e) {
    preflightError.value =
      e instanceof WeixinApiError && e.code === WX_ERROR_CODES.PREFLIGHT_FAILED
        ? `${e.message}`
        : '设备预检失败，可重试'
  } finally {
    preflighting.value = false
  }
}

// ---------- 异步搜索（2s 轮询） ----------
const keyword = ref('')
const searching = ref(false)
const searchStatus = ref<'idle' | 'pending' | 'running' | 'succeeded' | 'failed'>('idle')
const searchError = ref('')
const candidates = ref<GroupSearchCandidate[]>([])
const searchTruncated = ref(false)
/** 轮询代际：关闭弹框/新搜索即递增，旧轮询循环失效退出 */
let pollGeneration = 0
const SEARCH_POLL_INTERVAL_MS = 2000
const SEARCH_POLL_MAX_ATTEMPTS = 60

const canSearch = computed(() => !!selectedDeviceId.value && keyword.value.trim().length > 0)

async function startSearch() {
  if (!canSearch.value || searching.value) return
  const generation = ++pollGeneration
  searching.value = true
  searchStatus.value = 'pending'
  searchError.value = ''
  candidates.value = []
  searchTruncated.value = false
  selectedCandidate.value = null
  try {
    const created = await createGroupSearch({
      device_id: selectedDeviceId.value,
      keyword: keyword.value.trim(),
    })
    searchDetailId.value = created.search_id
    for (let attempt = 0; attempt < SEARCH_POLL_MAX_ATTEMPTS; attempt++) {
      if (generation !== pollGeneration) return
      const detail = await getGroupSearch(created.search_id)
      if (generation !== pollGeneration) return
      searchStatus.value = detail.status
      if (detail.status === 'succeeded') {
        candidates.value = detail.items || []
        searchTruncated.value = !!detail.truncated
        return
      }
      if (detail.status === 'failed') {
        searchError.value = detail.error_message || detail.error_code || '设备搜索失败'
        return
      }
      await sleep(SEARCH_POLL_INTERVAL_MS)
    }
    if (generation === pollGeneration) {
      searchStatus.value = 'failed'
      searchError.value = `搜索超时（${Math.round((SEARCH_POLL_MAX_ATTEMPTS * SEARCH_POLL_INTERVAL_MS) / 1000)} 秒无结果）`
    }
  } catch (e) {
    if (generation !== pollGeneration) return
    searchStatus.value = 'failed'
    searchError.value = e instanceof WeixinApiError ? e.message : '发起搜索失败'
  } finally {
    if (generation === pollGeneration) searching.value = false
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

const candidateColumns = [
  { key: 'title', label: '群名称' },
  { key: 'actions', label: '操作', width: '80px', tdAlign: 'center' as const },
]

// ---------- 创建绑定 ----------
const selectedCandidate = ref<GroupSearchCandidate | null>(null)
const bindingLabel = ref('')
const creating = ref(false)

const canCreateBinding = computed(
  () => !!selectedCandidate.value && bindingLabel.value.trim().length > 0 && !!selectedDeviceId.value,
)

function selectCandidate(candidate: GroupSearchCandidate) {
  selectedCandidate.value = candidate
  bindingLabel.value = String(candidate.title || '')
}

async function handleCreateBinding() {
  if (!canCreateBinding.value || creating.value || !selectedCandidate.value) return
  creating.value = true
  try {
    const res = await createGroupBinding({
      device_id: selectedDeviceId.value,
      label: bindingLabel.value.trim(),
      target_ref: selectedCandidate.value.target_ref,
      search_id: String((searchDetailId.value)),
    })
    toast.success('绑定已创建（待核验），点击「核验」完成验证')
    selectedCandidate.value = null
    bindingLabel.value = ''
    await loadBindings()
    // 创建后立即引导核验
    await handleVerifyById(res.binding_id)
  } catch (e) {
    toast.error(e instanceof WeixinApiError ? e.message : '创建绑定失败')
  } finally {
    creating.value = false
  }
}

// 搜索任务 id（创建时记录，创建绑定需引用该搜索的候选集合）
const searchDetailId = ref('')

// ---------- 既有绑定 / 核验 ----------
const bindings = ref<GroupBindingItem[]>([])
const loadingBindings = ref(false)
const bindingsError = ref('')
const verifyingIds = ref(new Set<string>())

const bindingColumns = [
  { key: 'label', label: '标签', width: '160px' },
  { key: 'device', label: '设备', width: '110px' },
  { key: 'state', label: '状态', width: '90px' },
  { key: 'verified_at', label: '核验时间', width: '140px' },
  { key: 'actions', label: '操作', width: '130px', tdAlign: 'center' as const },
]

async function loadBindings() {
  loadingBindings.value = true
  bindingsError.value = ''
  try {
    const res = await listGroupBindings({ page_size: 100 })
    bindings.value = res.items
  } catch (e) {
    bindingsError.value = e instanceof WeixinApiError ? e.message : '加载群绑定失败'
  } finally {
    loadingBindings.value = false
  }
}

async function handleVerify(binding: GroupBindingItem) {
  await handleVerifyById(binding.id)
}

async function handleVerifyById(bindingId: string) {
  if (verifyingIds.value.has(bindingId)) return
  verifyingIds.value = new Set([...verifyingIds.value, bindingId])
  try {
    const res = await verifyGroupBinding(bindingId)
    if (res.result === 'verified') {
      toast.success('核验通过：唯一精确命中，绑定可用于发送')
    } else {
      toast.error('核验否决：存在同名多个群（候选冲突），请删除后用更精确的群名重建')
    }
    await loadBindings()
  } catch (e) {
    if (e instanceof WeixinApiError && e.code === WX_ERROR_CODES.VERIFY_FAILED) {
      toast.warning(`${e.message}`)
    } else {
      toast.error(e instanceof WeixinApiError ? e.message : '核验失败')
    }
  } finally {
    const next = new Set(verifyingIds.value)
    next.delete(bindingId)
    verifyingIds.value = next
  }
}

function chooseBinding(binding: GroupBindingItem) {
  emit('selected', binding)
  emit('update:modelValue', false)
}

// ---------- 生命周期 ----------
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      loadDevices()
      loadBindings()
    } else {
      // 关闭即终止轮询代际
      pollGeneration++
      searching.value = false
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  pollGeneration++
})
</script>

<template>
  <!-- 自动化编辑器：baseVersion/draft/dirty 状态机 + 409 冲突保留输入 + 校验/发布/试发 -->
  <BaseModal
    :model-value="guard.showModal.value"
    :title="isCreate ? '新增自动化' : `编辑自动化 - ${form.name || ''}`"
    size="xl"
    :close-on-overlay="false"
    @update:model-value="handleModalToggle"
  >
    <div v-if="loading" class="py-10 text-center text-sm text-muted">加载配置中...</div>
    <div v-else-if="loadError" class="py-6">
      <p class="text-sm text-danger-600 mb-3">{{ loadError }}</p>
      <BaseButton size="sm" intent="secondary" @click="open">重试</BaseButton>
    </div>
    <div v-else class="space-y-5">
      <!-- 409 冲突面板：保留用户输入 + 服务器差异 -->
      <div v-if="conflict" class="rounded-lg border border-warning-300 bg-warning-50 p-3 space-y-2">
        <div class="text-sm font-medium text-warning-800">检测到并发修改（版本冲突）</div>
        <div class="text-xs text-warning-800 space-y-1">
          <p>你的编辑基于版本 {{ conflict.localVersion }}，服务器当前版本 {{ conflict.serverVersion }}。你的输入已保留。</p>
          <div class="font-medium">服务器侧当前值：</div>
          <ul class="list-disc list-inside">
            <li v-for="diff in conflict.diffs" :key="diff.field">{{ diff.field }}：{{ diff.server }}</li>
          </ul>
        </div>
        <div class="flex flex-wrap gap-2">
          <BaseButton size="sm" @click="retrySaveOnServerVersion">在新版本上重试保存我的草稿</BaseButton>
          <BaseButton size="sm" intent="secondary" @click="reloadFromServer">丢弃我的修改，改用服务器版本</BaseButton>
          <BaseButton size="sm" intent="ghost" @click="conflict = null">继续本地编辑</BaseButton>
        </div>
      </div>

      <!-- 复审 P1-1：无草稿但存在已发布版本 → 按已发布版本回显（保存创建新草稿） -->
      <div
        v-if="showingActive"
        class="rounded-lg border border-default bg-canvas p-3 text-sm text-default"
      >
        已按已发布版本回显，保存将创建新草稿；试发按已发布内容发送。
      </div>

      <!-- 基本信息 -->
      <section class="space-y-3">
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label class="form-label">任务名称 <span class="form-required">*</span></label>
            <BaseInput
              v-model="form.name"
              placeholder="如：每日早报群推送"
              :state="nameError ? 'error' : 'default'"
              :disabled="saving"
            />
            <p v-if="nameError" class="text-xs text-danger-600 mt-1">{{ nameError }}</p>
          </div>
          <div>
            <label class="form-label">状态 / 版本</label>
            <div class="flex items-center gap-2 h-10">
              <BaseBadge v-if="automationRow" :intent="AUTOMATION_STATUS_BADGE[automationRow.status]">
                {{ AUTOMATION_STATUS_LABELS[automationRow.status] }}
              </BaseBadge>
              <span class="text-xs text-muted">baseVersion {{ baseVersion }}</span>
              <span v-if="guard.isDirty.value" class="text-xs text-warning-600">有未保存修改</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 触发配置 -->
      <section>
        <h3 class="text-sm font-semibold text-default mb-2">触发配置</h3>
        <TriggerEditor :trigger="form.trigger" :disabled="saving" @update:trigger="t => (form.trigger = t)" />
      </section>

      <!-- 目标群绑定 -->
      <section>
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-sm font-semibold text-default">目标群绑定</h3>
          <BaseButton size="sm" intent="secondary" :disabled="saving" @click="showBindingPicker = true">
            {{ form.groupBindingId ? '更换绑定' : '选择绑定' }}
          </BaseButton>
        </div>
        <div v-if="form.groupBindingId" class="rounded-lg border border-default bg-canvas p-3 text-sm">
          <div class="flex items-center gap-2">
            <BaseBadge :intent="GROUP_BINDING_STATE_BADGE[form.groupBindingState ?? 'pending']">
              {{ GROUP_BINDING_STATE_LABELS[form.groupBindingState ?? 'pending'] }}
            </BaseBadge>
            <span class="font-medium text-default">{{ form.groupBindingLabel || '未命名绑定' }}</span>
          </div>
          <div class="text-xs text-muted mt-1 break-all">{{ form.groupBindingId }}</div>
          <p v-if="form.groupBindingState && form.groupBindingState !== 'complete'" class="text-xs text-warning-600 mt-1">
            仅「已核验（complete）」绑定可通过发布校验，请先在绑定管理中完成核验
          </p>
        </div>
        <div v-else class="empty-state">
          <div class="empty-state-icon">#</div>
          <p class="text-sm text-muted">尚未选择目标群，点击右上角选择已核验的群绑定</p>
        </div>
      </section>

      <!-- 内容块 -->
      <section>
        <h3 class="text-sm font-semibold text-default mb-2">内容包（按顺序发送）</h3>
        <!-- 无草稿可回显（draft_blocks 为 null 且无会话缓存）：明示覆盖语义，不静默覆盖 -->
        <div v-if="blocksUnknown && !blocksReentered" class="rounded-lg border border-warning-300 bg-warning-50 p-3 space-y-2">
          <p class="text-sm text-warning-800">
            当前任务无草稿内容块可回显（任务已发布或暂无草稿）。修改内容需重新录入全部内容块，保存后将作为新草稿。
          </p>
          <BaseButton size="sm" intent="secondary" @click="blocksReentered = true">重新录入全部内容块</BaseButton>
        </div>
        <template v-else>
          <ContentBlockEditor
            :blocks="form.blocks"
            :disabled="saving"
            :force-overflow="form.blocks.length > MAX_BLOCKS"
            @update:blocks="b => (form.blocks = b)"
          />
          <p v-if="blocksUnknown && blocksReentered" class="text-xs text-warning-600 mt-1">
            注意：本次保存将以上方内容整体覆盖草稿原有内容块。
          </p>
        </template>
      </section>

      <!-- 预览 -->
      <section>
        <h3 class="text-sm font-semibold text-default mb-2">内容预览</h3>
        <MessagePreview :blocks="previewBlocks" />
      </section>

      <!-- 校验结果 -->
      <section v-if="validateResult">
        <h3 class="text-sm font-semibold text-default mb-2">最近校验结果</h3>
        <div class="rounded-lg border p-3 space-y-1 text-sm" :class="validateResult.ok ? 'border-success-300 bg-success-50' : 'border-danger-300 bg-danger-50'">
          <div :class="validateResult.ok ? 'text-success-700' : 'text-danger-700'">
            {{ validateResult.ok ? '校验通过' : '校验未通过' }}
          </div>
          <div v-for="(e, i) in validateResult.errors" :key="`e${i}`" class="text-danger-700 text-xs">· [{{ e.field }}] {{ e.message }}</div>
          <div v-for="(w, i) in validateResult.warnings" :key="`w${i}`" class="text-warning-700 text-xs">· [{{ w.field }}] {{ w.message }}</div>
          <div v-if="validateResult.next_fires.length" class="text-xs text-muted">
            未来触发：{{ validateResult.next_fires.slice(0, 3).map(formatDateTime).join('；') }}
          </div>
        </div>
      </section>
    </div>

    <!-- 试发 -->
    <template #footer>
      <div class="flex items-center gap-2 flex-wrap justify-end">
        <BaseButton intent="secondary" :disabled="saving || testing" @click="handleModalToggle(false)">关闭</BaseButton>
        <BaseButton intent="secondary" :disabled="loading || validating || !automationId" @click="handleValidate">
          {{ validating ? '校验中...' : '校验' }}
        </BaseButton>
        <BaseButton
          v-if="automationRow?.status === 'active'"
          intent="secondary"
          :disabled="!canTestSend || testing"
          title="只试发选中的一条内容到指定群，独立审计与配额"
          @click="openTestSend"
        >试发...</BaseButton>
        <BaseButton
          intent="primary"
          :disabled="loading || !canSave || saving"
          @click="handleSave(false)"
        >{{ saving ? '保存中...' : '保存草稿' }}</BaseButton>
        <BaseButton
          intent="primary"
          :disabled="loading || !canPublish || publishing"
          :title="publishTitle"
          @click="handlePublish"
        >{{ publishing ? '发布中...' : '发布' }}</BaseButton>
      </div>
    </template>
  </BaseModal>

  <!-- 绑定选择 -->
  <GroupBindingPicker v-model="showBindingPicker" mode="select" @selected="applyBinding" />

  <!-- 试发确认（V-P1-3：固定按已发布内容发送，本地草稿不一致时只显示位置占位） -->
  <BaseModal v-model="showTestSend" title="确认试发（按已发布内容发送）" size="md">
    <div class="space-y-3">
      <p class="text-sm text-default">将向以下目标真实发送已发布内容中的 1 条（独立审计与配额）：</p>
      <div class="rounded-lg border border-default bg-canvas p-3 space-y-2 text-sm">
        <div>目标群：<span class="font-medium">{{ form.groupBindingLabel || form.groupBindingId }}</span></div>
        <div>
          <label class="form-label">条目序号（从 1 开始，按已发布顺序）</label>
          <BaseSelect v-if="blocksEditable && form.blocks.length" v-model="testSendEntryNo" :disabled="testing">
            <option v-for="(block, index) in form.blocks" :key="index" :value="String(index + 1)">
              {{ testSendOptionLabel(block, index) }}
            </option>
          </BaseSelect>
          <input
            v-else
            v-model="testSendEntryNo"
            type="number"
            min="1"
            :disabled="testing"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          />
        </div>
        <div v-if="publishedConsistent" data-testid="testsend-preview" class="break-all">{{ testSendPreviewText }}</div>
        <div v-else data-testid="testsend-preview" class="text-warning-700">
          第 {{ testSendEntryNo || '?' }} 条 · 内容以已发布版本为准（不展示本地草稿正文）
        </div>
      </div>
      <p v-if="!publishedConsistent" class="text-xs text-warning-600">
        本地草稿与已发布内容可能不一致：请先发布草稿，或核对已发布版本后再试发。
      </p>
      <p class="text-xs text-muted">提交中按钮将禁用；服务端以幂等键保证同一动作只执行一次。</p>
    </div>
    <template #footer>
      <BaseButton intent="secondary" :disabled="testing" @click="showTestSend = false">取消</BaseButton>
      <BaseButton intent="danger" :disabled="testing || !testSendEntryValid" @click="handleTestSend">
        {{ testing ? '提交中...' : '确认试发' }}
      </BaseButton>
    </template>
  </BaseModal>

  <!-- 发布确认（V-P2-2） -->
  <BaseModal v-model="showPublishConfirm" title="确认发布" size="sm">
    <div class="space-y-2">
      <p class="text-sm text-default">发布后草稿冻结不可变，任务将按触发配置执行。</p>
      <p class="text-xs text-muted">发布即对外生效（真实发送）；如需最后核对，可先关闭本框使用「校验」。</p>
    </div>
    <template #footer>
      <BaseButton intent="secondary" :disabled="publishing" @click="showPublishConfirm = false">取消</BaseButton>
      <BaseButton :disabled="publishing" @click="doPublish">{{ publishing ? '发布中...' : '确认发布' }}</BaseButton>
    </template>
  </BaseModal>

  <!-- 脏检测确认（保存 / 不保存 / 取消） -->
  <div
    v-if="guard.showConfirm.value"
    class="fixed inset-0 z-[70] flex items-center justify-center bg-black/40"
    @click.self="guard.confirmCancel()"
  >
    <div class="bg-surface rounded-lg shadow-xl w-full max-w-sm p-5">
      <div class="text-base font-semibold text-default mb-2">未保存的修改</div>
      <div class="text-sm text-muted mb-5">{{ guard.confirmMessage }}</div>
      <div class="flex justify-end gap-2">
        <BaseButton intent="secondary" size="sm" @click="guard.confirmCancel()">取消</BaseButton>
        <BaseButton intent="danger-ghost" size="sm" @click="handleConfirmDiscard">不保存关闭</BaseButton>
        <BaseButton size="sm" :disabled="saving" @click="guard.confirmSave()">保存并关闭</BaseButton>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * AutomationEditor：新增/编辑自动化草稿（R54②/R55）。
 * - 状态机：open 时快照（baseVersion + 表单初始值）→ dirty 比较 → 保存 CAS；
 * - 409 CONFLICT：保留用户输入，拉取服务器详情显示差异；提供「新版本重试保存 /
 *   放弃修改 / 继续编辑」三选；
 * - 关闭守卫：useModalCloseGuard（脏检测确认 保存/不保存/取消）；
 * - 明文不落浏览器存储：内容块仅会话内内存缓存（draftBlocksCache）；
 * - 内容块回显：P3-A1 契约 detail.draft_blocks（draft revision 有序块，无草稿 null）
 *   优先；未上线/无草稿时回退会话缓存，均无则 blocksUnknown（明示重录覆盖语义）。
 * - 复审 P1-1：无草稿但存在已发布版本时以 active_blocks/active_trigger/
 *   active_group_binding_id 回显（标注「已按已发布版本回显」），dirty 基线按回显值
 *   快照，试发按钮满足条件即启用（试发固定取已发布 revision 内容）。
 */
import { computed, ref, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import { useModalCloseGuard } from '@/composables/useModalCloseGuard'
import {
  MAX_BLOCKS,
  WeixinApiError,
  createAutomation,
  getAutomation,
  isVersionConflict,
  listGroupBindings,
  publishAutomation,
  testSend,
  updateDraft,
  validateAutomation,
  type AutomationDetail,
  type AutomationRow,
  type ContentBlockSpec,
  type DraftBlockRow,
  type GroupBindingItem,
  type GroupBindingState,
  type TriggerConfig,
  type ValidateResult,
} from '@/api/weixinMarketing'
import TriggerEditor from './TriggerEditor.vue'
import ContentBlockEditor from './ContentBlockEditor.vue'
import MessagePreview from './MessagePreview.vue'
import GroupBindingPicker from './GroupBindingPicker.vue'
import {
  AUTOMATION_STATUS_BADGE,
  AUTOMATION_STATUS_LABELS,
  GROUP_BINDING_STATE_BADGE,
  GROUP_BINDING_STATE_LABELS,
  blockSummary,
  blocksValid,
  formatDateTime,
  triggerSummary,
} from './weixinDisplay'

/** 会话内草稿内容块缓存（automationId → blocks）。不写 localStorage（R55 明文禁令）。 */
const draftBlocksCache = new Map<string, ContentBlockSpec[]>()

const props = defineProps<{
  modelValue: boolean
  /** null = 新增；否则编辑该自动化 */
  automationId: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 创建/保存/发布成功后通知列表刷新 */
  'saved': []
  /** 新建成功后携带 automationId（父组件应把编辑目标切到该任务以便续编） */
  'created': [automationId: string]
}>()

const toast = useToast()

// ============== 表单状态 ==============

interface EditorForm {
  name: string
  trigger: TriggerConfig
  blocks: ContentBlockSpec[]
  groupBindingId: string
  groupBindingLabel: string
  groupBindingState: GroupBindingState | null
}

function defaultTrigger(): TriggerConfig {
  return {
    type: 'once',
    run_at: new Date(Date.now() + 3600_000).toISOString(),
    timezone: 'Asia/Shanghai',
    grace_seconds: 300,
  }
}

function emptyForm(): EditorForm {
  return {
    name: '',
    trigger: defaultTrigger(),
    blocks: [],
    groupBindingId: '',
    groupBindingLabel: '',
    groupBindingState: null,
  }
}

const form = ref<EditorForm>(emptyForm())
const baseVersion = ref(1)
const automationRow = ref<AutomationRow | null>(null)
const blocksUnknown = ref(false)
const blocksReentered = ref(false)
/** 复审 P1-1：当前表单按已发布版本（active_*）回显——无待编辑草稿 */
const showingActive = ref(false)

const loading = ref(false)
const loadError = ref('')

const saving = ref(false)
const validating = ref(false)
const publishing = ref(false)
const testing = ref(false)

const validateResult = ref<ValidateResult | null>(null)

const isCreate = computed(() => !props.automationId)

const conflict = ref<{
  localVersion: number
  serverVersion: number
  diffs: { field: string; server: string }[]
} | null>(null)

// ============== 关闭守卫（脏检测） ==============

const guard = useModalCloseGuard(
  form,
  () => JSON.parse(JSON.stringify(form.value)) as EditorForm,
  async () => {
    await handleSave(true)
  },
)

// ============== 加载 / 打开 ==============

watch(
  () => props.modelValue,
  (open) => {
    if (open) openEditor()
  },
  { immediate: true },
)

function openEditor() {
  if (props.automationId) {
    open()
  } else {
    form.value = emptyForm()
    automationRow.value = null
    baseVersion.value = 1
    blocksUnknown.value = false
    blocksReentered.value = false
    showingActive.value = false
    loadError.value = ''
    conflict.value = null
    validateResult.value = null
    guard.openModal()
  }
}

async function open() {
  const automationId = props.automationId
  if (!automationId) return
  loading.value = true
  loadError.value = ''
  conflict.value = null
  validateResult.value = null
  blocksReentered.value = false
  guard.openModal() // 先展示弹框，加载态在框内呈现
  try {
    const detail = await getAutomation(automationId)
    applyDetail(detail, { keepForm: false })
    // 复审 P1-1：active 回显需先补全绑定 label/state 再做脏检测快照，
    // 否则异步回填会被误判为「有未保存修改」
    await resolveBindingInfo()
    guard.openModal() // 表单回填后重置脏检测快照
  } catch (e) {
    loadError.value = e instanceof WeixinApiError ? e.message : '加载自动化配置失败'
  } finally {
    loading.value = false
  }
}

/** 详情 → 表单（keepForm=true 用于创建成功/保存成功/409 后只刷新 baseVersion/服务器行，不动用户输入） */
function applyDetail(detail: AutomationDetail, opts: { keepForm: boolean }) {
  const row = detail.automation
  automationRow.value = row
  baseVersion.value = row.version
  const draftRevision = detail.revisions.find(r => r.status === 'draft')
  if (!opts.keepForm) {
    const id = String(row.id)
    // P3-A1 契约：draft_blocks = draft revision 有序块（无草稿空列表/null）。
    // 有值优先（服务器权威），否则退会话内缓存（创建/保存后写入），都没有才标记未知。
    const draftRows = detail.draft_blocks ?? null
    const fromDraft = draftRows && draftRows.length ? normalizeDraftBlocks(draftRows) : null
    const hasDraft = fromDraft != null || !!row.draft_revision_id
    // 复审 P1-1：无草稿但存在已发布版本（active_revision_id + active_blocks 有值）
    // → 表单整体按已发布版本回显：blocks/trigger/群绑定均取 active_*，触发配置
    // 不回退默认值；dirty 基线即回显值（open() 在回填后重做快照）。
    const activeRows = detail.active_blocks ?? null
    const fromActive = activeRows && activeRows.length ? normalizeDraftBlocks(activeRows) : null
    if (!hasDraft && row.active_revision_id && fromActive != null) {
      form.value = {
        name: row.name,
        trigger: detail.active_trigger || defaultTrigger(),
        blocks: [...fromActive],
        groupBindingId: String(detail.active_group_binding_id || ''),
        groupBindingLabel: '',
        groupBindingState: null,
      }
      blocksUnknown.value = false
      showingActive.value = true
      return
    }
    showingActive.value = false
    const known = fromDraft ?? draftBlocksCache.get(id) ?? null
    form.value = {
      name: row.name,
      trigger: detail.draft_trigger || defaultTrigger(),
      blocks: known ? [...known] : [],
      groupBindingId: String(draftRevision?.group_binding_id || ''),
      groupBindingLabel: '',
      groupBindingState: null,
    }
    blocksUnknown.value = known === null
  }
}

/**
 * 复审 P1-1：active 回显时补全原群绑定的 label/state（绑定选择器回显原群）。
 * 列表分页下可能查不到（page_size 100 内），查不到/失败均降级为仅展示绑定 id，
 * 不影响回显与后端发布/试发校验。仅在表单按 active 回显且 label 为空时发起。
 */
async function resolveBindingInfo() {
  const bindingId = form.value.groupBindingId
  if (!showingActive.value || !bindingId || form.value.groupBindingLabel) return
  try {
    const res = await listGroupBindings({ page: 1, page_size: 100 })
    const hit = (res.items || []).find(b => String(b.id) === bindingId)
    if (hit && form.value.groupBindingId === bindingId) {
      form.value.groupBindingLabel = hit.label
      form.value.groupBindingState = hit.state
    }
  } catch {
    // 拉取失败不影响回显主体：保留绑定 id 展示，状态留空由后端校验把关
  }
}

/** DraftBlockRow（存储行）→ ContentBlockSpec（契约快照形态），按 position 排序 */
function normalizeDraftBlocks(rows: DraftBlockRow[]): ContentBlockSpec[] {
  return [...rows]
    .sort((a, b) => a.position - b.position)
    .map((row): ContentBlockSpec => {
      if (row.kind === 'link') return { type: 'link', url: row.url ?? '' }
      if (row.kind === 'image') return { type: 'image', asset_id: row.asset_id ?? '' }
      return { type: 'text', text_content: row.text_content ?? '' }
    })
}

// ============== 校验（前端门槛） ==============

const nameError = computed(() => {
  const name = form.value.name.trim()
  if (!name) return '任务名称不能为空'
  if (name.length > 128) return '任务名称最长 128 字符'
  return ''
})

const canSave = computed(() => {
  if (nameError.value) return false
  if (!form.value.groupBindingId) return false
  if (!blocksEditable.value) return false
  return blocksValid(form.value.blocks)
})

/** 编辑已有草稿但内容块未知且未解锁重录时：不允许保存（防静默覆盖） */
const blocksEditable = computed(
  () => isCreate.value || !blocksUnknown.value || blocksReentered.value,
)

const canPublish = computed(() => {
  if (!automationRow.value) return false
  // V-P2-1：paused 下后端拒绝发布（"请先 resume"），前端直接禁用，避免 409 落入版本冲突面板
  if (!['draft', 'active'].includes(automationRow.value.status)) return false
  if (guard.isDirty.value) return false
  return !!automationRow.value.draft_revision_id
})

const publishTitle = computed(() => {
  if (automationRow.value?.status === 'paused') return '任务已暂停：请先恢复运行后再发布（后端同样拒绝暂停态发布）'
  if (guard.isDirty.value) return '有未保存修改：请先保存草稿再发布'
  if (automationRow.value && !automationRow.value.draft_revision_id) return '当前无待发布草稿（先编辑产生新草稿）'
  return '发布当前草稿（发布后不可变，任务转为运行中）'
})

const previewBlocks = computed(() => (blocksEditable.value ? form.value.blocks : []))

/**
 * V-P1-3：试发固定取 active（已发布）revision 同位置块。本地草稿与已发布一致才可信：
 * 无未保存修改 && 无待发布草稿（draft_revision_id 为空即 draft==active 的已发布态）&& 块已知。
 */
const publishedConsistent = computed(
  () =>
    !guard.isDirty.value &&
    !automationRow.value?.draft_revision_id &&
    !blocksUnknown.value &&
    !blocksReentered.value,
)

const canTestSend = computed(() => {
  if (automationRow.value?.status !== 'active') return false
  if (!form.value.groupBindingId) return false
  if (form.value.groupBindingState !== null && form.value.groupBindingState !== 'complete') return false
  // 块未知（发布后未重录）时仍可试发：条目序号手填，正文以已发布版本为准
  if (blocksEditable.value) return form.value.blocks.length > 0
  return true
})

// ============== 保存草稿（CAS + 409 处理） ==============

async function handleSave(fromGuard: boolean): Promise<void> {
  if (!canSave.value || saving.value) {
    if (fromGuard) {
      throw new Error(nameError.value || '请先补全表单（触发/内容块/目标群）')
    }
    if (!canSave.value) {
      toast.warning(nameError.value || '请先补全表单（触发/内容块/目标群）')
    }
    return
  }
  saving.value = true
  try {
    if (!props.automationId) {
      const detail = await createAutomation({
        name: form.value.name.trim(),
        trigger: form.value.trigger,
        blocks: form.value.blocks,
        group_binding_id: form.value.groupBindingId,
        policy: {},
      })
      // V-P1-1：创建成功先以「当前表单块」写缓存，再以 keepForm 应用详情——
      // 若先 applyDetail(keepForm:false)，会重读尚为空的缓存把表单块清空并污染缓存
      const createdId = String(detail.automation.id)
      draftBlocksCache.set(createdId, [...form.value.blocks])
      applyDetail(detail, { keepForm: true })
      blocksUnknown.value = false
      emit('created', createdId)
      guard.openModal() // 重置快照（保存后不再 dirty）
      toast.success('草稿已创建')
      emit('saved')
      return
    }
    const input: Parameters<typeof updateDraft>[1] = {
      expected_version: baseVersion.value,
      name: form.value.name.trim(),
      trigger: form.value.trigger,
      // 内容块未知且未解锁重录时不提交 blocks（防静默覆盖已有草稿块）
      blocks: blocksEditable.value ? form.value.blocks : undefined,
      group_binding_id: form.value.groupBindingId,
    }
    const detail = await updateDraft(props.automationId, input)
    applyDetail(detail, { keepForm: true })
    // 复审 P1-1：保存成功即产生新草稿，「已按已发布版本回显」标注随之撤下
    showingActive.value = false
    draftBlocksCache.set(props.automationId, [...form.value.blocks])
    blocksUnknown.value = false
    guard.openModal() // 重置脏检测快照
    conflict.value = null
    toast.success('草稿已保存')
    emit('saved')
  } catch (e) {
    if (isVersionConflict(e)) {
      // 冲突面板已呈现差异；守卫路径继续抛出以阻止关闭
      await handleVersionConflict(e)
      if (fromGuard) throw e
    } else if (fromGuard) {
      throw e
    } else {
      toast.error(e instanceof WeixinApiError ? e.message : '保存失败')
    }
  } finally {
    saving.value = false
  }
}

// ============== 409 冲突 ==============

async function handleVersionConflict(err: WeixinApiError) {
  if (!props.automationId) return
  try {
    const server = await getAutomation(props.automationId)
    const serverDraft = server.revisions.find(r => r.status === 'draft')
    const diffs: { field: string; server: string }[] = [
      { field: '版本', server: String(server.automation.version) },
      { field: '状态', server: AUTOMATION_STATUS_LABELS[server.automation.status] },
      { field: '名称', server: server.automation.name },
      { field: '触发', server: triggerSummary(server.draft_trigger) },
      { field: '目标群', server: String(serverDraft?.group_binding_id || '未设置') },
    ]
    conflict.value = {
      localVersion: baseVersion.value,
      serverVersion: server.automation.version,
      diffs,
    }
    // 只更新服务器行信息与 baseVersion 来源，不覆盖用户输入：
    // baseVersion 保持本地值，等用户三选一
    automationRow.value = server.automation
    toast.warning('版本冲突：检测到其他人已保存修改，你的输入已保留，请在上方提示中处理')
  } catch {
    conflict.value = {
      localVersion: baseVersion.value,
      serverVersion: baseVersion.value + 1,
      diffs: [{ field: '错误', server: err.message }],
    }
  }
}

function retrySaveOnServerVersion() {
  if (!conflict.value) return
  baseVersion.value = conflict.value.serverVersion
  conflict.value = null
  void handleSave(false)
}

async function reloadFromServer() {
  if (!props.automationId) return
  conflict.value = null
  await open()
}

// ============== 校验 / 发布 ==============

async function handleValidate() {
  if (!props.automationId || validating.value) return
  if (guard.isDirty.value) {
    toast.info('有未保存修改，校验针对已保存的草稿进行；请先保存')
  }
  validating.value = true
  try {
    validateResult.value = await validateAutomation(props.automationId)
  } catch (e) {
    toast.error(e instanceof WeixinApiError ? e.message : '校验失败')
  } finally {
    validating.value = false
  }
}

// ============== 发布（V-P2-2：BaseModal 确认替代 window.confirm） ==============

const showPublishConfirm = ref(false)

function handlePublish() {
  if (!canPublish.value || !props.automationId || publishing.value) return
  // V-P2-1 双保险：paused 在 canPublish 已禁用，此处再拦一次（防状态竞态）
  if (automationRow.value?.status === 'paused') {
    toast.warning('任务已暂停，请先恢复运行后再发布')
    return
  }
  showPublishConfirm.value = true
}

async function doPublish() {
  if (!canPublish.value || !props.automationId || publishing.value) return
  publishing.value = true
  try {
    await publishAutomation(props.automationId, {
      expected_version: baseVersion.value,
      revision_id: automationRow.value?.draft_revision_id || null,
      authorization_source: 'web',
    })
    toast.success('已发布，任务开始按触发配置运行')
    showPublishConfirm.value = false
    await open()
    emit('saved')
  } catch (e) {
    if (isVersionConflict(e)) {
      // 仅真版本 CAS 落冲突面板；paused 拒绝已被禁用/前置拦截（V-P2-1）
      await handleVersionConflict(e)
    } else {
      toast.error(e instanceof WeixinApiError ? e.message : '发布失败')
    }
  } finally {
    publishing.value = false
  }
}

// ============== 试发 ==============

// ============== 试发（V-P1-3：按已发布内容，条目序号 1 起展示） ==============

const showTestSend = ref(false)
/** 条目序号（1 起的字符串，BaseSelect/数字输入共用） */
const testSendEntryNo = ref('1')
const showBindingPicker = ref(false)

const testSendEntryValid = computed(() => {
  const n = Number(testSendEntryNo.value)
  return Number.isInteger(n) && n >= 1 && (!blocksEditable.value || n <= form.value.blocks.length)
})

function openTestSend() {
  if (!canTestSend.value) return
  testSendEntryNo.value = '1'
  showTestSend.value = true
}

/** 选项标签：本地与已发布完全一致才展示正文摘要，否则仅位置（V-P1-3） */
function testSendOptionLabel(block: ContentBlockSpec, index: number): string {
  return publishedConsistent.value ? `第 ${index + 1} 条 · ${blockSummary(block, 24)}` : `第 ${index + 1} 条`
}

const testSendPreviewText = computed(() => {
  const index = Number(testSendEntryNo.value) - 1
  const block = form.value.blocks[index]
  return block ? blockSummary(block) : '（无对应条目）'
})

async function handleTestSend() {
  if (!props.automationId || testing.value || !testSendEntryValid.value) return
  testing.value = true
  try {
    const res = await testSend(props.automationId, {
      group_binding_id: form.value.groupBindingId,
      // 契约 block_position 与后端内容块编号一致（1 起，workbench 按 position 精确匹配）
      block_position: Number(testSendEntryNo.value),
    })
    toast.success(`试发已提交（运行 ${String(res.run_id).slice(0, 8)}…），可在运行记录中查看结果`)
    showTestSend.value = false
  } catch (e) {
    toast.error(e instanceof WeixinApiError ? e.message : '试发失败')
  } finally {
    testing.value = false
  }
}

function applyBinding(binding: GroupBindingItem) {
  form.value.groupBindingId = binding.id
  form.value.groupBindingLabel = binding.label
  form.value.groupBindingState = binding.state
}

// ============== 关闭守卫（脏检测三选：保存/不保存/取消） ==============

function requestClose() {
  guard.requestClose(() => emit('update:modelValue', false))
}

/** BaseModal 只会 emit false（X 按钮）；统一走脏检测 */
function handleModalToggle(value: boolean) {
  if (value) {
    guard.showModal.value = true
    return
  }
  requestClose()
}

function handleConfirmDiscard() {
  guard.confirmDiscard()
  emit('update:modelValue', false)
}
</script>

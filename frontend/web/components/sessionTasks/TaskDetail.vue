<template>
  <div class="page-container p-4 overflow-y-auto">
    <div class="page-toolbar flex-wrap gap-2">
      <div class="flex flex-wrap gap-2"><BaseButton intent="secondary" @click="router.push({ name: 'tenant-weixin-session-tasks' })">返回会话任务</BaseButton><BaseBadge intent="warning">开发中</BaseBadge><BaseBadge v-if="task" :intent="task.status === 'completed' ? 'success' : 'neutral'">{{ phaseLabel(task) }}</BaseBadge></div>
      <BaseButton intent="ghost" :disabled="busy || editing || loading" @click="load">刷新</BaseButton>
    </div>
    <p v-if="error" role="alert" class="text-danger-600 p-3">{{ error }}</p>
    <p v-if="notice" role="status" class="text-success-600 p-3">{{ notice }}</p>
    <p v-if="loading && !task" class="p-6 text-muted">加载中…</p>
    <template v-if="task">
      <p v-if="!capabilities.publish_enabled" class="p-3 mb-4 bg-warning-50 text-warning-800 rounded">{{ capabilities.reason || '真实执行门禁尚未开启，仅可配置草稿和查看。' }}</p>
      <div class="flex flex-wrap gap-2 mb-4">
        <BaseButton v-if="canEdit" :disabled="busy" intent="secondary" @click="startEdit">编辑草稿</BaseButton>
        <BaseButton v-if="canEdit" :disabled="busy || editing || !capabilities.publish_enabled || !task.draft_spec" @click="reviewing = true">查看发布确认</BaseButton>
        <BaseButton v-if="task.status === 'active'" :disabled="busy || !capabilities.publish_enabled" intent="secondary" @click="controlAction = 'pause'">暂停</BaseButton>
        <BaseButton v-if="['paused', 'human_required', 'blocked'].includes(task.status)" :disabled="busy || editing || !capabilities.publish_enabled" intent="secondary" @click="openResume">恢复</BaseButton>
        <BaseButton v-if="['active', 'paused'].includes(task.status)" :disabled="busy || !capabilities.publish_enabled" intent="secondary" @click="controlAction = 'handoff'">人工接管</BaseButton>
        <BaseButton v-if="['active', 'paused', 'human_required', 'blocked'].includes(task.status)" :disabled="busy || !capabilities.publish_enabled" intent="danger-ghost" @click="controlAction = 'stop'">停止</BaseButton>
      </div>
      <BaseCard title="对象与执行状态" class="mb-4">
        <dl class="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm break-words">
          <div><dt class="text-muted">会话</dt><dd>{{ task.binding_label || task.conversation_binding_id }}</dd></div>
          <div><dt class="text-muted">设备</dt><dd>{{ task.device_id }} · {{ task.device_online ? '在线' : '离线 / 未知' }}</dd></div>
          <div><dt class="text-muted">账号绑定</dt><dd>{{ task.account_binding_id }}</dd></div>
          <div><dt class="text-muted">最后观察</dt><dd>{{ dateLabel(task.last_observed_at) }}</dd></div>
          <div><dt class="text-muted">进度</dt><dd>发送 {{ task.replies_count ?? 0 }}/{{ task.spec?.limits.max_replies ?? '—' }}（含开场白） · 有效轮数 {{ task.rounds_count ?? 0 }} · 决策 {{ task.decisions_count ?? 0 }}/{{ task.spec?.limits.max_decisions ?? '—' }}</dd></div>
          <div><dt class="text-muted">积分</dt><dd>已结算 {{ task.cost?.settled ?? 0 }} · 未决预留 {{ task.cost?.reserved ?? 0 }}</dd></div>
          <div><dt class="text-muted">控制版本 / 消息水位</dt><dd>{{ task.version }} / {{ task.input_version ?? '未知' }}</dd></div>
          <div v-if="task.blocked_reason || task.completion_reason"><dt class="text-muted">状态原因</dt><dd>{{ task.blocked_reason || task.completion_reason }}</dd></div>
        </dl>
      </BaseCard>
      <BaseCard v-if="displaySpec" :title="task.draft_spec ? '待确认草稿（尚未执行）' : '已发布授权范围'" class="mb-4">
        <SpecSummary :spec="displaySpec" />
      </BaseCard>
      <BaseCard v-if="editing" title="编辑草稿" class="mb-4">
        <form @submit.prevent="save"><div class="flex gap-2 mb-4"><BaseButton type="submit" :disabled="busy">保存草稿</BaseButton><BaseButton type="button" intent="secondary" :disabled="busy" @click="cancelEdit">放弃编辑</BaseButton></div><TaskSpecForm ref="editor" :disabled="busy" /></form>
      </BaseCard>
      <BaseCard title="消息批次、决策证据与发送账本">
        <div class="flex gap-2 mb-3"><BaseButton intent="secondary" :disabled="loading || busy || historyPage === 0" @click="changeHistory(-1)">上一页记录</BaseButton><span class="text-sm self-center">第 {{ historyPage + 1 }} 页 · 每类最多 100 条</span><BaseButton intent="secondary" :disabled="loading || busy || !hasMoreHistory" @click="changeHistory(1)">下一页记录</BaseButton></div>
        <p class="text-sm text-muted mb-3">纯等待不重复通知。发送结果不明需核对证据，恢复不会补发历史消息。截图保留在端侧受控存储。</p>
        <div v-for="section in timelineSections" :key="section.key" class="mb-5 overflow-x-auto">
          <h3 class="font-medium text-sm mb-2">{{ section.label }}</h3>
          <BaseTable :columns="section.columns" :data="timeline[section.key] || []" row-key="id">
            <template #delivery_state="{ row }">{{ row.delivery_phase === 'submitted' ? '已执行发送（未核验送达）' : row.delivery_state }}</template>
            <template #created_at="{ row }">{{ dateLabel(row.created_at) }}</template>
            <template #evidence="{ row }"><pre class="whitespace-pre-wrap break-all text-xs max-w-lg">{{ evidenceText(row) }}</pre></template>
            <template #run_id="{ row }"><RouterLink v-if="row.run_id" class="text-primary-600 underline" :to="{ name: 'tenant-weixin-marketing-run-detail', params: { runId: row.run_id } }">{{ row.run_id }}</RouterLink><span v-else>—</span></template>
          </BaseTable>
          <p v-if="!timeline[section.key]?.length" class="text-sm text-muted p-3">暂无记录</p>
        </div>
      </BaseCard>
    </template>
    <BaseModal accessible :model-value="reviewing" title="确认授权并发布" size="lg" :close-on-overlay="false" @update:model-value="value => { if (!busy) reviewing = value }">
      <template v-if="task?.draft_spec"><p class="mb-3 text-sm">对象：{{ task.binding_label || task.conversation_binding_id }}<br />设备：{{ task.device_id }}<br />账号：{{ task.account_binding_id }}<br />版本：{{ task.version }}</p><SpecSummary :spec="task.draft_spec" /><p class="text-sm text-muted mt-4">点击后签发一次性确认并发布。任务将在此范围内自行等待和逐条回复，无需保持聊天窗口打开。</p></template>
      <template #footer><BaseButton intent="secondary" :disabled="busy" @click="reviewing = false">取消</BaseButton><BaseButton :disabled="busy || !capabilities.publish_enabled" @click="publish">{{ busy ? '发布中…' : '确认范围并发布' }}</BaseButton></template>
    </BaseModal>
    <BaseModal accessible :model-value="!!controlAction" :title="actionLabels[controlAction] || '任务控制'" size="md" :close-on-overlay="false" @update:model-value="value => { if (!value && !busy) controlAction = '' }">
      <p class="text-sm">{{ controlAction === 'stop' ? '停止后不能恢复；已发生的发送事实和费用仍会同步。' : controlAction === 'handoff' ? '交由人工处理，禁止新的自动发送。' : controlAction === 'pause' ? '暂停新的观察决策及发送许可，已有结果仍会同步。' : '请明确选择恢复水位，旧消息不会静默补发。' }}</p>
      <label v-if="controlAction === 'resume'" class="block mt-4 text-sm text-muted">恢复水位 *<BaseSelect v-model="resumeMode"><option value="">请选择</option><option value="fresh_baseline">从新的观察基线开始，跳过现有消息（水位 {{ task?.input_version ?? '未知' }}）</option></BaseSelect></label>
      <template #footer><BaseButton intent="secondary" :disabled="busy" @click="controlAction = ''">取消</BaseButton><BaseButton :disabled="busy || (controlAction === 'resume' && (!resumeMode || task?.input_version === undefined))" @click="control">确认{{ actionLabels[controlAction] }}</BaseButton></template>
    </BaseModal>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import TaskSpecForm from './TaskSpecForm.vue'
import SpecSummary from './SpecSummary.vue'
import { sessionTasksApi, SessionTaskApiError, type SessionTask, type Timeline } from '@/api/sessionTasks'
import { phaseLabel, dateLabel, errorMessage } from './presentation'
const route = useRoute(), router = useRouter(), task = ref<SessionTask>(), timeline = ref<Timeline>({ batches: [], decisions: [], executions: [] })
const capabilities = ref({ publish_enabled: false, reason: '' }), loading = ref(false), busy = ref(false), error = ref(''), notice = ref(''), editing = ref(false), reviewing = ref(false), controlAction = ref(''), resumeMode = ref(''), editor = ref<InstanceType<typeof TaskSpecForm>>()
const historyPage = ref(0), hasMoreHistory = computed(() => Object.values(timeline.value).some(value => Array.isArray(value) && value.length >= 100))
const canEdit = computed(() => !!task.value && ['draft', 'paused'].includes(task.value.status)), displaySpec = computed(() => task.value?.draft_spec || task.value?.spec)
const actionLabels: Record<string, string> = { pause: '暂停', resume: '恢复', stop: '停止', handoff: '人工接管' }
let alive = true, generation = 0, controller: AbortController | undefined, mutationController: AbortController | undefined, timer: ReturnType<typeof setTimeout> | undefined
let publishAttempt: { id: string; version: number; confirmation: string; key: string } | undefined
const timelineSections: { key: keyof Timeline; label: string; columns: { key: string; label: string }[] }[] = [
  { key: 'batches', label: '消息批次', columns: [{ key: 'batch_id', label: '批次' }, { key: 'input_version', label: '消息水位' }, { key: 'status', label: '状态' }, { key: 'created_at', label: '时间' }] },
  { key: 'messages', label: '观察消息', columns: [{ key: 'message_id', label: '消息' }, { key: 'sender', label: '发送方' }, { key: 'text', label: '正文' }, { key: 'evidence', label: '证据' }] },
  { key: 'decisions', label: '决策与完成证据', columns: [{ key: 'decision_kind', label: '类型' }, { key: 'status', label: '状态' }, { key: 'text', label: '决策正文' }, { key: 'evidence', label: '证据' }, { key: 'created_at', label: '时间' }] },
  { key: 'executions', label: '逐条发送账本', columns: [{ key: 'decision_id', label: '决策' }, { key: 'delivery_state', label: '发送操作状态' }, { key: 'invocation_state', label: '执行状态' }, { key: 'run_id', label: '运行详情' }] },
]
function evidenceText(row: Record<string, any>) { return JSON.stringify(row.completion_evidence || row.evidence_ref || row.evidence || null, null, 2) || '—' }
function schedule() { clearTimeout(timer); if (alive && task.value && !['draft', 'completed', 'stopped'].includes(task.value.status) && !editing.value && !busy.value && !reviewing.value && !controlAction.value) timer = setTimeout(() => { void load() }, 10000) }
async function load() {
  clearTimeout(timer); controller?.abort(); controller = new AbortController(); const gen = ++generation, id = String(route.params.taskId); loading.value = true
  try { const [detail, history, caps] = await Promise.all([sessionTasksApi.get(id, controller.signal), sessionTasksApi.timeline(id, controller.signal, historyPage.value * 100), sessionTasksApi.capabilities(controller.signal)]); if (!alive || gen !== generation) return; task.value = detail; timeline.value = history; capabilities.value = { publish_enabled: caps.publish_enabled, reason: caps.reason || '' } }
  catch(e) { if (alive && gen === generation && (e as Error).name !== 'AbortError') error.value = errorMessage(e) }
  finally { if (alive && gen === generation) { loading.value = false; schedule() } }
}
async function startEdit() { clearTimeout(timer); controller?.abort(); generation++; loading.value = false; editing.value = true; await nextTick(); if (displaySpec.value) editor.value?.load(displaySpec.value) }
function cancelEdit() { if (!window.confirm('放弃未保存的草稿修改？')) return; editing.value = false; schedule() }
function changeHistory(delta: number) { if (editing.value || reviewing.value || controlAction.value) return; historyPage.value = Math.max(0, historyPage.value + delta); void load() }
async function mutate(action: (signal: AbortSignal) => Promise<void>) {
  if (busy.value || !task.value) return; clearTimeout(timer); controller?.abort(); const gen = ++generation; const actionController = new AbortController(); mutationController = actionController; busy.value = true; error.value = ''; notice.value = ''
  try { await action(mutationController.signal); if (!alive || gen !== generation) return; notice.value = '操作已完成'; reviewing.value = false; controlAction.value = ''; editing.value = false; publishAttempt = undefined; await load() }
  catch(e) { if (alive && gen === generation && (e as Error).name !== 'AbortError') { error.value = errorMessage(e); if (e instanceof SessionTaskApiError && ['CONFLICT', 'CONFIRMATION_INVALID', 'IDEMPOTENCY_CONFLICT'].includes(e.code)) { publishAttempt = undefined; error.value += '。请刷新核对最新版本后重试；未保存的编辑仍保留。'; reviewing.value = false } } }
  finally { if (alive && mutationController === actionController) { busy.value = false; schedule() } }
}
function save() { if (!task.value || !editor.value) return; let spec; try { spec = editor.value.getSpec() } catch(e) { error.value = errorMessage(e); return } const id = task.value.id, version = task.value.version; void mutate(async signal => { await sessionTasksApi.save(id, version, spec, signal) }) }
function publish() { if (!task.value || !capabilities.value.publish_enabled || !reviewing.value) return; const id = task.value.id, version = task.value.version; void mutate(async signal => { if (!publishAttempt || publishAttempt.id !== id || publishAttempt.version !== version) { const result = await sessionTasksApi.confirm(id, version, signal); if (signal.aborted) return; publishAttempt = { id, version, confirmation: result.confirmation_id, key: crypto.randomUUID() } } await sessionTasksApi.publish(id, version, publishAttempt.confirmation, publishAttempt.key, signal) }) }
function openResume() { resumeMode.value = ''; controlAction.value = 'resume' }
function control() { if (!task.value || !capabilities.value.publish_enabled || !controlAction.value || (controlAction.value === 'resume' && (!resumeMode.value || task.value.input_version === undefined))) return; const { id, version, input_version } = task.value, action = controlAction.value; void mutate(async signal => { await sessionTasksApi.control(id, version, action, input_version, signal) }) }
watch([reviewing, controlAction], () => { if (reviewing.value || controlAction.value) { clearTimeout(timer); controller?.abort(); generation++; loading.value = false } else schedule() })
watch(() => route.fullPath, () => { generation++; controller?.abort(); mutationController?.abort(); mutationController = undefined; task.value = undefined; historyPage.value = 0; timeline.value = { batches: [], decisions: [], executions: [] }; editing.value = false; reviewing.value = false; controlAction.value = ''; busy.value = false; publishAttempt = undefined; void load() }, { immediate: true })
onBeforeRouteLeave(() => !editing.value || window.confirm('草稿尚未保存，确定离开？'))
onBeforeUnmount(() => { alive = false; generation++; clearTimeout(timer); controller?.abort(); mutationController?.abort() })
</script>

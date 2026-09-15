<template>
  <div class="page-container p-4">
    <div class="page-toolbar flex-wrap gap-2">
      <div class="flex flex-wrap gap-2"><BaseButton intent="secondary" @click="router.push({ name: 'tenant-weixin-marketing-automations' })">营销自动化</BaseButton><BaseBadge intent="warning">会话任务 · 开发中</BaseBadge></div>
      <div class="flex gap-2"><BaseSelect v-model="status" aria-label="筛选状态" @update:model-value="load(1)"><option value="">全部状态</option><option v-for="(label, value) in statusLabels" :key="value" :value="value">{{ label }}</option></BaseSelect><BaseButton :disabled="loading" intent="ghost" @click="load(1)">刷新</BaseButton><BaseButton @click="openCreate">新建草稿</BaseButton></div>
    </div>
    <p v-if="error" role="alert" class="text-sm text-danger-600 p-3">{{ error }}</p>
    <details v-if="notifications.length" class="mb-3 text-sm border border-default rounded p-3"><summary class="cursor-pointer">站内任务通知（仅状态变化、完成或需人工）</summary><ul class="mt-2 space-y-2"><li v-for="item in notifications" :key="item.id"><BaseButton intent="ghost" size="sm" @click="goDetail(item.task_id)">{{ statusLabels[item.status] || item.status }} · {{ item.reason || '查看任务' }} · {{ dateLabel(item.created_at) }}</BaseButton></li></ul></details>
    <div class="table-scroll-wrapper flex-1" @scroll.passive="onScroll">
      <BaseTable class="min-w-[1100px]" :columns="columns" :data="items" row-key="id" :loading="loading && !items.length">
        <template #sequence="{ index }">{{ isMobile ? index + 1 : (page - 1) * 20 + index + 1 }}</template>
        <template #goal_summary="{ row }"><span>{{ row.goal_summary || '未提供目标摘要' }}</span></template>
        <template #status="{ row }">{{ phaseLabel(row as SessionTask) }}</template>
        <template #device_online="{ row }">{{ row.device_online ? '在线' : '离线 / 未知' }}</template>
        <template #last_observed_at="{ row }">{{ dateLabel(row.last_observed_at) }}</template>
        <template #progress="{ row }">发送 {{ row.replies_count ?? 0 }}/{{ row.max_replies ?? '—' }} · 有效轮数 {{ row.rounds_count ?? 0 }} · 决策 {{ row.decisions_count ?? 0 }}</template>
        <template #budget="{ row }">已用 {{ row.cost?.settled ?? '—' }} / 上限 {{ row.max_cost_units ?? '—' }}</template>
        <template #expires_at="{ row }">{{ dateLabel(row.expires_at) }}</template>
        <template #actions="{ row }"><BaseButton intent="ghost" size="sm" @click="goDetail(row.id)">详情</BaseButton></template>
      </BaseTable>
      <p v-if="!loading && !items.length" class="p-8 text-center text-muted">暂无会话任务，可先创建草稿。</p>
      <p v-if="isMobile && items.length" class="p-3 text-center text-muted">{{ loadingMore ? '加载中…' : items.length >= total ? '没有更多了' : '下滑加载更多' }}</p>
    </div>
    <BasePagination v-if="!isMobile" :current-page="page" :page-size="20" :total="total" :show-size-changer="false" @update:current-page="load" />
    <BaseModal accessible :model-value="creating" title="新建会话任务草稿" size="xl" :close-on-overlay="false" @update:model-value="value => { if (!value) closeCreate() }">
      <form @submit.prevent="save">
        <div class="flex gap-2 mb-4"><BaseButton type="submit" :disabled="saving || !selected">{{ saving ? '保存中…' : '保存草稿' }}</BaseButton><BaseButton type="button" intent="secondary" :disabled="saving" @click="closeCreate">取消</BaseButton></div>
        <p v-if="createError" role="alert" class="text-danger-600 mb-3">{{ createError }}</p>
        <label class="block text-sm text-muted mb-4">对象 / 设备 / 账号绑定 *<BaseSelect v-model="selected"><option value="">请选择已有会话绑定</option><option v-for="binding in bindings" :key="binding.id" :value="binding.id">{{ binding.conversation_label || binding.id }} · {{ binding.conversation_type }} · {{ binding.verification_status }} · 设备 {{ binding.device_id }}</option></BaseSelect></label>
        <p v-if="!bindings.length" class="text-sm text-muted mb-3">暂无可用绑定。请先在设备完成会话绑定；草稿不会执行或发送。</p>
        <TaskSpecForm ref="editor" :disabled="saving" />
      </form>
    </BaseModal>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useMobile } from '@/composables/useMobile'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import TaskSpecForm from './TaskSpecForm.vue'
import { sessionTasksApi, type SessionTask, type Binding } from '@/api/sessionTasks'
import { statusLabels, phaseLabel, dateLabel, errorMessage } from './presentation'
const router = useRouter(), route = useRoute(), { isMobile } = useMobile()
const items = ref<SessionTask[]>([]), total = ref(0), page = ref(1), status = ref(''), loading = ref(false), loadingMore = ref(false), error = ref('')
const creating = ref(false), saving = ref(false), createError = ref(''), bindings = ref<Binding[]>([]), selected = ref(''), editor = ref<InstanceType<typeof TaskSpecForm>>()
const notifications = ref<{ id: string; task_id: string; status: string; reason?: string; created_at: string }[]>([])
let generation = 0, alive = true, controller: AbortController | undefined, draftController: AbortController | undefined, draftGeneration = 0
let saveAttempt: { body: string; key: string } | undefined
const columns = [{ key: 'sequence', label: '序号', width: '60px' }, { key: 'goal_summary', label: '任务目标' }, { key: 'status', label: '状态' }, { key: 'device_online', label: '设备' }, { key: 'last_observed_at', label: '最后观察' }, { key: 'progress', label: '进度' }, { key: 'budget', label: '积分' }, { key: 'expires_at', label: '截止时间' }, { key: 'actions', label: '操作', width: '80px' }]
async function load(next = 1, append = false) {
  controller?.abort(); controller = new AbortController(); const gen = ++generation
  if (append) loadingMore.value = true; else loading.value = true
  error.value = ''
  try { const [result, inbox] = await Promise.all([sessionTasksApi.list(status.value, (next - 1) * 20, controller.signal), sessionTasksApi.notifications(controller.signal)]); if (!alive || gen !== generation) return; notifications.value = inbox.items; items.value = append ? [...items.value, ...result.items.filter(item => !items.value.some(old => old.id === item.id))] : result.items; total.value = result.total; page.value = next }
  catch (e) { if (alive && gen === generation && (e as Error).name !== 'AbortError') error.value = errorMessage(e) }
  finally { if (alive && gen === generation) { loading.value = false; loadingMore.value = false } }
}
function onScroll(event: Event) { const el = event.target as HTMLElement; if (isMobile.value && !loading.value && !loadingMore.value && items.value.length < total.value && el.scrollHeight - el.scrollTop - el.clientHeight < 80) void load(page.value + 1, true) }
function goDetail(id: string) { void router.push({ name: 'tenant-weixin-session-task-detail', params: { taskId: id } }) }
function closeCreate() { if (saving.value) return; if (!window.confirm('关闭后未保存的草稿内容将丢失，确定关闭？')) return; creating.value = false }
async function openCreate() { creating.value = true; selected.value = ''; createError.value = ''; saveAttempt = undefined; draftController?.abort(); draftController = new AbortController(); const gen = ++draftGeneration; try { const data = await sessionTasksApi.bindings(draftController.signal); if (alive && gen === draftGeneration && creating.value) bindings.value = data } catch(e) { if (alive && gen === draftGeneration && (e as Error).name !== 'AbortError') createError.value = errorMessage(e) } }
async function save() {
  if (saving.value) return
  const binding = bindings.value.find(b => b.id === selected.value); if (!binding || !editor.value) return
  const gen = draftGeneration; saving.value = true; createError.value = ''
  try { const body = { scenario_key: 'weixin.conversation.v1', device_id: binding.device_id, account_binding_id: binding.account_binding_id, conversation_binding_id: binding.id, spec: editor.value.getSpec() }; const serialized = JSON.stringify(body); if (saveAttempt?.body !== serialized) saveAttempt = { body: serialized, key: crypto.randomUUID() }; const result = await sessionTasksApi.create(body, saveAttempt.key, draftController?.signal); if (alive && gen === draftGeneration && creating.value) { creating.value = false; goDetail(result.task_id) } }
  catch(e) { if (alive && gen === draftGeneration && (e as Error).name !== 'AbortError') createError.value = errorMessage(e) }
  finally { if (alive && gen === draftGeneration) saving.value = false }
}
watch(creating, value => { if (!value) { draftController?.abort(); draftGeneration++; saving.value = false } })
watch(() => route.params.tenant_id, () => { items.value = []; notifications.value = []; creating.value = false; void load(1) })
watch(isMobile, () => { void load(1) })
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; generation++; draftGeneration++; controller?.abort(); draftController?.abort() })
</script>

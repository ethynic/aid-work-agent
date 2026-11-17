<template>
  <BaseModal :model-value="true" :title="plan ? '编辑定时消息' : '新建定时消息'" size="lg" :accessible="!pickerOpen"
    :show-fullscreen="false" :close-on-overlay="false" @update:model-value="close">
    <form id="scheduled-send-form" class="space-y-4" @submit.prevent="save">
      <p v-if="plan" class="m-0 text-sm text-muted break-words">
        固定发送设备 {{ plan.device_name || shortDevice(plan.device_id) }} · 企微账号 {{ plan.account }}
      </p>
      <p v-else class="m-0 text-sm text-muted">保存时绑定当前选定的设备和企微账号。保存后需手动启用。</p>

      <div>
        <label class="form-label" for="scheduled-name">配置名称</label>
        <BaseInput id="scheduled-name" v-model="name" required maxlength="100" />
      </div>
      <div class="space-y-2">
        <div class="flex flex-wrap justify-between items-center gap-2">
          <span class="form-label m-0">目标会话（{{ sessions.length }}/50）</span>
          <BaseButton type="button" size="sm" intent="secondary" :disabled="saving" @click="openPicker">从本地目录选择会话</BaseButton>
        </div>
        <ul v-if="sessions.length" class="m-0 p-0 list-none flex flex-wrap gap-2">
          <li v-for="session in sessions" :key="session" class="max-w-full flex items-center gap-1 border border-default rounded-lg pl-2 text-sm">
            <span class="truncate" :title="sessionName(session)">{{ sessionName(session) }}</span>
            <BaseButton type="button" size="sm" intent="ghost" :disabled="saving" :aria-label="`移除 ${sessionName(session)}`"
              @click="toggleSession(session)">×</BaseButton>
          </li>
        </ul>
        <p v-else class="text-sm text-muted m-0">可同时选择群聊和单聊。</p>
      </div>
      <div>
        <label class="form-label" for="scheduled-text">发送内容</label>
        <textarea id="scheduled-text" v-model="text" required maxlength="2000" rows="4"
          class="w-full rounded-lg border border-default bg-surface text-default px-3 py-2 text-sm focus:ring-1 focus:ring-primary-600"
          placeholder="输入要原样发送的文字，支持换行" />
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label class="form-label" for="scheduled-rule">发送规则</label>
          <BaseSelect id="scheduled-rule" v-model="rule">
            <option value="once">发送一次</option>
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
          </BaseSelect>
        </div>
        <div>
          <label class="form-label" for="scheduled-time">发送时间（北京时间）</label>
          <BaseInput v-if="rule === 'once'" id="scheduled-time" v-model="onceAt" type="datetime-local" :step="1" required />
          <BaseInput v-else id="scheduled-time" v-model="repeatAt" type="time" required />
        </div>
      </div>
      <fieldset v-if="rule === 'weekly'" class="space-y-2">
        <legend class="form-label">星期</legend>
        <div class="flex flex-wrap gap-x-4 gap-y-2">
          <label v-for="day in DAYS" :key="day.value" class="flex gap-1.5 items-center text-sm text-default">
            <input v-model="weekdays" type="checkbox" :value="day.value" />{{ day.label }}
          </label>
        </div>
      </fieldset>
      <p v-if="error" role="alert" class="m-0 text-sm text-danger-700">{{ error }}</p>
    </form>
    <template #footer>
      <BaseButton type="button" intent="secondary" :disabled="saving" @click="close(false)">取消</BaseButton>
      <BaseButton type="submit" form="scheduled-send-form" :disabled="saving">
        {{ saving ? '保存中…' : plan ? '保存修改' : '保存配置' }}
      </BaseButton>
    </template>
  </BaseModal>

  <BaseModal v-model="pickerOpen" title="选择目标会话" size="lg" accessible :show-fullscreen="false">
    <div class="space-y-3">
      <p v-if="source" class="m-0 text-sm text-muted break-words">
        当前设备 {{ shortDevice(source.device_id) }} · 企微账号 {{ source.self_user_id }}
      </p>
      <form class="flex gap-2" @submit.prevent="loadDirectory(false)">
        <BaseInput v-model="query" size="sm" placeholder="搜索群聊或单聊名称" aria-label="搜索群聊或单聊名称" maxlength="100" />
        <BaseButton type="submit" size="sm" intent="secondary" :disabled="directoryLoading">搜索</BaseButton>
      </form>
      <p v-if="directoryError" role="alert" class="m-0 text-sm text-danger-700">{{ directoryError }}</p>
      <p v-if="directoryLoading" role="status" class="m-0 text-sm text-muted">正在读取本地会话目录…</p>
      <div v-if="directory.length" class="max-h-72 overflow-y-auto rounded-lg border border-default divide-y divide-default">
        <label v-for="item in directory" :key="item.session" class="flex items-start gap-2 px-3 py-2 text-sm">
          <input type="checkbox" class="mt-0.5" :checked="sessions.includes(item.session)"
            :disabled="!sessions.includes(item.session) && sessions.length >= 50"
            @change="toggleSession(item.session)" />
          <span class="min-w-0 flex-1 break-words">{{ item.name || '未命名会话' }}
            <span class="ml-2 text-xs text-muted">{{ item.kind === 'group' ? '群聊' : '单聊' }}</span>
            <span v-if="item.same_name" class="ml-2 text-xs text-muted">{{ item.session.slice(-8) }}</span>
          </span>
        </label>
      </div>
      <p v-else-if="!directoryLoading && !directoryError" class="m-0 text-sm text-muted">没有匹配的会话。</p>
      <BaseButton v-if="nextCursor" type="button" size="sm" intent="secondary" :disabled="directoryLoading" @click="loadDirectory(true)">加载更多</BaseButton>
    </div>
    <template #footer>
      <span class="mr-auto text-sm text-muted">已选 {{ sessions.length }} 个会话</span>
      <BaseButton type="button" intent="secondary" @click="pickerOpen = false">完成选择</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { listDirectory, saveScheduledSend, type DirectoryConversation, type DirectoryPage,
  type ScheduledSendPlan, type ScheduledSendWrite } from '@/api/wecomConversation'

const props = defineProps<{ plan: ScheduledSendPlan | null }>()
const emit = defineEmits<{ close: []; saved: [plan: ScheduledSendPlan] }>()

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((value, index) => ({ value, label: `周${'一二三四五六日'[index]}` }))
const name = ref(props.plan?.name ?? '')
const text = ref(props.plan?.text ?? '')
const rule = ref(props.plan?.schedule_type ?? 'once')
const onceAt = ref(props.plan?.time_config.run_at?.replace(' ', 'T').slice(0, 19) ?? '')
const twoDigits = (value: number) => String(value).padStart(2, '0')
const repeatAt = ref(`${twoDigits(props.plan?.time_config.hour ?? 9)}:${twoDigits(props.plan?.time_config.minute ?? 0)}`)
const weekdays = ref([...(props.plan?.time_config.weekdays ?? [])])
const originalSessions = props.plan?.sessions ?? []
const sessions = ref([...originalSessions])
const sessionNames = ref({ ...props.plan?.session_names })
const saving = ref(false)
const error = ref('')

type DirectorySource = Pick<DirectoryPage, 'device_id' | 'self_user_id' | 'source_generation'>
const source = ref<DirectorySource | null>(null)
const pickerOpen = ref(false)
const query = ref('')
const appliedQuery = ref('')
const directory = ref<DirectoryConversation[]>([])
const nextCursor = ref<string | null>(null)
const directoryError = ref('')
const directoryLoading = ref(false)
let searchRequest = 0

function shortDevice(id: string) { return id.length > 8 ? id.slice(0, 8) : id }
function sessionName(session: string) { return sessionNames.value[session] || `未命名会话 ${session.slice(-6)}` }
function message(e: unknown) { return e instanceof Error ? e.message : String(e) }

function toggleSession(session: string) {
  if (sessions.value.includes(session)) sessions.value = sessions.value.filter(value => value !== session)
  else if (sessions.value.length < 50) sessions.value.push(session)
}

function openPicker() {
  pickerOpen.value = true
  query.value = ''
  void loadDirectory(false)
}

async function loadDirectory(more: boolean) {
  const request = ++searchRequest
  const cursor = more ? nextCursor.value || undefined : undefined
  if (!more) {
    appliedQuery.value = query.value.trim()
    directory.value = []
    nextCursor.value = null
  }
  directoryError.value = ''
  directoryLoading.value = true
  try {
    const page = await listDirectory({ query: appliedQuery.value, cursor })
    if (request !== searchRequest || !pickerOpen.value) return
    const previous = source.value
    const changed = previous && (previous.device_id !== page.device_id || previous.self_user_id !== page.self_user_id
      || previous.source_generation !== page.source_generation)
    if (changed || (props.plan && (page.device_id !== props.plan.device_id || page.self_user_id !== props.plan.account))) {
      sessions.value = sessions.value.filter(value => originalSessions.includes(value))
      directory.value = []
      nextCursor.value = null
      source.value = null
      if (props.plan && (page.device_id !== props.plan.device_id || page.self_user_id !== props.plan.account)) {
        throw new Error('请切回配置绑定的设备和企微账号后添加会话。')
      }
      directoryError.value = '目录来源已变化，请重新选择会话。'
      if (more) return
    }
    source.value = { device_id: page.device_id, self_user_id: page.self_user_id, source_generation: page.source_generation }
    directory.value = more ? [...directory.value, ...page.conversations] : page.conversations
    for (const item of page.conversations) sessionNames.value[item.session] = item.name
    nextCursor.value = page.next_cursor
  } catch (e) {
    if (request === searchRequest) directoryError.value = message(e)
  } finally {
    if (request === searchRequest) directoryLoading.value = false
  }
}

function close(open: boolean) {
  if (!open && !saving.value) emit('close')
}

async function save() {
  if (saving.value) return
  error.value = ''
  if (!name.value.trim() || !text.value.trim() || !sessions.value.length) {
    error.value = '请填写配置名称、发送内容并选择会话。'
    return
  }
  if (rule.value === 'weekly' && !weekdays.value.length) {
    error.value = '请至少选择一个星期。'
    return
  }
  const requiresDirectory = !props.plan || sessions.value.some(value => !originalSessions.includes(value))
  if (requiresDirectory && !source.value) {
    error.value = '请重新从本地目录选择目标会话。'
    return
  }
  const [hour, minute] = repeatAt.value.split(':').map(Number)
  const wallTime = onceAt.value.replace('T', ' ')
  const time_config = rule.value === 'once'
    ? { run_at: wallTime.length === 16 ? `${wallTime}:00` : wallTime }
    : { hour, minute, ...(rule.value === 'weekly' ? { weekdays: weekdays.value } : {}) }
  const body: ScheduledSendWrite = { name: name.value, text: text.value, sessions: sessions.value,
    schedule_type: rule.value, time_config,
    ...(requiresDirectory && source.value ? { expected_device_id: source.value.device_id,
      expected_source_generation: source.value.source_generation } : {}) }
  saving.value = true
  try {
    emit('saved', (await saveScheduledSend(body, props.plan?.task_id)).plan)
  } catch (e) {
    error.value = message(e)
  } finally {
    saving.value = false
  }
}
</script>

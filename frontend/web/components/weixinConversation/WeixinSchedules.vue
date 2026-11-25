<template>
  <BaseCard>
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="m-0 text-lg font-semibold text-default">定时总结</h2>
      <BaseButton type="button" :disabled="!device || !groups.length" @click="openEditor()">新建定时任务</BaseButton>
    </div>
    <p v-if="loadError" class="mt-3 text-sm text-danger-700" role="alert">{{ loadError }}</p>
    <p v-else-if="loading" class="mt-3 text-sm text-muted" role="status">正在加载定时任务…</p>
    <p v-else-if="!schedules.length" class="mt-3 text-sm text-muted">暂无定时任务。</p>
    <p v-if="!device || !groups.length" class="mt-3 text-sm text-muted">选择运行设备并添加跟踪群后可新建任务。</p>
    <ul v-if="schedules.length" class="m-0 mt-2 list-none divide-y divide-default p-0">
      <li v-for="item in schedules" :key="item.id" class="flex flex-wrap justify-between gap-2 py-3">
        <div class="min-w-0 space-y-1">
          <h3 class="m-0 break-words text-sm font-semibold text-default">{{ item.title }}</h3>
          <p class="m-0 text-sm text-muted">
            {{ item.frequency === 'daily' ? '每天' : '每周' + weekdays[item.weekday ?? 0] }}
            {{ timeValue(item.hour, item.minute) }} · {{ presetLabel(item.preset) }} · {{ included(item).length }} 群
          </p>
          <p class="m-0 text-sm text-muted">
            {{ item.next_due_at ? `下次 ${formatBeijing(item.next_due_at)}（北京时间）` : '已暂停' }}
            · {{ item.notify ? (item.notification_rule_id ? '群机器人通知' : '默认通知') : '关闭通知' }}
          </p>
        </div>
        <div class="flex flex-wrap items-start gap-1">
          <BaseButton type="button" intent="ghost" :aria-label="`编辑 ${item.title}`" @click="openEditor(item)">编辑</BaseButton>
          <BaseButton type="button" intent="secondary" :aria-label="`${item.paused ? '恢复' : '暂停'} ${item.title}`"
            :disabled="busySchedule === item.id" @click="togglePause(item)">{{ item.paused ? '恢复' : '暂停' }}</BaseButton>
        </div>
      </li>
    </ul>

    <BaseModal v-model="editor" :title="editing ? '编辑定时总结' : '新建定时总结'" accessible :show-fullscreen="false">
      <form class="space-y-4" @submit.prevent="submit">
        <label for="weixin-schedule-title" class="block space-y-1 text-sm text-default">
          <span>任务名称</span>
          <BaseInput id="weixin-schedule-title" v-model="draft.title" maxlength="80" required />
        </label>
        <fieldset class="space-y-2">
          <legend class="text-sm font-medium text-default">跟踪群聊</legend>
          <div class="grid max-h-36 gap-2 overflow-y-auto sm:grid-cols-2">
            <label v-for="group in groups" :key="group.id" class="flex items-start gap-2 break-all text-sm text-default">
              <input v-model="draft.group_ids" class="mt-1" type="checkbox" :value="group.id">{{ group.name }}
            </label>
          </div>
        </fieldset>
        <div class="grid gap-3 sm:grid-cols-2">
          <label for="weixin-schedule-preset" class="space-y-1 text-sm text-default">
            <span>总结时间范围</span>
            <BaseSelect id="weixin-schedule-preset" v-model="draft.preset">
              <option v-for="item in presets" :key="item.id" :value="item.id">{{ item.label }}</option>
            </BaseSelect>
          </label>
          <label for="weixin-schedule-frequency" class="space-y-1 text-sm text-default">
            <span>频率</span>
            <BaseSelect id="weixin-schedule-frequency" v-model="draft.frequency">
              <option value="daily">每天</option><option value="weekly">每周</option>
            </BaseSelect>
          </label>
          <label v-if="draft.frequency === 'weekly'" for="weixin-schedule-day" class="space-y-1 text-sm text-default">
            <span>星期</span>
            <BaseSelect id="weixin-schedule-day" v-model="weekday">
              <option v-for="(label, index) in weekdays" :key="index" :value="index.toString()">星期{{ label }}</option>
            </BaseSelect>
          </label>
          <label for="weixin-schedule-time" class="space-y-1 text-sm text-default">
            <span>运行时间（北京时间）</span>
            <BaseInput id="weixin-schedule-time" v-model="time" type="time" required />
          </label>
        </div>
        <p v-if="preview" class="m-0 text-sm text-muted">下次计划执行 · {{ formatBeijing(preview) }}（北京时间）</p>
        <div class="space-y-2 border-t border-default pt-3">
          <label class="flex items-center gap-2 text-sm text-default">
            <input v-model="draft.notify" type="checkbox">发送执行结果通知
          </label>
          <template v-if="draft.notify">
            <label class="flex items-center gap-2 text-sm text-default"><input v-model="notifyMode" type="radio" value="default">默认通知配置</label>
            <label class="flex items-center gap-2 text-sm text-default"><input v-model="notifyMode" type="radio" value="robot">群机器人通知配置</label>
            <label v-if="notifyMode === 'robot'" for="weixin-notification-rule" class="block space-y-1 text-sm text-default">
              <span>通知规则</span>
              <BaseSelect id="weixin-notification-rule" v-model="ruleId" :disabled="rulesLoading || !rules.length">
                <option value="">选择可用规则</option>
                <option v-for="rule in rules" :key="rule.rule_id" :value="rule.rule_id">
                  {{ rule.name }} · {{ rule.channels.map(channel => channelNames[channel] || channel).join('、') }}
                </option>
              </BaseSelect>
            </label>
            <p v-if="notifyMode === 'robot' && !rulesLoading && !rules.length" class="m-0 text-sm text-muted">
              {{ rulesError || '暂无可用的定时任务群机器人规则。' }}
              <RouterLink class="text-primary-700 underline" :to="{ name: 'tenant-notification-rules', params: { tenant_id: tenantId } }">通知规则</RouterLink>
            </p>
            <p class="m-0 text-sm text-muted">通知仅包含任务名称、执行状态和本次结果链接。</p>
          </template>
        </div>
        <p v-if="saveError" class="m-0 text-sm text-danger-700" role="alert">{{ saveError }}</p>
        <div class="flex justify-end gap-2">
          <BaseButton type="button" intent="secondary" @click="editor = false">取消</BaseButton>
          <BaseButton type="submit" :disabled="saving || !valid">保存任务</BaseButton>
        </div>
      </form>
    </BaseModal>
  </BaseCard>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import type { LocalToolDevice } from '@/api/localTools'
import { listRobotRules, listSchedules, presets, saveSchedule, setSchedulePaused, type Group, type RobotRule, type Schedule, type ScheduleDefinition } from '@/api/weixinAgent'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { formatBeijing } from './beijingTime'

const props = defineProps<{ groups: Group[]; device?: LocalToolDevice }>()
const route = useRoute()
const tenantId = computed(() => String(route.params.tenant_id ?? ''))
const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const timeValue = (hour: number, minute: number) => [hour, minute].map(part => String(part).padStart(2, '0')).join(':')
const presetLabel = (value: string) => presets.find(preset => preset.id === value)?.label
const included = (item: Schedule) => props.groups.filter(group => item.group_ids.includes(group.id))
const channelNames: Record<string, string> = { wecom_bot: '企业微信', dingtalk_bot: '钉钉', feishu_bot: '飞书' }
const schedules = ref<Schedule[]>([])
const loading = ref(false)
const loadError = ref('')
const busySchedule = ref('')
async function load() {
  loading.value = true
  loadError.value = ''
  try { schedules.value = await listSchedules() }
  catch (error) { loadError.value = (error as Error).message }
  finally { loading.value = false }
}
function update(item: Schedule) {
  const index = schedules.value.findIndex(existing => item.id === existing.id)
  if (index < 0) schedules.value.unshift(item)
  else schedules.value.splice(index, 1, item)
}
async function togglePause(item: Schedule) {
  if (busySchedule.value) return
  busySchedule.value = item.id
  loadError.value = ''
  try { update(await setSchedulePaused(item.id, !item.paused)) }
  catch (error) { loadError.value = (error as Error).message }
  finally { busySchedule.value = '' }
}

const editor = ref(false)
const editing = ref<Schedule>()
const saving = ref(false)
const saveError = ref('')
const newDraft = (): ScheduleDefinition => ({
  title: '', device_id: props.device?.device_id ?? '', group_ids: [], preset: 'today', frequency: 'daily',
  weekday: null, hour: 18, minute: 0, notify: false, notification_rule_id: null,
})
const draft = ref<ScheduleDefinition>(newDraft())
const weekday = ref('0')
const time = ref('18:00')
const notifyMode = ref<'default' | 'robot'>('default')
const ruleId = ref('')
const rulesLoading = ref(false)
const rulesError = ref('')
const rules = ref<RobotRule[]>([])
async function loadRules() {
  rulesLoading.value = true
  rulesError.value = ''
  try { rules.value = await listRobotRules() }
  catch (error) { rules.value = []; rulesError.value = (error as Error).message }
  finally { rulesLoading.value = false }
}
watch(notifyMode, value => { if (value === 'robot' && !rulesLoading.value) void loadRules() })
function openEditor(item?: Schedule) {
  editing.value = item
  if (item) {
    const { device_id, title, preset, frequency, notify, notification_rule_id } = item
    draft.value = { device_id, title, preset, frequency, notify, notification_rule_id,
      group_ids: included(item).map(group => group.id), weekday: item.weekday, hour: item.hour, minute: item.minute }
  } else draft.value = newDraft()
  weekday.value = String(draft.value.weekday ?? 0)
  time.value = timeValue(draft.value.hour, draft.value.minute)
  ruleId.value = draft.value.notification_rule_id ?? ''
  const mode = ruleId.value ? 'robot' : 'default'
  if (mode === notifyMode.value && mode === 'robot' && !rulesLoading.value) void loadRules()
  notifyMode.value = mode
  saveError.value = ''
  editor.value = true
}

const valid = computed(() => !!draft.value.title.trim() && !!draft.value.group_ids.length && !!draft.value.device_id
  && /^([01]\d|2[0-3]):[0-5]\d$/.test(time.value)
  && (!draft.value.notify || notifyMode.value !== 'robot' || !!rules.value.find(rule => rule.rule_id === ruleId.value)))
const preview = computed(() => {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time.value)) return null
  const [hour, minute] = time.value.split(':').map(Number)
  const offset = 8 * 3_600_000
  const now = Date.now() + offset
  const local = new Date(now)
  const today = (local.getUTCDay() + 6) % 7
  const span = draft.value.frequency === 'weekly' ? (7 + Number(weekday.value) - today) % 7 : 0
  local.setUTCDate(local.getUTCDate() + span)
  local.setUTCHours(hour, minute, 0, 0)
  if (local.getTime() <= now) local.setUTCDate(local.getUTCDate() + (draft.value.frequency === 'weekly' ? 7 : 1))
  return local.getTime() - offset
})
async function submit() {
  if (!valid.value || saving.value) return
  saving.value = true
  saveError.value = ''
  const [hour, minute] = time.value.split(':').map(Number)
  try {
    const result = await saveSchedule({
      ...draft.value, title: draft.value.title.trim(), hour, minute,
      weekday: draft.value.frequency === 'weekly' ? Number(weekday.value) : null,
      notification_rule_id: draft.value.notify && notifyMode.value === 'robot' ? ruleId.value : null,
    }, editing.value?.id)
    update(result)
    editor.value = false
  } catch (error) { saveError.value = (error as Error).message }
  finally { saving.value = false }
}
void load()
</script>

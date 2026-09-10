<template>
  <!-- 触发配置编辑：once/interval/calendar/event 判别联合（契约快照见 api/weixinMarketing.ts） -->
  <div class="space-y-3">
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div>
        <label class="form-label">触发类型</label>
        <BaseSelect :model-value="trigger.type" :disabled="disabled" @update:model-value="switchType">
          <option value="once">一次性</option>
          <option value="interval">间隔循环</option>
          <option value="calendar">日历（cron）</option>
          <option value="event">事件</option>
        </BaseSelect>
      </div>
      <div>
        <label class="form-label">时区</label>
        <BaseSelect :model-value="trigger.timezone" :disabled="disabled" @update:model-value="v => patch({ timezone: String(v) })">
          <option value="Asia/Shanghai">Asia/Shanghai</option>
          <option value="UTC">UTC</option>
        </BaseSelect>
      </div>
      <div v-if="trigger.type !== 'event'">
        <label class="form-label">宽限秒数</label>
        <input
          :value="trigger.grace_seconds"
          type="number"
          min="0"
          :disabled="disabled"
          class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          @change="e => patch({ grace_seconds: clampInt((e.target as HTMLInputElement).value, 0) })"
        />
        <p class="text-xs text-muted mt-1">错过时刻后的接纳窗口，默认 300</p>
      </div>
    </div>

    <!-- 一次性 -->
    <template v-if="trigger.type === 'once'">
      <div>
        <label class="form-label">执行时刻 <span class="form-required">*</span></label>
        <input
          :value="isoToLocalInput(trigger.run_at)"
          type="datetime-local"
          :disabled="disabled"
          class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          @change="e => patchDatetime('run_at', (e.target as HTMLInputElement).value)"
        />
        <p v-if="datetimeError" class="text-xs text-danger-600 mt-1">{{ datetimeError }}</p>
      </div>
    </template>

    <!-- 间隔循环 -->
    <template v-else-if="trigger.type === 'interval'">
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <label class="form-label">开始时刻 <span class="form-required">*</span></label>
          <input
            :value="isoToLocalInput(trigger.start_at)"
            type="datetime-local"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patchDatetime('start_at', (e.target as HTMLInputElement).value)"
          />
        </div>
        <div>
          <label class="form-label">间隔秒数 <span class="form-required">*</span></label>
          <input
            :value="trigger.interval_seconds"
            type="number"
            min="1"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patch({ interval_seconds: clampInt((e.target as HTMLInputElement).value, 1) })"
          />
          <p class="text-xs text-muted mt-1">频率上限默认最小 300 秒（发布时后端校验）</p>
        </div>
        <div>
          <label class="form-label">错过策略</label>
          <BaseSelect
            :model-value="trigger.miss_policy"
            :disabled="disabled"
            @update:model-value="v => patch({ miss_policy: String(v) as 'skip_overlap' | 'catch_up_latest' })"
          >
            <option value="skip_overlap">跳过错过（skip_overlap）</option>
            <option value="catch_up_latest">补跑最近一次（catch_up_latest）</option>
          </BaseSelect>
        </div>
      </div>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label class="form-label">结束时刻（可选）</label>
          <input
            :value="isoToLocalInput(trigger.ends_at ?? '')"
            type="datetime-local"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patchDatetime('ends_at', (e.target as HTMLInputElement).value, true)"
          />
        </div>
        <div>
          <label class="form-label">最多执行次数（可选）</label>
          <input
            :value="trigger.max_count ?? ''"
            type="number"
            min="1"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patch({ max_count: optionalInt((e.target as HTMLInputElement).value, 1) })"
          />
        </div>
      </div>
    </template>

    <!-- 日历 cron -->
    <template v-else-if="trigger.type === 'calendar'">
      <div>
        <label class="form-label">cron 表达式（五段或字段式）</label>
        <input
          :value="trigger.cron_expr ?? ''"
          type="text"
          placeholder="如 0 9 * * mon-fri（工作日 9 点）"
          :disabled="disabled"
          class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          @change="e => patch({ cron_expr: (e.target as HTMLInputElement).value.trim() || null })"
        />
        <p class="text-xs text-muted mt-1">day_of_week 用 mon..sun；也可在下方按字段填写，两者至少配一种</p>
      </div>
      <details class="rounded-lg border border-default p-3">
        <summary class="text-sm text-muted cursor-pointer select-none">字段式高级配置（可选）</summary>
        <div class="grid grid-cols-2 gap-3 mt-3 sm:grid-cols-3">
          <div v-for="field in CRON_FIELDS" :key="field">
            <label class="form-label">{{ field }}</label>
            <input
              :value="(trigger as CalendarTriggerConfig)[field] ?? ''"
              type="text"
              :placeholder="field === 'day_of_week' ? 'mon-fri' : '*'"
              :disabled="disabled"
              class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
              @change="e => patchCronField(field, (e.target as HTMLInputElement).value)"
            />
          </div>
        </div>
      </details>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <label class="form-label">错过策略</label>
          <BaseSelect
            :model-value="trigger.miss_policy"
            :disabled="disabled"
            @update:model-value="v => patch({ miss_policy: String(v) as 'skip_overlap' | 'catch_up_latest' })"
          >
            <option value="skip_overlap">跳过错过（skip_overlap）</option>
            <option value="catch_up_latest">补跑最近一次（catch_up_latest）</option>
          </BaseSelect>
        </div>
        <div>
          <label class="form-label">结束时刻（可选）</label>
          <input
            :value="isoToLocalInput(trigger.ends_at ?? '')"
            type="datetime-local"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patchDatetime('ends_at', (e.target as HTMLInputElement).value, true)"
          />
        </div>
        <div>
          <label class="form-label">最多执行次数（可选）</label>
          <input
            :value="trigger.max_count ?? ''"
            type="number"
            min="1"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patch({ max_count: optionalInt((e.target as HTMLInputElement).value, 1) })"
          />
        </div>
      </div>
    </template>

    <!-- 事件 -->
    <template v-else>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <label class="form-label">事件源 <span class="form-required">*</span></label>
          <input
            :value="trigger.source_ref"
            type="text"
            placeholder="事件源标识"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patch({ source_ref: (e.target as HTMLInputElement).value })"
          />
        </div>
        <div>
          <label class="form-label">事件类型</label>
          <input
            :value="trigger.event_type"
            type="text"
            placeholder="* 订阅全部"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patch({ event_type: (e.target as HTMLInputElement).value.trim() || '*' })"
          />
        </div>
        <div>
          <label class="form-label">延迟秒数</label>
          <input
            :value="trigger.delay_seconds"
            type="number"
            min="0"
            :disabled="disabled"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            @change="e => patch({ delay_seconds: clampInt((e.target as HTMLInputElement).value, 0) })"
          />
        </div>
      </div>
      <p class="text-xs text-warning-600">事件触发源的接收与接线在 P4 交付，当前配置仅保存订阅。</p>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * TriggerEditor：TriggerConfig 判别联合表单。
 * 类型切换时给出该类型的默认配置（避免残留异类型字段被严格模型 extra=forbid 拒绝）。
 */
import { ref } from 'vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import {
  type CalendarTriggerConfig,
  type IntervalTriggerConfig,
  type OnceTriggerConfig,
  type TriggerConfig,
} from '@/api/weixinMarketing'
import { isoToLocalInput, localInputToIso } from './weixinDisplay'

const props = defineProps<{
  trigger: TriggerConfig
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:trigger': [trigger: TriggerConfig]
}>()

const CRON_FIELDS = ['year', 'month', 'day', 'week', 'day_of_week', 'hour', 'minute', 'second'] as const

const datetimeError = ref('')

function patch(fields: Partial<TriggerConfig>) {
  emit('update:trigger', { ...props.trigger, ...fields } as TriggerConfig)
}

function patchCronField(field: (typeof CRON_FIELDS)[number], value: string) {
  const cleaned = value.trim() || null
  patch({ [field]: cleaned } as Partial<CalendarTriggerConfig>)
}

function patchDatetime(field: 'run_at' | 'start_at' | 'ends_at', value: string, optional = false) {
  if (!value) {
    if (optional) {
      patch({ [field]: null } as Partial<TriggerConfig>)
      datetimeError.value = ''
    } else {
      datetimeError.value = '请填写时间'
    }
    return
  }
  const iso = localInputToIso(value)
  if (!iso) {
    datetimeError.value = '时间格式不正确'
    return
  }
  datetimeError.value = ''
  patch({ [field]: iso } as Partial<TriggerConfig>)
}

function clampInt(value: string, min: number): number {
  const n = Math.floor(Number(value))
  return Number.isFinite(n) ? Math.max(min, n) : min
}

function optionalInt(value: string, min: number): number | null {
  if (value.trim() === '') return null
  return clampInt(value, min)
}

function switchType(type: unknown) {
  const timezone = props.trigger.timezone || 'Asia/Shanghai'
  const grace = props.trigger.type === 'event' ? 300 : props.trigger.grace_seconds
  switch (String(type)) {
    case 'once':
      emit('update:trigger', {
        type: 'once',
        run_at: new Date(Date.now() + 3600_000).toISOString(),
        timezone,
        grace_seconds: grace,
      } satisfies OnceTriggerConfig)
      break
    case 'interval':
      emit('update:trigger', {
        type: 'interval',
        start_at: new Date(Date.now() + 3600_000).toISOString(),
        interval_seconds: 86400,
        timezone,
        grace_seconds: grace,
        miss_policy: 'skip_overlap',
        ends_at: null,
        max_count: null,
      } satisfies IntervalTriggerConfig)
      break
    case 'calendar':
      emit('update:trigger', {
        type: 'calendar',
        timezone,
        cron_expr: '0 9 * * *',
        year: null,
        month: null,
        day: null,
        week: null,
        day_of_week: null,
        hour: null,
        minute: null,
        second: null,
        grace_seconds: grace,
        miss_policy: 'skip_overlap',
        ends_at: null,
        max_count: null,
      } satisfies CalendarTriggerConfig)
      break
    case 'event':
      emit('update:trigger', {
        type: 'event',
        source_ref: '',
        event_type: '*',
        delay_seconds: 0,
        timezone,
      })
      break
  }
}
</script>

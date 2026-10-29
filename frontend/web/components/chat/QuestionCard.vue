<template>
  <!-- 历史只读已答卡片（supplementalInputs 扩展投影，设计 §7.1）：题目 + 用户答案 + 答复时间，
       control 状态区分「已提交/已应用」，无任何提交按钮 -->
  <article
    v-if="answered"
    data-test="answered-card"
    class="mt-3 overflow-hidden rounded-2xl border border-gray-200 bg-gray-50/60 shadow-sm"
  >
    <div class="flex items-center justify-between gap-3 border-b border-gray-200 px-4 py-2.5">
      <div class="flex items-center gap-2 text-xs font-medium text-gray-500">
        <span class="h-2 w-2 rounded-full bg-primary-400"></span>
        提问回答
      </div>
      <div class="flex items-center gap-2 text-xs text-gray-400">
        <span data-test="answered-status" class="rounded-full bg-white px-2 py-0.5 font-medium text-gray-500">
          {{ answeredStatusLabel }}
        </span>
        <span v-if="answeredTime">{{ answeredTime }}</span>
      </div>
    </div>
    <div class="space-y-3 px-4 py-3">
      <div v-for="(question, index) in answered.form.questions" :key="question.id">
        <p class="text-sm font-medium text-gray-700">
          Q{{ index + 1 }}<template v-if="question.tag">【{{ question.tag }}】</template>{{ question.prompt }}
        </p>
        <ul v-if="question.type !== 'text'" class="mt-1.5 space-y-1">
          <li
            v-for="option in question.options || []"
            :key="option.key"
            class="flex items-start gap-2 text-sm"
          >
            <span
              :class="['mt-1 h-3.5 w-3.5 shrink-0 border',
                       question.type === 'multi' ? 'rounded-[3px]' : 'rounded-full',
                       isAnsweredKey(question, option.key) ? 'border-primary-600 bg-primary-600' : 'border-gray-300 bg-white']"
            ></span>
            <span :class="isAnsweredKey(question, option.key) ? 'text-primary-700' : 'text-gray-400'">
              {{ option.label }}
              <span v-if="option.description" class="block text-xs leading-relaxed opacity-80">{{ option.description }}</span>
            </span>
          </li>
        </ul>
        <p v-if="answeredText(question)" class="mt-1 text-sm text-gray-600">自定义：{{ answeredText(question) }}</p>
        <p v-else-if="!answeredKeys(question).length" class="mt-1 text-xs text-gray-400">（未回答）</p>
      </div>
    </div>
  </article>

  <!-- 可作答提问卡片（快照 waiting.form 投影，设计 §7.1 + §5.7 状态机） -->
  <article
    v-else-if="card"
    data-test="question-card"
    class="mt-3 overflow-hidden rounded-2xl border border-primary-200 bg-primary-50/40 shadow-sm"
  >
    <div class="flex items-start justify-between gap-3 border-b border-primary-200/70 px-4 py-3">
      <div class="flex items-center gap-2 text-xs font-medium text-primary-700">
        <span class="h-2 w-2 rounded-full bg-primary-500"></span>
        需要你的回答
      </div>
      <span
        v-if="statusLabel"
        data-test="card-status"
        :class="['rounded-full px-2 py-0.5 text-xs font-medium', statusTone]"
      >{{ statusLabel }}</span>
    </div>

    <div class="px-4 py-3">
      <h3 v-if="card.form.title" class="text-sm font-semibold text-slate-800">{{ card.form.title }}</h3>
      <p v-if="card.form.intro" class="mt-1 text-sm leading-relaxed text-gray-600">{{ card.form.intro }}</p>

      <!-- 表单级/状态级提示：422 question_id='form'、409 过期与冲突、终态标注（§5.7） -->
      <div
        v-if="banner"
        data-test="error-banner"
        role="alert"
        class="mt-2 rounded-lg border border-danger-200 bg-white px-3 py-2 text-xs leading-relaxed text-danger-600"
      >{{ banner }}</div>

      <div
        v-if="currentQuestion"
        :class="['mt-3 rounded-xl border bg-white p-3', currentError ? 'border-danger-300' : 'border-gray-200']"
      >
        <div class="flex items-start gap-2">
          <span
            v-if="currentQuestion.tag"
            class="mt-0.5 shrink-0 rounded-full bg-primary-100 px-2 py-0.5 text-xs font-medium text-primary-700"
          >{{ currentQuestion.tag }}</span>
          <p class="text-sm font-medium text-gray-700">
            {{ currentQuestion.prompt }}
            <span v-if="currentQuestion.required" class="text-danger-500">*</span>
            <span v-if="multiMaxSelect" data-test="multi-max-hint" class="text-xs font-normal text-gray-400">
              （最多选 {{ multiMaxSelect }} 个，已选 {{ selectedCount }} 个）
            </span>
          </p>
        </div>

        <!-- 选项：single=radio 圆点 / multi=checkbox 方框（严格按 type）；
             label+description 全量展示，选择与自定义输入并存不互斥 -->
        <div
          v-if="currentQuestion.type !== 'text'"
          class="mt-2 space-y-1.5"
          role="group"
          :aria-label="currentQuestion.prompt"
        >
          <label
            v-for="option in currentQuestion.options || []"
            :key="option.key"
            :class="['relative flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2 transition-colors',
                     isSelected(currentQuestion, option.key)
                       ? 'border-primary-300 bg-primary-50/60'
                       : 'border-gray-100 bg-gray-50/40 hover:border-gray-200']"
          >
            <!-- 完全受控：click.prevent 后由状态驱动勾选，超过 max_select 的点击
                 不会在视觉上误显示为已选（服务端 max_select 权威，前端不制造假象）。
                 原生 input 仅保留语义与键盘焦点（sr-only），勾选视觉由下方自绘控件
                 纯状态驱动——真机上浏览器对 radio/checkbox 的点击有激活/取消回滚，
                 preventDefault 后原生勾选渲染与受控状态脱节，不能依赖 -->
            <input
              :type="currentQuestion.type === 'multi' ? 'checkbox' : 'radio'"
              :name="'ask-' + card.waitId + '-' + currentQuestion.id"
              :checked="isSelected(currentQuestion, option.key)"
              :disabled="!editable"
              :data-test="'option-' + option.key"
              class="peer sr-only"
              @click.prevent="toggleOption(currentQuestion, option.key)"
            />
            <span
              aria-hidden="true"
              :class="['mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center border transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-primary-400',
                       currentQuestion.type === 'multi' ? 'rounded-[4px]' : 'rounded-full',
                       isSelected(currentQuestion, option.key)
                         ? 'border-primary-600 bg-primary-600'
                         : 'border-gray-300 bg-white']"
            >
              <span
                v-if="currentQuestion.type !== 'multi' && isSelected(currentQuestion, option.key)"
                class="h-1.5 w-1.5 rounded-full bg-white"
              ></span>
              <svg
                v-else-if="currentQuestion.type === 'multi' && isSelected(currentQuestion, option.key)"
                viewBox="0 0 16 16" class="h-3 w-3" fill="none" stroke="white" stroke-width="2.2"
                stroke-linecap="round" stroke-linejoin="round"
              ><path d="M3 8.5l3.2 3.2L13 5" /></svg>
            </span>
            <span class="min-w-0">
              <span class="block text-sm font-medium text-gray-700">{{ option.label }}</span>
              <span v-if="option.description" class="mt-0.5 block text-xs leading-relaxed text-gray-500">{{ option.description }}</span>
            </span>
          </label>
        </div>

        <!-- 自由输入：single/multi 的自定义行（allow_custom_text，输入不清空已选）与 text 题独立输入框 -->
        <input
          v-if="currentQuestion.type !== 'text' && currentQuestion.allow_custom_text !== false"
          type="text"
          :value="drafts[currentQuestion.id] || ''"
          :disabled="!editable"
          :placeholder="currentQuestion.custom_placeholder || '输入自定义内容…'"
          data-test="custom-input"
          class="mt-2 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 placeholder:text-gray-400 focus:border-primary-300 focus:outline-none disabled:bg-gray-50 disabled:text-gray-400"
          @input="setDraft(currentQuestion, ($event.target as HTMLInputElement).value)"
        />
        <textarea
          v-if="currentQuestion.type === 'text'"
          rows="3"
          :value="drafts[currentQuestion.id] || ''"
          :disabled="!editable"
          :placeholder="currentQuestion.placeholder || '输入你的回答…'"
          data-test="text-input"
          class="mt-2 w-full resize-y rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 placeholder:text-gray-400 focus:border-primary-300 focus:outline-none disabled:bg-gray-50 disabled:text-gray-400"
          @input="setDraft(currentQuestion, ($event.target as HTMLTextAreaElement).value)"
        ></textarea>

        <!-- required 前端预检（仅即时反馈，服务端权威）与 422 逐题错误高亮 -->
        <p v-if="currentError" data-test="question-error" class="mt-2 text-xs text-danger-600">{{ currentError }}</p>
      </div>

      <!-- 多题分页（一 ask 一 wait，多题打包；分页仅为展示形态，§5.1） -->
      <div v-if="questionCount > 1" class="mt-3 flex items-center justify-center gap-3 text-xs text-gray-500">
        <button
          type="button"
          data-test="prev-page"
          :disabled="page <= 0 || !editable"
          class="rounded-full border border-gray-200 bg-white px-2.5 py-0.5 hover:border-primary-300 disabled:opacity-40"
          @click="page--"
        >‹</button>
        <span data-test="page-indicator" class="font-medium">{{ page + 1 }} / {{ questionCount }}</span>
        <button
          type="button"
          data-test="next-page"
          :disabled="page >= questionCount - 1 || !editable"
          class="rounded-full border border-gray-200 bg-white px-2.5 py-0.5 hover:border-primary-300 disabled:opacity-40"
          @click="page++"
        >›</button>
      </div>

      <div class="mt-3 flex items-center justify-end gap-2">
        <!-- allow_skip=false 无忽略按钮（设计 §5.4/§7.1） -->
        <button
          v-if="allowSkip"
          type="button"
          data-test="skip-btn"
          :disabled="!editable"
          class="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          @click="skip"
        >忽略</button>
        <button
          type="button"
          data-test="submit-btn"
          :disabled="!editable"
          class="rounded-lg bg-primary-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-500 disabled:opacity-50"
          @click="submit"
        >{{ busy ? '提交中…' : '提交' }}</button>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { AnsweredFormBinding, AskFormQuestion, QuestionCardBinding, RunnerAnswerItem } from '@/types'
import type { AskSubmissionState } from '@/composables/useAgent'
import { useAgent } from '@/composables/useAgent'
import { buildAskAnswers, requiredUnmet } from '@/utils/askForm'

interface Props {
  /** 可作答卡片绑定（runnerMessages 投影自快照 waiting.form，评审 P1：绑定原 wait） */
  card?: QuestionCardBinding
  /** 历史只读已答绑定（投影自 supplementalInputs 扩展，设计 §7.1） */
  answered?: AnsweredFormBinding
  authHeaders?: Record<string, string>
}
const props = withDefaults(defineProps<Props>(), { authHeaders: () => ({}) })

const { submitAskAnswers, askSubmissionFor, askSubmissionForControl, askControlView } = useAgent()

// ---- 历史只读回显（本端无上下文时以快照重建，§5.7：不依赖本地内存）----
const answeredSubmission = computed(() =>
  props.answered ? askSubmissionForControl(props.answered.controlId).value : undefined)
const answeredControl = computed(() =>
  props.answered ? askControlView(props.answered.controlId).value : undefined)
const answeredStatusLabel = computed(() => {
  if (!props.answered) return ''
  // 本端提交上下文优先（§7.1）；刷新后无上下文时用已查询到的 control 状态
  // （consumed=已应用 / rejected=被拒），不把「任务已结束」推定成已应用（评审 P2）。
  if (answeredSubmission.value?.status === 'error') return '已提交（未应用）'
  if (answeredSubmission.value?.applied) return '已应用'
  const status = answeredControl.value?.status
  if (status === 'consumed') return '已应用'
  if (status === 'rejected') return '已提交（未应用）'
  if (status) return '已提交'
  return props.answered.terminal ? '已提交（任务已结束）' : '已提交'
})
const answeredTime = computed(() => {
  const value = props.answered?.acceptedAt
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`
})
const answeredItem = (question: AskFormQuestion): RunnerAnswerItem | undefined =>
  props.answered?.answers.find(item => item.question_id.toLowerCase() === question.id.toLowerCase())
const answeredKeys = (question: AskFormQuestion) => answeredItem(question)?.selected_keys || []
const isAnsweredKey = (question: AskFormQuestion, key: string) => answeredKeys(question).includes(key)
const answeredText = (question: AskFormQuestion) => answeredItem(question)?.text?.trim() || ''

// ---- 可作答卡片：§5.7 状态机（快照绑定 + 本端提交上下文）----
type Phase = 'input' | 'submitting' | 'accepted' | 'validation' | 'stale' | 'resume-pending' | 'closed'
const STALE_CODES = ['CONTROL_WAIT_STALE', 'CONTROL_WAIT_CONSUMED', 'CONTROL_WAIT_KIND_MISMATCH', 'IDEMPOTENCY_INPUT_MISMATCH']

const submission = computed<AskSubmissionState | undefined>(() =>
  props.card ? askSubmissionFor(props.card.runnerId, props.card.waitId).value : undefined)

const phase = computed<Phase>(() => {
  if (!props.card || props.card.terminal) return 'closed'
  const state = submission.value
  if (!state) return 'input'
  if (state.status === 'submitting') return 'submitting'
  if (state.status === 'accepted') return 'accepted'
  if (state.errorCode === 'CONTROL_EXECUTION_CLOSED') return 'closed'
  // 双端冲突以快照事实重算（评审 P2）：快照确认冲突已解除（wait 仍有效且无在途
  // 受理 control）→ 按过期态恢复可编辑，不再永久禁用。
  if (state.errorCode === 'RUNNER_RESUME_PENDING') return state.conflictCleared ? 'stale' : 'resume-pending'
  if (state.errorCode && STALE_CODES.includes(state.errorCode)) return 'stale'
  // 422 表单校验失败与受理后被拒 control：可修正重试
  return 'validation'
})

const editable = computed(() => ['input', 'validation', 'stale'].includes(phase.value))
const busy = computed(() => phase.value === 'submitting')

const statusLabel = computed(() => ({
  input: '', submitting: '提交中…', accepted: '已提交，等待应用', validation: '待修正',
  stale: '状态已更新', 'resume-pending': '已有提交待应用',
  closed: props.card?.terminal ? '任务已结束' : '任务已关闭',
}[phase.value]))
const statusTone = computed(() => {
  if (phase.value === 'accepted') return 'bg-primary-100 text-primary-700'
  if (phase.value === 'closed') return 'bg-gray-100 text-gray-500'
  return phase.value ? 'bg-amber-100 text-amber-700' : ''
})

const banner = computed(() => {
  const state = submission.value
  if (phase.value === 'closed') {
    return props.card?.terminal ? '任务已结束，问题已关闭，以下内容仅作回显。' : '任务已关闭，无法继续作答。'
  }
  if (phase.value === 'resume-pending') return '已有提交正在等待应用，请稍候；如需修改请等待本轮处理完成后再试。'
  if (phase.value === 'stale') {
    return state?.conflictCleared
      ? '另一端提交未生效，该问题已恢复有效，可重新作答。'
      : `该问题状态已更新（${state?.errorCode || '已过期'}），已刷新最新状态；若问题仍有效可直接重试。`
  }
  if (!state || state.status !== 'error') return ''
  // 表单级错误（question_id='form'，如 FORM_SKIP_NOT_ALLOWED）作卡片级提示
  const formLevel = (state.errors || []).find(item => item.question_id.toLowerCase() === 'form')
  if (formLevel) return formLevel.message || formLevel.code
  if (state.errors?.length) return '部分答案未通过校验，请按题目提示修正后重新提交。'
  return '提交未被应用，可修正后重新提交。'
})

// ---- 本地草稿（不持久化：快照恢复的是已发布表单与已提交答案，§7.1）----
const selections = reactive<Record<string, string[]>>({})
const drafts = reactive<Record<string, string>>({})
const precheck = reactive<Record<string, string>>({})
const page = ref(0)
const questionCount = computed(() => props.card?.form.questions.length || 0)
const currentQuestion = computed(() => props.card?.form.questions[page.value])

// 仅绑定身份（form_id/wait_id）变化时重置草稿；同 wait 的快照刷新（对象标识变化）
// 不重置，避免观察轮询抹掉正在输入的内容。
const boundFormId = computed(() => props.card?.form.form_id || props.card?.waitId || '')
watch(boundFormId, () => {
  page.value = 0
  for (const key of Object.keys(selections)) delete selections[key]
  for (const key of Object.keys(drafts)) delete drafts[key]
  for (const key of Object.keys(precheck)) delete precheck[key]
})
watch(questionCount, count => { if (page.value >= count) page.value = Math.max(0, count - 1) })

const isSelected = (question: AskFormQuestion, key: string) => (selections[question.id] || []).includes(key)

// 多选上限提示：仅当上限真正约束可选数量（上限小于可选项数）时显示
const multiMaxSelect = computed(() => {
  const question = currentQuestion.value
  if (!question || question.type !== 'multi') return 0
  const max = question.max_select
  const optionCount = question.options?.length ?? 0
  if (typeof max !== 'number' || max <= 0 || optionCount <= max) return 0
  return max
})
const selectedCount = computed(() => {
  const question = currentQuestion.value
  return question ? (selections[question.id] || []).length : 0
})

function toggleOption(question: AskFormQuestion, key: string) {
  if (!editable.value) return
  delete precheck[question.id]
  const current = selections[question.id] || []
  if (question.type === 'multi') {
    if (current.includes(key)) {
      selections[question.id] = current.filter(item => item !== key)
    } else {
      const max = question.max_select ?? question.options?.length ?? Infinity
      if (current.length >= max) return
      selections[question.id] = [...current, key]
    }
    return
  }
  // single：最多一项（再次点击取消）
  selections[question.id] = current.includes(key) ? [] : [key]
}

function setDraft(question: AskFormQuestion, value: string) {
  if (!editable.value) return
  delete precheck[question.id]
  drafts[question.id] = value
}

/** 逐题错误：本地 required 预检优先，其次 422 errors 逐题明细（question_id 大小写归一） */
function questionError(question: AskFormQuestion): string {
  if (precheck[question.id]) return precheck[question.id]
  const remote = submission.value?.errors?.find(item => item.question_id.toLowerCase() === question.id.toLowerCase())
  return remote ? (remote.message || remote.code) : ''
}
const currentError = computed(() => (currentQuestion.value ? questionError(currentQuestion.value) : ''))

const allowSkip = computed(() => props.card?.form.allow_skip === true)

async function submit() {
  if (!props.card || busy.value || !editable.value) return
  // required 前端预检：即时反馈并定位到首个未满足题；服务端仍权威（设计 §7.1）
  let firstUnmet = -1
  props.card.form.questions.forEach((question, index) => {
    const reason = requiredUnmet(question, selections[question.id] || [], drafts[question.id] || '')
    if (reason) {
      precheck[question.id] = reason
      if (firstUnmet < 0) firstUnmet = index
    } else {
      delete precheck[question.id]
    }
  })
  if (firstUnmet >= 0) { page.value = firstUnmet; return }
  // 结构化提交（answer 留空，canonical 服务端生成）；每次新提交由 useAgent 换新键，
  // 网络结果不明时其内部沿用原键重试（§5.7 换键规则）。
  await submitAskAnswers({
    runnerId: props.card.runnerId, waitId: props.card.waitId, targetExecutionId: props.card.targetExecutionId,
    form: props.card.form, answers: buildAskAnswers(props.card.form, selections, drafts),
  })
}

function skip() {
  if (!props.card || busy.value || !editable.value) return
  void submitAskAnswers({
    runnerId: props.card.runnerId, waitId: props.card.waitId, targetExecutionId: props.card.targetExecutionId,
    form: props.card.form, skip: true,
  })
}
</script>

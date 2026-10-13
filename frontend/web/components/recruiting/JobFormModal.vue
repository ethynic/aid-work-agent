<template>
  <BaseModal
    :model-value="modelValue"
    :title="isEdit ? '编辑职位' : '新增职位'"
    size="xl"
    accessible
    :scrollable="true"
    :mode="isEdit ? 'edit' : 'create'"
    :is-dirty="isDirty"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header-extra>
      <BaseButton size="sm" :disabled="!canSubmit || submitting" @click="handleSubmit">
        {{ submitting ? '提交中...' : '保存' }}
      </BaseButton>
    </template>
    <div class="space-y-5 min-w-0">
      <p v-if="validationError" ref="errorSummary" role="alert" tabindex="-1" class="text-sm text-danger-600">
        {{ validationError }}
      </p>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div>
          <label for="job-name" class="form-label">
            职位名称
            <span class="form-required">
              *
            </span>
          </label>
          <BaseInput id="job-name" v-model="form.job_name" placeholder="如：Python 后端工程师" />
        </div>
        <div>
          <label for="job-threshold" class="form-label">
            匹配及格线（0–100）
          </label>
          <BaseInput id="job-threshold" v-model="form.match_threshold" type="number" min="0" max="100" />
          <p class="text-xs text-muted mt-1">
            简历评分 ≥ 此线记「匹配」，默认 70
          </p>
        </div>
        <div>
          <label for="job-status" class="form-label">
            职位状态
          </label>
          <BaseSelect id="job-status" v-model="form.status">
            <option value="active">
              在招（可被选择/筛选）
            </option>
            <option value="paused">
              暂停（仅存档）
            </option>
          </BaseSelect>
        </div>
      </div>
      <div>
        <label for="job-notes" class="form-label">
          职位备注
        </label>
        <textarea
          id="job-notes"
          v-model="form.notes"
          rows="2"
          class="w-full rounded-lg border border-default bg-surface px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          placeholder="技术栈 / 团队说明等（选填）"
        />
      </div>
      <section aria-labelledby="job-ordinary-title" class="rounded-lg border border-default p-4 space-y-4">
        <h4 id="job-ordinary-title" class="text-sm font-medium text-default">
          普通筛选
        </h4>
        <p class="text-xs text-muted">
          不勾选或范围留空表示不限；经验、学历、求职状态均可多选。
        </p>
        <fieldset v-for="field in multiFields" :key="field.key" class="min-w-0">
          <legend class="form-label">
            {{ field.label }}（多选）
          </legend>
          <div class="flex flex-wrap gap-x-4 gap-y-2">
            <label class="flex items-center gap-1.5 text-sm text-default">
              <input type="checkbox" :checked="!form[field.key].length" @change="form[field.key] = []" />
              不限
            </label>
            <label v-for="option in multiOptions(field.key)" :key="option" class="flex items-center gap-1.5 text-sm text-default">
              <input
                v-model="form[field.key]"
                type="checkbox"
                :value="option"
                class="rounded border-default text-primary-600 focus:ring-primary-500/20"
              />
              {{ option }}
            </label>
          </div>
        </fieldset>
        <fieldset class="min-w-0 space-y-2">
          <legend class="form-label">
            薪资待遇（手填范围，单位 K / 月）
          </legend>
          <div v-if="form.salary_mode === 'legacy'" class="space-y-2">
            <p class="text-xs text-warning-700">
              旧薪资条件无法自动转换为范围，将原样保留；可清空或改为手填范围。
            </p>
            <label for="job-legacy-salary" class="form-label">
              兼容旧薪资条件
            </label>
            <BaseInput id="job-legacy-salary" v-model="form.salary_legacy" />
            <BaseButton size="sm" intent="secondary" @click="form.salary_mode = 'range'">
              改为手填范围
            </BaseButton>
          </div>
          <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label for="job-salary-min" class="form-label">
                薪资下限
              </label>
              <BaseInput id="job-salary-min" v-model="form.salary_min" type="number" min="0" step="any" placeholder="不限" />
            </div>
            <div>
              <label for="job-salary-max" class="form-label">
                薪资上限
              </label>
              <BaseInput id="job-salary-max" v-model="form.salary_max" type="number" min="0" step="any" placeholder="不限" />
            </div>
          </div>
          <p class="text-xs text-muted">
            可只填一端；实际 BOSS 页面仅提供档位时，执行会说明映射差异。
          </p>
        </fieldset>
      </section>
      <section aria-labelledby="job-vip-title" class="rounded-lg border border-default p-4 space-y-4">
        <h4 id="job-vip-title" class="text-sm font-medium text-default">
          VIP 专享筛选
        </h4>
        <p class="text-xs text-warning-700">
          可保存 VIP 筛选模板，执行取决于当前登录的 BOSS 账号会员权益。非会员会跳过高级条件；会员状态未知时会先询问。
        </p>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label for="job-age-min" class="form-label">
              年龄下限（16–45 岁）
            </label>
            <BaseInput id="job-age-min" v-model="form.age_min" type="number" min="16" max="45" step="1" placeholder="不限" />
          </div>
          <div>
            <label for="job-age-max" class="form-label">
              年龄上限（16–45 岁）
            </label>
            <BaseInput id="job-age-max" v-model="form.age_max" type="number" min="16" max="45" step="1" placeholder="不限" />
          </div>
          <div v-for="field in vipSingleFields" :key="field.key">
            <label :for="`job-vip-${field.key}`" class="form-label">
              {{ field.label }}（单选）
            </label>
            <BaseSelect :id="`job-vip-${field.key}`" v-model="form[field.key]">
              <option value="">
                不限
              </option>
              <option v-for="option in requirementOptions.vip_filters?.[field.key]" :key="option" :value="option">
                {{ option }}
              </option>
            </BaseSelect>
          </div>
        </div>
        <fieldset class="min-w-0">
          <legend class="form-label">
            院校（多选）
          </legend>
          <div class="flex flex-wrap gap-x-4 gap-y-2">
            <label class="flex items-center gap-1.5 text-sm text-default">
              <input type="checkbox" :checked="!form.schools.length" @change="form.schools = []" />
              不限
            </label>
            <label v-for="option in requirementOptions.vip_filters?.schools" :key="option" class="flex items-center gap-1.5 text-sm text-default">
              <input
                v-model="form.schools"
                type="checkbox"
                :value="option"
                class="rounded border-default text-primary-600 focus:ring-primary-500/20"
              />
              {{ option }}
            </label>
          </div>
          <label class="mt-3 flex items-start gap-1.5 text-sm text-default">
            <input
              v-model="form.first_degree_only"
              type="checkbox"
              :disabled="!form.schools.length"
              class="mt-1 rounded border-default text-primary-600 focus:ring-primary-500/20"
            />
            只看第一学历（第一学历为全日制本科）
          </label>
          <p v-if="!form.schools.length" class="mt-1 text-xs text-muted">
            选择院校条件后可启用第一学历限制。
          </p>
        </fieldset>
        <fieldset class="min-w-0">
          <legend class="form-label">
            牛人关键词（BOSS 页面筛选，多选）
          </legend>
          <div class="flex flex-wrap gap-x-4 gap-y-2">
            <label
              v-for="option in requirementOptions.vip_filters?.boss_keywords"
              :key="option"
              class="flex items-center gap-1.5 text-sm text-default"
            >
              <input
                v-model="form.boss_keywords"
                type="checkbox"
                :value="option"
                class="rounded border-default text-primary-600 focus:ring-primary-500/20"
              />
              {{ option }}
            </label>
          </div>
        </fieldset>
      </section>
      <section aria-labelledby="job-scoring-title" class="rounded-lg border border-default p-4 space-y-3">
        <h4 id="job-scoring-title" class="text-sm font-medium text-default">
          评分补充
        </h4>
        <div>
          <label for="job-keywords" class="form-label">
            加分关键词（LLM 评分用，逗号分隔）
          </label>
          <BaseInput id="job-keywords" v-model="form.keywords" placeholder="如：Linux, MySQL, Laravel" />
        </div>
        <div>
          <label for="job-requirements-notes" class="form-label">
            要求备注（评分加分项说明）
          </label>
          <textarea
            id="job-requirements-notes"
            v-model="form.requirements_notes"
            rows="2"
            class="w-full rounded-lg border border-default bg-surface px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            placeholder="如：接受 AI 工具深度使用者优先（选填）"
          />
        </div>
      </section>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
/**
 * 职位新增/编辑弹框（原 JobLibrary 内嵌弹框抽出共用）。
 *
 * - job=null：新增模式；job 非 null：编辑模式（表单回填该职位）
 * - 保存成功后 emit('saved', 保存后的职位详情)，由父组件决定刷新/跳转
 */
import { ref, computed, watch, nextTick } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { requirementsToForm, buildRequirements, DEFAULT_REQUIREMENT_OPTIONS, type RequirementsFormState } from './jobRequirementsForm'
import {
  createJob,
  updateJob,
  getRequirementOptions,
  type JobDetail,
  type JobListItem,
  type JobRequirementOptions,
  type JobRequirements,
} from '@/api/recruitingOperator'

const props = defineProps<{
  modelValue: boolean
  /** 编辑目标；null = 新增 */
  job: JobDetail | JobListItem | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 保存成功，携带后端返回的职位详情 */
  'saved': [job: JobDetail]
}>()

const toast = useToast()

// 匹配及格线默认值（与后端 DEFAULT_MATCH_THRESHOLD 一致）
const DEFAULT_MATCH_THRESHOLD = 70

const requirementOptions = ref<JobRequirementOptions>({ ...DEFAULT_REQUIREMENT_OPTIONS })

async function loadRequirementOptions() {
  // 拉取失败静默走本地兜底档位（DEFAULT_REQUIREMENT_OPTIONS），不弹错误打断表单
  try {
    const res = await getRequirementOptions()
    if (res.success && res.data) {
      requirementOptions.value = { ...DEFAULT_REQUIREMENT_OPTIONS, ...res.data, vip_filters: { ...DEFAULT_REQUIREMENT_OPTIONS.vip_filters!, ...res.data.vip_filters } }
    }
  } catch {
    // 静默：网络异常时本地兜底候选可用
  }
}

// 职位表单（新增/编辑共用）：requirements 各维度空值 =「不限」（提交时不传该键）
interface JobFormState extends RequirementsFormState {
  job_name: string
  notes: string
  status: 'active' | 'paused'
  match_threshold: string
}

const submitting = ref(false)

function emptyForm(): JobFormState {
  return { job_name: '', notes: '', status: 'active', match_threshold: String(DEFAULT_MATCH_THRESHOLD), ...requirementsToForm() }
}

const form = ref<JobFormState>(emptyForm())

const initialForm = ref('')
const validationError = ref('')
const errorSummary = ref<HTMLElement>()
const isDirty = computed(() => JSON.stringify(form.value) !== initialForm.value)
const multiFields = [
  { key: 'experiences' as const, label: '经验要求' },
  { key: 'educations' as const, label: '学历要求' },
  { key: 'job_statuses' as const, label: '求职状态' },
]
const vipSingleFields = [
  { key: 'activity' as const, label: '活跃度' },
  { key: 'gender' as const, label: '性别' },
  { key: 'not_viewed' as const, label: '近期没有看过' },
  { key: 'not_exchanged' as const, label: '是否与同事交换简历' },
  { key: 'job_hopping' as const, label: '跳槽频率' },
]
function multiOptions(key: 'experiences' | 'educations' | 'job_statuses'): string[] {
  const options = key === 'experiences' ? requirementOptions.value.experiences || requirementOptions.value.experience : requirementOptions.value[key] || []
  return [...new Set([...options, ...form.value[key]])].filter(value => value !== '不限')
}
watch(() => form.value.schools.length, length => { if (!length) form.value.first_degree_only = false })

const isEdit = computed(() => !!props.job)

// 弹框打开时按编辑目标回填表单
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    validationError.value = ''
    const job = props.job
    if (!job) {
      form.value = emptyForm()
      initialForm.value = JSON.stringify(form.value)
      return
    }
    const reqs: JobRequirements = job.job_requirements || {}
    form.value = {
      job_name: job.job_name,
      notes: job.notes || '',
      status: job.status,
      match_threshold: String(job.match_threshold ?? DEFAULT_MATCH_THRESHOLD),
      ...requirementsToForm(reqs),
    }
    initialForm.value = JSON.stringify(form.value)
  },
  { immediate: true },
)

const canSubmit = computed(() => form.value.job_name.trim().length > 0)

async function handleSubmit() {
  if (!canSubmit.value || submitting.value) return
  validationError.value = ''
  let requirements: JobRequirements
  try {
    requirements = buildRequirements(form.value)
  } catch (e: any) {
    validationError.value = e.message || '请检查筛选范围'
    await nextTick()
    errorSummary.value?.focus()
    return
  }
  submitting.value = true
  try {
    // 0 是合法及格线（全员及格，后端明确支持），不能用 || 兜底吞成默认 70；
    // 空输入/非法数字才回落默认；小数四舍五入（后端仅收整数）
    const rawThreshold = form.value.match_threshold
    const parsedThreshold = typeof rawThreshold === 'number' ? rawThreshold : parseFloat(String(rawThreshold))
    const threshold = Number.isFinite(parsedThreshold)
      ? Math.min(100, Math.max(0, Math.round(parsedThreshold)))
      : DEFAULT_MATCH_THRESHOLD
    if (isEdit.value && props.job) {
      // 编辑恒传 job_requirements：全空传 {} 表示清空要求（后端存 NULL）
      const res = await updateJob(props.job.id, {
        job_name: form.value.job_name.trim(),
        notes: form.value.notes.trim(),
        status: form.value.status,
        match_threshold: threshold,
        job_requirements: Object.keys(requirements).length ? requirements : {},
      })
      handleResult(res, isEdit.value)
    } else {
      const res = await createJob({
        job_name: form.value.job_name.trim(),
        notes: form.value.notes.trim() || undefined,
        status: form.value.status,
        match_threshold: threshold,
        job_requirements: Object.keys(requirements).length ? requirements : undefined,
      })
      handleResult(res, isEdit.value)
    }
  } catch (e: any) {
    toast.error(e.message || '保存异常')
  } finally {
    submitting.value = false
  }
}

function handleResult(res: { success: boolean; data?: JobDetail; error?: string }, wasEdit: boolean) {
  if (res.success && res.data) {
    toast.success(wasEdit ? '职位已更新' : '职位已创建')
    emit('update:modelValue', false)
    emit('saved', res.data)
  } else {
    toast.error(res.error || '保存失败')
  }
}

// 首次打开弹框前就把档位候选拉好（失败走本地兜底，不阻塞）
loadRequirementOptions()
</script>

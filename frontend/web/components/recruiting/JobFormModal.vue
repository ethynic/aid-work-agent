<template>
  <!-- 职位新增/编辑弹框（列表页新增与详情页编辑共用） -->
  <BaseModal :model-value="modelValue" :title="isEdit ? '编辑职位' : '新增职位'" size="lg" @update:model-value="emit('update:modelValue', $event)">
    <div class="space-y-4">
      <div>
        <label class="form-label">职位名称 <span class="form-required">*</span></label>
        <BaseInput v-model="form.job_name" placeholder="如：Python 后端工程师" />
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="form-label">匹配及格线（0-100）</label>
          <input
            v-model.number="form.match_threshold"
            type="number"
            min="0"
            max="100"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default"
          />
          <p class="text-xs text-muted mt-1">简历评分 ≥ 此线记「匹配」，默认 70</p>
        </div>
        <div>
          <label class="form-label">职位状态</label>
          <BaseSelect v-model="form.status">
            <option value="active">在招（可被选择/筛选）</option>
            <option value="paused">暂停（仅存档）</option>
          </BaseSelect>
        </div>
      </div>
      <div>
        <label class="form-label">职位备注</label>
        <textarea
          v-model="form.notes"
          rows="3"
          class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          placeholder="技术栈 / 团队说明等（选填）"
        />
      </div>
      <div class="rounded-lg border border-default p-3 space-y-3">
        <div class="text-sm font-medium text-default">职位要求（筛选项，留空 = 不限）</div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="form-label">经验</label>
            <BaseSelect v-model="form.experience">
              <option value="">不限</option>
              <option v-for="opt in requirementOptions.experience" :key="opt" :value="opt">{{ opt }}</option>
            </BaseSelect>
          </div>
          <div>
            <label class="form-label">薪资</label>
            <BaseSelect v-model="form.salary">
              <option value="">不限</option>
              <option v-for="opt in requirementOptions.salary" :key="opt" :value="opt">{{ opt }}</option>
            </BaseSelect>
          </div>
        </div>
        <div>
          <label class="form-label">学历（可多选）</label>
          <div class="flex flex-wrap gap-x-4 gap-y-2">
            <label
              v-for="opt in requirementOptions.educations"
              :key="opt"
              class="flex items-center gap-1.5 text-sm text-default"
            >
              <input
                type="checkbox"
                :value="opt"
                v-model="form.educations"
                class="rounded border-gray-300 text-primary-600 focus:ring-primary-500/20"
              />
              {{ opt }}
            </label>
          </div>
        </div>
        <div>
          <label class="form-label">加分关键词（LLM 评分用，逗号分隔）</label>
          <BaseInput v-model="form.keywords" placeholder="如：Linux, MySQL, Laravel" />
        </div>
        <div>
          <label class="form-label">要求备注（评分加分项说明）</label>
          <textarea
            v-model="form.requirements_notes"
            rows="2"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            placeholder="如：接受 AI 工具深度使用者优先（选填）"
          />
        </div>
      </div>
    </div>
    <template #footer>
      <BaseButton intent="secondary" :disabled="submitting" @click="emit('update:modelValue', false)">取消</BaseButton>
      <BaseButton :disabled="!canSubmit || submitting" @click="handleSubmit">
        {{ submitting ? '提交中...' : '保存' }}
      </BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
/**
 * 职位新增/编辑弹框（原 JobLibrary 内嵌弹框抽出共用）。
 *
 * - job=null：新增模式；job 非 null：编辑模式（表单回填该职位）
 * - 保存成功后 emit('saved', 保存后的职位详情)，由父组件决定刷新/跳转
 */
import { ref, computed, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
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

// 档位候选本地兜底（正常从后端 requirement-options 拉取，失败不阻塞表单）
const DEFAULT_REQUIREMENT_OPTIONS: JobRequirementOptions = {
  experience: ['1年以内', '1-3年', '3-5年', '5-10年', '10年以上'],
  educations: ['大专', '本科', '硕士', '博士'],
  salary: ['3-5K', '5-10K', '10-15K', '15-25K', '25-50K', '50K以上'],
}

const requirementOptions = ref<JobRequirementOptions>({ ...DEFAULT_REQUIREMENT_OPTIONS })

async function loadRequirementOptions() {
  const res = await getRequirementOptions()
  if (res.success && res.data) {
    requirementOptions.value = res.data
  }
}

// 职位表单（新增/编辑共用）：requirements 各维度空值 =「不限」（提交时不传该键）
interface JobFormState {
  job_name: string
  notes: string
  status: 'active' | 'paused'
  match_threshold: number
  experience: string
  educations: string[]
  salary: string
  keywords: string
  requirements_notes: string
}

const submitting = ref(false)

function emptyForm(): JobFormState {
  return {
    job_name: '',
    notes: '',
    status: 'active',
    match_threshold: DEFAULT_MATCH_THRESHOLD,
    experience: '',
    educations: [],
    salary: '',
    keywords: '',
    requirements_notes: '',
  }
}

const form = ref<JobFormState>(emptyForm())

const isEdit = computed(() => !!props.job)

// 弹框打开时按编辑目标回填表单
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const job = props.job
    if (!job) {
      form.value = emptyForm()
      return
    }
    const reqs: JobRequirements = job.job_requirements || {}
    form.value = {
      job_name: job.job_name,
      notes: job.notes || '',
      status: job.status,
      match_threshold: job.match_threshold ?? DEFAULT_MATCH_THRESHOLD,
      experience: reqs.experience || '',
      educations: [...(reqs.educations || [])],
      salary: reqs.salary || '',
      keywords: (reqs.keywords || []).join(', '),
      requirements_notes: reqs.notes || '',
    }
  },
  { immediate: true },
)

const canSubmit = computed(() => form.value.job_name.trim().length > 0)

// 「不限」= 该维度不设置（键缺省），不是传「不限」字符串；空串/空数组不传该键
function buildJobRequirements(): JobRequirements {
  const reqs: JobRequirements = {}
  if (form.value.experience) reqs.experience = form.value.experience
  if (form.value.educations.length) reqs.educations = [...form.value.educations]
  if (form.value.salary) reqs.salary = form.value.salary
  const keywords = form.value.keywords.split(/[,，]/).map(s => s.trim()).filter(Boolean)
  if (keywords.length) reqs.keywords = keywords
  if (form.value.requirements_notes.trim()) reqs.notes = form.value.requirements_notes.trim()
  return reqs
}

async function handleSubmit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    // 0 是合法及格线（全员及格，后端明确支持），不能用 || 兜底吞成默认 70；
    // 空输入/非法数字才回落默认；小数四舍五入（后端仅收整数）
    const rawThreshold = form.value.match_threshold
    const parsedThreshold = typeof rawThreshold === 'number' ? rawThreshold : parseFloat(String(rawThreshold))
    const threshold = Number.isFinite(parsedThreshold)
      ? Math.min(100, Math.max(0, Math.round(parsedThreshold)))
      : DEFAULT_MATCH_THRESHOLD
    const requirements = buildJobRequirements()
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

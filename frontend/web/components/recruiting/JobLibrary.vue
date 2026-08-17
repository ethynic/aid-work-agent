<template>
  <!-- 职位库页面：维护职位名称 + 该职位的常用沟通话术库（BOSS 聊天模板，固定四分类） -->
  <div class="page-container p-5">
    <div class="page-content flex-1 flex gap-4 min-h-0">
      <!-- ============== 左侧：职位列表 ============== -->
      <div class="w-72 flex-shrink-0 flex flex-col gap-3 min-h-0">
        <div class="page-toolbar">
          <div class="page-toolbar-left">
            <span class="text-sm font-semibold text-default">职位列表</span>
            <span class="text-muted text-xs">{{ jobs.length }} 个</span>
          </div>
          <div class="page-toolbar-right">
            <BaseButton size="sm" @click="openCreateJob">新增职位</BaseButton>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto rounded-lg border border-default bg-white">
          <div v-if="loadingJobs" class="p-4 text-muted text-sm">加载中...</div>
          <div v-else-if="!jobs.length" class="p-4 text-muted text-sm">暂无职位</div>
          <div
            v-for="job in jobs"
            :key="job.id"
            class="px-4 py-3 cursor-pointer border-b border-default last:border-b-0 transition-colors"
            :class="[
              job.id === selectedJobId ? 'bg-primary-50' : 'hover:bg-gray-50',
              job.status === 'paused' ? 'opacity-60' : '',
            ]"
            @click="selectJob(job.id)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="text-sm font-medium text-default truncate" :title="job.job_name">{{ job.job_name }}</span>
              <BaseBadge :intent="job.status === 'active' ? 'success' : 'neutral'">
                {{ job.status === 'active' ? '在招' : '暂停' }}
              </BaseBadge>
            </div>
            <div class="mt-1 flex items-center gap-2 text-xs text-muted">
              <span>简历 {{ job.resume_count }} · 匹配 {{ job.matched_count }}</span>
              <span>· 话术 {{ job.script_count }} 条</span>
            </div>
            <div v-if="job.categories.length" class="mt-1 flex flex-wrap gap-1">
              <BaseBadge
                v-for="cat in job.categories"
                :key="cat"
                :intent="categoryIntent(cat)"
              >{{ cat }}</BaseBadge>
            </div>
          </div>
        </div>
      </div>

      <!-- ============== 右侧：选中职位的话术（按分类分组卡片） ============== -->
      <div class="flex-1 flex flex-col gap-3 min-h-0">
        <div class="page-toolbar">
          <div class="page-toolbar-left flex-wrap">
            <span class="text-sm font-medium text-default">
              {{ selectedJob ? selectedJob.job_name : '未选择职位' }}
            </span>
            <span v-if="selectedJob" class="text-muted text-xs">共 {{ selectedJob.script_count }} 条话术</span>
          </div>
          <div class="page-toolbar-right" v-if="selectedJob">
            <BaseButton size="sm" intent="secondary" @click="openEditJob">编辑职位</BaseButton>
            <BaseButton size="sm" intent="danger-ghost" @click="handleDeleteJob">删除职位</BaseButton>
            <BaseButton size="sm" @click="openCreateScript">新增话术</BaseButton>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto pr-1 space-y-4">
          <template v-if="selectedJob">
            <!-- 职位设置卡（简历-职位匹配 Phase 5：状态切换 + 匹配阈值 + 要求摘要） -->
            <div class="rounded-lg border border-default bg-white p-4">
              <div class="text-sm font-semibold text-default mb-2">职位设置</div>
              <div class="flex items-center gap-3 text-sm">
                <span class="text-muted w-20 flex-shrink-0">状态</span>
                <BaseBadge :intent="selectedJob.status === 'active' ? 'success' : 'neutral'">
                  {{ selectedJob.status === 'active' ? '在招' : '暂停' }}
                </BaseBadge>
                <BaseButton
                  size="sm"
                  :intent="selectedJob.status === 'active' ? 'secondary' : 'primary'"
                  :disabled="togglingStatus"
                  @click="handleToggleStatus"
                >
                  {{ togglingStatus ? '切换中...' : (selectedJob.status === 'active' ? '暂停职位' : '恢复在招') }}
                </BaseButton>
                <span class="text-xs text-muted">暂停后不再出现在筛选/打招呼的可选职位里</span>
              </div>
              <div class="mt-2 flex items-center gap-3 text-sm">
                <span class="text-muted w-20 flex-shrink-0">匹配及格线</span>
                <span class="text-default">{{ selectedJob.match_threshold }} 分</span>
                <span class="text-xs text-muted">≥ 此线记「匹配」，50-69 记「接近」，&lt;50 记「不匹配」</span>
              </div>
              <div class="mt-2 flex items-center gap-3 text-sm">
                <span class="text-muted w-20 flex-shrink-0">职位要求</span>
                <span class="text-default">{{ requirementsSummary }}</span>
                <BaseButton intent="ghost" size="sm" class="whitespace-nowrap" @click="openEditJob">编辑</BaseButton>
              </div>
            </div>

            <!-- 职位备注卡 -->
            <div v-if="selectedJob.notes" class="rounded-lg border border-default bg-white p-4">
              <div class="text-sm font-semibold text-default mb-1">职位备注</div>
              <div class="text-sm text-default whitespace-pre-wrap">{{ selectedJob.notes }}</div>
            </div>

            <!-- 分类分组话术卡 -->
            <div
              v-for="group in selectedJob.script_groups"
              :key="group.category"
              class="rounded-lg border border-default bg-white p-4"
            >
              <div class="flex items-center gap-2 mb-3">
                <BaseBadge :intent="categoryIntent(group.category)">{{ group.category }}</BaseBadge>
                <span class="text-xs text-muted">{{ group.scripts.length }} 条</span>
              </div>
              <div class="space-y-3">
                <div
                  v-for="script in group.scripts"
                  :key="script.id"
                  class="rounded-md border border-default bg-gray-50 p-3"
                >
                  <div class="flex items-start justify-between gap-2 mb-1.5">
                    <span class="text-sm font-medium text-default">{{ script.title }}</span>
                    <div class="flex gap-1 flex-shrink-0">
                      <BaseButton intent="secondary" size="sm" class="whitespace-nowrap text-xs" @click="copyScript(script)">复制</BaseButton>
                      <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openEditScript(script)">编辑</BaseButton>
                      <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleDeleteScript(script)">删除</BaseButton>
                    </div>
                  </div>
                  <p class="text-sm text-default leading-relaxed whitespace-pre-wrap">{{ script.content }}</p>
                </div>
              </div>
            </div>
            <div v-if="!selectedJob.script_count" class="rounded-lg border border-default bg-white p-4 text-muted text-sm">
              暂无话术，点击右上角「新增话术」添加
            </div>
          </template>
          <div v-else class="rounded-lg border border-default bg-white p-4 text-muted text-sm">
            从左侧选择一个职位查看其沟通话术
          </div>
        </div>
      </div>
    </div>

    <!-- ============== 职位新增/编辑弹框 ============== -->
    <BaseModal v-model="showJobModal" :title="jobForm.id ? '编辑职位' : '新增职位'" size="lg">
      <div class="space-y-4">
        <div>
          <label class="form-label">职位名称 <span class="form-required">*</span></label>
          <BaseInput v-model="jobForm.job_name" placeholder="如：PHP开发工程师（Laravel）" />
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">匹配及格线（0-100）</label>
            <input
              v-model.number="jobForm.match_threshold"
              type="number"
              min="0"
              max="100"
              class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default"
            />
            <p class="text-xs text-muted mt-1">简历评分 ≥ 此线记「匹配」，默认 70</p>
          </div>
          <div>
            <label class="form-label">职位状态</label>
            <BaseSelect v-model="jobForm.status">
              <option value="active">在招（可被选择/筛选）</option>
              <option value="paused">暂停（仅存档）</option>
            </BaseSelect>
          </div>
        </div>
        <div>
          <label class="form-label">职位备注</label>
          <textarea
            v-model="jobForm.notes"
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
              <BaseSelect v-model="jobForm.experience">
                <option value="">不限</option>
                <option v-for="opt in requirementOptions.experience" :key="opt" :value="opt">{{ opt }}</option>
              </BaseSelect>
            </div>
            <div>
              <label class="form-label">薪资</label>
              <BaseSelect v-model="jobForm.salary">
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
                  v-model="jobForm.educations"
                  class="rounded border-gray-300 text-primary-600 focus:ring-primary-500/20"
                />
                {{ opt }}
              </label>
            </div>
          </div>
          <div>
            <label class="form-label">加分关键词（LLM 评分用，逗号分隔）</label>
            <BaseInput v-model="jobForm.keywords" placeholder="如：Linux, MySQL, Laravel" />
          </div>
          <div>
            <label class="form-label">要求备注（评分加分项说明）</label>
            <textarea
              v-model="jobForm.requirements_notes"
              rows="2"
              class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
              placeholder="如：接受 AI 工具深度使用者优先（选填）"
            />
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="submitting" @click="showJobModal = false">取消</BaseButton>
        <BaseButton :disabled="!canSubmitJob || submitting" @click="handleSubmitJob">
          {{ submitting ? '提交中...' : '保存' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- ============== 话术新增/编辑弹框 ============== -->
    <BaseModal v-model="showScriptModal" :title="scriptForm.id ? '编辑话术' : '新增话术'">
      <div class="space-y-4">
        <div>
          <label class="form-label">分类 <span class="form-required">*</span></label>
          <BaseSelect v-model="scriptForm.category">
            <option v-for="cat in JOB_SCRIPT_CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
          </BaseSelect>
        </div>
        <div>
          <label class="form-label">标题 <span class="form-required">*</span></label>
          <BaseInput v-model="scriptForm.title" placeholder="分类内小标题，如：开场·技术栈匹配" />
        </div>
        <div>
          <label class="form-label">话术内容 <span class="form-required">*</span></label>
          <textarea
            v-model="scriptForm.content"
            rows="6"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            placeholder="发给候选人的完整话术；可用 {{占位符}}，复制后手动替换"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="submitting" @click="showScriptModal = false">取消</BaseButton>
        <BaseButton :disabled="!canSubmitScript || submitting" @click="handleSubmitScript">
          {{ submitting ? '提交中...' : '保存' }}
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  listJobs,
  getRequirementOptions,
  createJob,
  getJob,
  updateJob,
  deleteJob,
  createJobScript,
  updateJobScript,
  deleteJobScript,
  JOB_SCRIPT_CATEGORIES,
  type JobListItem,
  type JobDetail,
  type JobScript,
  type JobRequirementOptions,
  type JobRequirements,
} from '@/api/recruitingOperator'

const toast = useToast()

// 分类徽标色（与简历状态徽标同风格）
function categoryIntent(category: string): 'info' | 'success' | 'warning' | 'neutral' {
  const map: Record<string, 'info' | 'success' | 'warning' | 'neutral'> = {
    初次开场: 'info',
    了解摸底: 'neutral',
    追问细节: 'warning',
    邀约推进: 'success',
  }
  return map[category] || 'neutral'
}

// ============== 职位列表 ==============

const jobs = ref<JobListItem[]>([])
const selectedJobId = ref('')
const selectedDetail = ref<JobDetail | null>(null)
const loadingJobs = ref(false)

// 右侧展示用当前选中职位的完整详情（含 script_groups）
const selectedJob = computed(() => selectedDetail.value)

// selectId：本次加载后要选中的职位 id（缺省维持当前选中，无选中则取第一个）
async function loadJobs(selectId?: string) {
  loadingJobs.value = true
  try {
    const res = await listJobs()
    if (res.success && res.data) {
      jobs.value = res.data.items
      let target = selectId && jobs.value.some(j => j.id === selectId)
        ? selectId
        : (jobs.value.some(j => j.id === selectedJobId.value) ? selectedJobId.value : '')
      if (!target) target = jobs.value[0]?.id || ''
      selectedJobId.value = target
      if (target) {
        await loadJobDetail(target)
      } else {
        selectedDetail.value = null
      }
    } else {
      toast.error(res.error || '加载职位列表失败')
    }
  } finally {
    loadingJobs.value = false
  }
}

async function loadJobDetail(jobId: string) {
  const res = await getJob(jobId)
  if (res.success && res.data) {
    selectedDetail.value = res.data
  } else {
    toast.error(res.error || '加载职位详情失败')
  }
}

function selectJob(jobId: string) {
  if (selectedJobId.value === jobId) return
  selectedJobId.value = jobId
  if (jobId) loadJobDetail(jobId)
}

// ============== 职位新增/编辑/删除 ==============

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
  id: string
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

const showJobModal = ref(false)
const submitting = ref(false)
const togglingStatus = ref(false)
const jobForm = ref<JobFormState>(emptyJobForm())

function emptyJobForm(): JobFormState {
  return {
    id: '',
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

const canSubmitJob = computed(() => jobForm.value.job_name.trim().length > 0)

function openCreateJob() {
  jobForm.value = emptyJobForm()
  showJobModal.value = true
}

function openEditJob() {
  if (!selectedJob.value) return
  const reqs: JobRequirements = selectedJob.value.job_requirements || {}
  jobForm.value = {
    id: selectedJob.value.id,
    job_name: selectedJob.value.job_name,
    notes: selectedJob.value.notes || '',
    status: selectedJob.value.status,
    match_threshold: selectedJob.value.match_threshold ?? DEFAULT_MATCH_THRESHOLD,
    experience: reqs.experience || '',
    educations: [...(reqs.educations || [])],
    salary: reqs.salary || '',
    keywords: (reqs.keywords || []).join(', '),
    requirements_notes: reqs.notes || '',
  }
  showJobModal.value = true
}

// 要求摘要一行（设置卡展示；与 boss_jobs_list options.description 拼法一致）
const requirementsSummary = computed(() => {
  const job = selectedJob.value
  if (!job) return ''
  const reqs = job.job_requirements
  if (!reqs) return '未配置'
  const dims: string[] = []
  if (reqs.experience) dims.push(reqs.experience)
  if (reqs.educations?.length) dims.push(reqs.educations.join('、'))
  if (reqs.salary) dims.push(reqs.salary)
  return dims.length ? dims.join(' / ') : '未配置'
})

// 「不限」= 该维度不设置（键缺省），不是传「不限」字符串；空串/空数组不传该键
function buildJobRequirements(): JobRequirements {
  const reqs: JobRequirements = {}
  if (jobForm.value.experience) reqs.experience = jobForm.value.experience
  if (jobForm.value.educations.length) reqs.educations = [...jobForm.value.educations]
  if (jobForm.value.salary) reqs.salary = jobForm.value.salary
  const keywords = jobForm.value.keywords.split(/[,，]/).map(s => s.trim()).filter(Boolean)
  if (keywords.length) reqs.keywords = keywords
  if (jobForm.value.requirements_notes.trim()) reqs.notes = jobForm.value.requirements_notes.trim()
  return reqs
}

async function handleSubmitJob() {
  if (!canSubmitJob.value) return
  submitting.value = true
  try {
    const isEdit = !!jobForm.value.id
    // 0 是合法及格线（全员及格，后端明确支持），不能用 || 兜底吞成默认 70；
    // 空输入/非法数字才回落默认；小数四舍五入（后端仅收整数）
    const rawThreshold = jobForm.value.match_threshold
    const parsedThreshold = typeof rawThreshold === 'number' ? rawThreshold : parseFloat(String(rawThreshold))
    const threshold = Number.isFinite(parsedThreshold)
      ? Math.min(100, Math.max(0, Math.round(parsedThreshold)))
      : DEFAULT_MATCH_THRESHOLD
    const requirements = buildJobRequirements()
    if (isEdit) {
      // 编辑恒传 job_requirements：全空传 {} 表示清空要求（后端存 NULL）
      const res = await updateJob(jobForm.value.id, {
        job_name: jobForm.value.job_name.trim(),
        notes: jobForm.value.notes.trim(),
        status: jobForm.value.status,
        match_threshold: threshold,
        job_requirements: Object.keys(requirements).length ? requirements : {},
      })
      await handleJobSubmitResult(res, isEdit, jobForm.value.id)
    } else {
      const res = await createJob({
        job_name: jobForm.value.job_name.trim(),
        notes: jobForm.value.notes.trim() || undefined,
        status: jobForm.value.status,
        match_threshold: threshold,
        job_requirements: Object.keys(requirements).length ? requirements : undefined,
      })
      await handleJobSubmitResult(res, isEdit, res.data?.id)
    }
  } catch (e: any) {
    toast.error(e.message || '保存异常')
  } finally {
    submitting.value = false
  }
}

async function handleJobSubmitResult(
  res: { success: boolean; data?: JobDetail; error?: string },
  isEdit: boolean,
  jobId?: string,
) {
  if (res.success) {
    toast.success(isEdit ? '职位已更新' : '职位已创建')
    showJobModal.value = false
    await loadJobs(jobId || undefined)
  } else {
    toast.error(res.error || '保存失败')
  }
}

// 状态互切（设置卡）：active ↔ paused，confirm 后 updateJob
async function handleToggleStatus() {
  const job = selectedJob.value
  if (!job || togglingStatus.value) return
  const target = job.status === 'active' ? 'paused' : 'active'
  const actionText = target === 'paused'
    ? '暂停？暂停后该职位不再出现在筛选/打招呼的可选职位里（简历与统计保留）'
    : '恢复在招？'
  if (!confirm(`确定将「${job.job_name}」${actionText}`)) return
  togglingStatus.value = true
  try {
    const res = await updateJob(job.id, { status: target })
    if (res.success) {
      toast.success(target === 'paused' ? '职位已暂停' : '职位已恢复在招')
      await loadJobs(job.id)
    } else {
      toast.error(res.error || '状态更新失败')
    }
  } catch (e: any) {
    toast.error(e.message || '状态更新异常')
  } finally {
    togglingStatus.value = false
  }
}

async function handleDeleteJob() {
  const job = selectedJob.value
  if (!job) return
  if (!confirm(`确定删除职位「${job.job_name}」？其 ${job.script_count} 条话术将一并删除。`)) return
  const res = await deleteJob(job.id)
  if (res.success) {
    toast.success('职位已删除')
    selectedJobId.value = ''
    await loadJobs()
  } else {
    toast.error(res.error || '删除失败')
  }
}

// ============== 话术新增/编辑/删除/复制 ==============

const showScriptModal = ref(false)
const scriptForm = ref<{ id: string; category: string; title: string; content: string }>({
  id: '', category: JOB_SCRIPT_CATEGORIES[0], title: '', content: '',
})

const canSubmitScript = computed(
  () => scriptForm.value.title.trim().length > 0 && scriptForm.value.content.trim().length > 0
)

function openCreateScript() {
  if (!selectedJob.value) return
  scriptForm.value = { id: '', category: JOB_SCRIPT_CATEGORIES[0], title: '', content: '' }
  showScriptModal.value = true
}

function openEditScript(script: JobScript) {
  scriptForm.value = {
    id: script.id,
    category: script.category,
    title: script.title,
    content: script.content,
  }
  showScriptModal.value = true
}

async function handleSubmitScript() {
  if (!canSubmitScript.value || !selectedJob.value) return
  submitting.value = true
  try {
    const jobId = selectedJob.value.id
    const payload = {
      category: scriptForm.value.category,
      title: scriptForm.value.title.trim(),
      content: scriptForm.value.content.trim(),
    }
    const res = scriptForm.value.id
      ? await updateJobScript(scriptForm.value.id, payload)
      : await createJobScript(jobId, payload)
    if (res.success) {
      toast.success(scriptForm.value.id ? '话术已更新' : '话术已创建')
      showScriptModal.value = false
      await loadJobs(jobId)
    } else {
      toast.error(res.error || '保存失败')
    }
  } catch (e: any) {
    toast.error(e.message || '保存异常')
  } finally {
    submitting.value = false
  }
}

async function handleDeleteScript(script: JobScript) {
  if (!confirm(`确定删除话术「${script.title}」？`)) return
  const res = await deleteJobScript(script.id)
  if (res.success) {
    toast.success('话术已删除')
    if (selectedJobId.value) await loadJobs(selectedJobId.value)
  } else {
    toast.error(res.error || '删除失败')
  }
}

// 一键复制正文（话术里的 {{占位符}} 复制后手动替换）
async function copyScript(script: JobScript) {
  try {
    await navigator.clipboard.writeText(script.content)
    toast.success('话术已复制，记得替换 {{占位符}}')
  } catch {
    toast.error('复制失败，请手动选择复制')
  }
}

onMounted(() => {
  loadJobs()
  loadRequirementOptions()
})
</script>

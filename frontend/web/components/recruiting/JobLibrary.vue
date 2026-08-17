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
            :class="job.id === selectedJobId ? 'bg-primary-50' : 'hover:bg-gray-50'"
            @click="selectJob(job.id)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="text-sm font-medium text-default truncate" :title="job.job_name">{{ job.job_name }}</span>
              <span class="text-xs text-muted flex-shrink-0">{{ job.script_count }} 条</span>
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
    <BaseModal v-model="showJobModal" :title="jobForm.id ? '编辑职位' : '新增职位'">
      <div class="space-y-4">
        <div>
          <label class="form-label">职位名称 <span class="form-required">*</span></label>
          <BaseInput v-model="jobForm.job_name" placeholder="如：PHP开发工程师（Laravel）" />
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

const showJobModal = ref(false)
const submitting = ref(false)
const jobForm = ref<{ id: string; job_name: string; notes: string }>({ id: '', job_name: '', notes: '' })

const canSubmitJob = computed(() => jobForm.value.job_name.trim().length > 0)

function openCreateJob() {
  jobForm.value = { id: '', job_name: '', notes: '' }
  showJobModal.value = true
}

function openEditJob() {
  if (!selectedJob.value) return
  jobForm.value = { id: selectedJob.value.id, job_name: selectedJob.value.job_name, notes: selectedJob.value.notes || '' }
  showJobModal.value = true
}

async function handleSubmitJob() {
  if (!canSubmitJob.value) return
  submitting.value = true
  try {
    const isEdit = !!jobForm.value.id
    const res = isEdit
      ? await updateJob(jobForm.value.id, { job_name: jobForm.value.job_name.trim(), notes: jobForm.value.notes.trim() })
      : await createJob({ job_name: jobForm.value.job_name.trim(), notes: jobForm.value.notes.trim() || undefined })
    if (res.success) {
      toast.success(isEdit ? '职位已更新' : '职位已创建')
      showJobModal.value = false
      if (!isEdit && res.data) selectedJobId.value = res.data.id
      await loadJobs()
    } else {
      toast.error(res.error || '保存失败')
    }
  } catch (e: any) {
    toast.error(e.message || '保存异常')
  } finally {
    submitting.value = false
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
})
</script>

<template>
  <!-- 职位详情页：基本信息 / 沟通话术 / 关联简历 三个 tab（从 JobLibrary 同页详情拆出，路由化） -->
  <div class="page-container p-5">
    <!-- ============== 顶部工具栏 ============== -->
    <div class="page-toolbar">
      <div class="page-toolbar-left flex-wrap">
        <BaseButton size="sm" intent="secondary" @click="goBack">← 返回列表</BaseButton>
        <template v-if="job">
          <span class="text-sm font-medium text-default" :title="job.job_name">{{ job.job_name }}</span>
          <BaseBadge :intent="job.status === 'active' ? 'success' : 'neutral'">
            {{ job.status === 'active' ? '在招' : '暂停' }}
          </BaseBadge>
          <BaseButton
            size="sm"
            :intent="job.status === 'active' ? 'secondary' : 'primary'"
            :disabled="togglingStatus"
            @click="handleToggleStatus"
          >
            {{ togglingStatus ? '切换中...' : (job.status === 'active' ? '暂停职位' : '恢复在招') }}
          </BaseButton>
          <BaseButton size="sm" @click="showEditModal = true">编辑</BaseButton>
          <BaseButton size="sm" intent="danger-ghost" @click="handleDeleteJob">删除</BaseButton>
        </template>
      </div>
    </div>

    <div v-if="loading" class="page-content p-4 text-muted text-sm">加载中...</div>

    <template v-else-if="job">
      <!-- ============== Tab 切换（按钮式 tab bar，与 ConnectionCenter 同模式） ============== -->
      <div class="flex-shrink-0 border-b border-default">
        <div class="flex gap-6">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            :class="[
              'py-2.5 text-sm font-medium border-b-2 transition-colors',
              activeTab === tab.key
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover'
            ]"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>

      <!-- ============== Tab 内容 ============== -->
      <div class="flex-1 min-h-0 overflow-y-auto pr-1 pt-4 space-y-4">
        <!-- ── 基本信息 ── -->
        <template v-if="activeTab === 'basic-info'">
          <div class="rounded-lg border border-default bg-white p-4 space-y-3">
            <div class="flex items-center gap-3 text-sm">
              <span class="text-muted w-20 flex-shrink-0">匹配及格线</span>
              <span class="text-default">{{ job.match_threshold }} 分</span>
              <span class="text-xs text-muted">≥ 此线记「匹配」，50-69 记「接近」，&lt;50 记「不匹配」</span>
            </div>
            <div class="pt-3 border-t border-default">
              <div class="text-sm font-medium text-default mb-2">职位要求</div>
              <div v-if="requirementEntries.length" class="space-y-1.5">
                <div v-for="[label, value] in requirementEntries" :key="label" class="flex text-sm">
                  <span class="text-muted w-24 flex-shrink-0">{{ label }}</span>
                  <span class="text-default break-all">{{ value }}</span>
                </div>
              </div>
              <div v-else class="text-muted text-sm">未配置</div>
            </div>
            <div class="pt-3 border-t border-default">
              <div class="text-sm font-medium text-default mb-1">职位备注</div>
              <div v-if="job.notes" class="text-sm text-default whitespace-pre-wrap">{{ job.notes }}</div>
              <div v-else class="text-muted text-sm">未填写</div>
            </div>
          </div>
        </template>

        <!-- ── 沟通话术（按分类分组卡片） ── -->
        <template v-else-if="activeTab === 'scripts'">
          <div class="flex justify-end">
            <BaseButton size="sm" @click="openCreateScript">新增话术</BaseButton>
          </div>
          <div
            v-for="group in job.script_groups"
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
          <div v-if="!job.script_count" class="rounded-lg border border-default bg-white p-4 text-muted text-sm">
            暂无话术，点击右上角「新增话术」添加
          </div>
        </template>

        <!-- ── 关联简历（job_id 硬关联，服务端筛选 + 分页） ── -->
        <template v-else-if="activeTab === 'linked-resumes'">
          <div class="table-scroll-wrapper">
            <BaseTable :columns="resumeColumns" :data="linkedResumes" row-key="id">
              <template #candidate_name="{ row }">{{ row.candidate_name || '-' }}</template>
              <template #match="{ row }">
                <BaseBadge :intent="matchIntent(row.match_status)">{{ matchText(row) }}</BaseBadge>
              </template>
              <template #status="{ row }">
                <BaseBadge :intent="statusIntent(row.status)">{{ STATUS_LABELS[row.status] || row.status }}</BaseBadge>
              </template>
              <template #fetched_at="{ row }">{{ formatDate(row.fetched_at) }}</template>
              <template #actions="{ row }">
                <BaseButton
                  intent="ghost"
                  size="sm"
                  class="whitespace-nowrap text-xs"
                  @click="goResumeDetail(row.id)"
                >查看</BaseButton>
              </template>
              <template v-if="loadingResumes" #empty>加载中...</template>
              <template v-else #empty>该职位暂无关联简历</template>
            </BaseTable>
          </div>
          <BasePagination
            v-model:current-page="resumePage"
            v-model:page-size="resumePageSize"
            :total="resumeTotal"
            :show-size-changer="false"
            @change="loadLinkedResumes"
          />
        </template>
      </div>
    </template>

    <div v-else class="page-content p-4 text-muted text-sm">职位不存在或已被删除</div>

    <!-- ============== 职位编辑弹框 ============== -->
    <JobFormModal v-model="showEditModal" :job="job" @saved="onJobSaved" />

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
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import JobFormModal from './JobFormModal.vue'
import {
  getJob,
  updateJob,
  deleteJob,
  createJobScript,
  updateJobScript,
  deleteJobScript,
  listResumes,
  JOB_SCRIPT_CATEGORIES,
  type JobDetail,
  type JobScript,
  type ResumeListItem,
} from '@/api/recruitingOperator'
import {
  STATUS_LABELS,
  statusIntent,
  matchIntent,
  matchText,
  formatDate,
  jobListPath,
  resumeDetailPath,
} from './recruitingDisplay'

const toast = useToast()
const route = useRoute()
const router = useRouter()

// 路由参数 jobId（demo 与租户前台同形）
const jobId = computed(() => String(route.params.jobId || ''))

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

// ============== 职位详情加载 ==============

const job = ref<JobDetail | null>(null)
const loading = ref(true)

async function loadJob() {
  loading.value = true
  try {
    const res = await getJob(jobId.value)
    if (res.success && res.data) {
      job.value = res.data
    } else {
      job.value = null
      toast.error(res.error || '加载职位详情失败')
    }
  } finally {
    loading.value = false
  }
}

// 编辑弹框保存成功：用返回的最新详情刷新视图
function onJobSaved(saved: JobDetail) {
  job.value = saved
}

function goBack() {
  router.push(jobListPath(route))
}

// ============== Tab 切换 ==============

type TabKey = 'basic-info' | 'scripts' | 'linked-resumes'

const activeTab = ref<TabKey>('basic-info')

// tab 标签带计数（话术数来自职位详情，关联简历数来自分页查询 total）
const tabs = computed<Array<{ key: TabKey; label: string }>>(() => [
  { key: 'basic-info', label: '基本信息' },
  { key: 'scripts', label: `沟通话术(${job.value?.script_count ?? 0})` },
  { key: 'linked-resumes', label: `关联简历(${resumeTotal.value})` },
])

// ============== 基本信息 tab ==============

// 要求明细行（未配置的维度不出现；全部未配置显示「未配置」）
const requirementEntries = computed<[string, string][]>(() => {
  const reqs = job.value?.job_requirements
  if (!reqs) return []
  const entries: [string, string][] = []
  if (reqs.experience) entries.push(['经验', reqs.experience])
  if (reqs.educations?.length) entries.push(['学历', reqs.educations.join('、')])
  if (reqs.salary) entries.push(['薪资', reqs.salary])
  if (reqs.keywords?.length) entries.push(['加分关键词', reqs.keywords.join('、')])
  if (reqs.notes) entries.push(['要求备注', reqs.notes])
  return entries
})

// ============== 状态切换 / 删除 ==============

const togglingStatus = ref(false)

// 状态互切：active ↔ paused，confirm 后 updateJob
async function handleToggleStatus() {
  const current = job.value
  if (!current || togglingStatus.value) return
  const target = current.status === 'active' ? 'paused' : 'active'
  const actionText = target === 'paused'
    ? '暂停？暂停后该职位不再出现在筛选/打招呼的可选职位里（简历与统计保留）'
    : '恢复在招？'
  if (!confirm(`确定将「${current.job_name}」${actionText}`)) return
  togglingStatus.value = true
  try {
    const res = await updateJob(current.id, { status: target })
    if (res.success && res.data) {
      toast.success(target === 'paused' ? '职位已暂停' : '职位已恢复在招')
      job.value = res.data
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
  const current = job.value
  if (!current) return
  if (!confirm(`确定删除职位「${current.job_name}」？其 ${current.script_count} 条话术将一并删除。`)) return
  const res = await deleteJob(current.id)
  if (res.success) {
    toast.success('职位已删除')
    router.push(jobListPath(route))
  } else {
    toast.error(res.error || '删除失败')
  }
}

const showEditModal = ref(false)

// ============== 关联简历 tab ==============

const linkedResumes = ref<ResumeListItem[]>([])
const resumeTotal = ref(0)
const resumePage = ref(1)
const resumePageSize = ref(20)
const loadingResumes = ref(false)

async function loadLinkedResumes() {
  if (!jobId.value) return
  loadingResumes.value = true
  try {
    const res = await listResumes({
      page: resumePage.value,
      page_size: resumePageSize.value,
      job_id: jobId.value,
    })
    if (res.success && res.data) {
      linkedResumes.value = res.data.items
      resumeTotal.value = res.data.total
    } else {
      toast.error(res.error || '加载关联简历失败')
    }
  } finally {
    loadingResumes.value = false
  }
}

const resumeColumns = [
  { key: 'candidate_name', label: '候选人', width: '160px' },
  { key: 'match', label: '匹配度', width: '90px' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'fetched_at', label: '获取日期', width: '120px' },
  { key: 'actions', label: '操作', width: '90px' },
]

function goResumeDetail(id: number) {
  router.push(resumeDetailPath(route, id))
}

// ============== 话术新增/编辑/删除/复制 ==============

const showScriptModal = ref(false)
const submitting = ref(false)
const scriptForm = ref<{ id: string; category: string; title: string; content: string }>({
  id: '', category: JOB_SCRIPT_CATEGORIES[0], title: '', content: '',
})

const canSubmitScript = computed(
  () => scriptForm.value.title.trim().length > 0 && scriptForm.value.content.trim().length > 0
)

function openCreateScript() {
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
  if (!canSubmitScript.value || !job.value) return
  submitting.value = true
  try {
    const payload = {
      category: scriptForm.value.category,
      title: scriptForm.value.title.trim(),
      content: scriptForm.value.content.trim(),
    }
    const res = scriptForm.value.id
      ? await updateJobScript(scriptForm.value.id, payload)
      : await createJobScript(job.value.id, payload)
    if (res.success) {
      toast.success(scriptForm.value.id ? '话术已更新' : '话术已创建')
      showScriptModal.value = false
      await loadJob()
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
    await loadJob()
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
  loadJob()
  loadLinkedResumes()
})
</script>

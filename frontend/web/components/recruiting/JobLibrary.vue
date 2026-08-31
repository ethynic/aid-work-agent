<template>
  <!-- 职位管理列表页：职位浏览/客户端过滤 + 新增入口；编辑/删除/话术在详情页（JobDetail） -->
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
      <div class="page-toolbar">
        <div class="page-toolbar-left flex-wrap">
          <span class="text-sm font-semibold text-default">职位列表</span>
          <span class="text-muted text-xs">{{ filteredJobs.length }} 个</span>
          <!-- 职位量小，列表接口无搜索参数，关键字/状态在前端过滤 -->
          <BaseInput
            v-model="keyword"
            placeholder="搜索职位名称"
            size="sm"
            class="w-44"
          />
          <BaseSelect v-model="statusFilter" size="sm" class="w-32">
            <option value="">全部状态</option>
            <option value="active">在招</option>
            <option value="paused">暂停</option>
          </BaseSelect>
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" @click="openCreateJob">新增职位</BaseButton>
        </div>
      </div>

      <div class="table-scroll-wrapper flex-1">
        <BaseTable :columns="columns" :data="filteredJobs" row-key="id" :on-row-click="goDetail">
          <template #job_name="{ row }">
            <span class="text-sm font-medium text-default" :title="row.job_name">{{ row.job_name }}</span>
          </template>
          <template #status="{ row }">
            <BaseBadge :intent="row.status === 'active' ? 'success' : 'neutral'">
              {{ row.status === 'active' ? '在招' : '暂停' }}
            </BaseBadge>
          </template>
          <template #requirements="{ row }">{{ requirementsSummary(row as JobListItem) }}</template>
          <!-- 简历数 · 匹配数（与 boss_jobs_list options.description 统计口径一致） -->
          <template #resumes="{ row }">{{ row.resume_count }} · {{ row.matched_count }}</template>
          <template #script_count="{ row }">{{ row.script_count }} 条</template>
          <template #updated_at="{ row }">{{ formatDate(row.updated_at) }}</template>
          <template v-if="loadingJobs" #empty>加载中...</template>
          <template v-else-if="!jobs.length" #empty>
            暂无职位，点击右上角「新增职位」创建第一个职位
          </template>
          <template v-else #empty>无匹配的职位</template>
        </BaseTable>
      </div>
    </div>

    <!-- 职位新增弹框（编辑在详情页） -->
    <JobFormModal v-model="showCreateModal" :job="null" @saved="loadJobs" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import JobFormModal from './JobFormModal.vue'
import { listJobs, type JobListItem } from '@/api/recruitingOperator'
import { jobDetailPath, formatDate } from './recruitingDisplay'

const toast = useToast()
const route = useRoute()
const router = useRouter()

// ============== 职位列表 ==============

const jobs = ref<JobListItem[]>([])
const loadingJobs = ref(false)

async function loadJobs() {
  loadingJobs.value = true
  try {
    const res = await listJobs()
    if (res.success && res.data) {
      jobs.value = res.data.items
    } else {
      toast.error(res.error || '加载职位列表失败')
    }
  } finally {
    loadingJobs.value = false
  }
}

// 客户端过滤：关键字（名称包含）+ 状态
const keyword = ref('')
const statusFilter = ref<'' | 'active' | 'paused'>('')

const filteredJobs = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return jobs.value.filter((job) => {
    if (kw && !job.job_name.toLowerCase().includes(kw)) return false
    if (statusFilter.value && job.status !== statusFilter.value) return false
    return true
  })
})

// 要求摘要一行（与 boss_jobs_list options.description 拼法一致：经验/学历/薪资 join）
function requirementsSummary(job: JobListItem): string {
  const reqs = job.job_requirements
  if (!reqs) return '未配置'
  const dims: string[] = []
  if (reqs.experience) dims.push(reqs.experience)
  if (reqs.educations?.length) dims.push(reqs.educations.join('、'))
  if (reqs.salary) dims.push(reqs.salary)
  return dims.length ? dims.join(' / ') : '未配置'
}

const columns = [
  { key: 'job_name', label: '职位名称', width: '220px' },
  { key: 'status', label: '状态', width: '80px' },
  { key: 'requirements', label: '要求摘要' },
  { key: 'resumes', label: '简历 · 匹配', width: '100px' },
  { key: 'script_count', label: '话术数', width: '90px' },
  { key: 'updated_at', label: '更新时间', width: '110px' },
]

// 行点击进详情（path 拼接，兼容 demo 与租户前台路由）
function goDetail(row: Record<string, any>) {
  router.push(jobDetailPath(route, row.id as string))
}

function openCreateJob() {
  showCreateModal.value = true
}

const showCreateModal = ref(false)

onMounted(() => {
  loadJobs()
})
</script>

<template>
  <!-- 简历库列表页：浏览/筛选（含 job_id 职位筛选）/删除/手动录入；详情路由化到 ResumeDetail -->
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
      <div class="page-toolbar">
        <div class="page-toolbar-left flex-wrap">
          <BaseInput
            v-model="filters.keyword"
            placeholder="搜索候选人姓名"
            size="sm"
            class="w-44"
            @keyup.enter="handleSearchClick()"
          />
          <!-- 职位选择器：label=职位名 value=职位 id，筛选走 job_id 硬关联 -->
          <BaseSelect v-model="filters.job_id" size="sm" class="w-44" @change="handleSearchClick()">
            <option value="">全部职位</option>
            <option v-for="job in jobOptions" :key="job.id" :value="job.id">{{ job.job_name }}</option>
          </BaseSelect>
          <BaseSelect v-model="filters.status" size="sm" class="w-32" @change="handleSearchClick()">
            <option value="">全部状态</option>
            <option v-for="(label, value) in STATUS_LABELS" :key="value" :value="value">{{ label }}</option>
          </BaseSelect>
          <input
            v-model="filters.fetched_at_from"
            type="date"
            class="rounded-lg border border-default bg-white px-2 py-1.5 text-sm text-default"
          />
          <span class="text-muted text-sm">至</span>
          <input
            v-model="filters.fetched_at_to"
            type="date"
            class="rounded-lg border border-default bg-white px-2 py-1.5 text-sm text-default"
          />
          <BaseButton size="sm" @click="handleSearchClick()">搜索</BaseButton>
          <BaseButton size="sm" intent="secondary" @click="handleReset">重置</BaseButton>
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" @click="openCreate">手动录入</BaseButton>
        </div>
      </div>

      <div class="table-scroll-wrapper flex-1">
        <BaseTable :columns="columns" :data="items" row-key="id">
          <template #seq="{ index }">{{ seqNumber(index) }}</template>
          <template #candidate_name="{ row }">{{ row.candidate_name || '-' }}</template>
          <template #match="{ row }">
            <BaseBadge :intent="matchIntent(row.match_status)">{{ matchText(row) }}</BaseBadge>
          </template>
          <template #job_name="{ row }">{{ row.job_name || '-' }}</template>
          <template #info="{ row }">{{ infoSummary(row.candidate_info) }}</template>
          <template #fetched_at="{ row }">{{ formatDate(row.fetched_at) }}</template>
          <template #status="{ row }">
            <BaseBadge :intent="statusIntent(row.status)">{{ STATUS_LABELS[row.status] || row.status }}</BaseBadge>
          </template>
          <template #images="{ row }">{{ (row.images || []).length }} 张</template>
          <template #source="{ row }">
            <BaseBadge :intent="row.source === 'boss' ? 'info' : 'neutral'">{{ sourceLabel(row.source) }}</BaseBadge>
          </template>
          <template #actions="{ row }">
            <div class="flex gap-2">
              <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="goDetail(row.id)">详情</BaseButton>
              <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleDelete(row as ResumeListItem)">删除</BaseButton>
            </div>
          </template>
          <template v-if="loading" #empty>加载中...</template>
          <template v-else #empty>暂无简历</template>
        </BaseTable>
      </div>

      <BasePagination
        :total="total"
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :show-size-changer="true"
        @change="refresh"
      />
    </div>

    <!-- ============== 手动录入弹框 ============== -->
    <BaseModal v-model="showCreateModal" title="手动录入简历" size="lg">
      <div class="space-y-4">
        <div>
          <label class="form-label">简历图片（可多选）</label>
          <input
            ref="createInputRef"
            type="file"
            multiple
            accept="image/*"
            class="block w-full text-sm text-muted"
            @change="handleFilePick"
          />
          <p v-if="createForm.files.length" class="text-xs text-muted mt-1">
            已选 {{ createForm.files.length }} 张：{{ createForm.files.map(f => f.name).join('、') }}
          </p>
          <p class="text-xs text-muted mt-1">逐张上传到文件服务，简历详情页可查看原图</p>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">候选人姓名 <span class="form-required">*</span></label>
            <BaseInput v-model="createForm.candidate_name" />
          </div>
          <!-- 关联职位：职位选择器（提交 job_id 硬关联，不再传自由文本） -->
          <div>
            <label class="form-label">关联职位</label>
            <BaseSelect v-model="createForm.job_id">
              <option value="">不关联</option>
              <option v-for="job in jobOptions" :key="job.id" :value="job.id">{{ job.job_name }}</option>
            </BaseSelect>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">获取日期</label>
            <input
              v-model="createForm.fetched_at"
              type="date"
              class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default"
            />
          </div>
          <div>
            <label class="form-label">备注</label>
            <BaseInput v-model="createForm.remark" placeholder="选填" />
          </div>
        </div>
        <div>
          <label class="form-label">OCR 文本</label>
          <textarea
            v-model="createForm.ocr_text"
            rows="5"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            placeholder="粘贴简历文本内容（选填）"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="submitting" @click="showCreateModal = false">取消</BaseButton>
        <BaseButton :disabled="!canSubmitCreate || submitting" @click="handleSubmitCreate">
          {{ submitting ? '提交中...' : '保存' }}
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'
import {
  listResumes,
  listJobs,
  createResume,
  deleteResume,
  type ResumeListItem,
  type ResumeStatus,
} from '@/api/recruitingOperator'
import { uploadFile } from '@/api/agent'
import { getAuthHeader } from '@/api/auth'
import {
  STATUS_LABELS,
  statusIntent,
  sourceLabel,
  matchIntent,
  matchText,
  formatDate,
  resumeDetailPath,
} from './recruitingDisplay'

const toast = useToast()
const route = useRoute()
const router = useRouter()

// 基本信息摘要：取前 3 个键值对拼一行（学历/工作年限/期望薪资等）
function infoSummary(info?: Record<string, any>): string {
  if (!info) return '-'
  const entries = Object.entries(info).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (!entries.length) return '-'
  return entries.slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(' · ')
}

// ============== 列表 ==============

const items = ref<ResumeListItem[]>([])
const total = ref(0)

// 筛选条件：job_id 走职位硬关联筛选（职位选择器），不再用 distinct job_name 文本
const filters = ref({
  keyword: '',
  job_id: '',
  status: '' as '' | ResumeStatus,
  fetched_at_from: '',
  fetched_at_to: '',
})

// 从 URL query 初始化筛选（刷新/后退可还原；非法值兜底为空）
function readFiltersFromQuery() {
  const q = route.query
  const str = (v: unknown) => (typeof v === 'string' ? v : '')
  const status = str(q.status)
  filters.value = {
    keyword: str(q.keyword),
    job_id: str(q.job_id),
    status: (Object.keys(STATUS_LABELS) as string[]).includes(status) ? (status as ResumeStatus) : '',
    fetched_at_from: str(q.fetched_at_from),
    fetched_at_to: str(q.fetched_at_to),
  }
}

// 搜索时把筛选条件同步到 URL（replace 不产生历史栈；空值剔除保持地址干净）
function syncFiltersToQuery() {
  const query: Record<string, string> = {}
  const f = filters.value
  if (f.keyword.trim()) query.keyword = f.keyword.trim()
  if (f.job_id) query.job_id = f.job_id
  if (f.status) query.status = f.status
  if (f.fetched_at_from) query.fetched_at_from = f.fetched_at_from
  if (f.fetched_at_to) query.fetched_at_to = f.fetched_at_to
  router.replace({ query })
}

const { currentPage, pageSize, seqNumber, loading, handleSearch, refresh } = usePageContext(async () => {
  const res = await listResumes({
    page: currentPage.value,
    page_size: pageSize.value,
    keyword: filters.value.keyword.trim() || undefined,
    job_id: filters.value.job_id || undefined,
    status: filters.value.status || undefined,
    fetched_at_from: filters.value.fetched_at_from || undefined,
    fetched_at_to: filters.value.fetched_at_to || undefined,
  })
  if (res.success && res.data) {
    items.value = res.data.items
    total.value = res.data.total
  } else {
    toast.error(res.error || '加载简历列表失败')
  }
})

// 点搜索/切换筛选：同步 URL + 回第一页加载
async function handleSearchClick() {
  syncFiltersToQuery()
  await handleSearch()
}

// 后退/前进恢复筛选：query 变化导致筛选实际变化时才重新加载（自己的 replace 不会二次触发）
watch(
  () => route.query,
  () => {
    const before = JSON.stringify(filters.value)
    readFiltersFromQuery()
    if (JSON.stringify(filters.value) !== before) {
      handleSearch()
    }
  },
)

// 职位选择器选项（listJobs 全量；label=职位名 value=职位 id）
const jobOptions = ref<Array<{ id: string; job_name: string }>>([])

async function loadJobOptions() {
  const res = await listJobs()
  if (res.success && res.data) {
    jobOptions.value = res.data.items.map(j => ({ id: j.id, job_name: j.job_name }))
  }
}

async function handleReset() {
  filters.value = { keyword: '', job_id: '', status: '', fetched_at_from: '', fetched_at_to: '' }
  await handleSearchClick()
}

const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'candidate_name', label: '候选人', width: '120px' },
  { key: 'match', label: '匹配度', width: '90px' },
  { key: 'job_name', label: '关联职位' },
  { key: 'info', label: '基本信息' },
  { key: 'fetched_at', label: '获取日期', width: '110px' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'images', label: '图片', width: '80px' },
  { key: 'source', label: '来源', width: '110px' },
  { key: 'actions', label: '操作', width: '140px' },
]

// 详情路由化：跳简历详情页（path 拼接，兼容 demo 与租户前台路由）
function goDetail(id: number) {
  router.push(resumeDetailPath(route, id))
}

async function handleDelete(row: ResumeListItem) {
  if (!confirm(`确定删除「${row.candidate_name || '未命名候选人'}」的简历？`)) return
  const res = await deleteResume(row.id)
  if (res.success) {
    toast.success('删除成功')
    refresh()
  } else {
    toast.error(res.error || '删除失败')
  }
}

// ============== 手动录入 ==============

const showCreateModal = ref(false)
const submitting = ref(false)
const createInputRef = ref<HTMLInputElement | null>(null)

function emptyCreateForm() {
  return {
    candidate_name: '',
    job_id: '',
    ocr_text: '',
    remark: '',
    fetched_at: new Date().toISOString().slice(0, 10),
    files: [] as File[],
  }
}

const createForm = ref(emptyCreateForm())

const canSubmitCreate = computed(() => createForm.value.candidate_name.trim().length > 0)

function openCreate() {
  createForm.value = emptyCreateForm()
  if (createInputRef.value) createInputRef.value.value = ''
  showCreateModal.value = true
}

function handleFilePick(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) {
    createForm.value.files = Array.from(input.files)
  }
}

async function handleSubmitCreate() {
  if (!canSubmitCreate.value) return
  submitting.value = true
  try {
    // 1. 逐张调 /api/upload 上传图片拿 file_id
    const images: { file_id: string; name?: string }[] = []
    for (const file of createForm.value.files) {
      const uploadRes = await uploadFile(file, getAuthHeader())
      if (!uploadRes.file_id) {
        toast.error(`图片「${file.name}」上传失败`)
        return
      }
      images.push({ file_id: uploadRes.file_id, name: file.name })
    }
    // 2. 创建简历记录（source=manual；关联职位提交 job_id 硬关联，后端校验并回填职位名）
    const res = await createResume({
      candidate_name: createForm.value.candidate_name.trim(),
      job_id: createForm.value.job_id || undefined,
      ocr_text: createForm.value.ocr_text.trim() || undefined,
      remark: createForm.value.remark.trim() || undefined,
      fetched_at: createForm.value.fetched_at || undefined,
      images: images.length ? images : undefined,
      source: 'manual',
    })
    if (res.success) {
      toast.success('简历已保存')
      showCreateModal.value = false
      handleSearchClick()
    } else {
      toast.error(res.error || '保存失败')
    }
  } catch (e: any) {
    toast.error(e.message || '保存异常')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  // 进页面从 URL query 还原筛选，再按其加载列表
  readFiltersFromQuery()
  refresh()
  loadJobOptions()
})
</script>

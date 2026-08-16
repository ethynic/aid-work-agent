<template>
  <!-- 简历库页面：保存从 BOSS 直聘采集的候选人简历，支持浏览/筛选/详情/状态流转/手动录入 -->
  <div class="page-container p-5">
    <!-- ============== 列表视图 ============== -->
    <div v-if="!detail" class="page-content flex-1 flex flex-col min-h-0">
      <div class="page-toolbar">
        <div class="page-toolbar-left flex-wrap">
          <BaseInput
            v-model="filters.keyword"
            placeholder="搜索候选人姓名"
            size="sm"
            class="w-44"
            @keyup.enter="handleSearch()"
          />
          <BaseSelect v-model="filters.job_name" size="sm" class="w-44" @change="handleSearch()">
            <option value="">全部职位</option>
            <option v-for="job in jobOptions" :key="job" :value="job">{{ job }}</option>
          </BaseSelect>
          <BaseSelect v-model="filters.status" size="sm" class="w-32" @change="handleSearch()">
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
          <BaseButton size="sm" @click="handleSearch()">搜索</BaseButton>
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
              <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openDetail(row.id)">详情</BaseButton>
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

    <!-- ============== 详情视图 ============== -->
    <div v-else class="page-content flex-1 flex flex-col min-h-0">
      <div class="page-toolbar">
        <div class="page-toolbar-left">
          <BaseButton size="sm" intent="secondary" @click="closeDetail">← 返回列表</BaseButton>
          <span class="text-sm font-medium text-default">
            {{ detail.candidate_name || '未命名候选人' }}
            <span v-if="detail.job_name" class="text-muted font-normal">· {{ detail.job_name }}</span>
          </span>
        </div>
      </div>

      <div class="flex gap-4 flex-1 min-h-0">
        <!-- 左侧：简历图片画廊 -->
        <div class="w-1/2 flex flex-col gap-3 min-h-0">
          <div class="flex-1 min-h-0 flex items-center justify-center rounded-lg border border-default bg-gray-50 overflow-hidden">
            <img
              v-if="currentImage"
              :src="`/api/files/${currentImage.file_id}`"
              class="max-w-full max-h-full object-contain cursor-zoom-in"
              @click="openOriginal(currentImage)"
            />
            <span v-else class="text-muted text-sm">无简历图片</span>
          </div>
          <!-- 缩略图列表 + 新窗口看原图 -->
          <div v-if="detailImages.length" class="flex gap-2 overflow-x-auto pb-1">
            <img
              v-for="(img, i) in detailImages"
              :key="img.file_id"
              :src="`/api/files/${img.file_id}`"
              class="h-16 w-14 flex-shrink-0 rounded-md border-2 object-cover cursor-pointer"
              :class="currentImageIndex === i ? 'border-primary-500' : 'border-default'"
              :title="img.name || `第 ${i + 1} 张`"
              @click="currentImageIndex = i"
            />
            <BaseButton
              v-if="currentImage"
              intent="secondary"
              size="sm"
              class="whitespace-nowrap"
              @click="openOriginal(currentImage)"
            >
              新窗口看原图
            </BaseButton>
          </div>
        </div>

        <!-- 右侧：基本信息 / 状态 / 备注 / OCR 文本 -->
        <div class="w-1/2 flex flex-col gap-3 min-h-0 overflow-y-auto pr-1">
          <!-- 基本信息卡 -->
          <div class="rounded-lg border border-default bg-white p-4">
            <div class="text-sm font-semibold text-default mb-2">基本信息</div>
            <div v-if="infoEntries.length" class="space-y-1">
              <div v-for="[key, value] in infoEntries" :key="key" class="flex text-sm">
                <span class="text-muted w-24 flex-shrink-0">{{ key }}</span>
                <span class="text-default break-all">{{ value }}</span>
              </div>
            </div>
            <div v-else class="text-muted text-sm">暂无基本信息</div>
            <div class="mt-2 pt-2 border-t border-default text-xs text-muted">
              来源：{{ sourceLabel(detail.source) }} · 获取日期：{{ formatDate(detail.fetched_at) }}
            </div>
          </div>

          <!-- 状态切换（即时保存） -->
          <div class="rounded-lg border border-default bg-white p-4 flex items-center gap-3">
            <span class="text-sm font-semibold text-default">状态</span>
            <BaseSelect v-model="detail.status" size="sm" class="w-36" @change="saveStatus">
              <option v-for="(label, value) in STATUS_LABELS" :key="value" :value="value">{{ label }}</option>
            </BaseSelect>
            <span v-if="savingStatus" class="text-xs text-muted">保存中...</span>
          </div>

          <!-- 备注（可编辑保存） -->
          <div class="rounded-lg border border-default bg-white p-4">
            <div class="text-sm font-semibold text-default mb-2">备注</div>
            <textarea
              v-model="remarkDraft"
              rows="3"
              class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
              placeholder="补充候选人备注..."
            />
            <div class="mt-2 flex justify-end">
              <BaseButton size="sm" :disabled="savingRemark" @click="saveRemark">
                {{ savingRemark ? '保存中...' : '保存备注' }}
              </BaseButton>
            </div>
          </div>

          <!-- OCR 文本（等宽滚动 + 复制） -->
          <div class="rounded-lg border border-default bg-white p-4 flex flex-col flex-1 min-h-40">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold text-default">OCR 文本</div>
              <BaseButton
                v-if="detail.ocr_text"
                intent="secondary"
                size="sm"
                class="whitespace-nowrap"
                @click="copyOcr"
              >
                复制
              </BaseButton>
            </div>
            <pre
              v-if="detail.ocr_text"
              class="flex-1 overflow-auto rounded-md bg-gray-50 p-3 font-mono text-xs leading-relaxed text-default whitespace-pre-wrap"
            >{{ detail.ocr_text }}</pre>
            <div v-else class="text-muted text-sm">暂无 OCR 文本</div>
          </div>
        </div>
      </div>
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
          <div>
            <label class="form-label">关联职位</label>
            <BaseInput v-model="createForm.job_name" placeholder="如：Python 后端工程师" />
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
import { ref, computed, onMounted } from 'vue'
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
  listResumeJobs,
  createResume,
  getResume,
  updateResume,
  deleteResume,
  type ResumeDetail,
  type ResumeListItem,
  type ResumeStatus,
} from '@/api/recruitingOperator'
import { uploadFile } from '@/api/agent'
import { getAuthHeader } from '@/api/auth'

const toast = useToast()

// 状态中文标签（顺序即下拉顺序）
const STATUS_LABELS: Record<string, string> = {
  new: '新简历',
  viewed: '已查看',
  shortlisted: '有意向',
  interviewed: '已约面',
  rejected: '不合适',
}

function statusIntent(status?: string): 'info' | 'neutral' | 'success' | 'warning' | 'danger' {
  const map: Record<string, 'info' | 'neutral' | 'success' | 'warning' | 'danger'> = {
    new: 'info',
    viewed: 'neutral',
    shortlisted: 'success',
    interviewed: 'warning',
    rejected: 'danger',
  }
  return map[status || ''] || 'neutral'
}

function sourceLabel(source?: string): string {
  const map: Record<string, string> = { boss: 'CLI 入库', manual: '页面补录' }
  return map[source || ''] || source || '-'
}

function formatDate(t?: string): string {
  if (!t) return '-'
  return t.slice(0, 10)
}

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
const jobOptions = ref<string[]>([])
const filters = ref({
  keyword: '',
  job_name: '',
  status: '' as '' | ResumeStatus,
  fetched_at_from: '',
  fetched_at_to: '',
})

const { currentPage, pageSize, seqNumber, loading, handleSearch, refresh } = usePageContext(async () => {
  const res = await listResumes({
    page: currentPage.value,
    page_size: pageSize.value,
    keyword: filters.value.keyword.trim() || undefined,
    job_name: filters.value.job_name || undefined,
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

async function loadJobs() {
  const res = await listResumeJobs()
  if (res.success && res.data) {
    jobOptions.value = res.data.jobs
  }
}

function handleReset() {
  filters.value = { keyword: '', job_name: '', status: '', fetched_at_from: '', fetched_at_to: '' }
  handleSearch()
}

const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'candidate_name', label: '候选人', width: '120px' },
  { key: 'job_name', label: '关联职位' },
  { key: 'info', label: '基本信息' },
  { key: 'fetched_at', label: '获取日期', width: '110px' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'images', label: '图片', width: '80px' },
  { key: 'source', label: '来源', width: '110px' },
  { key: 'actions', label: '操作', width: '140px' },
]

async function handleDelete(row: ResumeListItem) {
  if (!confirm(`确定删除「${row.candidate_name || '未命名候选人'}」的简历？`)) return
  const res = await deleteResume(row.id)
  if (res.success) {
    toast.success('删除成功')
    refresh()
    loadJobs()
  } else {
    toast.error(res.error || '删除失败')
  }
}

// ============== 详情 ==============

const detail = ref<ResumeDetail | null>(null)
const currentImageIndex = ref(0)
const remarkDraft = ref('')
const savingStatus = ref(false)
const savingRemark = ref(false)

const detailImages = computed(() => detail.value?.images || [])
const currentImage = computed(() => detailImages.value[currentImageIndex.value] || null)
const infoEntries = computed(() => Object.entries(detail.value?.candidate_info || {}))

async function openDetail(id: number) {
  const res = await getResume(id)
  if (res.success && res.data) {
    detail.value = res.data
    currentImageIndex.value = 0
    remarkDraft.value = res.data.remark || ''
  } else {
    toast.error(res.error || '加载简历详情失败')
  }
}

function closeDetail() {
  detail.value = null
}

function openOriginal(img: { file_id: string }) {
  window.open(`/api/files/${img.file_id}/download`, '_blank')
}

// 状态切换即时保存
async function saveStatus() {
  if (!detail.value) return
  savingStatus.value = true
  try {
    const res = await updateResume(detail.value.id, { status: detail.value.status })
    if (res.success && res.data) {
      detail.value = res.data
      toast.success('状态已更新')
    } else {
      toast.error(res.error || '状态更新失败')
    }
  } finally {
    savingStatus.value = false
  }
}

async function saveRemark() {
  if (!detail.value) return
  savingRemark.value = true
  try {
    const res = await updateResume(detail.value.id, { remark: remarkDraft.value })
    if (res.success && res.data) {
      detail.value = res.data
      toast.success('备注已保存')
    } else {
      toast.error(res.error || '备注保存失败')
    }
  } finally {
    savingRemark.value = false
  }
}

async function copyOcr() {
  if (!detail.value?.ocr_text) return
  try {
    await navigator.clipboard.writeText(detail.value.ocr_text)
    toast.success('OCR 文本已复制')
  } catch {
    toast.error('复制失败，请手动选择复制')
  }
}

// ============== 手动录入 ==============

const showCreateModal = ref(false)
const submitting = ref(false)
const createInputRef = ref<HTMLInputElement | null>(null)

function emptyCreateForm() {
  return {
    candidate_name: '',
    job_name: '',
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
    // 2. 创建简历记录（source=manual）
    const res = await createResume({
      candidate_name: createForm.value.candidate_name.trim(),
      job_name: createForm.value.job_name.trim() || undefined,
      ocr_text: createForm.value.ocr_text.trim() || undefined,
      remark: createForm.value.remark.trim() || undefined,
      fetched_at: createForm.value.fetched_at || undefined,
      images: images.length ? images : undefined,
      source: 'manual',
    })
    if (res.success) {
      toast.success('简历已保存')
      showCreateModal.value = false
      handleSearch()
      loadJobs()
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
  refresh()
  loadJobs()
})
</script>

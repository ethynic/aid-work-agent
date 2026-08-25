<template>
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
    <div class="flex justify-between items-center gap-4 mb-3 flex-wrap">
      <div class="flex gap-2.5 items-center">
        <BaseInput v-model="searchQuery" placeholder="输入关键词搜索景点，如：黄果树 5A景区" class="max-w-[500px]" size="sm" @keyup.enter="doSearch" />
        <BaseButton size="sm" :disabled="searching" @click="doSearch">{{ searching ? '搜索中...' : '搜索' }}</BaseButton>
        <BaseButton size="sm" v-if="searched" intent="secondary" @click="clearSearch">显示全部</BaseButton>
        <BaseButton size="sm" :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">
          批量删除 ({{ selectedArr.length }})
        </BaseButton>
      </div>
      <div class="flex gap-2 items-center">
        <input ref="fileInput" type="file" accept=".xlsx,.zip" style="display:none"
               @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
        <BaseButton intent="secondary" size="sm" @click="handleDownloadTemplate">下载模板</BaseButton>
        <BaseButton size="sm" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel/Zip' }}
        </BaseButton>
        <BaseButton intent="secondary" size="sm" @click="handleExport">导出 Excel</BaseButton>
      </div>
    </div>

    <!-- 统计信息 -->
    <div class="text-[13px] text-muted mb-4">
      <template v-if="!searched">
        共 {{ total }} 个景点，当前第 {{ currentPage }}/{{ totalPages }} 页
      </template>
      <template v-else>
        搜索结果：{{ currentList.length }} 个
      </template>
    </div>

    <!-- 表格 -->
    <div class="table-scroll-wrapper flex-1">
    <BaseTable :columns="columns" :data="currentList" row-key="doc_id">
      <template #checkbox_header>
        <input type="checkbox" :checked="isAllSelected(currentList)" @change="(e: Event) => toggleAll(currentList, (e.target as HTMLInputElement).checked)" />
      </template>
      <template #checkbox="{ row }">
        <input type="checkbox" :value="row.doc_id" v-model="selectedArr" />
      </template>
      <template #index="{ index }">{{ (currentPage - 1) * pageSize + index + 1 }}</template>
      <template #cover="{ row }">
        <div v-if="coverFileId(row)" class="w-[60px] h-[40px] overflow-hidden rounded border border-default">
          <img
            :src="`/api/files/${coverFileId(row)}/download`"
            :alt="row.title"
            loading="lazy"
            class="w-full h-full object-cover"
            @error="onCoverError($event, row)"
          />
        </div>
        <div v-else class="w-[60px] h-[40px] flex items-center justify-center rounded border border-default text-muted text-[10px]">无图</div>
      </template>
      <template #title="{ row }"><span class="font-medium">{{ row.title }}</span></template>
      <template #region="{ row }">{{ row.metadata?.region || '-' }}</template>
      <template #category_cn="{ row }">{{ row.metadata?.category_cn || '-' }}</template>
      <template #source_file="{ row }">
        <span class="max-w-[120px] overflow-hidden text-ellipsis whitespace-nowrap block text-muted">{{ row.source_file || '-' }}</span>
      </template>
      <template #info="{ row }">
        <span class="max-w-[200px] overflow-hidden text-ellipsis whitespace-nowrap block text-muted">{{ (row.info || '').slice(0, 60) }}{{ (row.info || '').length > 60 ? '...' : '' }}</span>
      </template>
      <template #actions="{ row }">
        <div class="flex gap-2">
          <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openEdit(row)">编辑</BaseButton>
          <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleDelete(row)">删除</BaseButton>
        </div>
      </template>
      <template v-if="loading" #empty>加载中...</template>
    </BaseTable>
    </div>

    <!-- 分页 -->
    <BasePagination
      v-if="!searched"
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      :show-size-changer="true"
      @change="onPageChange"
    />

    <!-- 编辑弹窗 -->
    <BaseModal v-model="showModal" title="编辑景点" size="xl" mode="edit" :is-dirty="isFormDirty">
      <div>
        <label class="text-sm text-muted mb-1 block">景点名称</label>
        <BaseInput v-model="form.title" placeholder="景点名称" />
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">区域</label>
          <BaseInput v-model="form.region" placeholder="如：贵阳、安顺" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">类型</label>
          <BaseInput v-model="form.category_cn" placeholder="如：自然景观、人文景观" />
        </div>
      </div>
      <!-- 图片管理：封面 + 图集（即时生效，不走保存按钮） -->
      <div class="mt-5 p-3 border border-default rounded bg-canvas">
        <div class="flex items-center justify-between mb-2">
          <label class="text-sm text-muted m-0">景点图片</label>
          <span class="text-[11px] text-muted">上传即时生效，不需点保存</span>
        </div>
        <!-- 封面 -->
        <div class="flex items-center gap-3 mb-3">
          <span class="text-xs text-muted w-12 flex-shrink-0">封面</span>
          <div v-if="editingCover" class="relative w-[80px] h-[60px] flex-shrink-0">
            <img :src="`/api/files/${editingCover}/download`" alt="封面" class="w-full h-full object-cover rounded border border-default" />
            <button class="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-danger-600 text-white text-xs leading-none hover:bg-danger-700" @click="removeCover" title="删除封面">×</button>
          </div>
          <div v-else class="w-[80px] h-[60px] flex-shrink-0 border border-dashed border-default rounded flex items-center justify-center text-[10px] text-muted">无封面</div>
          <input ref="coverInput" type="file" accept=".jpg,.jpeg,.png,.webp" class="hidden" @change="(e: any) => e.target.files[0] && uploadCover(e.target.files[0])" />
          <BaseButton size="sm" intent="secondary" :disabled="imageBusy === 'cover'" @click="coverInput?.click()">{{ imageBusy === 'cover' ? '上传中...' : (editingCover ? '替换封面' : '上传封面') }}</BaseButton>
        </div>
        <!-- 图集 -->
        <div class="flex items-start gap-3">
          <span class="text-xs text-muted w-12 flex-shrink-0 pt-1">图集</span>
          <div class="flex-1 flex flex-wrap gap-2 items-center">
            <div v-for="fid in editingGallery" :key="fid" class="relative w-[60px] h-[60px]">
              <img :src="`/api/files/${fid}/download`" :alt="fid" class="w-full h-full object-cover rounded border border-default" />
              <button class="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-danger-600 text-white text-xs leading-none hover:bg-danger-700" @click="removeGallery(fid)" title="删除">×</button>
            </div>
            <div v-if="!editingGallery.length" class="text-[11px] text-muted py-3">无图集</div>
            <input ref="galleryInput" type="file" accept=".jpg,.jpeg,.png,.webp" class="hidden" multiple @change="(e: any) => handleGalleryFiles(e.target.files)" />
            <BaseButton size="sm" intent="secondary" :disabled="imageBusy === 'gallery'" @click="galleryInput?.click()">{{ imageBusy === 'gallery' ? '上传中...' : '追加图集' }}</BaseButton>
          </div>
        </div>
        <div v-if="imageError" class="text-danger-600 text-xs mt-2">{{ imageError }}</div>
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">景点信息</label>
        <MyTextarea
          v-model="form.info"
          :rows="6"
          :min-height="'160px'"
          enable-preview
          :textarea-class="'text-[13px] box-border font-inherit'"
          placeholder="景点详细信息"
        />
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">门票价格</label>
        <textarea v-model="form.ticket_table" rows="6" placeholder="门票价格信息" class="w-full p-2 border border-default rounded text-[13px] box-border font-inherit"></textarea>
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">项目/服务价格</label>
        <textarea v-model="form.project_table" rows="6" placeholder="项目或服务价格信息" class="w-full p-2 border border-default rounded text-[13px] box-border font-inherit"></textarea>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
        <BaseButton @click="handleSave">保存</BaseButton>
      </template>
    </BaseModal>

    <!-- 景点详情弹窗 -->
    <BaseModal v-model="detailVisible" :title="detailData.title || '景点详情'" size="xl" mode="view">
      <div v-if="detailData.info" class="mb-4">
        <h4 class="m-0 mb-2 text-sm text-muted">景点信息</h4>
        <pre class="bg-surface p-3 rounded whitespace-pre-wrap break-words text-[13px] leading-relaxed m-0 font-inherit">{{ detailData.info }}</pre>
      </div>
      <div v-if="galleryImageIds.length" class="mb-4">
        <h4 class="m-0 mb-2 text-sm text-muted">图片（封面 + 图集）</h4>
        <div class="grid grid-cols-3 gap-2">
          <div v-for="fid in galleryImageIds" :key="fid" class="aspect-square overflow-hidden rounded border border-default">
            <img
              :src="`/api/files/${fid}/download`"
              :alt="fid"
              loading="lazy"
              class="w-full h-full object-cover cursor-zoom-in"
              @click="openImageLightbox(`/api/files/${fid}/download`, detailData.title)"
            />
          </div>
        </div>
      </div>
      <div v-if="detailData.ticket_table" class="mb-4">
        <h4 class="m-0 mb-2 text-sm text-muted">门票价格</h4>
        <pre class="bg-surface p-3 rounded whitespace-pre-wrap break-words text-[13px] leading-relaxed m-0 font-inherit">{{ detailData.ticket_table }}</pre>
      </div>
      <div v-if="detailData.project_table" class="mb-4">
        <h4 class="m-0 mb-2 text-sm text-muted">项目/服务价格</h4>
        <pre class="bg-surface p-3 rounded whitespace-pre-wrap break-words text-[13px] leading-relaxed m-0 font-inherit">{{ detailData.project_table }}</pre>
      </div>
      <div v-if="!detailData.info && !detailData.ticket_table && !detailData.project_table" class="p-5 text-center text-muted">
        暂无详细信息
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="detailVisible = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- 景点知识库导入结果弹窗 -->
    <BaseModal v-model="showImportResult" title="导入景点知识库结果" size="md" mode="view">
      <p>共解析 <strong>{{ importResult?.total_attractions || 0 }}</strong> 个景点，新增 <strong>{{ importResult?.imported || 0 }}</strong> 条，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 条</p>
      <div v-if="importResult?.errors?.length" class="mt-3">
        <div class="text-[13px] text-danger-600 mb-1">错误信息：</div>
        <div v-for="err in importResult.errors.slice(0, 10)" :key="err" class="text-danger-600 text-xs">{{ err }}</div>
      </div>
      <template #footer>
        <BaseButton @click="showImportResult = false">确定</BaseButton>
      </template>
    </BaseModal>

    <!-- 图片预览 lightbox -->
    <div v-if="lightboxUrl" class="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center p-6" @click="lightboxUrl = ''">
      <img :src="lightboxUrl" :alt="lightboxTitle" class="max-w-full max-h-full object-contain" />
      <button class="absolute top-4 right-4 text-white text-2xl bg-surface/20 hover:bg-surface/40 rounded-full w-10 h-10 flex items-center justify-center" @click.stop="lightboxUrl = ''">×</button>
    </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  searchAttractionsKB, listAttractionsKB, getAttractionKB,
  deleteAttractionKB, batchDeleteAttractionsKB, updateAttractionKB,
  exportAttractions, downloadTemplate, patchAttractionImage
} from '@/api/travelQuote'
import { useAttractionKBImport } from '@/composables/useImport'
import { useTableSelection } from '@/composables/useTableSelection'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import MyTextarea from '@/components/ui/MyTextarea.vue'

// 批量选择逻辑
const { selectedArr, isAllSelected, toggleAll, clearSelection } = useTableSelection<any>({
  getRowId: (row) => row.doc_id
})

const pageSize = ref(20)

const columns = [
  { key: 'checkbox', label: '', width: '40px' },
  { key: 'index', label: '序号', width: '60px' },
  { key: 'cover', label: '封面', width: '80px' },
  { key: 'title', label: '景点名称' },
  { key: 'region', label: '区域', tooltip: (row: any) => row.metadata?.region || '-' },
  { key: 'category_cn', label: '类型', tooltip: (row: any) => row.metadata?.category_cn || '-' },
  { key: 'source_file', label: '来源文件' },
  { key: 'info', label: '摘要' },
  { key: 'actions', label: '操作', width: '140px' },
]

const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, triggerFileInput } = useAttractionKBImport(loadAll)
async function handleDownloadTemplate() {
  await downloadTemplate()
}

// 列表数据
const loading = ref(false)
const items = ref<any[]>([])
const total = ref(0)
const currentPage = ref(1)

// 搜索
const searchResults = ref<any[]>([])
const searched = ref(false)
const searching = ref(false)
const searchQuery = ref('')

// 编辑弹窗
const showModal = ref(false)
const editingItem = ref<any>(null)
const form = ref({ title: '', region: '', category_cn: '', info: '', ticket_table: '', project_table: '' })
const formSnapshot = ref<string>('')
const isFormDirty = () => JSON.stringify(form.value) !== formSnapshot.value

// 详情弹窗
const detailVisible = ref(false)
const detailData = ref<any>({})

// 图片预览 lightbox
const lightboxUrl = ref('')
const lightboxTitle = ref('')

// 编辑弹窗图片管理状态（独立于文本表单，即时生效）
const coverInput = ref<HTMLInputElement | null>(null)
const galleryInput = ref<HTMLInputElement | null>(null)
const editingCover = ref<string | null>(null)
const editingGallery = ref<string[]>([])
const imageBusy = ref<null | 'cover' | 'gallery'>(null)
const imageError = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const currentList = computed(() => searched.value ? searchResults.value : items.value)

// 封面图 file_id：从 row.metadata.images.cover 提取
function coverFileId(row: any): string | null {
  const images = row?.metadata?.images
  if (images && typeof images === 'object' && typeof images.cover === 'string') {
    return images.cover
  }
  return null
}

// 详情弹窗图集 file_id 列表（封面 + 图集，去重）
const galleryImageIds = computed<string[]>(() => {
  const images = detailData.value?.metadata?.images
  if (!images || typeof images !== 'object') return []
  const ids: string[] = []
  if (typeof images.cover === 'string') ids.push(images.cover)
  if (Array.isArray(images.gallery)) {
    for (const fid of images.gallery) {
      if (typeof fid === 'string' && !ids.includes(fid)) ids.push(fid)
    }
  }
  return ids
})

function onCoverError(ev: Event, row: any) {
  // 封面图加载失败：隐藏 img，显示占位（避免 broken icon）
  const target = ev.target as HTMLImageElement
  target.style.display = 'none'
  const parent = target.parentElement
  if (parent) {
    parent.classList.add('flex', 'items-center', 'justify-center')
    parent.innerHTML = '<span class="text-muted text-[10px]">失败</span>'
  }
  console.warn('[AttractionManager] 封面图加载失败', row?.doc_id)
}

function openImageLightbox(url: string, title: string = '') {
  lightboxUrl.value = url
  lightboxTitle.value = title
}

async function loadAll() {
  loading.value = true
  try {
    const offset = (currentPage.value - 1) * pageSize.value
    const result = await listAttractionsKB({ limit: pageSize.value, offset })
    items.value = result.items || []
    total.value = result.total || 0
  } catch (e) {
    console.error('加载景点列表失败', e)
  } finally {
    loading.value = false
  }
}

function onPageChange(page: number, size: number) {
  pageSize.value = size
  currentPage.value = page
  clearSelection()
  loadAll()
}

async function doSearch() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  try {
    searchResults.value = await searchAttractionsKB({ q: searchQuery.value })
    searched.value = true
    clearSelection()
  } catch (e) {
    console.error('搜索景点失败', e)
    searchResults.value = []
    searched.value = true
  } finally {
    searching.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  searched.value = false
  searchResults.value = []
  clearSelection()
}

async function openEdit(item: any) {
  editingItem.value = item
  form.value = {
    title: item.title || '',
    region: item.metadata?.region || '',
    category_cn: item.metadata?.category_cn || '',
    info: item.info || '',
    ticket_table: '',
    project_table: ''
  }
  // 同步加载图片状态（从 list row 的 metadata 直接拿，不需要再请详情接口）
  const imgs = item.metadata?.images
  editingCover.value = (imgs && typeof imgs.cover === 'string') ? imgs.cover : null
  editingGallery.value = (imgs && Array.isArray(imgs.gallery)) ? [...imgs.gallery] : []
  imageError.value = ''
  showModal.value = true
  try {
    const detail = await getAttractionKB(item.doc_id)
    if (detail) {
      form.value.ticket_table = detail.ticket_table || ''
      form.value.project_table = detail.project_table || ''
      // 以详情接口的图片为准（更权威）
      const dimgs = detail.metadata?.images
      if (dimgs && typeof dimgs === 'object') {
        if (typeof dimgs.cover === 'string') editingCover.value = dimgs.cover
        else editingCover.value = null
        if (Array.isArray(dimgs.gallery)) editingGallery.value = [...dimgs.gallery]
        else editingGallery.value = []
      }
    }
    formSnapshot.value = JSON.stringify(form.value)
  } catch (e) {
    console.error('加载景点详情失败', e)
    formSnapshot.value = JSON.stringify(form.value)
  }
}

async function uploadCover(file: File) {
  if (!editingItem.value) return
  imageBusy.value = 'cover'
  imageError.value = ''
  try {
    const res = await patchAttractionImage(editingItem.value.doc_id, 'replace_cover', { file })
    editingCover.value = res.cover
    // 同步回写 list row，避免下次打开编辑时显示旧值
    if (editingItem.value.metadata?.images) {
      editingItem.value.metadata.images.cover = res.cover
    }
  } catch (e: any) {
    imageError.value = e?.message || '封面上传失败'
  } finally {
    imageBusy.value = null
    if (coverInput.value) coverInput.value.value = ''
  }
}

async function removeCover() {
  if (!editingItem.value) return
  if (!confirm('确定删除封面图？')) return
  imageBusy.value = 'cover'
  imageError.value = ''
  try {
    const res = await patchAttractionImage(editingItem.value.doc_id, 'remove_cover', {})
    editingCover.value = res.cover
    if (editingItem.value.metadata?.images) {
      editingItem.value.metadata.images.cover = null
    }
  } catch (e: any) {
    imageError.value = e?.message || '封面删除失败'
  } finally {
    imageBusy.value = null
  }
}

async function handleGalleryFiles(files: FileList) {
  if (!editingItem.value || !files.length) return
  imageBusy.value = 'gallery'
  imageError.value = ''
  try {
    for (const f of Array.from(files)) {
      await patchAttractionImage(editingItem.value.doc_id, 'add_gallery', { file: f })
    }
    // 重新加载详情获取最新 gallery（多文件上传后避免前端拼错顺序）
    const detail = await getAttractionKB(editingItem.value.doc_id)
    if (detail?.metadata?.images?.gallery) {
      editingGallery.value = [...detail.metadata.images.gallery]
      if (editingItem.value.metadata?.images) {
        editingItem.value.metadata.images.gallery = [...editingGallery.value]
      }
    }
  } catch (e: any) {
    imageError.value = e?.message || '图集上传失败'
  } finally {
    imageBusy.value = null
    if (galleryInput.value) galleryInput.value.value = ''
  }
}

async function removeGallery(fid: string) {
  if (!editingItem.value) return
  if (!confirm('确定从图集删除这张图？')) return
  imageError.value = ''
  try {
    const res = await patchAttractionImage(editingItem.value.doc_id, 'remove_gallery_file_id', { file_id: fid })
    editingGallery.value = res.gallery
    if (editingItem.value.metadata?.images) {
      editingItem.value.metadata.images.gallery = [...res.gallery]
    }
  } catch (e: any) {
    imageError.value = e?.message || '图集删除失败'
  }
}

async function handleSave() {
  if (!editingItem.value) return
  try {
    await updateAttractionKB(editingItem.value.doc_id, {
      title: form.value.title,
      info: form.value.info,
      ticket_table: form.value.ticket_table,
      project_table: form.value.project_table,
      metadata: {
        region: form.value.region,
        category_cn: form.value.category_cn
      }
    })
    showModal.value = false
    if (searched.value) {
      await doSearch()
    } else {
      await loadAll()
    }
  } catch (e) {
    console.error('保存失败', e)
    alert('保存失败，请检查数据')
  }
}

async function handleDelete(item: any) {
  if (!confirm(`确定删除景点「${item.title}」？`)) return
  try {
    await deleteAttractionKB(item.doc_id)
    selectedArr.value = selectedArr.value.filter((id: any) => id !== item.doc_id)
    if (searched.value) {
      searchResults.value = searchResults.value.filter((r: any) => r.doc_id !== item.doc_id)
    } else {
      await loadAll()
    }
  } catch (e) {
    console.error('删除失败', e)
    alert('删除失败')
  }
}

async function handleBatchDelete() {
  const count = selectedArr.value.length
  if (!confirm(`确定删除选中的 ${count} 个景点？`)) return
  try {
    await batchDeleteAttractionsKB([...selectedArr.value])
    clearSelection()
    if (searched.value) {
      await doSearch()
    } else {
      await loadAll()
    }
  } catch (e) {
    console.error('批量删除失败', e)
    alert('批量删除失败')
  }
}

async function handleExport() {
  try {
    await exportAttractions()
  } catch (e) {
    console.error('导出失败', e)
    alert('导出失败')
  }
}

onMounted(() => { loadAll() })
</script>

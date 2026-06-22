<template>
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
    <div class="flex justify-between items-center gap-4 mb-3 flex-wrap">
      <div class="flex gap-2.5 items-center">
        <BaseInput v-model="searchQuery" placeholder="输入关键词搜索酒店，如：贵阳 4钻酒店" class="max-w-[500px]" size="sm" @keyup.enter="doSearch" />
        <BaseButton size="sm" :disabled="searching" @click="doSearch">{{ searching ? '搜索中...' : '搜索' }}</BaseButton>
        <BaseButton size="sm" v-if="searched" intent="secondary" @click="clearSearch">显示全部</BaseButton>
        <BaseButton size="sm" :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">
          批量删除 ({{ selectedArr.length }})
        </BaseButton>
      </div>
      <div class="flex gap-2 items-center">
        <input ref="fileInput" type="file" accept=".xlsx" style="display:none"
               @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
        <BaseButton intent="secondary" size="sm" @click="handleDownloadTemplate">下载模板</BaseButton>
        <BaseButton size="sm" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </BaseButton>
        <BaseButton intent="secondary" size="sm" @click="handleExport">导出 Excel</BaseButton>
      </div>
    </div>

    <!-- 统计信息 -->
    <div class="text-[13px] text-muted mb-4">
      <template v-if="!searched">
        共 {{ total }} 家酒店，当前第 {{ currentPage }}/{{ totalPages }} 页
      </template>
      <template v-else>
        搜索结果：{{ currentList.length }} 家
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
      <template #title="{ row }"><span class="font-medium">{{ row.title }}</span></template>
      <template #sub_region="{ row }">{{ row.metadata?.sub_region || '-' }}</template>
      <template #diamond_level="{ row }">{{ row.metadata?.diamond_level || '-' }}</template>
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
    <BaseModal v-model="showModal" title="编辑酒店" size="xl" :mode="editingItem ? 'edit' : 'create'" :is-dirty="isFormDirty">
      <div>
        <label class="text-sm text-muted mb-1 block">酒店名称</label>
        <BaseInput v-model="form.title" placeholder="酒店名称" />
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">区域</label>
          <BaseInput v-model="form.sub_region" placeholder="如：贵阳市区、荔波" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">钻石等级</label>
          <BaseInput v-model="form.diamond_level" placeholder="如：4钻、5钻" />
        </div>
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">酒店信息</label>
        <MyTextarea
          v-model="form.info"
          :rows="6"
          :min-height="'160px'"
          enable-preview
          :textarea-class="'text-[13px] box-border font-inherit'"
          placeholder="酒店详细信息"
        />
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">价格明细</label>
        <textarea v-model="form.price_table" rows="6" placeholder="房型价格信息" class="w-full p-2 border border-default rounded text-[13px] box-border font-inherit"></textarea>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
        <BaseButton @click="handleSave">保存</BaseButton>
      </template>
    </BaseModal>

    <!-- 酒店详情弹窗 -->
    <BaseModal v-model="detailVisible" :title="detailData.title || '酒店详情'" size="xl" mode="view">
      <div v-if="detailData.info" class="mb-4">
        <h4 class="m-0 mb-2 text-sm text-muted">酒店信息</h4>
        <pre class="bg-surface p-3 rounded whitespace-pre-wrap break-words text-[13px] leading-relaxed m-0 font-inherit">{{ detailData.info }}</pre>
      </div>
      <div v-if="detailData.price_table">
        <h4 class="m-0 mb-2 text-sm text-muted">价格明细</h4>
        <pre class="bg-surface p-3 rounded whitespace-pre-wrap break-words text-[13px] leading-relaxed m-0 font-inherit">{{ detailData.price_table }}</pre>
      </div>
      <div v-if="!detailData.info && !detailData.price_table" class="p-5 text-center text-muted">
        暂无详细信息
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="detailVisible = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- 酒店知识库导入结果弹窗 -->
    <BaseModal v-model="showImportResult" title="导入酒店知识库结果" size="md" mode="view">
      <p>共解析 <strong>{{ importResult?.total_hotels || 0 }}</strong> 家酒店，新增 <strong>{{ importResult?.imported || 0 }}</strong> 条，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 条</p>
      <div v-if="importResult?.errors?.length" class="mt-3">
        <div class="text-[13px] text-danger-600 mb-1">错误信息：</div>
        <div v-for="err in importResult.errors.slice(0, 10)" :key="err" class="text-danger-600 text-xs">{{ err }}</div>
      </div>
      <template #footer>
        <BaseButton @click="showImportResult = false">确定</BaseButton>
      </template>
    </BaseModal>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  searchHotelsKB, listHotelsKB, getHotelKB,
  deleteHotelKB, batchDeleteHotelsKB, updateHotelKB,
  exportHotels, downloadTemplate
} from '@/api/travelQuote'
import { useHotelKBImport } from '@/composables/useImport'
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
  { key: 'title', label: '酒店名称' },
  { key: 'sub_region', label: '区域' },
  { key: 'diamond_level', label: '等级' },
  { key: 'source_file', label: '来源文件' },
  { key: 'info', label: '摘要' },
  { key: 'actions', label: '操作', width: '140px' },
]

const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, triggerFileInput } = useHotelKBImport(loadAll)
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
const form = ref({ title: '', sub_region: '', diamond_level: '', info: '', price_table: '' })
const formSnapshot = ref<string>('')
const isFormDirty = () => JSON.stringify(form.value) !== formSnapshot.value

// 详情弹窗
const detailVisible = ref(false)
const detailData = ref<any>({})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const currentList = computed(() => searched.value ? searchResults.value : items.value)

async function loadAll() {
  loading.value = true
  try {
    const offset = (currentPage.value - 1) * pageSize.value
    const result = await listHotelsKB({ limit: pageSize.value, offset })
    items.value = result.items || []
    total.value = result.total || 0
  } catch (e) {
    console.error('加载酒店列表失败', e)
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
    searchResults.value = await searchHotelsKB({ q: searchQuery.value })
    searched.value = true
    clearSelection()
  } catch (e) {
    console.error('搜索酒店失败', e)
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
    sub_region: item.metadata?.sub_region || '',
    diamond_level: item.metadata?.diamond_level || '',
    info: item.info || '',
    price_table: ''
  }
  showModal.value = true
  try {
    const detail = await getHotelKB(item.doc_id)
    if (detail) {
      form.value.price_table = detail.price_table || ''
    }
    formSnapshot.value = JSON.stringify(form.value)
  } catch (e) {
    console.error('加载酒店详情失败', e)
    formSnapshot.value = JSON.stringify(form.value)
  }
}

async function handleSave() {
  if (!editingItem.value) return
  try {
    await updateHotelKB(editingItem.value.doc_id, {
      title: form.value.title,
      info: form.value.info,
      price_table: form.value.price_table,
      metadata: {
        sub_region: form.value.sub_region,
        diamond_level: form.value.diamond_level
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
  if (!confirm(`确定删除酒店「${item.title}」？`)) return
  try {
    await deleteHotelKB(item.doc_id)
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
  if (!confirm(`确定删除选中的 ${count} 家酒店？`)) return
  try {
    await batchDeleteHotelsKB([...selectedArr.value])
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
    await exportHotels()
  } catch (e) {
    console.error('导出失败', e)
    alert('导出失败')
  }
}

onMounted(() => { loadAll() })
</script>

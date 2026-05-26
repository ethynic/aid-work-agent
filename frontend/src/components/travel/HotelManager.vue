<template>
  <div class="p-5 max-w-[1400px] mx-auto">
    <div class="flex justify-between items-center mb-5">
      <h2 class="m-0 text-lg">酒店知识库</h2>
      <div class="flex gap-2.5 items-center">
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none"
               @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
        <button class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" @click="handleDownloadTemplate">下载模板</button>
        <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </button>
      </div>
    </div>

    <!-- 搜索栏 -->
    <div class="flex gap-2.5 mb-3 items-center">
      <input v-model="searchQuery" class="flex-1 max-w-[500px] px-3 py-2 border border-default rounded text-sm box-border focus:outline-none focus:border-primary-600 focus:ring-2 focus:ring-primary-600/10" placeholder="输入关键词搜索酒店，如：贵阳 4钻酒店"
             @keyup.enter="doSearch" />
      <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:bg-primary-300 disabled:cursor-not-allowed" @click="doSearch" :disabled="searching">
        {{ searching ? '搜索中...' : '搜索' }}
      </button>
      <button v-if="searched" class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" @click="clearSearch">显示全部</button>
      <button v-if="selectedIds.size > 0" class="text-danger-600 hover:bg-danger-50 px-4 py-2 rounded-lg text-sm font-medium border border-danger-600" @click="handleBatchDelete">
        批量删除 ({{ selectedIds.size }})
      </button>
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
    <table class="w-full border-collapse text-[13px]">
      <thead>
        <tr>
          <th class="w-10 p-2 text-center border-b border-gray-200 bg-surface font-semibold text-default">
            <input type="checkbox" :checked="allSelected" @change="toggleAll" />
          </th>
          <th class="w-16 p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">序号</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">酒店名称</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">区域</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">等级</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">来源文件</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">摘要</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap min-w-[150px]">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="8" class="p-2 text-center text-muted">加载中...</td></tr>
        <tr v-else-if="currentList.length === 0"><td colspan="8" class="p-2 text-center text-muted">暂无数据</td></tr>
        <tr v-for="(item, index) in currentList" :key="item.doc_id" class="hover:bg-surface-hover">
          <td class="w-10 p-2 text-center border-b border-gray-200">
            <input type="checkbox" :value="item.doc_id" v-model="selectedArr" />
          </td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ (currentPage - 1) * PAGE_SIZE + index + 1 }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200 min-w-[120px] font-medium">{{ item.title }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.metadata?.sub_region || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.metadata?.diamond_level || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200 max-w-[120px] overflow-hidden text-ellipsis whitespace-nowrap text-muted">{{ item.source_file || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200 max-w-[200px] overflow-hidden text-ellipsis whitespace-nowrap text-muted">{{ (item.info || '').slice(0, 60) }}{{ (item.info || '').length > 60 ? '...' : '' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200 whitespace-nowrap min-w-[150px]">
            <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer" @click="showDetail(item.doc_id)">详情</button>
            <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer" @click="openEdit(item)">编辑</button>
            <button class="text-danger-600 hover:bg-danger-50 px-2.5 py-1 rounded text-[13px] border border-danger-600 cursor-pointer" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 分页 -->
    <div v-if="!searched && totalPages > 1" class="flex gap-1.5 items-center mt-4 justify-center">
      <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed" :disabled="currentPage <= 1" @click="goPage(currentPage - 1)">上一页</button>
      <template v-for="p in pageNumbers" :key="p">
        <button v-if="p === '...'" class="px-2.5 py-1 border border-default rounded bg-white text-[13px] disabled:opacity-50" disabled>...</button>
        <button v-else class="px-2.5 py-1 rounded text-[13px] cursor-pointer" :class="p === currentPage ? 'bg-primary-600 text-white border border-primary-600' : 'border border-default bg-white hover:bg-gray-50'" @click="goPage(p as number)">{{ p }}</button>
      </template>
      <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed" :disabled="currentPage >= totalPages" @click="goPage(currentPage + 1)">下一页</button>
    </div>

    <!-- 编辑弹窗 -->
    <div v-if="showModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showModal = false">
      <div class="bg-white rounded-lg p-6 w-[700px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <h3 class="m-0 mb-5">编辑酒店</h3>
        <div class="mb-3.5">
          <label class="block mb-1 text-[13px] text-muted">酒店名称</label>
          <input v-model="form.title" placeholder="酒店名称" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">区域</label>
            <input v-model="form.sub_region" placeholder="如：贵阳市区、荔波" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">钻石等级</label>
            <input v-model="form.diamond_level" placeholder="如：4钻、5钻" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="mb-3.5">
          <label class="block mb-1 text-[13px] text-muted">酒店信息</label>
          <textarea v-model="form.info" rows="8" placeholder="酒店详细信息" class="w-full p-2 border border-default rounded text-[13px] box-border font-inherit"></textarea>
        </div>
        <div class="mb-3.5">
          <label class="block mb-1 text-[13px] text-muted">价格明细</label>
          <textarea v-model="form.price_table" rows="6" placeholder="房型价格信息" class="w-full p-2 border border-default rounded text-[13px] box-border font-inherit"></textarea>
        </div>
        <div class="flex justify-end gap-2.5 mt-5">
          <button class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" @click="showModal = false">取消</button>
          <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="handleSave">保存</button>
        </div>
      </div>
    </div>

    <!-- 酒店详情弹窗 -->
    <div v-if="detailVisible" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="detailVisible = false">
      <div class="bg-white rounded-lg p-6 w-[750px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <h3 class="m-0 mb-5">{{ detailData.title }}</h3>
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
        <div class="flex justify-end gap-2.5 mt-5">
          <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="detailVisible = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 导入结果弹窗 -->
    <div v-if="showImportResult" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showImportResult = false">
      <div class="bg-white rounded-lg p-6 w-[600px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <h3 class="m-0 mb-5">导入知识库结果</h3>
        <p>共识别 <strong>{{ importResult?.total_hotels || 0 }}</strong> 家酒店，成功导入 <strong>{{ importResult?.imported || 0 }}</strong> 家，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 家</p>
        <div v-for="r in importResult?.details" :key="r.sheet" class="mb-2 text-[13px]">
          {{ r.sheet }}：共 {{ r.total }} 家，导入 {{ r.imported }} 家，跳过 {{ r.skipped }} 家
        </div>
        <div v-if="importResult?.errors?.length" class="mt-3">
          <div class="text-[13px] text-danger-600 mb-1">错误信息：</div>
          <div v-for="err in importResult.errors.slice(0, 10)" :key="err" class="text-danger-600 text-xs">{{ err }}</div>
        </div>
        <div class="flex justify-end gap-2.5 mt-5">
          <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="showImportResult = false">确定</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  searchHotelsKB, listHotelsKB, getHotelKB,
  deleteHotelKB, batchDeleteHotelsKB, updateHotelKB
} from '@/api/travelQuote'
import { useHotelKBImport } from '@/composables/useImport'

const PAGE_SIZE = 50

const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useHotelKBImport(loadAll)

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

// 选择
const selectedIds = ref<Set<number>>(new Set())

// 编辑弹窗
const showModal = ref(false)
const editingItem = ref<any>(null)
const form = ref({ title: '', sub_region: '', diamond_level: '', info: '', price_table: '' })

// 详情弹窗
const detailVisible = ref(false)
const detailData = ref<any>({})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const currentList = computed(() => searched.value ? searchResults.value : items.value)

// 选择逻辑
const selectedArr = computed({
  get: () => [...selectedIds.value],
  set: (vals: number[]) => { selectedIds.value = new Set(vals) }
})
const allSelected = computed(() =>
  currentList.value.length > 0 && currentList.value.every((item: any) => selectedIds.value.has(item.doc_id))
)
function toggleAll() {
  if (allSelected.value) {
    currentList.value.forEach((item: any) => selectedIds.value.delete(item.doc_id))
  } else {
    currentList.value.forEach((item: any) => selectedIds.value.add(item.doc_id))
  }
}

// 分页页码
const pageNumbers = computed(() => {
  const tp = totalPages.value
  const cp = currentPage.value
  if (tp <= 7) return Array.from({ length: tp }, (_, i) => i + 1)
  const pages: (number | string)[] = [1]
  if (cp > 3) pages.push('...')
  for (let i = Math.max(2, cp - 1); i <= Math.min(tp - 1, cp + 1); i++) pages.push(i)
  if (cp < tp - 2) pages.push('...')
  pages.push(tp)
  return pages
})

async function loadAll() {
  loading.value = true
  try {
    const offset = (currentPage.value - 1) * PAGE_SIZE
    const result = await listHotelsKB({ limit: PAGE_SIZE, offset })
    items.value = result.items || []
    total.value = result.total || 0
  } catch (e) {
    console.error('加载酒店列表失败', e)
  } finally {
    loading.value = false
  }
}

function goPage(page: number) {
  currentPage.value = page
  selectedIds.value.clear()
  loadAll()
}

async function doSearch() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  try {
    searchResults.value = await searchHotelsKB({ q: searchQuery.value })
    searched.value = true
    selectedIds.value.clear()
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
  selectedIds.value.clear()
}

async function showDetail(docId: number) {
  try {
    detailData.value = await getHotelKB(docId)
    detailVisible.value = true
  } catch (e) {
    console.error('获取酒店详情失败', e)
    alert('获取酒店详情失败')
  }
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
  } catch (e) {
    console.error('加载酒店详情失败', e)
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
    selectedIds.value.delete(item.doc_id)
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
  const count = selectedIds.value.size
  if (!confirm(`确定删除选中的 ${count} 家酒店？`)) return
  try {
    await batchDeleteHotelsKB([...selectedIds.value])
    selectedIds.value.clear()
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

onMounted(() => { loadAll() })
</script>


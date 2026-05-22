<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>酒店知识库</h2>
      <div class="actions">
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none"
               @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
        <button class="btn-secondary" @click="handleDownloadTemplate">下载模板</button>
        <button class="btn-primary" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </button>
      </div>
    </div>

    <!-- 搜索栏 -->
    <div class="kb-search-bar">
      <input v-model="searchQuery" class="kb-search-input" placeholder="输入关键词搜索酒店，如：贵阳 4钻酒店"
             @keyup.enter="doSearch" />
      <button class="btn-primary" @click="doSearch" :disabled="searching">
        {{ searching ? '搜索中...' : '搜索' }}
      </button>
      <button v-if="searched" class="btn-secondary" @click="clearSearch">显示全部</button>
      <button v-if="selectedIds.size > 0" class="btn-danger" @click="handleBatchDelete">
        批量删除 ({{ selectedIds.size }})
      </button>
    </div>

    <!-- 统计信息 -->
    <div class="kb-stats">
      <template v-if="!searched">
        共 {{ total }} 家酒店，当前第 {{ currentPage }}/{{ totalPages }} 页
      </template>
      <template v-else>
        搜索结果：{{ currentList.length }} 家
      </template>
    </div>

    <!-- 表格 -->
    <table class="data-table">
      <thead>
        <tr>
          <th class="col-check">
            <input type="checkbox" :checked="allSelected" @change="toggleAll" />
          </th>
          <th class="w-16">序号</th>
          <th>酒店名称</th>
          <th>区域</th>
          <th>等级</th>
          <th>来源文件</th>
          <th>摘要</th>
          <th class="col-actions">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="8" class="center">加载中...</td></tr>
        <tr v-else-if="currentList.length === 0"><td colspan="8" class="center">暂无数据</td></tr>
        <tr v-for="(item, index) in currentList" :key="item.doc_id">
          <td class="col-check">
            <input type="checkbox" :value="item.doc_id" v-model="selectedArr" />
          </td>
          <td>{{ (currentPage - 1) * PAGE_SIZE + index + 1 }}</td>
          <td class="col-title">{{ item.title }}</td>
          <td>{{ item.metadata?.sub_region || '-' }}</td>
          <td>{{ item.metadata?.diamond_level || '-' }}</td>
          <td class="col-source">{{ item.source_file || '-' }}</td>
          <td class="col-snippet">{{ (item.info || '').slice(0, 60) }}{{ (item.info || '').length > 60 ? '...' : '' }}</td>
          <td class="col-actions">
            <button class="btn-sm" @click="showDetail(item.doc_id)">详情</button>
            <button class="btn-sm" @click="openEdit(item)">编辑</button>
            <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 分页 -->
    <div v-if="!searched && totalPages > 1" class="pagination">
      <button class="btn-sm" :disabled="currentPage <= 1" @click="goPage(currentPage - 1)">上一页</button>
      <template v-for="p in pageNumbers" :key="p">
        <button v-if="p === '...'" class="btn-sm btn-page" disabled>...</button>
        <button v-else class="btn-sm btn-page" :class="{ active: p === currentPage }" @click="goPage(p as number)">{{ p }}</button>
      </template>
      <button class="btn-sm" :disabled="currentPage >= totalPages" @click="goPage(currentPage + 1)">下一页</button>
    </div>

    <!-- 编辑弹窗 -->
    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content" style="width: 700px;">
        <h3>编辑酒店</h3>
        <div class="form-group">
          <label>酒店名称</label>
          <input v-model="form.title" placeholder="酒店名称" />
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>区域</label>
            <input v-model="form.sub_region" placeholder="如：贵阳市区、荔波" />
          </div>
          <div class="form-group">
            <label>钻石等级</label>
            <input v-model="form.diamond_level" placeholder="如：4钻、5钻" />
          </div>
        </div>
        <div class="form-group">
          <label>酒店信息</label>
          <textarea v-model="form.info" rows="8" placeholder="酒店详细信息" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;font-size:13px;box-sizing:border-box;font-family:inherit;"></textarea>
        </div>
        <div class="form-group">
          <label>价格明细</label>
          <textarea v-model="form.price_table" rows="6" placeholder="房型价格信息" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;font-size:13px;box-sizing:border-box;font-family:inherit;"></textarea>
        </div>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showModal = false">取消</button>
          <button class="btn-primary" @click="handleSave">保存</button>
        </div>
      </div>
    </div>

    <!-- 酒店详情弹窗 -->
    <div v-if="detailVisible" class="modal-overlay" @click.self="detailVisible = false">
      <div class="modal-content" style="width: 750px;">
        <h3>{{ detailData.title }}</h3>
        <div v-if="detailData.info" style="margin-bottom: 16px;">
          <h4 style="margin: 0 0 8px; font-size: 14px; color: #555;">酒店信息</h4>
          <pre class="kb-pre">{{ detailData.info }}</pre>
        </div>
        <div v-if="detailData.price_table">
          <h4 style="margin: 0 0 8px; font-size: 14px; color: #555;">价格明细</h4>
          <pre class="kb-pre">{{ detailData.price_table }}</pre>
        </div>
        <div v-if="!detailData.info && !detailData.price_table" class="center" style="padding: 20px; color: #999;">
          暂无详细信息
        </div>
        <div class="modal-actions">
          <button class="btn-primary" @click="detailVisible = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 导入结果弹窗 -->
    <div v-if="showImportResult" class="modal-overlay" @click.self="showImportResult = false">
      <div class="modal-content">
        <h3>导入知识库结果</h3>
        <p>共识别 <strong>{{ importResult?.total_hotels || 0 }}</strong> 家酒店，成功导入 <strong>{{ importResult?.imported || 0 }}</strong> 家，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 家</p>
        <div v-for="r in importResult?.details" :key="r.sheet" style="margin-bottom:8px;font-size:13px">
          {{ r.sheet }}：共 {{ r.total }} 家，导入 {{ r.imported }} 家，跳过 {{ r.skipped }} 家
        </div>
        <div v-if="importResult?.errors?.length" style="margin-top:12px;">
          <div style="font-size:13px;color:#dc2626;margin-bottom:4px;">错误信息：</div>
          <div v-for="err in importResult.errors.slice(0, 10)" :key="err" style="color:#dc2626;font-size:12px;">{{ err }}</div>
        </div>
        <div class="modal-actions">
          <button class="btn-primary" @click="showImportResult = false">确定</button>
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

<style scoped>
.manager-container { padding: 20px; max-width: 1400px; margin: 0 auto; }
.header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.header-bar h2 { margin: 0; font-size: 18px; }
.actions { display: flex; gap: 10px; align-items: center; }

.kb-search-bar { display: flex; gap: 10px; margin-bottom: 12px; align-items: center; }
.kb-search-input { flex: 1; max-width: 500px; padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
.kb-search-input:focus { outline: none; border-color: #4f46e5; box-shadow: 0 0 0 2px rgba(79,70,229,0.1); }
.kb-stats { font-size: 13px; color: #888; margin-bottom: 16px; }

.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th, .data-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee; }
.data-table th { background: #f5f7fa; font-weight: 600; color: #333; white-space: nowrap; }
.data-table tr:hover { background: #fafbfc; }
.col-check { width: 40px; text-align: center; }
.col-title { min-width: 120px; font-weight: 500; }
.col-source { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #9ca3af; }
.col-snippet { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #666; }
.col-actions { white-space: nowrap; min-width: 150px; }
.center { text-align: center; color: #999; }

.pagination { display: flex; gap: 6px; align-items: center; margin-top: 16px; justify-content: center; }
.btn-page.active { background: #4f46e5; color: white; border-color: #4f46e5; }

.btn-primary { background: #4f46e5; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.btn-primary:hover { background: #4338ca; }
.btn-primary:disabled { background: #a5a5d4; cursor: not-allowed; }
.btn-secondary { background: #f3f4f6; color: #333; border: 1px solid #ddd; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.btn-sm { padding: 4px 10px; border: 1px solid #ddd; border-radius: 3px; background: white; cursor: pointer; font-size: 13px; }
.btn-sm:hover { background: #f5f5f5; }
.btn-sm:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-danger { color: #dc2626; border-color: #dc2626; background: white; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.btn-danger:hover { background: #fef2f2; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal-content { background: white; border-radius: 8px; padding: 24px; width: 600px; max-width: 90vw; max-height: 85vh; overflow-y: auto; }
.modal-content h3 { margin: 0 0 20px; }
.form-row { display: flex; gap: 12px; }
.form-row .form-group { flex: 1; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; margin-bottom: 4px; font-size: 13px; color: #555; }
.form-group input, .form-group select { width: 100%; padding: 7px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
.kb-pre { background: #f5f7fa; padding: 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.6; margin: 0; font-family: inherit; }
</style>

<template>
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <BaseInput v-model="searchKeyword" placeholder="筛选区域" size="sm" class="w-80" @keyup.enter="handleSearch(searchKeyword)" />
        <BaseButton size="sm" @click="handleSearch(searchKeyword)">搜索</BaseButton>
        <BaseSelect v-model="filterType" size="sm" class="ml-2" @change="handleSearch(searchKeyword)">
          <option value="">全部类型</option>
          <option value="local">地接导游</option>
          <option value="national">全陪导游</option>
          <option value="research">研学导师</option>
          <option value="driver_guide">司兼导</option>
        </BaseSelect>
      </div>
      <div class="page-toolbar-right">
        <BaseButton :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">批量删除 ({{ selectedArr.length }})</BaseButton>
        <BaseButton @click="openCreate">新增导游</BaseButton>
        <BaseButton intent="secondary" @click="handleDownloadTemplate">下载模板</BaseButton>
        <BaseButton intent="secondary" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </BaseButton>
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none" @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
      </div>
    </div>

    <div class="table-scroll-wrapper flex-1">
    <BaseTable :columns="columns" :data="pagedItems" row-key="id">
      <!-- 表头全选框 -->
      <template #checkbox_header>
        <input
          type="checkbox"
          :checked="isAllSelected(pagedItems)"
          @change="(e: Event) => toggleAll(pagedItems, (e.target as HTMLInputElement).checked)"
        />
      </template>
      <!-- 行选择框 -->
      <template #checkbox="{ row }">
        <input
          type="checkbox"
          :checked="isSelected(row)"
          @change="() => toggleRow(row)"
        />
      </template>
      <template #index="{ index }">{{ seqNumber(index) }}</template>
      <template #region_name="{ row }">{{ row.region_name || '通用' }}</template>
      <template #guide_type="{ row }">{{ row.guide_type_label || row.guide_type }}</template>
      <template #guide_level="{ row }">{{ row.guide_level_label || row.guide_level }}</template>
      <template #billing_method="{ row }">{{ row.billing_method === 'daily' ? '按天' : '按团' }}</template>
      <template #daily_rate="{ row }">{{ row.daily_rate || '-' }}</template>
      <template #trip_rate="{ row }">{{ row.trip_rate || '-' }}</template>
      <template #language_premium="{ row }">{{ row.language_premium || '-' }}</template>
      <template #peak_season_multiplier="{ row }">{{ row.peak_season_multiplier || '-' }}</template>
      <template #actions="{ row }">
        <div class="flex gap-2">
          <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openEdit(row)">编辑</BaseButton>
          <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleDelete(row)">删除</BaseButton>
        </div>
      </template>
      <template v-if="loading" #empty>加载中...</template>
    </BaseTable>
    </div>

    <BasePagination
      v-if="total > 0"
      :total="total"
      v-model:current-page="currentPage"
      :page-size="pageSize"
      :show-size-changer="true"
      @update:page-size="handlePageSizeChange"
    />

    <BaseModal v-model="showModal" :title="editingItem ? '编辑导游' : '新增导游'" size="lg">
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="text-sm text-muted mb-1 block">导游类型 <span class="text-danger-500">*</span></label>
          <BaseSelect v-model="form.guide_type">
            <option value="local">地接导游</option>
            <option value="national">全陪导游</option>
            <option value="research">研学导师</option>
            <option value="driver_guide">司兼导</option>
          </BaseSelect>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">类型显示名 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="form.guide_type_label" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">级别</label>
          <BaseSelect v-model="form.guide_level">
            <option value="junior">初级</option>
            <option value="standard">标准</option>
            <option value="senior">高级</option>
            <option value="premium">十佳</option>
          </BaseSelect>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">级别显示名</label>
          <BaseInput v-model="form.guide_level_label" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">计费方式</label>
          <BaseSelect v-model="form.billing_method">
            <option value="daily">按天</option>
            <option value="per_trip">按团</option>
          </BaseSelect>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">区域</label>
          <BaseInput v-model="form.region_name" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">日薪</label>
          <input v-model.number="form.daily_rate" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">整团费</label>
          <input v-model.number="form.trip_rate" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">外语加价(元/天)</label>
          <input v-model.number="form.language_premium" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">旺季上浮倍率</label>
          <input v-model.number="form.peak_season_multiplier" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">季节类型</label>
        <BaseSelect v-model="form.season_type">
          <option value="default">默认</option>
          <option value="peak">旺季</option>
          <option value="shoulder">平季</option>
          <option value="off">淡季</option>
        </BaseSelect>
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">备注</label>
        <BaseInput v-model="form.remark" />
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
        <BaseButton @click="handleSave">保存</BaseButton>
      </template>
    </BaseModal>

    <!-- 导入结果弹窗 -->
    <BaseModal v-model="showImportResult" title="导入结果" size="md">
      <p>成功导入 <strong>{{ importResult?.total_imported || 0 }}</strong> 条，跳过 <strong>{{ importResult?.total_skipped || 0 }}</strong> 条</p>
      <div v-for="r in importResult?.results" :key="r.sheet" class="mb-2 text-[13px]">
        {{ r.sheet }}：导入 {{ r.imported }} 条，跳过 {{ r.skipped }} 条
        <div v-if="r.errors.length" class="text-danger-600 text-xs mt-0.5">
          <div v-for="err in r.errors" :key="err">{{ err }}</div>
        </div>
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
import { guides } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'
import { usePageContext } from '@/composables/usePageContext'
import { useTableSelection } from '@/composables/useTableSelection'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'

const columns = [
  { key: 'checkbox', label: '', width: '40px' },
  { key: 'index', label: '序号', width: '60px' },
  { key: 'region_name', label: '区域' },
  { key: 'guide_type', label: '导游类型' },
  { key: 'guide_level', label: '级别' },
  { key: 'billing_method', label: '计费方式' },
  { key: 'daily_rate', label: '日薪' },
  { key: 'trip_rate', label: '整团费' },
  { key: 'language_premium', label: '外语加价' },
  { key: 'peak_season_multiplier', label: '旺季倍率' },
  { key: 'season_type', label: '季节' },
  { key: 'actions', label: '操作', width: '140px' },
]

const allItems = ref<any[]>([])
const loading = ref(false)
const showModal = ref(false)
const editingItem = ref<any>(null)
const filterType = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadData)

const total = ref(0)
const { currentPage, pageSize, searchKeyword, seqNumber, handleSearch, handlePageSizeChange } =
  usePageContext(async () => {
    await loadData()
  })

// 批量选择
const { selectedArr, isAllSelected, toggleAll, toggleRow, clearSelection, isSelected } = useTableSelection<any>({
  getRowId: (row) => row.id
})

const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return allItems.value.slice(start, start + pageSize.value)
})

const form = ref({
  guide_type: 'local', guide_type_label: '', guide_level: 'standard', guide_level_label: '',
  billing_method: 'daily', region_name: '', daily_rate: null as number | null,
  trip_rate: null as number | null, language_premium: 0, peak_season_multiplier: 1,
  season_type: 'default', remark: ''
})

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (searchKeyword.value) params.region_name = searchKeyword.value
    if (filterType.value) params.guide_type = filterType.value
    allItems.value = await guides.list(params)
    total.value = allItems.value.length
    // 搜索/刷新时清空选择
    clearSelection()
  } catch (e) { console.error('加载导游数据失败', e) }
  finally { loading.value = false }
}

function openCreate() {
  editingItem.value = null
  form.value = { guide_type: 'local', guide_type_label: '', guide_level: 'standard', guide_level_label: '', billing_method: 'daily', region_name: '', daily_rate: null, trip_rate: null, language_premium: 0, peak_season_multiplier: 1, season_type: 'default', remark: '' }
  showModal.value = true
}

function openEdit(item: any) {
  editingItem.value = item
  form.value = { guide_type: item.guide_type, guide_type_label: item.guide_type_label || '', guide_level: item.guide_level || 'standard', guide_level_label: item.guide_level_label || '', billing_method: item.billing_method || 'daily', region_name: item.region_name || '', daily_rate: item.daily_rate, trip_rate: item.trip_rate, language_premium: item.language_premium || 0, peak_season_multiplier: item.peak_season_multiplier || 1, season_type: item.season_type || 'default', remark: item.remark || '' }
  showModal.value = true
}

async function handleSave() {
  try {
    if (editingItem.value) await guides.update(editingItem.value.id, form.value)
    else await guides.create(form.value)
    showModal.value = false; await loadData()
  } catch (e) { console.error('保存失败', e); alert('保存失败') }
}

async function handleDelete(item: any) {
  if (!confirm('确定删除该导游费用？')) return
  try { await guides.delete(item.id); await loadData() }
  catch (e) { console.error('删除失败', e); alert('删除失败') }
}

async function handleBatchDelete() {
  if (selectedArr.value.length === 0) return
  if (!confirm(`确定删除选中的 ${selectedArr.value.length} 个导游费用？`)) return
  try {
    for (const id of selectedArr.value) {
      await guides.delete(id)
    }
    clearSelection()
    await loadData()
    alert('批量删除成功')
  } catch (e) { console.error('批量删除失败', e); alert('批量删除失败') }
}

onMounted(() => { loadData() })
</script>

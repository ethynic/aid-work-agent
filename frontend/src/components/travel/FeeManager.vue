<template>
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
    <!-- Tab 切换 -->
    <div class="flex mb-5 border-b-2 border-gray-200">
      <BaseButton
        intent="ghost"
        :class="['rounded-none border-b-2 -mb-0.5', activeTab === 'fees' ? '!text-primary-600 !border-b-primary-600 font-semibold' : '!border-transparent text-muted']"
        @click="activeTab = 'fees'"
      >其他费用</BaseButton>
      <BaseButton
        intent="ghost"
        :class="['rounded-none border-b-2 -mb-0.5', activeTab === 'seasons' ? '!text-primary-600 !border-b-primary-600 font-semibold' : '!border-transparent text-muted']"
        @click="activeTab = 'seasons'"
      >淡旺季配置</BaseButton>
    </div>

    <!-- 其他费用 Tab -->
    <div v-if="activeTab === 'fees'">
      <div class="page-toolbar">
        <div class="page-toolbar-left">
          <BaseSelect v-model="filterCategory" size="sm" class="w-40" @change="handleFeeSearch(filterCategory)">
            <option value="">全部分类</option>
            <option value="insurance">保险</option>
            <option value="service">服务费</option>
            <option value="transport">交通</option>
            <option value="other">其他</option>
          </BaseSelect>
        </div>
        <div class="page-toolbar-right">
          <BaseButton :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">批量删除 ({{ selectedArr.length }})</BaseButton>
          <BaseButton @click="openFeeCreate">新增费用</BaseButton>
          <BaseButton intent="secondary" @click="handleDownloadTemplate">下载模板</BaseButton>
          <BaseButton intent="secondary" :disabled="importing" @click="triggerFileInput(fileInput)">
            {{ importing ? '导入中...' : '导入 Excel' }}
          </BaseButton>
          <BaseButton intent="secondary" @click="handleFeesExport">导出 Excel</BaseButton>
          <BaseButton intent="secondary" :disabled="uuidImporting" @click="triggerUuidFileInput(uuidFileInput)">
            {{ uuidImporting ? 'UUID导入中...' : 'UUID导入' }}
          </BaseButton>
          <input ref="fileInput" type="file" accept=".xlsx" style="display:none" @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
          <input ref="uuidFileInput" type="file" accept=".xlsx" style="display:none" @change="(e: any) => e.target.files[0] && handleUuidImport(e.target.files[0])" />
        </div>
      </div>

      <div class="table-scroll-wrapper flex-1">
      <BaseTable :columns="feeColumns" :data="pagedFeeItems" row-key="id">
        <!-- 表头全选框 -->
        <template #checkbox_header>
          <input
            type="checkbox"
            :checked="isAllSelected(pagedFeeItems)"
            @change="(e: Event) => toggleAll(pagedFeeItems, (e.target as HTMLInputElement).checked)"
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
        <template #index="{ index }">{{ feeSeqNumber(index) }}</template>
        <template #billing_method="{ row }">{{ billingLabel(row.billing_method) }}</template>
        <template #is_mandatory="{ row }">{{ row.is_mandatory ? '是' : '否' }}</template>
        <template #remark="{ row }">{{ row.remark || '-' }}</template>
        <template #actions="{ row }">
          <div class="flex gap-2">
            <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openFeeEdit(row)">编辑</BaseButton>
            <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleFeeDelete(row)">删除</BaseButton>
          </div>
        </template>
        <template v-if="feeLoading" #empty>加载中...</template>
      </BaseTable>
      </div>

      <BasePagination
        :total="feeTotal"
        v-model:current-page="feeCurrentPage"
        v-model:page-size="feePageSize"
        :show-size-changer="true"
        @change="loadFees"
      />

      <!-- 费用弹窗 -->
      <BaseModal v-model="showFeeModal" :title="editingFee ? '编辑费用' : '新增费用'" size="lg" :mode="editingFee ? 'edit' : 'create'" :is-dirty="isFeeFormDirty">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">费用名称 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="feeForm.fee_name" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">费用分类 <span class="text-danger-500">*</span></label>
            <BaseSelect v-model="feeForm.fee_category">
              <option value="insurance">保险</option>
              <option value="service">服务费</option>
              <option value="transport">交通</option>
              <option value="other">其他</option>
            </BaseSelect>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4 mt-4">
          <div>
            <label class="text-sm text-muted mb-1 block">计费方式 <span class="text-danger-500">*</span></label>
            <BaseSelect v-model="feeForm.billing_method">
              <option value="per_person">按人</option>
              <option value="per_person_per_day">按人天</option>
              <option value="per_trip">按团</option>
              <option value="per_vehicle_per_day">按车天</option>
            </BaseSelect>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">单价 <span class="text-danger-500">*</span></label>
            <input v-model.number="feeForm.unit_price" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4 mt-4">
          <div>
            <label class="text-sm text-muted mb-1 block">是否必含</label>
            <select v-model="feeForm.is_mandatory" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500">
              <option :value="false">否</option>
              <option :value="true">是</option>
            </select>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">排序</label>
            <input v-model.number="feeForm.sort_order" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
          </div>
        </div>
        <div class="mt-4">
          <label class="text-sm text-muted mb-1 block">备注</label>
          <BaseInput v-model="feeForm.remark" />
        </div>
        <template #footer>
          <BaseButton intent="secondary" @click="showFeeModal = false">取消</BaseButton>
          <BaseButton @click="handleFeeSave">保存</BaseButton>
        </template>
      </BaseModal>
    </div>

    <!-- 淡旺季配置 Tab -->
    <div v-if="activeTab === 'seasons'">
      <div class="page-toolbar">
        <div class="page-toolbar-left"></div>
        <div class="page-toolbar-right">
          <BaseButton @click="openSeasonCreate">新增季节</BaseButton>
          <BaseButton intent="secondary" @click="handleSeasonsExport">导出 Excel</BaseButton>
          <BaseButton intent="secondary" :disabled="seasonUuidImporting" @click="triggerSeasonUuidFileInput(seasonUuidFileInput)">
            {{ seasonUuidImporting ? 'UUID导入中...' : 'UUID导入' }}
          </BaseButton>
          <input ref="seasonUuidFileInput" type="file" accept=".xlsx" style="display:none" @change="(e: any) => e.target.files[0] && handleSeasonUuidImport(e.target.files[0])" />
        </div>
      </div>

      <div class="table-scroll-wrapper flex-1">
      <BaseTable :columns="seasonColumns" :data="pagedSeasonItems" row-key="id">
        <template #index="{ index }">{{ seasonSeqNumber(index) }}</template>
        <template #remark="{ row }">{{ row.remark || '-' }}</template>
        <template #actions="{ row }">
          <div class="flex gap-2">
            <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openSeasonEdit(row)">编辑</BaseButton>
            <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleSeasonDelete(row)">删除</BaseButton>
          </div>
        </template>
        <template v-if="seasonLoading" #empty>加载中...</template>
      </BaseTable>
      </div>

      <BasePagination
        :total="seasonTotal"
        v-model:current-page="seasonCurrentPage"
        v-model:page-size="seasonPageSize"
        :show-size-changer="true"
        @change="loadSeasons"
      />

      <!-- 季节弹窗 -->
      <BaseModal v-model="showSeasonModal" :title="editingSeason ? '编辑季节' : '新增季节'" size="lg" :mode="editingSeason ? 'edit' : 'create'" :is-dirty="isSeasonFormDirty">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">季节类型编码 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="seasonForm.season_type" placeholder="如：peak, off" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">季节显示名 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="seasonForm.season_type_label" placeholder="如：旺季、淡季" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4 mt-4">
          <div>
            <label class="text-sm text-muted mb-1 block">开始日期 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="seasonForm.start_date" type="date" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">结束日期 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="seasonForm.end_date" type="date" />
          </div>
        </div>
        <div class="mt-4">
          <label class="text-sm text-muted mb-1 block">价格倍率</label>
          <input v-model.number="seasonForm.price_multiplier" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
        <div class="mt-4">
          <label class="text-sm text-muted mb-1 block">备注</label>
          <BaseInput v-model="seasonForm.remark" />
        </div>
        <template #footer>
          <BaseButton intent="secondary" @click="showSeasonModal = false">取消</BaseButton>
          <BaseButton @click="handleSeasonSave">保存</BaseButton>
        </template>
      </BaseModal>
    </div>

    <!-- 导入结果弹窗 -->
    <BaseModal v-model="showImportResult" title="导入结果" size="md" mode="view">
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

    <!-- UUID 导入结果弹窗（费用） -->
    <BaseModal v-model="showUuidImportResult" title="UUID导入结果" size="md" mode="view">
      <p>新增 <strong>{{ uuidImportResult?.imported || 0 }}</strong> 条，更新 <strong>{{ uuidImportResult?.updated || 0 }}</strong> 条，跳过 <strong>{{ uuidImportResult?.skipped || 0 }}</strong> 条</p>
      <div v-if="uuidImportResult?.errors?.length" class="text-danger-600 text-xs mt-2">
        <div v-for="err in uuidImportResult.errors" :key="err">{{ err }}</div>
      </div>
      <template #footer>
        <BaseButton @click="showUuidImportResult = false">确定</BaseButton>
      </template>
    </BaseModal>

    <!-- UUID 导入结果弹窗（淡旺季） -->
    <BaseModal v-model="showSeasonUuidImportResult" title="UUID导入结果" size="md" mode="view">
      <p>新增 <strong>{{ seasonUuidImportResult?.imported || 0 }}</strong> 条，更新 <strong>{{ seasonUuidImportResult?.updated || 0 }}</strong> 条，跳过 <strong>{{ seasonUuidImportResult?.skipped || 0 }}</strong> 条</p>
      <div v-if="seasonUuidImportResult?.errors?.length" class="text-danger-600 text-xs mt-2">
        <div v-for="err in seasonUuidImportResult.errors" :key="err">{{ err }}</div>
      </div>
      <template #footer>
        <BaseButton @click="showSeasonUuidImportResult = false">确定</BaseButton>
      </template>
    </BaseModal>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { fees, seasons, exportFees, exportSeasons, importFees, importSeasons } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'
import { useUuidImport } from '@/composables/useImport'
import { usePageContext } from '@/composables/usePageContext'
import { useTableSelection } from '@/composables/useTableSelection'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'

const activeTab = ref('fees')
const fileInput = ref<HTMLInputElement | null>(null)
const uuidFileInput = ref<HTMLInputElement | null>(null)
const seasonUuidFileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadFees)
const { importing: uuidImporting, showImportResult: showUuidImportResult, importResult: uuidImportResult, handleImport: handleUuidImport, triggerFileInput: triggerUuidFileInput } = useUuidImport(importFees, loadFees)
const { importing: seasonUuidImporting, showImportResult: showSeasonUuidImportResult, importResult: seasonUuidImportResult, handleImport: handleSeasonUuidImport, triggerFileInput: triggerSeasonUuidFileInput } = useUuidImport(importSeasons, loadSeasons)

// --- 费用 ---
const feeColumns = [
  { key: 'checkbox', label: '', width: '40px' },
  { key: 'index', label: '序号', width: '60px' },
  { key: 'fee_name', label: '费用名称' },
  { key: 'fee_category', label: '分类' },
  { key: 'billing_method', label: '计费方式' },
  { key: 'unit_price', label: '单价' },
  { key: 'is_mandatory', label: '是否必含' },
  { key: 'sort_order', label: '排序' },
  { key: 'remark', label: '备注' },
  { key: 'actions', label: '操作', width: '140px' },
]

const seasonColumns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'season_type', label: '季节类型' },
  { key: 'season_type_label', label: '显示名' },
  { key: 'start_date', label: '开始日期' },
  { key: 'end_date', label: '结束日期' },
  { key: 'price_multiplier', label: '价格倍率' },
  { key: 'remark', label: '备注' },
  { key: 'actions', label: '操作', width: '140px' },
]

const feeItems = ref<any[]>([])
const feeLoading = ref(false)
const showFeeModal = ref(false)
const editingFee = ref<any>(null)
const filterCategory = ref('')

const feeTotal = ref(0)
const { currentPage: feeCurrentPage, pageSize: feePageSize, seqNumber: feeSeqNumber, handleSearch: handleFeeSearch } =
  usePageContext(async () => {
    await loadFees()
  })

// 批量选择（费用Tab）
const { selectedArr, isAllSelected, toggleAll, toggleRow, clearSelection, isSelected } = useTableSelection<any>({
  getRowId: (row) => row.id
})

const pagedFeeItems = computed(() => {
  const start = (feeCurrentPage.value - 1) * feePageSize.value
  return feeItems.value.slice(start, start + feePageSize.value)
})

const feeForm = ref({
  fee_name: '', fee_category: 'insurance', billing_method: 'per_person',
  unit_price: 0, is_mandatory: false, sort_order: 0, remark: ''
})
const feeFormSnapshot = ref<string>('')
const isFeeFormDirty = () => JSON.stringify(feeForm.value) !== feeFormSnapshot.value

function billingLabel(m: string) {
  const map: Record<string, string> = { per_person: '按人', per_person_per_day: '按人天', per_trip: '按团', per_vehicle_per_day: '按车天' }
  return map[m] || m
}

async function loadFees() {
  feeLoading.value = true
  try {
    const params: any = {}
    if (filterCategory.value) params.fee_category = filterCategory.value
    feeItems.value = await fees.list(params)
    feeTotal.value = feeItems.value.length
    // 搜索/刷新时清空选择
    clearSelection()
  } catch (e) { console.error('加载费用失败', e) }
  finally { feeLoading.value = false }
}

function openFeeCreate() {
  editingFee.value = null
  feeForm.value = { fee_name: '', fee_category: 'insurance', billing_method: 'per_person', unit_price: 0, is_mandatory: false, sort_order: 0, remark: '' }
  showFeeModal.value = true
}

function openFeeEdit(item: any) {
  editingFee.value = item
  feeForm.value = { fee_name: item.fee_name, fee_category: item.fee_category, billing_method: item.billing_method, unit_price: item.unit_price, is_mandatory: item.is_mandatory, sort_order: item.sort_order || 0, remark: item.remark || '' }
  feeFormSnapshot.value = JSON.stringify(feeForm.value)
  showFeeModal.value = true
}

async function handleFeeSave() {
  try {
    if (editingFee.value) await fees.update(editingFee.value.id, feeForm.value)
    else await fees.create(feeForm.value)
    showFeeModal.value = false; await loadFees()
  } catch (e) { console.error('保存失败', e); alert('保存失败') }
}

async function handleFeeDelete(item: any) {
  if (!confirm(`确定删除费用「${item.fee_name}」？`)) return
  try { await fees.delete(item.id); await loadFees() }
  catch (e) { console.error('删除失败', e); alert('删除失败') }
}

async function handleBatchDelete() {
  if (selectedArr.value.length === 0) return
  if (!confirm(`确定删除选中的 ${selectedArr.value.length} 个费用？`)) return
  try {
    for (const id of selectedArr.value) {
      await fees.delete(id)
    }
    clearSelection()
    await loadFees()
    alert('批量删除成功')
  } catch (e) { console.error('批量删除失败', e); alert('批量删除失败') }
}

// --- 淡旺季 ---
const seasonItems = ref<any[]>([])
const seasonLoading = ref(false)
const showSeasonModal = ref(false)
const editingSeason = ref<any>(null)

const seasonTotal = ref(0)
const { currentPage: seasonCurrentPage, pageSize: seasonPageSize, seqNumber: seasonSeqNumber } =
  usePageContext(async () => {
    await loadSeasons()
  })

const pagedSeasonItems = computed(() => {
  const start = (seasonCurrentPage.value - 1) * seasonPageSize.value
  return seasonItems.value.slice(start, start + seasonPageSize.value)
})

const seasonForm = ref({
  season_type: '', season_type_label: '', start_date: '', end_date: '',
  price_multiplier: 1, remark: ''
})
const seasonFormSnapshot = ref<string>('')
const isSeasonFormDirty = () => JSON.stringify(seasonForm.value) !== seasonFormSnapshot.value

async function loadSeasons() {
  seasonLoading.value = true
  try {
    seasonItems.value = await seasons.list()
    seasonTotal.value = seasonItems.value.length
  }
  catch (e) { console.error('加载淡旺季失败', e) }
  finally { seasonLoading.value = false }
}

function openSeasonCreate() {
  editingSeason.value = null
  seasonForm.value = { season_type: '', season_type_label: '', start_date: '', end_date: '', price_multiplier: 1, remark: '' }
  showSeasonModal.value = true
}

function openSeasonEdit(item: any) {
  editingSeason.value = item
  seasonForm.value = { season_type: item.season_type, season_type_label: item.season_type_label || '', start_date: item.start_date || '', end_date: item.end_date || '', price_multiplier: item.price_multiplier || 1, remark: item.remark || '' }
  seasonFormSnapshot.value = JSON.stringify(seasonForm.value)
  showSeasonModal.value = true
}

async function handleSeasonSave() {
  try {
    if (editingSeason.value) await seasons.update(editingSeason.value.id, seasonForm.value)
    else await seasons.create(seasonForm.value)
    showSeasonModal.value = false; await loadSeasons()
  } catch (e) { console.error('保存失败', e); alert('保存失败') }
}

async function handleSeasonDelete(item: any) {
  if (!confirm(`确定删除「${item.season_type_label}」？`)) return
  try { await seasons.delete(item.id); await loadSeasons() }
  catch (e) { console.error('删除失败', e); alert('删除失败') }
}

watch(activeTab, (tab) => {
  if (tab === 'fees' && feeItems.value.length === 0) loadFees()
  if (tab === 'seasons' && seasonItems.value.length === 0) loadSeasons()
})

onMounted(() => { loadFees() })

async function handleFeesExport() {
  try {
    await exportFees()
  } catch (e) {
    console.error('导出失败', e)
    alert('导出失败')
  }
}

async function handleSeasonsExport() {
  try {
    await exportSeasons()
  } catch (e) {
    console.error('导出失败', e)
    alert('导出失败')
  }
}
</script>

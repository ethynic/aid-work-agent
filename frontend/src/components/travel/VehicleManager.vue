<template>
  <div class="page-container p-5">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <BaseInput v-model="searchKeyword" placeholder="筛选区域" size="sm" class="w-80" @keyup.enter="handleSearch(searchKeyword)" />
        <BaseButton size="sm" @click="handleSearch(searchKeyword)">搜索</BaseButton>
      </div>
      <div class="page-toolbar-right">
        <BaseButton :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">批量删除 ({{ selectedArr.length }})</BaseButton>
        <BaseButton @click="openCreate">新增车辆</BaseButton>
        <BaseButton intent="secondary" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </BaseButton>
        <BaseButton intent="secondary" @click="handleExport">导出 Excel</BaseButton>
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
      <template #vehicle_type="{ row }">{{ row.vehicle_type }}</template>
      <template #vehicle_type_label="{ row }">{{ row.vehicle_type_label || '-' }}</template>
      <template #region_name="{ row }">{{ row.region_name || '通用' }}</template>
      <template #seats_max="{ row }">{{ row.seats_max }}座</template>
      <template #pricing_mode="{ row }">{{ row.pricing_mode === 'per_km' ? '按公里' : '按天' }}</template>
      <template #per_km_rate="{ row }">{{ row.per_km_rate || '-' }}</template>
      <template #driver_meal_allowance="{ row }">{{ row.driver_meal_allowance || '-' }}</template>
      <template #driver_accommodation="{ row }">{{ row.driver_accommodation || '-' }}</template>
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
      @change="loadData"
    />

    <BaseModal
      v-model="showModal"
      :title="editingItem ? '编辑车辆' : '新增车辆'"
      size="lg"
      :mode="editingItem ? 'edit' : 'create'"
      :is-dirty="isFormDirty"
    >
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="text-sm text-muted mb-1 block">车型编码 <span class="text-danger-500">*</span></label>
          <BaseSelect v-model="form.vehicle_type">
            <option value="business">商务车</option>
            <option value="coaster">考斯特</option>
            <option value="minibus">中巴</option>
            <option value="bus">大巴</option>
            <option value="large_bus">大型大巴</option>
          </BaseSelect>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">车型显示名 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="form.vehicle_type_label" placeholder="如：别克GL8商务车" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">座位数 <span class="text-danger-500">*</span></label>
          <input v-model.number="form.seats_max" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">区域</label>
          <BaseInput v-model="form.region_name" placeholder="留空为全国通用" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">计价方式</label>
          <BaseSelect v-model="form.pricing_mode">
            <option value="per_km">按公里</option>
            <option value="daily">按天</option>
          </BaseSelect>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">每公里费用（元/km）</label>
          <input v-model.number="form.per_km_rate" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">司机餐补（元/天）</label>
          <input v-model.number="form.driver_meal_allowance" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">司机住宿费（元/晚）</label>
          <input v-model.number="form.driver_accommodation" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
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
    <BaseModal v-model="showImportResult" title="导入结果" size="md" mode="view">
      <p>新增 <strong>{{ importResult?.imported || 0 }}</strong> 条，更新 <strong>{{ importResult?.updated || 0 }}</strong> 条，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 条</p>
      <div v-if="importResult?.errors?.length" class="text-danger-600 text-xs mt-2">
        <div v-for="err in importResult.errors" :key="err">{{ err }}</div>
      </div>
      <template #footer>
        <BaseButton @click="showImportResult = false">确定</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { vehicles, exportVehicles, importVehicles } from '@/api/travelQuote'
import { useUuidImport } from '@/composables/useImport'
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
  { key: 'vehicle_type', label: '车型' },
  { key: 'vehicle_type_label', label: '显示名' },
  { key: 'region_name', label: '区域' },
  { key: 'seats_max', label: '座位数' },
  { key: 'pricing_mode', label: '计价方式' },
  { key: 'per_km_rate', label: '每公里费用' },
  { key: 'driver_meal_allowance', label: '司机餐补' },
  { key: 'driver_accommodation', label: '司机住宿' },
  { key: 'actions', label: '操作', width: '140px' },
]

const allItems = ref<any[]>([])
const loading = ref(false)
const showModal = ref(false)
const editingItem = ref<any>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, triggerFileInput } = useUuidImport(importVehicles, loadData)

const total = ref(0)
const { currentPage, pageSize, searchKeyword, seqNumber, handleSearch } =
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

const defaultForm = {
  vehicle_type: 'business', vehicle_type_label: '', region_name: '',
  seats_max: 7,
  pricing_mode: 'per_km', per_km_rate: null as number | null,
  driver_meal_allowance: null as number | null, driver_accommodation: null as number | null,
  remark: ''
}
const form = ref({ ...defaultForm })
const formSnapshot = ref<string>('')
const isFormDirty = () => JSON.stringify(form.value) !== formSnapshot.value

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (searchKeyword.value) params.region_name = searchKeyword.value
    allItems.value = await vehicles.list(params)
    total.value = allItems.value.length
    // 搜索/刷新时清空选择
    clearSelection()
  } catch (e) {
    console.error('加载车辆数据失败', e)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingItem.value = null
  form.value = { ...defaultForm }
  showModal.value = true
}

function openEdit(item: any) {
  editingItem.value = item
  form.value = {
    vehicle_type: item.vehicle_type, vehicle_type_label: item.vehicle_type_label || '',
    region_name: item.region_name || '', seats_max: item.seats_max,
    pricing_mode: item.pricing_mode || 'per_km', per_km_rate: item.per_km_rate,
    driver_meal_allowance: item.driver_meal_allowance, driver_accommodation: item.driver_accommodation,
    remark: item.remark || ''
  }
  formSnapshot.value = JSON.stringify(form.value)
  showModal.value = true
}

async function handleSave() {
  try {
    if (editingItem.value) {
      await vehicles.update(editingItem.value.id, form.value)
    } else {
      await vehicles.create(form.value)
    }
    showModal.value = false
    await loadData()
  } catch (e) {
    console.error('保存失败', e)
    alert('保存失败，请检查数据')
  }
}

async function handleDelete(item: any) {
  if (!confirm(`确定删除车辆「${item.vehicle_type_label || item.vehicle_type}」？`)) return
  try {
    await vehicles.delete(item.id)
    await loadData()
  } catch (e) {
    console.error('删除失败', e)
    alert('删除失败')
  }
}

async function handleBatchDelete() {
  if (selectedArr.value.length === 0) return
  if (!confirm(`确定删除选中的 ${selectedArr.value.length} 个车辆？`)) return
  try {
    for (const id of selectedArr.value) {
      await vehicles.delete(id)
    }
    clearSelection()
    await loadData()
    alert('批量删除成功')
  } catch (e) {
    console.error('批量删除失败', e)
    alert('批量删除失败')
  }
}

async function handleExport() {
  try {
    await exportVehicles()
  } catch (e) {
    console.error('导出失败', e)
    alert('导出失败')
  }
}

onMounted(() => { loadData() })
</script>

<template>
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <BaseInput v-model="searchKeyword" placeholder="筛选区域" size="sm" class="w-80" @keyup.enter="handleSearch(searchKeyword)" />
        <BaseButton size="sm" @click="handleSearch(searchKeyword)">搜索</BaseButton>
        <BaseSelect v-model="filterTier" size="sm" class="ml-2" @change="handleSearch(searchKeyword)">
          <option value="">全部档次</option>
          <option value="economy">经济餐</option>
          <option value="standard">标准餐</option>
          <option value="quality">品质餐</option>
          <option value="premium">高餐标</option>
          <option value="luxury">豪华餐标</option>
        </BaseSelect>
      </div>
      <div class="page-toolbar-right">
        <BaseButton @click="openCreate">新增餐标</BaseButton>
        <BaseButton intent="secondary" @click="handleDownloadTemplate">下载模板</BaseButton>
        <BaseButton intent="secondary" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </BaseButton>
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none" @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
      </div>
    </div>

    <div class="table-scroll-wrapper flex-1">
    <BaseTable :columns="columns" :data="pagedItems" row-key="id">
      <template #index="{ index }">{{ seqNumber(index) }}</template>
      <template #region_name="{ row }">{{ row.region_name || '通用' }}</template>
      <template #meal_tier="{ row }">{{ row.meal_tier_label || row.meal_tier }}</template>
      <template #meal_type="{ row }">{{ row.meal_type_label || row.meal_type }}</template>
      <template #dishes_standard="{ row }">{{ row.dishes_standard || '-' }}</template>
      <template #remark="{ row }">{{ row.remark || '-' }}</template>
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

    <BaseModal v-model="showModal" :title="editingItem ? '编辑餐标' : '新增餐标'" size="lg">
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="text-sm text-muted mb-1 block">餐标档次 <span class="text-danger-500">*</span></label>
          <BaseSelect v-model="form.meal_tier">
            <option value="economy">经济餐</option>
            <option value="standard">标准餐</option>
            <option value="quality">品质餐</option>
            <option value="premium">高餐标</option>
            <option value="luxury">豪华餐标</option>
          </BaseSelect>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">档次显示名 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="form.meal_tier_label" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">餐类 <span class="text-danger-500">*</span></label>
          <BaseSelect v-model="form.meal_type">
            <option value="breakfast">早餐</option>
            <option value="lunch">午餐</option>
            <option value="dinner">晚餐</option>
            <option value="pack_lunch">路餐</option>
          </BaseSelect>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">餐类显示名 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="form.meal_type_label" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">每人价格 <span class="text-danger-500">*</span></label>
          <input v-model.number="form.price_per_person" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">区域</label>
          <BaseInput v-model="form.region_name" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4 mt-4">
        <div>
          <label class="text-sm text-muted mb-1 block">每桌人数</label>
          <input v-model.number="form.pax_per_table" type="number" class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">季节类型</label>
          <BaseSelect v-model="form.season_type">
            <option value="default">默认</option>
            <option value="peak">旺季</option>
            <option value="shoulder">平季</option>
            <option value="off">淡季</option>
          </BaseSelect>
        </div>
      </div>
      <div class="mt-4">
        <label class="text-sm text-muted mb-1 block">菜品标准</label>
        <BaseInput v-model="form.dishes_standard" placeholder="如：八菜一汤" />
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
import { meals } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'
import { usePageContext } from '@/composables/usePageContext'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'region_name', label: '区域' },
  { key: 'meal_tier', label: '餐标档次' },
  { key: 'meal_type', label: '餐类' },
  { key: 'price_per_person', label: '每人价格' },
  { key: 'pax_per_table', label: '每桌人数' },
  { key: 'dishes_standard', label: '菜品标准' },
  { key: 'season_type', label: '季节' },
  { key: 'remark', label: '备注' },
  { key: 'actions', label: '操作', width: '140px' },
]

const allItems = ref<any[]>([])
const loading = ref(false)
const showModal = ref(false)
const editingItem = ref<any>(null)
const filterTier = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadData)

const total = ref(0)
const { currentPage, pageSize, searchKeyword, seqNumber, handleSearch, handlePageSizeChange } =
  usePageContext(async () => {
    await loadData()
  })

const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return allItems.value.slice(start, start + pageSize.value)
})

const form = ref({
  meal_tier: 'standard', meal_tier_label: '', meal_type: 'lunch', meal_type_label: '',
  price_per_person: 0, region_name: '', pax_per_table: 10,
  dishes_standard: '', season_type: 'default', remark: ''
})

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (searchKeyword.value) params.region_name = searchKeyword.value
    if (filterTier.value) params.meal_tier = filterTier.value
    allItems.value = await meals.list(params)
    total.value = allItems.value.length
  } catch (e) { console.error('加载餐标数据失败', e) }
  finally { loading.value = false }
}

function openCreate() {
  editingItem.value = null
  form.value = { meal_tier: 'standard', meal_tier_label: '', meal_type: 'lunch', meal_type_label: '', price_per_person: 0, region_name: '', pax_per_table: 10, dishes_standard: '', season_type: 'default', remark: '' }
  showModal.value = true
}

function openEdit(item: any) {
  editingItem.value = item
  form.value = { meal_tier: item.meal_tier, meal_tier_label: item.meal_tier_label || '', meal_type: item.meal_type, meal_type_label: item.meal_type_label || '', price_per_person: item.price_per_person, region_name: item.region_name || '', pax_per_table: item.pax_per_table || 10, dishes_standard: item.dishes_standard || '', season_type: item.season_type || 'default', remark: item.remark || '' }
  showModal.value = true
}

async function handleSave() {
  try {
    if (editingItem.value) await meals.update(editingItem.value.id, form.value)
    else await meals.create(form.value)
    showModal.value = false; await loadData()
  } catch (e) { console.error('保存失败', e); alert('保存失败') }
}

async function handleDelete(item: any) {
  if (!confirm('确定删除该餐标？')) return
  try { await meals.delete(item.id); await loadData() }
  catch (e) { console.error('删除失败', e); alert('删除失败') }
}

onMounted(() => { loadData() })
</script>

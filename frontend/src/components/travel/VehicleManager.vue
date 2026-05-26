<template>
  <div class="p-5 max-w-[1400px] mx-auto">
    <div class="flex justify-between items-center mb-5">
      <h2 class="m-0 text-lg">车辆价格管理</h2>
      <div class="flex gap-2.5 items-center">
        <input v-model="filterRegion" @change="loadData" class="px-2.5 py-1.5 border border-default rounded text-sm" placeholder="筛选区域" />
        <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="openCreate">+ 新增车辆</button>
        <button class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </button>
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none" @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
      </div>
    </div>

    <table class="w-full border-collapse text-[13px]">
      <thead>
        <tr>
          <th class="w-16 p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">序号</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">车型</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">显示名</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">区域</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">座位数</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">计价方式</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">每公里费用</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">司机餐补</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">司机住宿</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="10" class="p-2 text-center text-muted">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="10" class="p-2 text-center text-muted">暂无数据</td></tr>
        <tr v-for="(item, index) in items" :key="item.id" class="hover:bg-surface-hover">
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ index + 1 }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.vehicle_type }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.vehicle_type_label || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.region_name || '通用' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.seats_max }}座</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.pricing_mode === 'per_km' ? '按公里' : '按天' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.per_km_rate || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.driver_meal_allowance || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.driver_accommodation || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200 whitespace-nowrap">
            <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer" @click="openEdit(item)">编辑</button>
            <button class="text-danger-600 hover:bg-danger-50 px-2.5 py-1 rounded text-[13px] border border-danger-600 cursor-pointer" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showModal = false">
      <div class="bg-white rounded-lg p-6 w-[600px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <h3 class="m-0 mb-5">{{ editingItem ? '编辑车辆' : '新增车辆' }}</h3>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">车型编码 *</label>
            <select v-model="form.vehicle_type" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="business">商务车</option>
              <option value="coaster">考斯特</option>
              <option value="minibus">中巴</option>
              <option value="bus">大巴</option>
              <option value="large_bus">大型大巴</option>
            </select>
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">车型显示名 *</label>
            <input v-model="form.vehicle_type_label" placeholder="如：别克GL8商务车" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">座位数 *</label>
            <input v-model.number="form.seats_max" type="number" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">区域</label>
            <input v-model="form.region_name" placeholder="留空为全国通用" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">计价方式</label>
            <select v-model="form.pricing_mode" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="per_km">按公里</option>
              <option value="daily">按天</option>
            </select>
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">每公里费用（元/km）</label>
            <input v-model.number="form.per_km_rate" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">司机餐补（元/天）</label>
            <input v-model.number="form.driver_meal_allowance" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">司机住宿费（元/晚）</label>
            <input v-model.number="form.driver_accommodation" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="mb-3.5">
          <label class="block mb-1 text-[13px] text-muted">备注</label>
          <input v-model="form.remark" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
        </div>
        <div class="flex justify-end gap-2.5 mt-5">
          <button class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" @click="showModal = false">取消</button>
          <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="handleSave">保存</button>
        </div>
      </div>
    </div>

    <!-- 导入结果弹窗 -->
    <div v-if="showImportResult" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showImportResult = false">
      <div class="bg-white rounded-lg p-6 w-[600px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <h3 class="m-0 mb-5">导入结果</h3>
        <p>成功导入 <strong>{{ importResult?.imported || 0 }}</strong> 条，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 条</p>
        <div v-if="importResult?.errors?.length" class="text-danger-600 text-xs mt-2">
          <div v-for="err in importResult.errors" :key="err">{{ err }}</div>
        </div>
        <div class="flex justify-end gap-2.5 mt-5">
          <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="showImportResult = false">确定</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { vehicles } from '@/api/travelQuote'
import { useVehicleImport } from '@/composables/useImport'

const items = ref<any[]>([])
const loading = ref(false)
const showModal = ref(false)
const editingItem = ref<any>(null)
const filterRegion = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, triggerFileInput } = useVehicleImport(loadData)

const defaultForm = {
  vehicle_type: 'business', vehicle_type_label: '', region_name: '',
  seats_max: 7,
  pricing_mode: 'per_km', per_km_rate: null as number | null,
  driver_meal_allowance: null as number | null, driver_accommodation: null as number | null,
  remark: ''
}
const form = ref({ ...defaultForm })

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (filterRegion.value) params.region_name = filterRegion.value
    items.value = await vehicles.list(params)
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

onMounted(() => { loadData() })
</script>


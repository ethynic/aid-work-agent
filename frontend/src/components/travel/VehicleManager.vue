<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>车辆价格管理</h2>
      <div class="actions">
        <input v-model="filterRegion" @change="loadData" class="filter-select" placeholder="筛选区域" />
        <button class="btn-primary" @click="openCreate">+ 新增车辆</button>
        <button class="btn-secondary" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </button>
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none" @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
      </div>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>车型</th>
          <th>显示名</th>
          <th>区域</th>
          <th>座位数</th>
          <th>计价方式</th>
          <th>每公里费用</th>
          <th>司机餐补</th>
          <th>司机住宿</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="9" class="center">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="9" class="center">暂无数据</td></tr>
        <tr v-for="item in items" :key="item.id">
          <td>{{ item.vehicle_type }}</td>
          <td>{{ item.vehicle_type_label || '-' }}</td>
          <td>{{ item.region_name || '通用' }}</td>
          <td>{{ item.seats_max }}座</td>
          <td>{{ item.pricing_mode === 'per_km' ? '按公里' : '按天' }}</td>
          <td>{{ item.per_km_rate || '-' }}</td>
          <td>{{ item.driver_meal_allowance || '-' }}</td>
          <td>{{ item.driver_accommodation || '-' }}</td>
          <td class="actions-cell">
            <button class="btn-sm" @click="openEdit(item)">编辑</button>
            <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <h3>{{ editingItem ? '编辑车辆' : '新增车辆' }}</h3>
        <div class="form-row">
          <div class="form-group">
            <label>车型编码 *</label>
            <select v-model="form.vehicle_type">
              <option value="business">商务车</option>
              <option value="coaster">考斯特</option>
              <option value="minibus">中巴</option>
              <option value="bus">大巴</option>
              <option value="large_bus">大型大巴</option>
            </select>
          </div>
          <div class="form-group">
            <label>车型显示名 *</label>
            <input v-model="form.vehicle_type_label" placeholder="如：别克GL8商务车" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>座位数 *</label>
            <input v-model.number="form.seats_max" type="number" />
          </div>
          <div class="form-group">
            <label>区域</label>
            <input v-model="form.region_name" placeholder="留空为全国通用" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>计价方式</label>
            <select v-model="form.pricing_mode">
              <option value="per_km">按公里</option>
              <option value="daily">按天</option>
            </select>
          </div>
          <div class="form-group">
            <label>每公里费用（元/km）</label>
            <input v-model.number="form.per_km_rate" type="number" step="0.01" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>司机餐补（元/天）</label>
            <input v-model.number="form.driver_meal_allowance" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>司机住宿费（元/晚）</label>
            <input v-model.number="form.driver_accommodation" type="number" step="0.01" />
          </div>
        </div>
        <div class="form-group">
          <label>备注</label>
          <input v-model="form.remark" />
        </div>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showModal = false">取消</button>
          <button class="btn-primary" @click="handleSave">保存</button>
        </div>
      </div>
    </div>

    <!-- 导入结果弹窗 -->
    <div v-if="showImportResult" class="modal-overlay" @click.self="showImportResult = false">
      <div class="modal-content">
        <h3>导入结果</h3>
        <p>成功导入 <strong>{{ importResult?.imported || 0 }}</strong> 条，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 条</p>
        <div v-if="importResult?.errors?.length" style="color:#dc2626;font-size:12px;margin-top:8px">
          <div v-for="err in importResult.errors" :key="err">{{ err }}</div>
        </div>
        <div class="modal-actions">
          <button class="btn-primary" @click="showImportResult = false">确定</button>
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

<style scoped>
.manager-container { padding: 20px; max-width: 1400px; margin: 0 auto; }
.header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.header-bar h2 { margin: 0; font-size: 18px; }
.actions { display: flex; gap: 10px; align-items: center; }
.filter-select { padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th, .data-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee; }
.data-table th { background: #f5f7fa; font-weight: 600; color: #333; }
.data-table tr:hover { background: #fafbfc; }
.center { text-align: center; color: #999; }
.actions-cell { white-space: nowrap; }
.btn-primary { background: #4f46e5; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; }
.btn-primary:hover { background: #4338ca; }
.btn-secondary { background: #f3f4f6; color: #333; border: 1px solid #ddd; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; }
.btn-sm { padding: 4px 10px; border: 1px solid #ddd; border-radius: 3px; background: white; cursor: pointer; font-size: 13px; }
.btn-sm:hover { background: #f5f5f5; }
.btn-danger { color: #dc2626; border-color: #dc2626; }
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
</style>

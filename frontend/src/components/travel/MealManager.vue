<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>餐标价格管理</h2>
      <div class="actions">
        <input v-model="filterRegion" @change="loadData" class="filter-select" placeholder="筛选区域" />
        <select v-model="filterTier" @change="loadData" class="filter-select">
          <option value="">全部档次</option>
          <option value="economy">经济餐</option>
          <option value="standard">标准餐</option>
          <option value="quality">品质餐</option>
          <option value="premium">高餐标</option>
          <option value="luxury">豪华餐标</option>
        </select>
        <button class="btn-primary" @click="openCreate">+ 新增餐标</button>
        <button class="btn-secondary" @click="handleDownloadTemplate">下载模板</button>
        <button class="btn-secondary" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </button>
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none" @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
      </div>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>区域</th>
          <th>餐标档次</th>
          <th>餐类</th>
          <th>每人价格</th>
          <th>每桌人数</th>
          <th>菜品标准</th>
          <th>季节</th>
          <th>备注</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="9" class="center">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="9" class="center">暂无数据</td></tr>
        <tr v-for="item in items" :key="item.id">
          <td>{{ item.region_name || '通用' }}</td>
          <td>{{ item.meal_tier_label || item.meal_tier }}</td>
          <td>{{ item.meal_type_label || item.meal_type }}</td>
          <td>{{ item.price_per_person }}</td>
          <td>{{ item.pax_per_table }}</td>
          <td>{{ item.dishes_standard || '-' }}</td>
          <td>{{ item.season_type }}</td>
          <td>{{ item.remark || '-' }}</td>
          <td class="actions-cell">
            <button class="btn-sm" @click="openEdit(item)">编辑</button>
            <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <h3>{{ editingItem ? '编辑餐标' : '新增餐标' }}</h3>
        <div class="form-row">
          <div class="form-group">
            <label>餐标档次 *</label>
            <select v-model="form.meal_tier">
              <option value="economy">经济餐</option>
              <option value="standard">标准餐</option>
              <option value="quality">品质餐</option>
              <option value="premium">高餐标</option>
              <option value="luxury">豪华餐标</option>
            </select>
          </div>
          <div class="form-group">
            <label>档次显示名 *</label>
            <input v-model="form.meal_tier_label" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>餐类 *</label>
            <select v-model="form.meal_type">
              <option value="breakfast">早餐</option>
              <option value="lunch">午餐</option>
              <option value="dinner">晚餐</option>
              <option value="pack_lunch">路餐</option>
            </select>
          </div>
          <div class="form-group">
            <label>餐类显示名 *</label>
            <input v-model="form.meal_type_label" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>每人价格 *</label>
            <input v-model.number="form.price_per_person" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>区域</label>
            <input v-model="form.region_name" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>每桌人数</label>
            <input v-model.number="form.pax_per_table" type="number" />
          </div>
          <div class="form-group">
            <label>季节类型</label>
            <select v-model="form.season_type">
              <option value="default">默认</option>
              <option value="peak">旺季</option>
              <option value="shoulder">平季</option>
              <option value="off">淡季</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>菜品标准</label>
          <input v-model="form.dishes_standard" placeholder="如：八菜一汤" />
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
        <p>成功导入 <strong>{{ importResult?.total_imported || 0 }}</strong> 条，跳过 <strong>{{ importResult?.total_skipped || 0 }}</strong> 条</p>
        <div v-for="r in importResult?.results" :key="r.sheet" style="margin-bottom:8px;font-size:13px">
          {{ r.sheet }}：导入 {{ r.imported }} 条，跳过 {{ r.skipped }} 条
          <div v-if="r.errors.length" style="color:#dc2626;font-size:12px;margin-top:2px">
            <div v-for="err in r.errors" :key="err">{{ err }}</div>
          </div>
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
import { meals } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'

const items = ref<any[]>([])
const loading = ref(false)
const showModal = ref(false)
const editingItem = ref<any>(null)
const filterRegion = ref('')
const filterTier = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadData)

const form = ref({
  meal_tier: 'standard', meal_tier_label: '', meal_type: 'lunch', meal_type_label: '',
  price_per_person: 0, region_name: '', pax_per_table: 10,
  dishes_standard: '', season_type: 'default', remark: ''
})

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (filterRegion.value) params.region_name = filterRegion.value
    if (filterTier.value) params.meal_tier = filterTier.value
    items.value = await meals.list(params)
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

<style scoped>
.manager-container { padding: 20px; max-width: 1400px; margin: 0 auto; }
.header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.header-bar h2 { margin: 0; font-size: 18px; }
.actions { display: flex; gap: 10px; align-items: center; }
.filter-select { padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th, .data-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee; }
.data-table th { background: #f5f7fa; font-weight: 600; }
.data-table tr:hover { background: #fafbfc; }
.center { text-align: center; color: #999; }
.actions-cell { white-space: nowrap; }
.btn-primary { background: #4f46e5; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.btn-primary:hover { background: #4338ca; }
.btn-secondary { background: #f3f4f6; color: #333; border: 1px solid #ddd; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.btn-sm { padding: 4px 10px; border: 1px solid #ddd; border-radius: 3px; background: white; cursor: pointer; font-size: 12px; }
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

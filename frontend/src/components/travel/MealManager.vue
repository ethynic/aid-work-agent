<template>
  <div class="p-5 max-w-[1400px] mx-auto">
    <div class="flex justify-between items-center mb-5">
      <h2 class="m-0 text-lg">餐标价格管理</h2>
      <div class="flex gap-2.5 items-center">
        <input v-model="filterRegion" @change="loadData" class="px-2.5 py-1.5 border border-default rounded text-sm" placeholder="筛选区域" />
        <select v-model="filterTier" @change="loadData" class="px-2.5 py-1.5 border border-default rounded text-sm">
          <option value="">全部档次</option>
          <option value="economy">经济餐</option>
          <option value="standard">标准餐</option>
          <option value="quality">品质餐</option>
          <option value="premium">高餐标</option>
          <option value="luxury">豪华餐标</option>
        </select>
        <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="openCreate">+ 新增餐标</button>
        <button class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" @click="handleDownloadTemplate">下载模板</button>
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
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">区域</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">餐标档次</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">餐类</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">每人价格</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">每桌人数</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">菜品标准</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">季节</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">备注</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="10" class="p-2 text-center text-muted">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="10" class="p-2 text-center text-muted">暂无数据</td></tr>
        <tr v-for="(item, index) in items" :key="item.id" class="hover:bg-surface-hover">
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ index + 1 }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.region_name || '通用' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.meal_tier_label || item.meal_tier }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.meal_type_label || item.meal_type }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.price_per_person }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.pax_per_table }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.dishes_standard || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.season_type }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.remark || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200 whitespace-nowrap">
            <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer" @click="openEdit(item)">编辑</button>
            <button class="text-danger-600 hover:bg-danger-50 px-2.5 py-1 rounded text-[13px] border border-danger-600 cursor-pointer" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showModal = false">
      <div class="bg-white rounded-lg p-6 w-[600px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <h3 class="m-0 mb-5">{{ editingItem ? '编辑餐标' : '新增餐标' }}</h3>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">餐标档次 *</label>
            <select v-model="form.meal_tier" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="economy">经济餐</option>
              <option value="standard">标准餐</option>
              <option value="quality">品质餐</option>
              <option value="premium">高餐标</option>
              <option value="luxury">豪华餐标</option>
            </select>
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">档次显示名 *</label>
            <input v-model="form.meal_tier_label" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">餐类 *</label>
            <select v-model="form.meal_type" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="breakfast">早餐</option>
              <option value="lunch">午餐</option>
              <option value="dinner">晚餐</option>
              <option value="pack_lunch">路餐</option>
            </select>
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">餐类显示名 *</label>
            <input v-model="form.meal_type_label" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">每人价格 *</label>
            <input v-model.number="form.price_per_person" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">区域</label>
            <input v-model="form.region_name" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">每桌人数</label>
            <input v-model.number="form.pax_per_table" type="number" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">季节类型</label>
            <select v-model="form.season_type" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="default">默认</option>
              <option value="peak">旺季</option>
              <option value="shoulder">平季</option>
              <option value="off">淡季</option>
            </select>
          </div>
        </div>
        <div class="mb-3.5">
          <label class="block mb-1 text-[13px] text-muted">菜品标准</label>
          <input v-model="form.dishes_standard" placeholder="如：八菜一汤" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
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
        <p>成功导入 <strong>{{ importResult?.total_imported || 0 }}</strong> 条，跳过 <strong>{{ importResult?.total_skipped || 0 }}</strong> 条</p>
        <div v-for="r in importResult?.results" :key="r.sheet" class="mb-2 text-[13px]">
          {{ r.sheet }}：导入 {{ r.imported }} 条，跳过 {{ r.skipped }} 条
          <div v-if="r.errors.length" class="text-danger-600 text-xs mt-0.5">
            <div v-for="err in r.errors" :key="err">{{ err }}</div>
          </div>
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


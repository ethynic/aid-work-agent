<template>
  <div class="p-5 max-w-[1400px] mx-auto">
    <div class="flex justify-between items-center mb-5">
      <h2 class="m-0 text-lg">导游费用管理</h2>
      <div class="flex gap-2.5 items-center">
        <input v-model="filterRegion" @change="loadData" class="px-2.5 py-1.5 border border-default rounded text-sm" placeholder="筛选区域" />
        <select v-model="filterType" @change="loadData" class="px-2.5 py-1.5 border border-default rounded text-sm">
          <option value="">全部类型</option>
          <option value="local">地接导游</option>
          <option value="national">全陪导游</option>
          <option value="research">研学导师</option>
          <option value="driver_guide">司兼导</option>
        </select>
        <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="openCreate">+ 新增导游</button>
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
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">导游类型</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">级别</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">计费方式</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">日薪</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">整团费</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">外语加价</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">旺季倍率</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">季节</th>
          <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default whitespace-nowrap">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="11" class="p-2 text-center text-muted">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="11" class="p-2 text-center text-muted">暂无数据</td></tr>
        <tr v-for="(item, index) in items" :key="item.id" class="hover:bg-surface-hover">
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ index + 1 }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.region_name || '通用' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.guide_type_label || item.guide_type }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.guide_level_label || item.guide_level }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.billing_method === 'daily' ? '按天' : '按团' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.daily_rate || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.trip_rate || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.language_premium || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.peak_season_multiplier || '-' }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.season_type }}</td>
          <td class="p-2 px-2.5 text-left border-b border-gray-200 whitespace-nowrap">
            <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer" @click="openEdit(item)">编辑</button>
            <button class="text-danger-600 hover:bg-danger-50 px-2.5 py-1 rounded text-[13px] border border-danger-600 cursor-pointer" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showModal = false">
      <div class="bg-white rounded-lg p-6 w-[600px] max-w-[90vw] max-h-[85vh] overflow-y-auto">
        <h3 class="m-0 mb-5">{{ editingItem ? '编辑导游' : '新增导游' }}</h3>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">导游类型 *</label>
            <select v-model="form.guide_type" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="local">地接导游</option>
              <option value="national">全陪导游</option>
              <option value="research">研学导师</option>
              <option value="driver_guide">司兼导</option>
            </select>
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">类型显示名 *</label>
            <input v-model="form.guide_type_label" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">级别</label>
            <select v-model="form.guide_level" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="junior">初级</option>
              <option value="standard">标准</option>
              <option value="senior">高级</option>
              <option value="premium">十佳</option>
            </select>
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">级别显示名</label>
            <input v-model="form.guide_level_label" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">计费方式</label>
            <select v-model="form.billing_method" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
              <option value="daily">按天</option>
              <option value="per_trip">按团</option>
            </select>
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">区域</label>
            <input v-model="form.region_name" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">日薪</label>
            <input v-model.number="form.daily_rate" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">整团费</label>
            <input v-model.number="form.trip_rate" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="flex gap-3">
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">外语加价(元/天)</label>
            <input v-model.number="form.language_premium" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex-1 mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">旺季上浮倍率</label>
            <input v-model.number="form.peak_season_multiplier" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
        </div>
        <div class="mb-3.5">
          <label class="block mb-1 text-[13px] text-muted">季节类型</label>
          <select v-model="form.season_type" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
            <option value="default">默认</option>
            <option value="peak">旺季</option>
            <option value="shoulder">平季</option>
            <option value="off">淡季</option>
          </select>
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
import { guides } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'

const items = ref<any[]>([])
const loading = ref(false)
const showModal = ref(false)
const editingItem = ref<any>(null)
const filterRegion = ref('')
const filterType = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadData)

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
    if (filterRegion.value) params.region_name = filterRegion.value
    if (filterType.value) params.guide_type = filterType.value
    items.value = await guides.list(params)
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

onMounted(() => { loadData() })
</script>


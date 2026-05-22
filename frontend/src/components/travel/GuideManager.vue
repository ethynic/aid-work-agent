<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>导游费用管理</h2>
      <div class="actions">
        <input v-model="filterRegion" @change="loadData" class="filter-select" placeholder="筛选区域" />
        <select v-model="filterType" @change="loadData" class="filter-select">
          <option value="">全部类型</option>
          <option value="local">地接导游</option>
          <option value="national">全陪导游</option>
          <option value="research">研学导师</option>
          <option value="driver_guide">司兼导</option>
        </select>
        <button class="btn-primary" @click="openCreate">+ 新增导游</button>
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
          <th class="w-16">序号</th>
          <th>区域</th>
          <th>导游类型</th>
          <th>级别</th>
          <th>计费方式</th>
          <th>日薪</th>
          <th>整团费</th>
          <th>外语加价</th>
          <th>旺季倍率</th>
          <th>季节</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="11" class="center">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="11" class="center">暂无数据</td></tr>
        <tr v-for="(item, index) in items" :key="item.id">
          <td>{{ index + 1 }}</td>
          <td>{{ item.region_name || '通用' }}</td>
          <td>{{ item.guide_type_label || item.guide_type }}</td>
          <td>{{ item.guide_level_label || item.guide_level }}</td>
          <td>{{ item.billing_method === 'daily' ? '按天' : '按团' }}</td>
          <td>{{ item.daily_rate || '-' }}</td>
          <td>{{ item.trip_rate || '-' }}</td>
          <td>{{ item.language_premium || '-' }}</td>
          <td>{{ item.peak_season_multiplier || '-' }}</td>
          <td>{{ item.season_type }}</td>
          <td class="actions-cell">
            <button class="btn-sm" @click="openEdit(item)">编辑</button>
            <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <h3>{{ editingItem ? '编辑导游' : '新增导游' }}</h3>
        <div class="form-row">
          <div class="form-group">
            <label>导游类型 *</label>
            <select v-model="form.guide_type">
              <option value="local">地接导游</option>
              <option value="national">全陪导游</option>
              <option value="research">研学导师</option>
              <option value="driver_guide">司兼导</option>
            </select>
          </div>
          <div class="form-group">
            <label>类型显示名 *</label>
            <input v-model="form.guide_type_label" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>级别</label>
            <select v-model="form.guide_level">
              <option value="junior">初级</option>
              <option value="standard">标准</option>
              <option value="senior">高级</option>
              <option value="premium">十佳</option>
            </select>
          </div>
          <div class="form-group">
            <label>级别显示名</label>
            <input v-model="form.guide_level_label" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>计费方式</label>
            <select v-model="form.billing_method">
              <option value="daily">按天</option>
              <option value="per_trip">按团</option>
            </select>
          </div>
          <div class="form-group">
            <label>区域</label>
            <input v-model="form.region_name" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>日薪</label>
            <input v-model.number="form.daily_rate" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>整团费</label>
            <input v-model.number="form.trip_rate" type="number" step="0.01" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>外语加价(元/天)</label>
            <input v-model.number="form.language_premium" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>旺季上浮倍率</label>
            <input v-model.number="form.peak_season_multiplier" type="number" step="0.01" />
          </div>
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

<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>费用与淡旺季管理</h2>
    </div>

    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'fees' }]" @click="activeTab = 'fees'">其他费用</button>
      <button :class="['tab-btn', { active: activeTab === 'seasons' }]" @click="activeTab = 'seasons'">淡旺季配置</button>
    </div>

    <!-- 其他费用 Tab -->
    <div v-if="activeTab === 'fees'">
      <div class="header-bar">
        <div></div>
        <div class="actions">
          <select v-model="filterCategory" @change="loadFees" class="filter-select">
            <option value="">全部分类</option>
            <option value="insurance">保险</option>
            <option value="service">服务费</option>
            <option value="transport">交通</option>
            <option value="other">其他</option>
          </select>
          <button class="btn-primary" @click="openFeeCreate">+ 新增费用</button>
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
            <th>费用名称</th>
            <th>分类</th>
            <th>计费方式</th>
            <th>单价</th>
            <th>是否必含</th>
            <th>排序</th>
            <th>备注</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="feeLoading"><td colspan="8" class="center">加载中...</td></tr>
          <tr v-else-if="feeItems.length === 0"><td colspan="8" class="center">暂无数据</td></tr>
          <tr v-for="item in feeItems" :key="item.id">
            <td>{{ item.fee_name }}</td>
            <td>{{ item.fee_category }}</td>
            <td>{{ billingLabel(item.billing_method) }}</td>
            <td>{{ item.unit_price }}</td>
            <td>{{ item.is_mandatory ? '是' : '否' }}</td>
            <td>{{ item.sort_order }}</td>
            <td>{{ item.remark || '-' }}</td>
            <td class="actions-cell">
              <button class="btn-sm" @click="openFeeEdit(item)">编辑</button>
              <button class="btn-sm btn-danger" @click="handleFeeDelete(item)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 费用弹窗 -->
      <div v-if="showFeeModal" class="modal-overlay" @click.self="showFeeModal = false">
        <div class="modal-content">
          <h3>{{ editingFee ? '编辑费用' : '新增费用' }}</h3>
          <div class="form-row">
            <div class="form-group">
              <label>费用名称 *</label>
              <input v-model="feeForm.fee_name" />
            </div>
            <div class="form-group">
              <label>费用分类 *</label>
              <select v-model="feeForm.fee_category">
                <option value="insurance">保险</option>
                <option value="service">服务费</option>
                <option value="transport">交通</option>
                <option value="other">其他</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>计费方式 *</label>
              <select v-model="feeForm.billing_method">
                <option value="per_person">按人</option>
                <option value="per_person_per_day">按人天</option>
                <option value="per_trip">按团</option>
                <option value="per_vehicle_per_day">按车天</option>
              </select>
            </div>
            <div class="form-group">
              <label>单价 *</label>
              <input v-model.number="feeForm.unit_price" type="number" step="0.01" />
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>是否必含</label>
              <select v-model="feeForm.is_mandatory">
                <option :value="false">否</option>
                <option :value="true">是</option>
              </select>
            </div>
            <div class="form-group">
              <label>排序</label>
              <input v-model.number="feeForm.sort_order" type="number" />
            </div>
          </div>
          <div class="form-group">
            <label>备注</label>
            <input v-model="feeForm.remark" />
          </div>
          <div class="modal-actions">
            <button class="btn-secondary" @click="showFeeModal = false">取消</button>
            <button class="btn-primary" @click="handleFeeSave">保存</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 淡旺季配置 Tab -->
    <div v-if="activeTab === 'seasons'">
      <div class="header-bar">
        <div></div>
        <button class="btn-primary" @click="openSeasonCreate">+ 新增季节</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>季节类型</th>
            <th>显示名</th>
            <th>开始日期</th>
            <th>结束日期</th>
            <th>价格倍率</th>
            <th>备注</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="seasonLoading"><td colspan="7" class="center">加载中...</td></tr>
          <tr v-else-if="seasonItems.length === 0"><td colspan="7" class="center">暂无数据</td></tr>
          <tr v-for="item in seasonItems" :key="item.id">
            <td>{{ item.season_type }}</td>
            <td>{{ item.season_type_label }}</td>
            <td>{{ item.start_date }}</td>
            <td>{{ item.end_date }}</td>
            <td>{{ item.price_multiplier }}</td>
            <td>{{ item.remark || '-' }}</td>
            <td class="actions-cell">
              <button class="btn-sm" @click="openSeasonEdit(item)">编辑</button>
              <button class="btn-sm btn-danger" @click="handleSeasonDelete(item)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 季节弹窗 -->
      <div v-if="showSeasonModal" class="modal-overlay" @click.self="showSeasonModal = false">
        <div class="modal-content">
          <h3>{{ editingSeason ? '编辑季节' : '新增季节' }}</h3>
          <div class="form-row">
            <div class="form-group">
              <label>季节类型编码 *</label>
              <input v-model="seasonForm.season_type" placeholder="如：peak, off" />
            </div>
            <div class="form-group">
              <label>季节显示名 *</label>
              <input v-model="seasonForm.season_type_label" placeholder="如：旺季、淡季" />
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>开始日期 *</label>
              <input v-model="seasonForm.start_date" type="date" />
            </div>
            <div class="form-group">
              <label>结束日期 *</label>
              <input v-model="seasonForm.end_date" type="date" />
            </div>
          </div>
          <div class="form-group">
            <label>价格倍率</label>
            <input v-model.number="seasonForm.price_multiplier" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>备注</label>
            <input v-model="seasonForm.remark" />
          </div>
          <div class="modal-actions">
            <button class="btn-secondary" @click="showSeasonModal = false">取消</button>
            <button class="btn-primary" @click="handleSeasonSave">保存</button>
          </div>
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
import { ref, onMounted, watch } from 'vue'
import { fees, seasons } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'

const activeTab = ref('fees')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadFees)

// --- 费用 ---
const feeItems = ref<any[]>([])
const feeLoading = ref(false)
const showFeeModal = ref(false)
const editingFee = ref<any>(null)
const filterCategory = ref('')

const feeForm = ref({
  fee_name: '', fee_category: 'insurance', billing_method: 'per_person',
  unit_price: 0, is_mandatory: false, sort_order: 0, remark: ''
})

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

// --- 淡旺季 ---
const seasonItems = ref<any[]>([])
const seasonLoading = ref(false)
const showSeasonModal = ref(false)
const editingSeason = ref<any>(null)

const seasonForm = ref({
  season_type: '', season_type_label: '', start_date: '', end_date: '',
  price_multiplier: 1, remark: ''
})

async function loadSeasons() {
  seasonLoading.value = true
  try { seasonItems.value = await seasons.list() }
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

// Tab 切换时加载数据
watch(activeTab, (tab) => {
  if (tab === 'fees' && feeItems.value.length === 0) loadFees()
  if (tab === 'seasons' && seasonItems.value.length === 0) loadSeasons()
})

onMounted(() => { loadFees() })
</script>

<style scoped>
.manager-container { padding: 20px; max-width: 1400px; margin: 0 auto; }
.header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.header-bar h2 { margin: 0; font-size: 18px; }
.actions { display: flex; gap: 10px; align-items: center; }
.filter-select { padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
.tab-bar { display: flex; gap: 0; margin-bottom: 20px; border-bottom: 2px solid #e5e7eb; }
.tab-btn { padding: 10px 20px; background: none; border: none; border-bottom: 2px solid transparent; margin-bottom: -2px; cursor: pointer; font-size: 14px; color: #666; }
.tab-btn:hover { color: #333; }
.tab-btn.active { color: #4f46e5; border-bottom-color: #4f46e5; font-weight: 600; }
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
.modal-content { background: white; border-radius: 8px; padding: 24px; width: 560px; max-width: 90vw; }
.modal-content h3 { margin: 0 0 20px; }
.form-row { display: flex; gap: 12px; }
.form-row .form-group { flex: 1; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; margin-bottom: 4px; font-size: 13px; color: #555; }
.form-group input, .form-group select { width: 100%; padding: 7px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
</style>

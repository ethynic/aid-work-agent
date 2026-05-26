<template>
  <div class="p-5 max-w-[1400px] mx-auto">
    <div class="flex justify-between items-center mb-5">
      <h2 class="m-0 text-lg">费用与淡旺季管理</h2>
    </div>

    <!-- Tab 切换 -->
    <div class="flex mb-5 border-b-2 border-gray-200">
      <button :class="['px-5 py-2.5 bg-transparent border-none border-b-2 border-transparent -mb-0.5 cursor-pointer text-sm', activeTab === 'fees' ? 'text-primary-600 border-b-primary-600 font-semibold' : 'text-muted hover:text-default']" @click="activeTab = 'fees'">其他费用</button>
      <button :class="['px-5 py-2.5 bg-transparent border-none border-b-2 border-transparent -mb-0.5 cursor-pointer text-sm', activeTab === 'seasons' ? 'text-primary-600 border-b-primary-600 font-semibold' : 'text-muted hover:text-default']" @click="activeTab = 'seasons'">淡旺季配置</button>
    </div>

    <!-- 其他费用 Tab -->
    <div v-if="activeTab === 'fees'">
      <div class="flex justify-between items-center mb-5">
        <div></div>
        <div class="flex gap-2.5 items-center">
          <select v-model="filterCategory" @change="loadFees" class="px-2.5 py-1.5 border border-default rounded text-sm">
            <option value="">全部分类</option>
            <option value="insurance">保险</option>
            <option value="service">服务费</option>
            <option value="transport">交通</option>
            <option value="other">其他</option>
          </select>
          <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="openFeeCreate">+ 新增费用</button>
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
            <th class="w-16 p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">序号</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">费用名称</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">分类</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">计费方式</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">单价</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">是否必含</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">排序</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">备注</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="feeLoading"><td colspan="9" class="p-2 text-center text-muted">加载中...</td></tr>
          <tr v-else-if="feeItems.length === 0"><td colspan="9" class="p-2 text-center text-muted">暂无数据</td></tr>
          <tr v-for="(item, index) in feeItems" :key="item.id" class="hover:bg-surface-hover">
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ index + 1 }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.fee_name }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.fee_category }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ billingLabel(item.billing_method) }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.unit_price }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.is_mandatory ? '是' : '否' }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.sort_order }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.remark || '-' }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200 whitespace-nowrap">
              <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer" @click="openFeeEdit(item)">编辑</button>
              <button class="text-danger-600 hover:bg-danger-50 px-2.5 py-1 rounded text-[13px] border border-danger-600 cursor-pointer" @click="handleFeeDelete(item)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 费用弹窗 -->
      <div v-if="showFeeModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showFeeModal = false">
        <div class="bg-white rounded-lg p-6 w-[560px] max-w-[90vw]">
          <h3 class="m-0 mb-5">{{ editingFee ? '编辑费用' : '新增费用' }}</h3>
          <div class="flex gap-3">
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">费用名称 *</label>
              <input v-model="feeForm.fee_name" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
            </div>
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">费用分类 *</label>
              <select v-model="feeForm.fee_category" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
                <option value="insurance">保险</option>
                <option value="service">服务费</option>
                <option value="transport">交通</option>
                <option value="other">其他</option>
              </select>
            </div>
          </div>
          <div class="flex gap-3">
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">计费方式 *</label>
              <select v-model="feeForm.billing_method" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
                <option value="per_person">按人</option>
                <option value="per_person_per_day">按人天</option>
                <option value="per_trip">按团</option>
                <option value="per_vehicle_per_day">按车天</option>
              </select>
            </div>
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">单价 *</label>
              <input v-model.number="feeForm.unit_price" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
            </div>
          </div>
          <div class="flex gap-3">
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">是否必含</label>
              <select v-model="feeForm.is_mandatory" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border">
                <option :value="false">否</option>
                <option :value="true">是</option>
              </select>
            </div>
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">排序</label>
              <input v-model.number="feeForm.sort_order" type="number" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
            </div>
          </div>
          <div class="mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">备注</label>
            <input v-model="feeForm.remark" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex justify-end gap-2.5 mt-5">
            <button class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" @click="showFeeModal = false">取消</button>
            <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="handleFeeSave">保存</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 淡旺季配置 Tab -->
    <div v-if="activeTab === 'seasons'">
      <div class="flex justify-between items-center mb-5">
        <div></div>
        <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="openSeasonCreate">+ 新增季节</button>
      </div>
      <table class="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            <th class="w-16 p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">序号</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">季节类型</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">显示名</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">开始日期</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">结束日期</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">价格倍率</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">备注</th>
            <th class="p-2 px-2.5 text-left border-b border-gray-200 bg-surface font-semibold text-default">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="seasonLoading"><td colspan="8" class="p-2 text-center text-muted">加载中...</td></tr>
          <tr v-else-if="seasonItems.length === 0"><td colspan="8" class="p-2 text-center text-muted">暂无数据</td></tr>
          <tr v-for="(item, index) in seasonItems" :key="item.id" class="hover:bg-surface-hover">
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ index + 1 }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.season_type }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.season_type_label }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.start_date }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.end_date }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.price_multiplier }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200">{{ item.remark || '-' }}</td>
            <td class="p-2 px-2.5 text-left border-b border-gray-200 whitespace-nowrap">
              <button class="px-2.5 py-1 border border-default rounded bg-white text-[13px] hover:bg-gray-50 cursor-pointer" @click="openSeasonEdit(item)">编辑</button>
              <button class="text-danger-600 hover:bg-danger-50 px-2.5 py-1 rounded text-[13px] border border-danger-600 cursor-pointer" @click="handleSeasonDelete(item)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 季节弹窗 -->
      <div v-if="showSeasonModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showSeasonModal = false">
        <div class="bg-white rounded-lg p-6 w-[560px] max-w-[90vw]">
          <h3 class="m-0 mb-5">{{ editingSeason ? '编辑季节' : '新增季节' }}</h3>
          <div class="flex gap-3">
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">季节类型编码 *</label>
              <input v-model="seasonForm.season_type" placeholder="如：peak, off" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
            </div>
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">季节显示名 *</label>
              <input v-model="seasonForm.season_type_label" placeholder="如：旺季、淡季" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
            </div>
          </div>
          <div class="flex gap-3">
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">开始日期 *</label>
              <input v-model="seasonForm.start_date" type="date" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
            </div>
            <div class="flex-1 mb-3.5">
              <label class="block mb-1 text-[13px] text-muted">结束日期 *</label>
              <input v-model="seasonForm.end_date" type="date" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
            </div>
          </div>
          <div class="mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">价格倍率</label>
            <input v-model.number="seasonForm.price_multiplier" type="number" step="0.01" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="mb-3.5">
            <label class="block mb-1 text-[13px] text-muted">备注</label>
            <input v-model="seasonForm.remark" class="w-full px-2.5 py-1.5 border border-default rounded text-sm box-border" />
          </div>
          <div class="flex justify-end gap-2.5 mt-5">
            <button class="bg-gray-100 text-gray-700 hover:bg-gray-200 px-4 py-2 rounded-lg text-sm font-medium" @click="showSeasonModal = false">取消</button>
            <button class="bg-primary-600 hover:bg-primary-700 text-white px-4 py-2 rounded-lg text-sm font-medium" @click="handleSeasonSave">保存</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 导入结果弹窗 -->
    <div v-if="showImportResult" class="fixed inset-0 bg-black/40 flex items-center justify-center z-[100]" @click.self="showImportResult = false">
      <div class="bg-white rounded-lg p-6 w-[560px] max-w-[90vw]">
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


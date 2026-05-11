<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>景点门票管理</h2>
      <div class="actions">
        <select v-model="filterRegion" @change="loadData" class="filter-select">
          <option value="">全部区域</option>
          <option v-for="r in regionOptions" :key="r" :value="r">{{ r }}</option>
        </select>
        <button class="btn-primary" @click="openCreate">+ 新增景点</button>
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
          <th style="width:30px"></th>
          <th>景点名称</th>
          <th>区域</th>
          <th>分类</th>
          <th>游览时长(h)</th>
          <th>景区交通</th>
          <th>交通费</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="8" class="center">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="8" class="center">暂无数据</td></tr>
        <template v-for="item in items" :key="item.id">
          <tr>
            <td>
              <button class="btn-expand" @click="toggleExpand(item.id)">
                {{ expandedId === item.id ? '▼' : '▶' }}
              </button>
            </td>
            <td>{{ item.name }}</td>
            <td>{{ item.region_name || '通用' }}</td>
            <td>{{ item.category || '-' }}</td>
            <td>{{ item.visit_duration_hours || '-' }}</td>
            <td>{{ item.internal_transport_name || '-' }}</td>
            <td>{{ item.internal_transport_price || '-' }}</td>
            <td class="actions-cell">
              <button class="btn-sm" @click="openEdit(item)">编辑</button>
              <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
            </td>
          </tr>
          <!-- 门票子表格 -->
          <tr v-if="expandedId === item.id">
            <td colspan="8" class="sub-table-cell">
              <div class="sub-table-wrap">
                <div class="sub-header">
                  <span>门票价格</span>
                  <button class="btn-sm" @click="openTicketCreate(item.id, item.name)">+ 新增票种</button>
                </div>
                <table class="sub-table">
                  <thead>
                    <tr>
                      <th>票种</th>
                      <th>显示名</th>
                      <th>挂牌价</th>
                      <th>协议价</th>
                      <th>团体价</th>
                      <th>团体最低人数</th>
                      <th>季节</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="ticketLoading"><td colspan="8" class="center">加载中...</td></tr>
                    <tr v-else-if="currentTickets.length === 0"><td colspan="8" class="center">暂无票种</td></tr>
                    <tr v-for="t in currentTickets" :key="t.id">
                      <td>{{ t.ticket_type }}</td>
                      <td>{{ t.ticket_type_label }}</td>
                      <td>{{ t.retail_price }}</td>
                      <td>{{ t.agency_price || '-' }}</td>
                      <td>{{ t.group_price || '-' }}</td>
                      <td>{{ t.group_min_people || '-' }}</td>
                      <td>{{ t.season_type }}</td>
                      <td class="actions-cell">
                        <button class="btn-sm" @click="openTicketEdit(t)">编辑</button>
                        <button class="btn-sm btn-danger" @click="handleTicketDelete(t)">删除</button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>

    <!-- 景点弹窗 -->
    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <h3>{{ editingItem ? '编辑景点' : '新增景点' }}</h3>
        <div class="form-row">
          <div class="form-group">
            <label>景点名称 *</label>
            <input v-model="form.name" />
          </div>
          <div class="form-group">
            <label>区域</label>
            <input v-model="form.region_name" placeholder="如：黔南" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>分类</label>
            <select v-model="form.category">
              <option value="">无</option>
              <option value="natural">自然风光</option>
              <option value="cultural">人文历史</option>
              <option value="theme_park">主题乐园</option>
              <option value="museum">博物馆</option>
              <option value="research">研学基地</option>
            </select>
          </div>
          <div class="form-group">
            <label>游览时长(小时)</label>
            <input v-model.number="form.visit_duration_hours" type="number" step="0.5" />
          </div>
        </div>
        <div class="form-group">
          <label>地址</label>
          <input v-model="form.address" />
        </div>
        <div class="form-group">
          <label>开放时间</label>
          <input v-model="form.open_time" placeholder="如：08:00-18:00" />
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>景区交通名称</label>
            <input v-model="form.internal_transport_name" placeholder="如：环保车" />
          </div>
          <div class="form-group">
            <label>景区交通费(元/人)</label>
            <input v-model.number="form.internal_transport_price" type="number" step="0.01" />
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

    <!-- 门票弹窗 -->
    <div v-if="showTicketModal" class="modal-overlay" @click.self="showTicketModal = false">
      <div class="modal-content">
        <h3>{{ editingTicket ? '编辑票种' : `新增票种 - ${ticketParentName}` }}</h3>
        <div class="form-row">
          <div class="form-group">
            <label>票种编码 *</label>
            <select v-model="ticketForm.ticket_type">
              <option value="adult">成人票</option>
              <option value="child_free">儿童免票</option>
              <option value="child_half">儿童优惠票</option>
              <option value="student">学生票</option>
              <option value="elder_half">老人半价票</option>
              <option value="elder_free">老人免票</option>
              <option value="military">军人/优抚票</option>
              <option value="group">团体票</option>
            </select>
          </div>
          <div class="form-group">
            <label>票种显示名 *</label>
            <input v-model="ticketForm.ticket_type_label" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>挂牌价 *</label>
            <input v-model.number="ticketForm.retail_price" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>协议价</label>
            <input v-model.number="ticketForm.agency_price" type="number" step="0.01" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>团体价</label>
            <input v-model.number="ticketForm.group_price" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>团体最低人数</label>
            <input v-model.number="ticketForm.group_min_people" type="number" />
          </div>
        </div>
        <div class="form-group">
          <label>季节类型</label>
          <select v-model="ticketForm.season_type">
            <option value="default">默认</option>
            <option value="peak">旺季</option>
            <option value="shoulder">平季</option>
            <option value="off">淡季</option>
          </select>
        </div>
        <div class="form-group">
          <label>备注</label>
          <input v-model="ticketForm.remark" />
        </div>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showTicketModal = false">取消</button>
          <button class="btn-primary" @click="handleTicketSave">保存</button>
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
import { attractions, tickets, regions } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'

const items = ref<any[]>([])
const regionOptions = ref<string[]>([])
const loading = ref(false)
const filterRegion = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadData)
const expandedId = ref<number | null>(null)
const currentTickets = ref<any[]>([])
const ticketLoading = ref(false)

// 景点表单
const showModal = ref(false)
const editingItem = ref<any>(null)
const form = ref({
  name: '', region_name: '', category: '', address: '', open_time: '',
  visit_duration_hours: null as number | null,
  internal_transport_name: '', internal_transport_price: null as number | null,
  sort_order: 0, remark: ''
})

// 门票表单
const showTicketModal = ref(false)
const editingTicket = ref<any>(null)
const ticketParentId = ref(0)
const ticketParentName = ref('')
const ticketForm = ref({
  ticket_type: 'adult', ticket_type_label: '', retail_price: 0,
  agency_price: null as number | null, group_price: null as number | null,
  group_min_people: null as number | null, season_type: 'default', remark: ''
})

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (filterRegion.value) params.region_name = filterRegion.value
    items.value = await attractions.list(params)
  } catch (e) {
    console.error('加载景点数据失败', e)
  } finally {
    loading.value = false
  }
}

async function loadRegions() {
  try {
    const data = await regions.list()
    regionOptions.value = [...new Set(data.map((r: any) => r.name).filter(Boolean))]
  } catch (e) { /* ignore */ }
}

async function toggleExpand(id: number) {
  if (expandedId.value === id) {
    expandedId.value = null
    return
  }
  expandedId.value = id
  ticketLoading.value = true
  try {
    currentTickets.value = await tickets.list(id)
  } catch (e) {
    console.error('加载门票失败', e)
    currentTickets.value = []
  } finally {
    ticketLoading.value = false
  }
}

function openCreate() {
  editingItem.value = null
  form.value = { name: '', region_name: '', category: '', address: '', open_time: '', visit_duration_hours: null, internal_transport_name: '', internal_transport_price: null, sort_order: 0, remark: '' }
  showModal.value = true
}

function openEdit(item: any) {
  editingItem.value = item
  form.value = { name: item.name, region_name: item.region_name || '', category: item.category || '', address: item.address || '', open_time: item.open_time || '', visit_duration_hours: item.visit_duration_hours, internal_transport_name: item.internal_transport_name || '', internal_transport_price: item.internal_transport_price, sort_order: item.sort_order || 0, remark: item.remark || '' }
  showModal.value = true
}

async function handleSave() {
  try {
    if (editingItem.value) {
      await attractions.update(editingItem.value.id, form.value)
    } else {
      await attractions.create(form.value)
    }
    showModal.value = false
    await loadData()
  } catch (e) {
    console.error('保存失败', e)
    alert('保存失败')
  }
}

async function handleDelete(item: any) {
  if (!confirm(`确定删除景点「${item.name}」？关联的门票也会一并删除。`)) return
  try {
    await attractions.delete(item.id)
    if (expandedId.value === item.id) expandedId.value = null
    await loadData()
  } catch (e) {
    console.error('删除失败', e)
    alert('删除失败')
  }
}

// --- 门票操作 ---
function openTicketCreate(attractionId: number, attractionName: string) {
  editingTicket.value = null
  ticketParentId.value = attractionId
  ticketParentName.value = attractionName
  ticketForm.value = { ticket_type: 'adult', ticket_type_label: '', retail_price: 0, agency_price: null, group_price: null, group_min_people: null, season_type: 'default', remark: '' }
  showTicketModal.value = true
}

function openTicketEdit(t: any) {
  editingTicket.value = t
  ticketForm.value = { ticket_type: t.ticket_type, ticket_type_label: t.ticket_type_label, retail_price: t.retail_price, agency_price: t.agency_price, group_price: t.group_price, group_min_people: t.group_min_people, season_type: t.season_type || 'default', remark: t.remark || '' }
  showTicketModal.value = true
}

async function handleTicketSave() {
  try {
    if (editingTicket.value) {
      await tickets.update(editingTicket.value.id, ticketForm.value)
    } else {
      await tickets.create(ticketParentId.value, ticketForm.value)
    }
    showTicketModal.value = false
    await tickets.list(ticketParentId.value).then(data => currentTickets.value = data)
  } catch (e) {
    console.error('保存门票失败', e)
    alert('保存失败')
  }
}

async function handleTicketDelete(t: any) {
  if (!confirm(`确定删除票种「${t.ticket_type_label}」？`)) return
  try {
    await tickets.delete(t.id)
    await tickets.list(ticketParentId.value).then(data => currentTickets.value = data)
  } catch (e) {
    console.error('删除门票失败', e)
    alert('删除失败')
  }
}

onMounted(() => { loadData(); loadRegions() })
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
.btn-expand { background: none; border: none; cursor: pointer; font-size: 12px; padding: 2px 4px; }
.sub-table-cell { padding: 0 !important; background: #f9fafb; }
.sub-table-wrap { padding: 12px 20px 12px 40px; }
.sub-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; font-size: 13px; }
.sub-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.sub-table th, .sub-table td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #e5e7eb; }
.sub-table th { background: #f3f4f6; font-weight: 500; }
.btn-primary { background: #4f46e5; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; }
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

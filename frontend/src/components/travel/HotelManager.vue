<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>酒店房型管理</h2>
      <div class="actions">
        <select v-model="filterRegion" @change="loadData" class="filter-select">
          <option value="">全部区域</option>
          <option v-for="r in regionOptions" :key="r" :value="r">{{ r }}</option>
        </select>
        <button class="btn-primary" @click="openCreate">+ 新增酒店</button>
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
          <th>酒店名称</th>
          <th>区域</th>
          <th>星级</th>
          <th>地址</th>
          <th>联系电话</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="7" class="center">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="7" class="center">暂无数据</td></tr>
        <template v-for="item in items" :key="item.id">
          <tr>
            <td>
              <button class="btn-expand" @click="toggleExpand(item.id)">
                {{ expandedId === item.id ? '▼' : '▶' }}
              </button>
            </td>
            <td>{{ item.name }}</td>
            <td>{{ item.region_name || '通用' }}</td>
            <td>{{ item.star_rating_label || item.star_rating || '-' }}</td>
            <td>{{ item.address || '-' }}</td>
            <td>{{ item.contact_phone || '-' }}</td>
            <td class="actions-cell">
              <button class="btn-sm" @click="openEdit(item)">编辑</button>
              <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
            </td>
          </tr>
          <tr v-if="expandedId === item.id">
            <td colspan="7" class="sub-table-cell">
              <div class="sub-table-wrap">
                <div class="sub-header">
                  <span>房型价格</span>
                  <button class="btn-sm" @click="openRoomCreate(item.id, item.name)">+ 新增房型</button>
                </div>
                <table class="sub-table">
                  <thead>
                    <tr>
                      <th>房型</th>
                      <th>显示名</th>
                      <th>入住人数</th>
                      <th>床位数</th>
                      <th>门市价</th>
                      <th>协议价</th>
                      <th>含早</th>
                      <th>加床费</th>
                      <th>季节</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-if="roomLoading"><td colspan="10" class="center">加载中...</td></tr>
                    <tr v-else-if="currentRooms.length === 0"><td colspan="10" class="center">暂无房型</td></tr>
                    <tr v-for="r in currentRooms" :key="r.id">
                      <td>{{ r.room_type }}</td>
                      <td>{{ r.room_type_label }}</td>
                      <td>{{ r.max_occupancy }}</td>
                      <td>{{ r.bed_count || '-' }}</td>
                      <td>{{ r.retail_price }}</td>
                      <td>{{ r.agency_price || '-' }}</td>
                      <td>{{ r.includes_breakfast ? `${r.breakfast_count}份` : '无' }}</td>
                      <td>{{ r.extra_bed_rate || '-' }}</td>
                      <td>{{ r.season_type }}</td>
                      <td class="actions-cell">
                        <button class="btn-sm" @click="openRoomEdit(r)">编辑</button>
                        <button class="btn-sm btn-danger" @click="handleRoomDelete(r)">删除</button>
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

    <!-- 酒店弹窗 -->
    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <h3>{{ editingItem ? '编辑酒店' : '新增酒店' }}</h3>
        <div class="form-row">
          <div class="form-group">
            <label>酒店名称 *</label>
            <input v-model="form.name" />
          </div>
          <div class="form-group">
            <label>区域</label>
            <input v-model="form.region_name" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>星级</label>
            <select v-model="form.star_rating">
              <option value="">无</option>
              <option value="economy">经济型</option>
              <option value="comfort">舒适型(3星)</option>
              <option value="premium">高档型(4星)</option>
              <option value="luxury">豪华型(5星)</option>
            </select>
          </div>
          <div class="form-group">
            <label>星级显示名</label>
            <input v-model="form.star_rating_label" />
          </div>
        </div>
        <div class="form-group">
          <label>地址</label>
          <input v-model="form.address" />
        </div>
        <div class="form-group">
          <label>联系电话</label>
          <input v-model="form.contact_phone" />
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

    <!-- 房型弹窗 -->
    <div v-if="showRoomModal" class="modal-overlay" @click.self="showRoomModal = false">
      <div class="modal-content">
        <h3>{{ editingRoom ? '编辑房型' : `新增房型 - ${roomParentName}` }}</h3>
        <div class="form-row">
          <div class="form-group">
            <label>房型编码 *</label>
            <select v-model="roomForm.room_type">
              <option value="standard">标准间/双床房</option>
              <option value="double">大床房/双人间</option>
              <option value="triple">三人间</option>
              <option value="family">家庭房</option>
              <option value="suite">套房</option>
              <option value="single">单人间</option>
            </select>
          </div>
          <div class="form-group">
            <label>房型显示名 *</label>
            <input v-model="roomForm.room_type_label" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>最大入住人数 *</label>
            <input v-model.number="roomForm.max_occupancy" type="number" />
          </div>
          <div class="form-group">
            <label>床位数</label>
            <input v-model.number="roomForm.bed_count" type="number" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>门市价 *</label>
            <input v-model.number="roomForm.retail_price" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>协议价</label>
            <input v-model.number="roomForm.agency_price" type="number" step="0.01" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>含早餐</label>
            <select v-model="roomForm.includes_breakfast">
              <option :value="false">不含</option>
              <option :value="true">含</option>
            </select>
          </div>
          <div class="form-group">
            <label>早餐份数</label>
            <input v-model.number="roomForm.breakfast_count" type="number" />
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>加床费(元/晚)</label>
            <input v-model.number="roomForm.extra_bed_rate" type="number" step="0.01" />
          </div>
          <div class="form-group">
            <label>季节类型</label>
            <select v-model="roomForm.season_type">
              <option value="default">默认</option>
              <option value="peak">旺季</option>
              <option value="shoulder">平季</option>
              <option value="off">淡季</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>备注</label>
          <input v-model="roomForm.remark" />
        </div>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showRoomModal = false">取消</button>
          <button class="btn-primary" @click="handleRoomSave">保存</button>
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
import { hotels, rooms, regions } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'

const items = ref<any[]>([])
const regionOptions = ref<string[]>([])
const loading = ref(false)
const filterRegion = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadData)
const expandedId = ref<number | null>(null)
const currentRooms = ref<any[]>([])
const roomLoading = ref(false)

const showModal = ref(false)
const editingItem = ref<any>(null)
const form = ref({ name: '', region_name: '', star_rating: '', star_rating_label: '', address: '', contact_phone: '', sort_order: 0, remark: '' })

const showRoomModal = ref(false)
const editingRoom = ref<any>(null)
const roomParentId = ref(0)
const roomParentName = ref('')
const roomForm = ref({
  room_type: 'standard', room_type_label: '', max_occupancy: 2, bed_count: 2,
  retail_price: 0, agency_price: null as number | null, includes_breakfast: false,
  breakfast_count: 0, extra_bed_rate: null as number | null, season_type: 'default', remark: ''
})

async function loadData() {
  loading.value = true
  try {
    const params: any = {}
    if (filterRegion.value) params.region_name = filterRegion.value
    items.value = await hotels.list(params)
  } catch (e) { console.error('加载酒店数据失败', e) }
  finally { loading.value = false }
}

async function loadRegions() {
  try {
    const data = await regions.list()
    regionOptions.value = [...new Set(data.map((r: any) => r.name).filter(Boolean))]
  } catch (e) { /* ignore */ }
}

async function toggleExpand(id: number) {
  if (expandedId.value === id) { expandedId.value = null; return }
  expandedId.value = id
  roomLoading.value = true
  try { currentRooms.value = await rooms.list(id) }
  catch (e) { currentRooms.value = [] }
  finally { roomLoading.value = false }
}

function openCreate() {
  editingItem.value = null
  form.value = { name: '', region_name: '', star_rating: '', star_rating_label: '', address: '', contact_phone: '', sort_order: 0, remark: '' }
  showModal.value = true
}

function openEdit(item: any) {
  editingItem.value = item
  form.value = { name: item.name, region_name: item.region_name || '', star_rating: item.star_rating || '', star_rating_label: item.star_rating_label || '', address: item.address || '', contact_phone: item.contact_phone || '', sort_order: item.sort_order || 0, remark: item.remark || '' }
  showModal.value = true
}

async function handleSave() {
  try {
    if (editingItem.value) await hotels.update(editingItem.value.id, form.value)
    else await hotels.create(form.value)
    showModal.value = false
    await loadData()
  } catch (e) { console.error('保存失败', e); alert('保存失败') }
}

async function handleDelete(item: any) {
  if (!confirm(`确定删除酒店「${item.name}」？关联的房型也会一并删除。`)) return
  try { await hotels.delete(item.id); if (expandedId.value === item.id) expandedId.value = null; await loadData() }
  catch (e) { console.error('删除失败', e); alert('删除失败') }
}

function openRoomCreate(hotelId: number, hotelName: string) {
  editingRoom.value = null
  roomParentId.value = hotelId
  roomParentName.value = hotelName
  roomForm.value = { room_type: 'standard', room_type_label: '', max_occupancy: 2, bed_count: 2, retail_price: 0, agency_price: null, includes_breakfast: false, breakfast_count: 0, extra_bed_rate: null, season_type: 'default', remark: '' }
  showRoomModal.value = true
}

function openRoomEdit(r: any) {
  editingRoom.value = r
  roomForm.value = { room_type: r.room_type, room_type_label: r.room_type_label, max_occupancy: r.max_occupancy, bed_count: r.bed_count, retail_price: r.retail_price, agency_price: r.agency_price, includes_breakfast: r.includes_breakfast, breakfast_count: r.breakfast_count, extra_bed_rate: r.extra_bed_rate, season_type: r.season_type || 'default', remark: r.remark || '' }
  showRoomModal.value = true
}

async function handleRoomSave() {
  try {
    if (editingRoom.value) await rooms.update(editingRoom.value.id, roomForm.value)
    else await rooms.create(roomParentId.value, roomForm.value)
    showRoomModal.value = false
    currentRooms.value = await rooms.list(roomParentId.value)
  } catch (e) { console.error('保存房型失败', e); alert('保存失败') }
}

async function handleRoomDelete(r: any) {
  if (!confirm(`确定删除房型「${r.room_type_label}」？`)) return
  try { await rooms.delete(r.id); currentRooms.value = await rooms.list(roomParentId.value) }
  catch (e) { console.error('删除房型失败', e); alert('删除失败') }
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

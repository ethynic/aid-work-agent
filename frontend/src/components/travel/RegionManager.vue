<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>区域管理</h2>
      <div class="actions">
        <select v-model="filterLevel" @change="loadData" class="filter-select">
          <option value="">全部层级</option>
          <option value="province">省</option>
          <option value="city">市</option>
          <option value="district">区县</option>
        </select>
        <button class="btn-primary" @click="openCreate">+ 新增区域</button>
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
          <th>ID</th>
          <th>区域名称</th>
          <th>别名</th>
          <th>上级区域</th>
          <th>层级</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading"><td colspan="6" class="center">加载中...</td></tr>
        <tr v-else-if="items.length === 0"><td colspan="6" class="center">暂无数据</td></tr>
        <tr v-for="item in filteredItems" :key="item.id">
          <td>{{ item.id }}</td>
          <td>{{ item.name }}</td>
          <td>{{ item.aliases || '-' }}</td>
          <td>{{ item.parent_name || '-' }}</td>
          <td>{{ levelLabel(item.level) }}</td>
          <td class="actions-cell">
            <button class="btn-sm" @click="openEdit(item)">编辑</button>
            <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 新增/编辑弹窗 -->
    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <h3>{{ editingItem ? '编辑区域' : '新增区域' }}</h3>
        <div class="form-group">
          <label>区域名称 *</label>
          <input v-model="form.name" placeholder="如：贵阳、黔南" />
        </div>
        <div class="form-group">
          <label>别名</label>
          <input v-model="form.aliases" placeholder="逗号分隔，如：筑,贵阳市" />
        </div>
        <div class="form-group">
          <label>上级区域</label>
          <input v-model="form.parent_name" placeholder="如：贵州" />
        </div>
        <div class="form-group">
          <label>层级</label>
          <select v-model="form.level">
            <option value="province">省</option>
            <option value="city">市</option>
            <option value="district">区县</option>
          </select>
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
import { ref, computed, onMounted } from 'vue'
import { regions } from '@/api/travelQuote'
import { useImport } from '@/composables/useImport'

const items = ref<any[]>([])
const loading = ref(false)
const showModal = ref(false)
const editingItem = ref<any>(null)
const filterLevel = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useImport(loadData)

const form = ref({
  name: '',
  aliases: '',
  parent_name: '',
  level: 'city'
})

const filteredItems = computed(() => {
  if (!filterLevel.value) return items.value
  return items.value.filter(i => i.level === filterLevel.value)
})

function levelLabel(level: string) {
  const map: Record<string, string> = { province: '省', city: '市', district: '区县' }
  return map[level] || level
}

async function loadData() {
  loading.value = true
  try {
    items.value = await regions.list()
  } catch (e) {
    console.error('加载区域数据失败', e)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingItem.value = null
  form.value = { name: '', aliases: '', parent_name: '', level: 'city' }
  showModal.value = true
}

function openEdit(item: any) {
  editingItem.value = item
  form.value = { name: item.name, aliases: item.aliases || '', parent_name: item.parent_name || '', level: item.level || 'city' }
  showModal.value = true
}

async function handleSave() {
  try {
    if (editingItem.value) {
      await regions.update(editingItem.value.id, form.value)
    } else {
      await regions.create(form.value)
    }
    showModal.value = false
    await loadData()
  } catch (e) {
    console.error('保存失败', e)
    alert('保存失败，请检查数据')
  }
}

async function handleDelete(item: any) {
  if (!confirm(`确定删除区域「${item.name}」？`)) return
  try {
    await regions.delete(item.id)
    await loadData()
  } catch (e) {
    console.error('删除失败', e)
    alert('删除失败')
  }
}

onMounted(loadData)
</script>

<style scoped>
.manager-container { padding: 20px; max-width: 1200px; margin: 0 auto; }
.header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.header-bar h2 { margin: 0; font-size: 18px; }
.actions { display: flex; gap: 10px; align-items: center; }
.filter-select { padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th, .data-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }
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
.modal-content { background: white; border-radius: 8px; padding: 24px; width: 480px; max-width: 90vw; }
.modal-content h3 { margin: 0 0 20px; }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 4px; font-size: 14px; color: #555; }
.form-group input, .form-group select { width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
</style>

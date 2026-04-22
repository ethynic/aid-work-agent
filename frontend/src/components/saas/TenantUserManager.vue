<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-slate-800">用户管理</h1>
      <div class="flex gap-2">
        <button @click="showImport = true"
          class="px-4 py-2 border border-slate-300 text-slate-600 hover:bg-slate-50 rounded-lg text-sm font-medium transition-colors">
          CSV 导入
        </button>
        <button @click="openAddUser"
          class="px-4 py-2 bg-cyan-500 hover:bg-cyan-600 text-white rounded-lg text-sm font-medium transition-colors">
          添加用户
        </button>
      </div>
    </div>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <!-- 用户列表 -->
    <div v-else-if="users.length > 0" class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      <table class="w-full">
        <thead class="bg-slate-50 border-b border-slate-200">
          <tr>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">用户名</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">手机号</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">部门</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">角色</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-for="u in users" :key="u.mapping_id" class="hover:bg-slate-50">
            <td class="px-4 py-3 text-sm text-slate-800">{{ u.username || '-' }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ u.phone || '-' }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ u.department || '-' }}</td>
            <td class="px-4 py-3">
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
                :class="u.role === 'admin' ? 'bg-purple-100 text-purple-700' : 'bg-slate-100 text-slate-600'">
                {{ u.role === 'admin' ? '管理员' : '普通用户' }}
              </span>
            </td>
            <td class="px-4 py-3">
              <button @click="handleRemove(u.user_id)"
                class="text-xs px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 transition-colors">
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="text-center py-12 text-slate-500">
      <p class="text-lg mb-2">暂无企业用户</p>
      <p class="text-sm">点击"添加用户"或"CSV 导入"添加用户</p>
    </div>

    <!-- 添加用户弹窗 -->
    <div v-if="showAdd" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showAdd = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h3 class="text-lg font-bold text-slate-800 mb-4">添加用户</h3>
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-slate-600 mb-1">用户名</label>
            <input v-model="addForm.username" type="text" placeholder="请输入用户名"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">手机号 <span class="text-red-500">*</span></label>
            <input v-model="addForm.phone" type="tel" placeholder="请输入11位手机号"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">部门</label>
            <input v-model="addForm.department" type="text" placeholder="选填"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">角色</label>
            <select v-model="addForm.role"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400">
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
          </div>
        </div>
        <div v-if="addError" class="mt-3 p-2 bg-red-50 border border-red-200 rounded text-red-600 text-sm">{{ addError }}</div>
        <div class="flex gap-3 mt-6">
          <button @click="showAdd = false" class="flex-1 py-2 border border-slate-300 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
          <button @click="handleAddUser" :disabled="adding" class="flex-1 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg transition-colors">
            {{ adding ? '添加中...' : '确认' }}
          </button>
        </div>
      </div>
    </div>

    <!-- CSV 导入弹窗 -->
    <div v-if="showImport" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showImport = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h3 class="text-lg font-bold text-slate-800 mb-4">CSV 批量导入</h3>
        <div class="mb-4">
          <p class="text-sm text-slate-500 mb-2">CSV 格式：phone（必填，11位数字）, username（选填）</p>
          <input type="file" accept=".csv" @change="onFileSelect" ref="fileInput"
            class="w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-cyan-50 file:text-cyan-700 hover:file:bg-cyan-100" />
        </div>
        <div v-if="importResult" class="mb-4 p-3 bg-slate-50 rounded-lg text-sm">
          <p class="text-green-600">成功导入：{{ importResult.imported }} / {{ importResult.total }}</p>
          <p v-if="importResult.errors?.length" class="text-red-600 mt-1">
            <span v-for="(err, i) in importResult.errors" :key="i">{{ err }}<br /></span>
          </p>
        </div>
        <div v-if="importError" class="mb-4 p-2 bg-red-50 border border-red-200 rounded text-red-600 text-sm">{{ importError }}</div>
        <div class="flex gap-3">
          <button @click="showImport = false" class="flex-1 py-2 border border-slate-300 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">关闭</button>
          <button @click="handleImport" :disabled="!importFile || importing" class="flex-1 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg transition-colors">
            {{ importing ? '导入中...' : '开始导入' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { listTenantUsers, createTenantUser, batchImportUsers, removeTenantUser } from '@/api/saasTenant'

const toast = useToast()

const loading = ref(true)
const users = ref<any[]>([])
const showAdd = ref(false)
const adding = ref(false)
const addError = ref('')
const showImport = ref(false)
const importing = ref(false)
const importError = ref('')
const importFile = ref<File | null>(null)
const importResult = ref<{ imported: number; total: number; errors?: string[] } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const addForm = ref({ phone: '', username: '', department: '', role: 'user' })

function openAddUser() {
  addForm.value = { phone: '', username: '', department: '', role: 'user' }
  addError.value = ''
  showAdd.value = true
}

function onFileSelect(e: Event) {
  const target = e.target as HTMLInputElement
  importFile.value = target.files?.[0] || null
  importResult.value = null
  importError.value = ''
}

async function loadUsers() {
  loading.value = true
  try {
    const res = await listTenantUsers()
    users.value = res.users || []
  } catch (e) {
    console.error('加载用户列表失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleAddUser() {
  if (!addForm.value.phone) {
    addError.value = '请填写手机号'
    return
  }
  if (!/^\d{11}$/.test(addForm.value.phone)) {
    addError.value = '手机号必须为11位数字'
    return
  }
  if (!addForm.value.username) {
    addError.value = '请填写用户名'
    return
  }
  adding.value = true
  addError.value = ''
  try {
    await createTenantUser(addForm.value)
    showAdd.value = false
    await loadUsers()
    toast.success('创建用户成功，密码为空，用户首次登录时，需要点击"忘记密码"进行重置')
  } catch (e: any) {
    addError.value = e.message || '添加失败'
  } finally {
    adding.value = false
  }
}

async function handleImport() {
  if (!importFile.value) return
  importing.value = true
  importError.value = ''
  importResult.value = null
  try {
    importResult.value = await batchImportUsers(importFile.value)
    await loadUsers()
    toast.success('导入用户成功，密码为空，用户首次登录时，需要点击"忘记密码"进行重置')
  } catch (e: any) {
    importError.value = e.message || '导入失败'
  } finally {
    importing.value = false
  }
}

async function handleRemove(userId: string) {
  if (!confirm('确定要删除此用户吗？')) return
  try {
    await removeTenantUser(userId)
    await loadUsers()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

onMounted(() => loadUsers())
</script>

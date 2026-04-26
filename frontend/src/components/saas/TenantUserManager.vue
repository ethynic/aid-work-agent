<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Header Bar -->
    <AppHeader
      title="用户管理"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button
          @click="goToChat(); closeMenu()"
          class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          返回对话
        </button>
        <button
          @click="openCustomerInfo(); closeMenu()"
          class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
          </svg>
          我的客户
        </button>
        <button
          @click="openScheduledTasks(); closeMenu()"
          class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          我的定时任务
        </button>
      </template>
    </AppHeader>

    <!-- Main Content -->
    <div class="flex-1 overflow-y-auto p-6">
      <div class="flex items-center justify-between mb-6">
        <div></div>
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
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">数字员工授权</th>
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
                :class="u.role === 'tenant_admin' ? 'bg-purple-100 text-purple-700' : 'bg-slate-100 text-slate-600'">
                {{ u.role === 'tenant_admin' ? '管理员' : '普通用户' }}
              </span>
            </td>
            <td class="px-4 py-3">
              <span v-if="u.role === 'tenant_admin'"
                class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                默认全部
              </span>
              <span v-else-if="u.agent_permission_count > 0"
                class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                {{ u.agent_permission_count }} 个已授权
              </span>
              <span v-else
                class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                未授权
              </span>
            </td>
            <td class="px-4 py-3">
              <div class="flex gap-2">
                <button @click="openPermissionDialog(u)"
                  class="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 transition-colors">
                  授权
                </button>
                <button @click="handleRemove(u.user_id)"
                  class="text-xs px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 transition-colors">
                  删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="text-center py-12 text-slate-500">
      <p class="text-lg mb-2">暂无企业用户</p>
      <p class="text-sm">点击"添加用户"或"CSV 导入"添加用户</p>
    </div>
    </div>

    <!-- 添加用户弹窗 -->
    <div v-if="showAdd" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showAdd = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h3 class="text-lg font-bold text-slate-800 mb-4">添加用户</h3>
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-slate-600 mb-1">用户名 <span class="text-red-500">*</span></label>
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
              <option value="tenant_admin">管理员</option>
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

    <!-- 用户数字员工授权弹窗 -->
    <div v-if="showPermissionDialog" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showPermissionDialog = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6 max-h-[80vh] overflow-y-auto">
        <h3 class="text-lg font-bold text-slate-800 mb-4">
          数字员工授权 - {{ currentUser?.username || currentUser?.phone }}
        </h3>
        <div v-if="loadingAgents" class="text-center py-6 text-slate-500 text-sm">加载中...</div>
        <div v-else-if="availableAgents.length === 0" class="text-center py-6 text-slate-500 text-sm">
          当前租户未授权任何数字员工，无法给用户授权
        </div>
        <div v-else class="space-y-2 py-2">
          <label v-for="agent in availableAgents" :key="agent.agent_id" class="flex items-center p-2 hover:bg-slate-50 rounded cursor-pointer">
            <input
              type="checkbox"
              :checked="selectedAgentIds.includes(agent.agent_id)"
              @change="toggleAgentSelection(agent.agent_id)"
              class="w-4 h-4 text-cyan-600 border-slate-300 rounded focus:ring-cyan-500"
            />
            <div class="ml-3 flex-1">
              <div class="text-sm font-medium text-slate-800">{{ agent.name }}</div>
              <div v-if="agent.description" class="text-xs text-slate-500">{{ agent.description }}</div>
            </div>
            <span class="ml-2 text-xs px-1.5 py-0.5 rounded"
              :class="agent.type === 'builtin' ? 'bg-blue-100 text-blue-700' : 'bg-green-100 text-green-700'">
              {{ agent.type === 'builtin' ? '内置' : '定制' }}
            </span>
          </label>
        </div>
        <div v-if="permissionError" class="mt-3 p-2 bg-red-50 border border-red-200 rounded text-red-600 text-sm">
          {{ permissionError }}
        </div>
        <div class="flex gap-3 mt-6">
          <button @click="showPermissionDialog = false" class="flex-1 py-2 border border-slate-300 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">
            取消
          </button>
          <button @click="saveUserPermissions" :disabled="savingPermissions" class="flex-1 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg transition-colors">
            {{ savingPermissions ? '保存中...' : '确认保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import { listTenantUsers, createTenantUser, batchImportUsers, removeTenantUser } from '@/api/saasTenant'
import { getTenantAvailableUserAgents, getUserAgentPermissions, setUserAgentPermissions, type AgentItem } from '@/api/saasPermissions'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const tenantId = computed(() => route.params.tenant_id as string)
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

// 统一的用户信息
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 从 PortalLayout 注入侧边栏状态
const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')

// 侧边栏折叠状态
const localSidebarCollapsed = ref(false)
const isSidebarCollapsed = computed({
  get: () => sidebarCollapsed?.value ?? localSidebarCollapsed.value,
  set: (val: boolean) => {
    if (sidebarCollapsed) {
      sidebarCollapsed.value = val
    } else {
      localSidebarCollapsed.value = val
    }
  }
})

function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  } else {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}

function goToChat() {
  router.push(`/t/${tenantId.value}/chat`)
}

function openCustomerInfo() {
  const userId = effectiveUser.value?.user_id
  if (userId) {
    window.open(`/customer-info?user_id=${userId}`, '_blank')
  } else {
    toast.warning('请先登录')
  }
}

function openScheduledTasks() {
  window.open('/scheduled-tasks', '_blank')
}

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

// 数字员工授权弹窗相关
const showPermissionDialog = ref(false)
const currentUser = ref<any>(null)
const availableAgents = ref<AgentItem[]>([])
const selectedAgentIds = ref<string[]>([])
const loadingAgents = ref(false)
const savingPermissions = ref(false)
const permissionError = ref('')

function openAddUser() {
  addForm.value = { phone: '', username: '', department: '', role: 'user' }
  addError.value = ''
  showAdd.value = true
}

function openPermissionDialog(user: any) {
  currentUser.value = user
  selectedAgentIds.value = []
  loadingAgents.value = true
  permissionError.value = ''
  showPermissionDialog.value = true
  // 加载可用数字员工和当前授权
  ;(async () => {
    try {
      const [availableRes, permissionsRes] = await Promise.all([
        getTenantAvailableUserAgents(),
        getUserAgentPermissions(user.user_id),
      ])
      if (availableRes.success && availableRes.data) {
        availableAgents.value = availableRes.data
      }
      if (permissionsRes.success && permissionsRes.data) {
        selectedAgentIds.value = permissionsRes.data.agent_ids || []
      }
    } catch (e) {
      console.error('加载数字员工授权失败:', e)
      permissionError.value = '加载失败，请重试'
    } finally {
      loadingAgents.value = false
    }
  })()
}

function toggleAgentSelection(agentId: string) {
  const index = selectedAgentIds.value.indexOf(agentId)
  if (index >= 0) {
    selectedAgentIds.value.splice(index, 1)
  } else {
    selectedAgentIds.value.push(agentId)
  }
}

async function saveUserPermissions() {
  if (!currentUser.value) return
  savingPermissions.value = true
  permissionError.value = ''
  try {
    await setUserAgentPermissions(currentUser.value.user_id, selectedAgentIds.value)
    toast.success('授权保存成功')
    showPermissionDialog.value = false
    await loadUsers()
  } catch (e: any) {
    permissionError.value = e.message || '保存失败'
  } finally {
    savingPermissions.value = false
  }
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
    await createTenantUser({ ...addForm.value, tenant_id: tenantId.value })
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

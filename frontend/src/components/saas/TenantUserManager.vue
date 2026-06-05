<template>
  <div class="page-container bg-canvas" v-bind="$attrs">
    <AppHeader
      title="用户管理"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="page-content p-6">
      <div class="page-toolbar">
          <div class="page-toolbar-left">
            <BaseInput v-model="searchKeyword" placeholder="搜索用户名/手机号" size="sm" class="w-80" @keyup.enter="handleSearchWrapper(searchKeyword)" />
            <BaseButton size="sm" @click="handleSearchWrapper(searchKeyword)">搜索</BaseButton>
          </div>
          <div class="page-toolbar-right">
            <BaseButton :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">批量删除 ({{ selectedArr.length }})</BaseButton>
            <BaseButton intent="secondary" @click="showImport = true">CSV 导入</BaseButton>
            <BaseButton @click="openAddUser">添加用户</BaseButton>
          </div>
        </div>

        <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

        <template v-else-if="filteredUsers.length > 0">
          <div class="table-scroll-wrapper">
            <BaseTable :columns="columns" :data="pagedUsers" row-key="mapping_id">
              <!-- 表头全选框 -->
              <template #checkbox_header>
                <input
                  type="checkbox"
                  :checked="isAllSelected(pagedUsers)"
                  @change="(e: Event) => toggleAll(pagedUsers, (e.target as HTMLInputElement).checked)"
                />
              </template>
              <!-- 行选择框 -->
              <template #checkbox="{ row }">
                <input
                  type="checkbox"
                  :checked="isSelected(row)"
                  @change="() => toggleRow(row)"
                />
              </template>
              <template #index="{ index }">{{ seqNumber(index) }}</template>
              <template #username="{ row }">{{ row.username || '-' }}</template>
              <template #phone="{ row }">{{ row.phone || '-' }}</template>
              <template #department="{ row }">{{ row.department || '-' }}</template>
              <template #role="{ row }">
                <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="row.role === 'tenant_admin' ? 'bg-primary-100 text-primary-700' : 'bg-gray-100 text-gray-700'">
                  {{ row.role === 'tenant_admin' ? '管理员' : '普通用户' }}
                </span>
              </template>
              <template #agent_auth="{ row }">
                <span v-if="row.role === 'tenant_admin'"
                  class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-primary-100 text-primary-700">
                  默认全部
                </span>
                <span v-else-if="row.agent_count > 0"
                  class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-success-100 text-success-700">
                  {{ row.agent_count }} 个已授权
                </span>
                <span v-else
                  class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-danger-100 text-danger-700">
                  未授权
                </span>
              </template>
              <template #actions="{ row }">
                <div class="flex gap-2">
                  <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openPermissionDialog(row)">授权</BaseButton>
                  <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleRemove(row.user_id)">删除</BaseButton>
                </div>
              </template>
            </BaseTable>
          </div>

          <BasePagination
            v-if="total > 0"
            :total="total"
            v-model:currentPage="currentPage"
            :page-size="pageSize"
          />
        </template>

        <div v-else class="text-center py-12 text-muted">
          <p class="text-lg mb-2">暂无企业用户</p>
          <p class="text-sm">点击"添加用户"或"CSV 导入"添加用户</p>
        </div>
      </div>
    </div>

    <!-- 添加用户弹窗 -->
    <BaseModal v-model="showAdd" title="添加用户" size="md">
      <div class="space-y-4">
        <div>
          <label class="text-sm text-muted mb-1 block">用户名 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="addForm.username" placeholder="请输入用户名" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">手机号 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="addForm.phone" placeholder="请输入11位手机号" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">部门</label>
          <BaseInput v-model="addForm.department" placeholder="选填" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">角色</label>
          <BaseSelect v-model="addForm.role">
            <option value="user">普通用户</option>
            <option value="tenant_admin">管理员</option>
          </BaseSelect>
        </div>
      </div>
      <div v-if="addError" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ addError }}</div>
      <template #footer>
        <BaseButton intent="secondary" @click="showAdd = false">取消</BaseButton>
        <BaseButton :disabled="adding" @click="handleAddUser">{{ adding ? '添加中...' : '确认' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- CSV 导入弹窗 -->
    <BaseModal v-model="showImport" title="CSV 批量导入" size="md">
      <div class="mb-4">
        <p class="text-sm text-muted mb-2">CSV 格式：phone（必填，11位数字）, username（选填）</p>
        <input type="file" accept=".csv" @change="onFileSelect" ref="fileInput"
          class="w-full text-sm text-muted file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary-50 file:text-primary-700 hover:file:bg-primary-100" />
      </div>
      <div v-if="importResult" class="mb-4 p-3 bg-canvas rounded-lg text-sm">
        <p class="text-success-600">成功导入：{{ importResult.imported }} / {{ importResult.total }}</p>
        <p v-if="importResult.errors?.length" class="text-danger-600 mt-1">
          <span v-for="(err, i) in importResult.errors" :key="i">{{ err }}<br /></span>
        </p>
      </div>
      <div v-if="importError" class="mb-4 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ importError }}</div>
      <template #footer>
        <BaseButton intent="secondary" @click="showImport = false">关闭</BaseButton>
        <BaseButton :disabled="!importFile || importing" @click="handleImport">{{ importing ? '导入中...' : '开始导入' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- 数字员工授权弹窗 -->
    <BaseModal v-model="showPermissionDialog" title="数字员工授权" size="md">
      <template v-if="currentUser">
        <p class="text-sm text-muted mb-4">{{ currentUser.username || currentUser.phone }}</p>
      </template>
      <div v-if="loadingAgents" class="text-center py-6 text-muted text-sm">加载中...</div>
      <div v-else-if="availableAgents.length === 0" class="text-center py-6 text-muted text-sm">
        当前租户未授权任何数字员工，无法给用户授权
      </div>
      <div v-else class="space-y-2 py-2">
        <label v-for="agent in availableAgents" :key="agent.agent_id" class="flex items-center p-2 hover:bg-surface-hover rounded cursor-pointer">
          <input
            type="checkbox"
            :checked="selectedAgentIds.includes(agent.agent_id)"
            @change="toggleAgentSelection(agent.agent_id)"
            class="w-4 h-4 text-primary-600 border-hover rounded focus:ring-primary-500"
          />
          <div class="ml-3 flex-1">
            <div class="text-sm font-medium text-default">{{ agent.name }}</div>
            <div v-if="agent.description" class="text-xs text-muted">{{ agent.description }}</div>
          </div>
          <span class="ml-2 text-xs px-1.5 py-0.5 rounded"
            :class="agent.type === 'builtin' ? 'bg-info-100 text-info-700' : 'bg-success-100 text-success-700'">
            {{ agent.type === 'builtin' ? '内置' : '定制' }}
          </span>
        </label>
      </div>
      <div v-if="permissionError" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">
        {{ permissionError }}
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showPermissionDialog = false">取消</BaseButton>
        <BaseButton :disabled="savingPermissions" @click="saveUserPermissions">{{ savingPermissions ? '保存中...' : '确认保存' }}</BaseButton>
      </template>
    </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'

// 禁用属性继承，因为组件通过 RouterView 异步加载时会触发多根渲染警告
defineOptions({
  inheritAttrs: false,
})
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'
import { useTableSelection } from '@/composables/useTableSelection'
import { listTenantUsers, createTenantUser, batchImportUsers, removeTenantUser } from '@/api/saasTenant'
import { getTenantAvailableUserAgents, getUserAgentPermissions, setUserAgentPermissions, type AgentItem } from '@/api/saasPermissions'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const tenantId = computed(() => route.params.tenant_id as string)
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')

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

const columns = [
  { key: 'checkbox', label: '', width: '40px' },
  { key: 'index', label: '序号', width: '60px' },
  { key: 'username', label: '用户名' },
  { key: 'phone', label: '手机号' },
  { key: 'department', label: '部门' },
  { key: 'role', label: '角色' },
  { key: 'agent_auth', label: '数字员工授权' },
  { key: 'actions', label: '操作', width: '120px', thAlign: 'center' as const },
]

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

const showPermissionDialog = ref(false)
const currentUser = ref<any>(null)
const availableAgents = ref<AgentItem[]>([])
const selectedAgentIds = ref<string[]>([])
const loadingAgents = ref(false)
const savingPermissions = ref(false)
const permissionError = ref('')

const total = ref(0)
const { currentPage, pageSize, searchKeyword, seqNumber, handleSearch } =
  usePageContext(async () => {
    await loadUsers()
  })

// 批量选择
const { selectedArr, isAllSelected, toggleAll, toggleRow, clearSelection, isSelected } = useTableSelection<any>({
  getRowId: (row) => row.user_id
})

const filteredUsers = computed(() => {
  if (!searchKeyword.value) return users.value
  const kw = searchKeyword.value.toLowerCase()
  return users.value.filter((u: any) =>
    (u.username || '').toLowerCase().includes(kw) ||
    (u.phone || '').toLowerCase().includes(kw)
  )
})

const pagedUsers = computed(() => {
  total.value = filteredUsers.value.length
  const start = (currentPage.value - 1) * pageSize.value
  return filteredUsers.value.slice(start, start + pageSize.value)
})

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

// 搜索时清空选择
async function handleSearchWrapper(keyword: string) {
  clearSelection()
  await handleSearch(keyword)
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

async function handleBatchDelete() {
  if (selectedArr.value.length === 0) return
  if (!confirm(`确定要删除选中的 ${selectedArr.value.length} 个用户吗？`)) return
  try {
    for (const userId of selectedArr.value) {
      await removeTenantUser(userId)
    }
    clearSelection()
    await loadUsers()
    toast.success('批量删除成功')
  } catch (e: any) {
    toast.error(e.message || '批量删除失败')
  }
}

onMounted(() => { loadUsers() })
</script>

<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-slate-800">租户管理</h1>
      <button @click="openAddDialog"
        class="px-4 py-2 bg-cyan-500 hover:bg-cyan-600 text-white rounded-lg text-sm font-medium transition-colors">
        新增租户
      </button>
    </div>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <div v-else class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      <table class="w-full">
        <thead class="bg-slate-50 border-b border-slate-200">
          <tr>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">租户ID</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">企业名称</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">联系人</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">联系电话</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">套餐</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">状态</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-for="tenant in tenants" :key="tenant.tenant_id" class="hover:bg-slate-50">
            <td class="px-4 py-3 text-sm text-slate-800 font-mono">{{ tenant.tenant_id }}</td>
            <td class="px-4 py-3 text-sm text-slate-800">{{ tenant.company_name }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ tenant.contact_name || '-' }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ tenant.contact_phone || '-' }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ tenant.plan }}</td>
            <td class="px-4 py-3">
              <span
                :class="getStatusClass(tenant.status)"
                class="px-2 py-1 rounded-full text-xs font-medium"
              >
                {{ getStatusLabel(tenant.status) }}
              </span>
            </td>
            <td class="px-4 py-3">
              <div class="flex gap-2">
                <button @click="openDetailDialog(tenant)"
                  class="text-xs px-2 py-1 bg-slate-100 text-slate-600 rounded hover:bg-slate-200 transition-colors">
                  详情
                </button>
                <button @click="openEditDialog(tenant)"
                  class="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 transition-colors">
                  编辑
                </button>
                <button @click="handleDelete(tenant)"
                  class="text-xs px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 transition-colors">
                  删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="tenants.length === 0" class="text-center py-12 text-slate-500">
        暂无租户数据
      </div>
    </div>

    <!-- 新增/编辑弹窗 -->
    <div v-if="showFormDialog" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showFormDialog = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 p-6">
        <h3 class="text-lg font-bold text-slate-800 mb-4">{{ isEdit ? '编辑租户' : '新增租户' }}</h3>
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-slate-600 mb-1">企业名称 <span class="text-red-500">*</span></label>
            <input v-model="formData.company_name" type="text" placeholder="请输入企业名称" maxlength="100"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">联系人</label>
            <input v-model="formData.contact_name" type="text" placeholder="请输入联系人姓名" maxlength="50"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">联系电话</label>
            <input v-model="formData.contact_phone" type="tel" placeholder="请输入联系电话" maxlength="20"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">套餐</label>
            <select v-model="formData.plan"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400">
              <option value="basic">基础版 (basic)</option>
              <option value="standard">标准版 (standard)</option>
              <option value="premium">高级版 (premium)</option>
            </select>
          </div>
          <div v-if="isEdit">
            <label class="block text-sm text-slate-600 mb-1">状态</label>
            <select v-model="formData.status"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400">
              <option value="active">正常</option>
              <option value="suspended">停用</option>
              <option value="deactivated">已删除</option>
            </select>
          </div>
        </div>
        <div v-if="formError" class="mt-3 p-2 bg-red-50 border border-red-200 rounded text-red-600 text-sm">{{ formError }}</div>
        <div class="flex gap-3 mt-6">
          <button @click="showFormDialog = false" class="flex-1 py-2 border border-slate-300 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
          <button @click="handleSubmit" :disabled="submitting" class="flex-1 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg transition-colors">
            {{ submitting ? '处理中...' : '确认' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 详情弹窗 -->
    <div v-if="showDetailDialog" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showDetailDialog = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 p-6">
        <h3 class="text-lg font-bold text-slate-800 mb-4">租户详情</h3>
        <div class="space-y-3">
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">租户ID</span>
            <span class="text-sm text-slate-800 font-mono">{{ currentTenant?.tenant_id }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">企业名称</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.company_name }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">联系人</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.contact_name || '-' }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">联系电话</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.contact_phone || '-' }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">套餐</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.plan }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">最大实例数</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.max_instances }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">最大用户数</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.max_users }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">状态</span>
            <span :class="getStatusClass(currentTenant?.status)" class="text-sm font-medium">
              {{ getStatusLabel(currentTenant?.status) }}
            </span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">创建时间</span>
            <span class="text-sm text-slate-800">{{ formatDate(currentTenant?.created_at) }}</span>
          </div>
          <div class="flex">
            <span class="w-24 text-sm text-slate-500">更新时间</span>
            <span class="text-sm text-slate-800">{{ formatDate(currentTenant?.updated_at) }}</span>
          </div>
        </div>
        <div class="flex gap-3 mt-6">
          <button @click="showDetailDialog = false" class="flex-1 py-2 border border-slate-300 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">关闭</button>
          <button @click="openEditDialog(currentTenant); showDetailDialog = false"
            class="flex-1 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-colors">编辑</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { listTenants, createTenant, updateTenant, deleteTenant, type TenantFormData } from '@/api/saasTenant'
import { TenantStatus, TenantStatusMap } from '@/api/enums'

const loading = ref(true)
const tenants = ref<any[]>([])
const showFormDialog = ref(false)
const showDetailDialog = ref(false)
const isEdit = ref(false)
const submitting = ref(false)
const formError = ref('')
const currentTenant = ref<any>(null)

const defaultFormData: TenantFormData = {
  company_name: '',
  contact_name: '',
  contact_phone: '',
  plan: 'basic',
}

const formData = ref<TenantFormData & { status: string }>({ ...defaultFormData, status: 'active' })

async function loadTenants() {
  loading.value = true
  try {
    const res = await listTenants()
    if (res.success) {
      tenants.value = res.tenants || []
    }
  } catch (e) {
    console.error('加载租户列表失败:', e)
  } finally {
    loading.value = false
  }
}

function formatDate(dateStr: string | undefined) {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN') + ' ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function openAddDialog() {
  isEdit.value = false
  formData.value = { ...defaultFormData, status: 'active' }
  formError.value = ''
  showFormDialog.value = true
}

function openEditDialog(tenant: any) {
  isEdit.value = true
  currentTenant.value = tenant
  formData.value = {
    company_name: tenant.company_name,
    contact_name: tenant.contact_name || '',
    contact_phone: tenant.contact_phone || '',
    plan: tenant.plan,
    status: String(tenant.status),
  }
  formError.value = ''
  showFormDialog.value = true
}

function openDetailDialog(tenant: any) {
  currentTenant.value = tenant
  showDetailDialog.value = true
}

async function handleSubmit() {
  if (!formData.value.company_name?.trim()) {
    formError.value = '请填写企业名称'
    return
  }
  submitting.value = true
  formError.value = ''
  try {
    if (isEdit.value && currentTenant.value) {
      // 直接提交 formData，status 已经是字符串格式
      await updateTenant(currentTenant.value.tenant_id, formData.value)
    } else {
      await createTenant(formData.value)
    }
    showFormDialog.value = false
    await loadTenants()
  } catch (e: any) {
    formError.value = e.message || '操作失败'
  } finally {
    submitting.value = false
  }
}

async function handleDelete(tenant: any) {
  if (!confirm(`确定要删除租户 "${tenant.company_name}" 吗？删除后将无法恢复。`)) return
  try {
    await deleteTenant(tenant.tenant_id)
    await loadTenants()
  } catch (e: any) {
    alert(e.message || '删除失败')
  }
}

function getStatusLabel(status: number | string | undefined): string {
  if (status === undefined || status === null) return '未知'
  const strStatus = String(status)
  const info = TenantStatusMap[strStatus as TenantStatus]
  return info?.label ?? '未知'
}

function getStatusClass(status: number | string | undefined): string {
  if (status === undefined || status === null) return 'bg-gray-100 text-gray-600'
  const strStatus = String(status)
  const info = TenantStatusMap[strStatus as TenantStatus]
  if (info?.color === 'green') return 'bg-green-100 text-green-700'
  if (info?.color === 'red') return 'bg-red-100 text-red-700'
  return 'bg-gray-100 text-gray-600'
}

onMounted(() => {
  loadTenants()
})
</script>

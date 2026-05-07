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
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">初始管理员手机号</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">状态</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">到期日期</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">数字员工授权</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">租户入口网址</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-for="tenant in tenants" :key="tenant.tenant_id" class="hover:bg-slate-50">
            <td class="px-4 py-3 text-sm text-slate-800 font-mono">{{ tenant.tenant_id }}</td>
            <td class="px-4 py-3 text-sm text-slate-800">{{ tenant.company_name }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ tenant.initial_admin_phone || '-' }}</td>
            <td class="px-4 py-3">
              <span
                :class="getStatusClass(tenant.status)"
                class="px-2 py-1 rounded-full text-xs font-medium"
              >
                {{ getStatusLabel(tenant.status) }}
              </span>
            </td>
            <td class="px-4 py-3">
              <span v-if="tenant.expire_at" :class="getExpireStatusClass(tenant.expire_at)" class="text-sm">
                {{ formatExpireDate(tenant.expire_at) }}
              </span>
              <span v-else class="text-sm text-slate-400">永久有效</span>
            </td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <span v-if="tenant.agent_count > 0"
                  class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                  {{ tenant.agent_count }} 个已授权
                </span>
                <span v-else
                  class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                  未授权
                </span>
                <button
                  @click="handleSyncInstances(tenant.tenant_id)"
                  :disabled="syncingInstances === tenant.tenant_id"
                  class="inline-flex items-center px-2 py-1 text-xs bg-cyan-100 text-cyan-700 hover:bg-cyan-200 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  title="根据配额创建/删除实例"
                >
                  <svg v-if="syncingInstances === tenant.tenant_id" class="animate-spin h-3 w-3 mr-1" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  创建实例
                </button>
              </div>
            </td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <a :href="getTenantUrl(tenant.tenant_id)" target="_blank"
                  class="text-cyan-600 hover:text-cyan-800 hover:underline text-sm">
                  {{ getTenantUrl(tenant.tenant_id) }}
                </a>
                <button @click="copyTenantUrl(tenant.tenant_id)"
                  class="p-1 text-slate-400 hover:text-cyan-600 hover:bg-cyan-50 rounded transition-colors"
                  title="复制网址">
                  <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                  </svg>
                </button>
              </div>
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
        <!-- 标签页 -->
        <div class="flex border-b border-slate-200 mb-4">
          <button
            @click="activeTab = 'basic'"
            :class="['px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === 'basic' ? 'border-cyan-500 text-cyan-600' : 'border-transparent text-slate-500 hover:text-slate-700']"
          >
            基本信息
          </button>
          <button
            @click="activeTab = 'agents'"
            :class="['px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === 'agents' ? 'border-cyan-500 text-cyan-600' : 'border-transparent text-slate-500 hover:text-slate-700']"
          >
            数字员工授权
            <span v-if="selectedAgentIds.length > 0" class="ml-1 text-xs">({{ selectedAgentIds.length }})</span>
          </button>
        </div>
        <!-- 基本信息标签页 -->
        <div v-if="activeTab === 'basic'" class="space-y-4 min-h-[640px]">
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
          <div class="pt-2 border-t border-slate-200">
            <p class="text-sm font-medium text-slate-700 mb-3">初始管理员（可选）</p>
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">初始管理员姓名</label>
            <input v-model="formData.initial_admin_name" type="text" placeholder="请输入管理员姓名" maxlength="50"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">初始管理员手机号</label>
            <input v-model="formData.initial_admin_phone" type="tel" placeholder="请输入11位手机号" maxlength="11"
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
          <div v-if="isEdit">
            <label class="block text-sm text-slate-600 mb-1">到期日期</label>
            <input v-model="formData.expire_at" type="date" placeholder="不设置则永久有效"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
            <p class="text-xs text-slate-500 mt-1">到期当天 23:59:59 前仍可登录，清空则永久有效</p>
          </div>
        </div>
        <!-- 数字员工授权标签页 -->
        <div v-if="activeTab === 'agents'" class="overflow-y-auto min-h-[640px]">
          <div v-if="loadingAgents" class="text-center py-6 text-slate-500 text-sm">加载中...</div>
          <div v-else-if="availableAgents.length === 0" class="text-center py-6 text-slate-500 text-sm">暂无可用数字员工</div>
          <div v-else class="space-y-2 py-2">
            <div v-for="agent in availableAgents" :key="agent.agent_id" class="flex items-center p-2 hover:bg-slate-50 rounded">
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
              <div class="flex items-center gap-2 ml-2">
                <span class="text-xs text-slate-500 whitespace-nowrap">实例数</span>
                <input
                  type="number"
                  :value="selectedAgentQuotas[agent.agent_id] || 1"
                  @input="updateAgentQuota(agent.agent_id, parseInt(($event.target as HTMLInputElement).value) || 1)"
                  min="1"
                  step="1"
                  class="w-16 px-2 py-1 text-sm border border-slate-300 rounded focus:outline-none focus:border-cyan-400"
                />
              </div>
              <span class="ml-2 text-xs px-1.5 py-0.5 rounded"
                :class="agent.type === 'builtin' ? 'bg-blue-100 text-blue-700' : 'bg-green-100 text-green-700'">
                {{ agent.type === 'builtin' ? '内置' : '定制' }}
              </span>
            </div>
          </div>
          <!-- 实例检查和创建按钮 -->
          <div class="pt-4 mt-4 border-t border-slate-200">
            <div class="text-xs text-slate-500 mb-2">
              💡 提示：点击"检查实例"先保存设置并查看实例数与配额的匹配情况，点击"创建实例"将自动保存授权设置并根据配额创建/删除实例。
            </div>
            <div class="flex gap-2">
              <button
                @click="handleCheckInstances"
                :disabled="checkingInstances || syncingInstances === currentTenant?.tenant_id"
                class="flex-1 px-4 py-2 bg-green-500 hover:bg-green-600 disabled:bg-slate-300 text-white rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                <svg v-if="checkingInstances" class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                检查实例
              </button>
              <button
                @click="handleSyncInstancesInEdit"
                :disabled="syncingInstances === currentTenant?.tenant_id || checkingInstances"
                class="flex-1 px-4 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                <svg v-if="syncingInstances === currentTenant?.tenant_id" class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                创建实例
              </button>
            </div>
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
            <span class="w-24 text-sm text-slate-500">初始管理员</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.initial_admin_name || '-' }}</span>
          </div>
          <div class="flex border-b border-slate-100 pb-2">
            <span class="w-24 text-sm text-slate-500">管理员手机</span>
            <span class="text-sm text-slate-800">{{ currentTenant?.initial_admin_phone || '-' }}</span>
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
            <span class="w-24 text-sm text-slate-500">到期日期</span>
            <span v-if="currentTenant?.expire_at" :class="getExpireStatusClass(currentTenant.expire_at)" class="text-sm font-medium">
              {{ formatExpireDate(currentTenant.expire_at) }}
            </span>
            <span v-else class="text-sm text-slate-500">永久有效</span>
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
import { useToast } from 'vue-toastification'
import { listTenants, createTenant, updateTenant, deleteTenant, type TenantFormData } from '@/api/saasTenant'
import { getAllAvailableAgents, getTenantAgentPermissions, setTenantAgentPermissions, syncTenantInstances, checkTenantInstances, type AgentItem } from '@/api/saasPermissions'
import { TenantStatus, TenantStatusMap } from '@/api/enums'

const toast = useToast()

const loading = ref(true)
const tenants = ref<any[]>([])
const showFormDialog = ref(false)
const showDetailDialog = ref(false)
const isEdit = ref(false)
const submitting = ref(false)
const formError = ref('')
const currentTenant = ref<any>(null)

// 数字员工授权标签页相关
const activeTab = ref<'basic' | 'agents'>('basic')
const availableAgents = ref<AgentItem[]>([])
const selectedAgentIds = ref<string[]>([])
const selectedAgentQuotas = ref<Record<string, number>>({})
const loadingAgents = ref(false)
const syncingInstances = ref<string | null>(null)
const checkingInstances = ref(false)

const defaultFormData: TenantFormData = {
  company_name: '',
  contact_name: '',
  contact_phone: '',
  initial_admin_name: '',
  initial_admin_phone: '',
  plan: 'basic',
  expire_at: '',
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

function formatExpireDate(dateStr: string | undefined) {
  if (!dateStr) return '永久有效'
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN')
}

function getExpireStatusClass(dateStr: string | undefined): string {
  if (!dateStr) return 'text-slate-600'

  const expireDate = new Date(dateStr)
  const now = new Date()
  const diffDays = Math.ceil((expireDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))

  if (diffDays < 0) return 'text-red-600 font-medium'
  if (diffDays < 15) return 'text-orange-600 font-medium'
  return 'text-slate-600'
}

function openAddDialog() {
  isEdit.value = false
  formData.value = { ...defaultFormData, status: 'active' }
  formError.value = ''
  activeTab.value = 'basic'
  // 加载所有可用数字员工
  selectedAgentIds.value = []
  selectedAgentQuotas.value = {}
  loadingAgents.value = true
  getAllAvailableAgents().then(res => {
    if (res.success && res.data) {
      availableAgents.value = res.data
    }
  }).catch(e => {
    console.error('加载数字员工授权失败:', e)
  }).finally(() => {
    loadingAgents.value = false
  })
  showFormDialog.value = true
}

async function openEditDialog(tenant: any) {
  isEdit.value = true
  currentTenant.value = tenant
  formData.value = {
    company_name: tenant.company_name,
    contact_name: tenant.contact_name || '',
    contact_phone: tenant.contact_phone || '',
    initial_admin_name: tenant.initial_admin_name || '',
    initial_admin_phone: tenant.initial_admin_phone || '',
    plan: tenant.plan,
    status: String(tenant.status),
    expire_at: tenant.expire_at ? tenant.expire_at.split('T')[0].split(' ')[0] : '',
  }
  // 切换到基本信息标签页
  activeTab.value = 'basic'
  // 加载所有可用数字员工
  selectedAgentIds.value = []
  loadingAgents.value = true
  try {
    const [agentsRes, permissionsRes] = await Promise.all([
      getAllAvailableAgents(),
      getTenantAgentPermissions(tenant.tenant_id),
    ])
    if (agentsRes.success && agentsRes.data) {
      availableAgents.value = agentsRes.data
    }
    if (permissionsRes.success && permissionsRes.data) {
      selectedAgentIds.value = permissionsRes.data.agent_ids || []
      const quotas = permissionsRes.data.agent_quotas || {}
      selectedAgentQuotas.value = quotas
      // 确保每个选中的agent都有配额
      for (const agentId of selectedAgentIds.value) {
        if (!(agentId in quotas)) {
          quotas[agentId] = 1
        }
      }
    }
  } catch (e) {
    console.error('加载数字员工授权失败:', e)
  } finally {
    loadingAgents.value = false
  }
  formError.value = ''
  showFormDialog.value = true
}

function openDetailDialog(tenant: any) {
  currentTenant.value = tenant
  showDetailDialog.value = true
}

function toggleAgentSelection(agentId: string) {
  const index = selectedAgentIds.value.indexOf(agentId)
  if (index >= 0) {
    selectedAgentIds.value.splice(index, 1)
    // 移除配额
    delete selectedAgentQuotas.value[agentId]
  } else {
    selectedAgentIds.value.push(agentId)
    // 添加默认配额
    if (!selectedAgentQuotas.value[agentId]) {
      selectedAgentQuotas.value[agentId] = 1
    }
  }
}

function updateAgentQuota(agentId: string, value: number) {
  if (value < 1) value = 1
  selectedAgentQuotas.value[agentId] = value
}

async function handleSubmit() {
  if (!formData.value.company_name?.trim()) {
    formError.value = '请填写企业名称'
    return
  }
  submitting.value = true
  formError.value = ''
  try {
    let result
    let createdTenantId = null
    if (isEdit.value && currentTenant.value) {
      // 直接提交 formData，status 已经是字符串格式
      result = await updateTenant(currentTenant.value.tenant_id, formData.value)
    } else {
      result = await createTenant(formData.value)
      // 获取新创建租户的ID
      if (result.success && result.tenant?.tenant_id) {
        createdTenantId = result.tenant.tenant_id
      }
    }
    if (!result.success) {
      formError.value = result.message || result.error || '操作失败'
      submitting.value = false
      return
    }
    // 保存数字员工授权（编辑已有租户或新建租户）
    const targetTenantId = isEdit.value ? currentTenant.value?.tenant_id : createdTenantId
    if (targetTenantId) {
      await setTenantAgentPermissions(targetTenantId, selectedAgentIds.value, selectedAgentQuotas.value)
    }
    // 如果后端返回了消息（创建初始管理员），显示成功消息
    if (result.message) {
      toast.success(result.message)
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
    toast.error(e.message || '删除失败')
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

function getTenantUrl(tenantId: string): string {
  return `${window.location.origin}/t/${tenantId}`
}

async function copyTenantUrl(tenantId: string) {
  const url = getTenantUrl(tenantId)
  try {
    await navigator.clipboard.writeText(url)
    toast.success('网址已复制到剪贴板')
  } catch (e) {
    // 降级方案：使用 document.execCommand
    const textarea = document.createElement('textarea')
    textarea.value = url
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    toast.success('网址已复制到剪贴板')
  }
}

async function handleSyncInstances(tenantId: string) {
  if (!confirm('确定要同步租户的数字员工实例吗？\n\n系统将根据当前配额创建或删除实例。\n已创建的实例名称格式为："数字员工名称 - 实例序号"。')) {
    return
  }
  syncingInstances.value = tenantId
  try {
    const res = await syncTenantInstances(tenantId)
    if (res.success) {
      toast.success(res.message || '实例同步成功')
    } else {
      toast.error(res.message || '实例同步失败')
    }
  } catch (e: any) {
    toast.error(e.message || '实例同步失败')
  } finally {
    syncingInstances.value = null
  }
}

async function handleCheckInstances() {
  if (!currentTenant.value?.tenant_id) {
    toast.error('未找到租户信息')
    return
  }
  // 先保存当前的授权设置（不关闭弹窗）
  const saved = await savePermissionsOnly()
  if (!saved) {
    console.log('前端日志：保存授权设置失败，取消检查')
    return
  }
  checkingInstances.value = true
  try {
    const res = await checkTenantInstances(currentTenant.value.tenant_id)
    if (res.success) {
      // 用 toast 显示详细结果
      const detailLines = res.details.map((d: any) =>
        `• ${d.name}：当前 ${d.current} / 配额 ${d.quota}`
      ).join('\n')
      const summary = `总计：${res.total_instances} 实例 / ${res.total_quota} 配额`
      if (res.matched) {
        toast.success(`实例数与配额匹配\n\n${detailLines}\n\n${summary}`, { duration: 6000 })
      } else {
        toast.warning(`${res.message}\n\n${detailLines}\n\n${summary}`, { duration: 6000 })
      }
    } else {
      toast.error(res.message || '检查失败')
    }
  } catch (e: any) {
    toast.error(e.message || '检查失败')
  } finally {
    checkingInstances.value = false
  }
}

async function handleSyncInstancesInEdit() {
  if (!currentTenant.value?.tenant_id) {
    toast.error('未找到租户信息')
    return
  }
  // 1. 先保存当前的授权设置（不关闭弹窗）
  const saved = await savePermissionsOnly()
  if (!saved) {
    console.log('前端日志：保存授权设置失败，取消创建实例')
    return
  }
  // 2. 保存成功后再同步实例
  if (!confirm('确定要根据当前配额创建实例吗？\n\n系统将根据数据库中已保存的配额创建或删除实例。\n已创建的实例名称格式为："数字员工名称 - 实例序号"。')) {
    return
  }
  syncingInstances.value = currentTenant.value.tenant_id
  try {
    const res = await syncTenantInstances(currentTenant.value.tenant_id)
    if (res.success) {
      // 用 toast 显示详细结果
      if (res.details && res.details.length > 0) {
        const detailLines = res.details.map((d: any) => {
          const change = []
          if (d.created > 0) change.push(`+${d.created}`)
          if (d.deleted > 0) change.push(`-${d.deleted}`)
          const changeStr = change.length > 0 ? ` (${change.join(', ')})` : ''
          return `• ${d.name}：${d.before} → ${d.after} / 配额 ${d.quota}${changeStr}`
        }).join('\n')
        toast.success(`${res.message}\n\n${detailLines}`, { duration: 6000 })
      } else {
        toast.success(res.message || '实例同步成功')
      }
    } else {
      toast.error(res.message || '实例同步失败')
    }
  } catch (e: any) {
    toast.error(e.message || '实例同步失败')
  } finally {
    syncingInstances.value = null
  }
}

/**
 * 仅保存数字员工授权（不关闭弹窗）
 * 返回 true 表示成功，false 表示失败
 */
async function savePermissionsOnly(): Promise<boolean> {
  const targetTenantId = isEdit.value ? currentTenant.value?.tenant_id : null
  if (!targetTenantId) {
    console.error('前端日志：未找到租户ID，无法保存授权')
    return false
  }
  try {
    const res = await setTenantAgentPermissions(targetTenantId, selectedAgentIds.value, selectedAgentQuotas.value)
    if (res.success) {
      return true
    } else {
      toast.error(res.message || '保存授权设置失败')
      return false
    }
  } catch (e: any) {
    toast.error(e.message || '保存授权设置失败')
    return false
  }
}

onMounted(() => {
  loadTenants()
})
</script>

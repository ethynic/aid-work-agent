<template>
  <div class="page-container">
    <AppHeader
      title="租户充值"
      :is-logged-in="isLoggedIn"
      :user="admin"
      @toggle-sidebar="handleToggleSidebar"
    />

    <div class="page-content">
      <!-- 搜索区 + 操作按钮区 -->
      <div class="page-toolbar">
        <div class="page-toolbar-left">
          <select v-model="filterTenantId" @change="handleFilterChange"
            class="px-3 py-1.5 border border-default rounded-lg text-sm bg-surface focus:outline-none focus:border-primary-400 focus:ring-1 focus:ring-primary-200 w-60">
            <option value="">全部租户</option>
            <option v-for="t in allTenants" :key="t.tenant_id" :value="t.tenant_id">
              {{ t.company_name }} ({{ t.tenant_code }})
            </option>
          </select>
          <input type="date" v-model="filterDateFrom" @change="handleFilterChange"
            class="px-3 py-1.5 border border-default rounded-lg text-sm bg-surface focus:outline-none focus:border-primary-400" />
          <span class="text-muted text-sm">至</span>
          <input type="date" v-model="filterDateTo" @change="handleFilterChange"
            class="px-3 py-1.5 border border-default rounded-lg text-sm bg-surface focus:outline-none focus:border-primary-400" />
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" @click="refresh">刷新</BaseButton>
          <BaseButton @click="openAddDialog">新增充值</BaseButton>
        </div>
      </div>

      <!-- 表格 -->
      <div class="table-scroll-wrapper flex-1 min-h-0">
        <BaseTable :columns="columns" :data="items" row-key="id">
          <template #seq="{ index }">
            {{ seqNumber(index) }}
          </template>
          <template #tenant_name="{ row }">
            <span>{{ row.tenant_name || row.tenant_id || '-' }}</span>
          </template>
          <template #amount_yuan="{ row }">
            <span class="text-default font-medium">¥ {{ formatAmount(row.amount_yuan) }}</span>
          </template>
          <template #credits="{ row }">
            <span class="text-primary-600 font-medium">{{ row.credits }}</span>
          </template>
          <template #balance_after="{ row }">
            <span v-if="row.balance_after != null" class="text-default font-medium">{{ formatCredit(row.balance_after) }}</span>
            <span v-else class="text-muted">-</span>
          </template>
          <template #source="{ row }">
            <span :class="getSourceBadgeClass(row.source)"
              class="px-2 py-0.5 rounded-full text-xs font-medium">
              {{ getSourceLabel(row.source) }}
            </span>
          </template>
          <template #created_at="{ row }">
            <span class="text-sm text-muted">{{ formatDateTime(row.created_at) }}</span>
          </template>
          <template #actions="{ row }">
            <div class="flex justify-center gap-1">
              <BaseButton intent="danger-ghost" size="sm" @click="handleDelete(row)">删除</BaseButton>
            </div>
          </template>
          <template #empty>暂无充值记录</template>
        </BaseTable>
      </div>

      <!-- 分页器 -->
      <BasePagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :show-size-changer="true"
        @change="onPageChange"
      />
    </div>

    <!-- 新增弹框 -->
    <BaseModal
      v-model="showFormDialog"
      title="新增充值"
      size="md"
    >
      <div class="space-y-4">
        <div>
          <label class="text-sm text-muted mb-1 block">租户 <span class="text-danger-500">*</span></label>
          <div class="relative">
            <input
              v-model="tenantSearch"
              @focus="showTenantList = true"
              @blur="hideTenantListDelayed"
              placeholder="输入租户名称或代码筛选"
              autocomplete="off"
              class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400"
            />
            <div v-if="showTenantList && filteredTenants.length"
              class="absolute z-20 mt-1 w-full max-h-60 overflow-y-auto bg-surface border border-default rounded-lg shadow-lg">
              <div
                v-for="t in filteredTenants"
                :key="t.tenant_id"
                @mousedown.prevent="selectTenant(t)"
                class="px-3 py-2 hover:bg-surface-hover cursor-pointer text-sm text-default"
              >
                {{ t.company_name }} ({{ t.tenant_code }})
              </div>
            </div>
            <p v-if="formData.tenant_id" class="text-xs text-success-700 mt-1">已选租户 ID：{{ formData.tenant_id }}</p>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">充值金额（元） <span class="text-danger-500">*</span></label>
            <input v-model.number="formData.amount_yuan" type="number" min="1" step="1" placeholder="正整数"
              class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400" />
            <p class="text-xs text-muted mt-1">正整数，单位元</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">转化积分 <span class="text-danger-500">*</span></label>
            <input v-model.number="formData.credits" type="number" min="1" step="1" placeholder="正整数"
              class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400" />
            <p class="text-xs text-muted mt-1">正整数</p>
          </div>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">充值日期</label>
          <input v-model="formData.created_at" type="datetime-local"
            class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400" />
          <p class="text-xs text-muted mt-1">默认为当前时间，可修改</p>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">备注</label>
          <textarea v-model="formData.remark" rows="3" placeholder="可填写充值备注"
            class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400"></textarea>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showFormDialog = false">取消</BaseButton>
        <BaseButton @click="handleSubmit">确认充值</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { usePageContext } from '@/composables/usePageContext'
import { listTenants } from '@/api/saasTenant'
import { listRecharges, createRecharge, deleteRecharge, type RechargeItem } from '@/api/billing'
import { formatCredit } from '@/utils/formatCredit'

const toast = useToast()
const { admin, isLoggedIn } = useTenantAuth()

const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

// 全部租户（用于下拉选择和租户名展示）
const allTenants = ref<any[]>([])

async function loadAllTenants() {
  try {
    const res = await listTenants({ page: 1, page_size: 1000 })
    if (res.success) {
      allTenants.value = res.tenants || []
    }
  } catch (e: any) {
    toast.error(e.message || '加载租户列表失败')
  }
}

// 筛选条件
const filterTenantId = ref('')
const filterDateFrom = ref('')
const filterDateTo = ref('')

// 列表数据
const items = ref<RechargeItem[]>([])
const total = ref(0)

const { currentPage, pageSize, seqNumber, refresh } = usePageContext(async () => {
  await loadData()
})

async function loadData() {
  try {
    const res = await listRecharges({
      tenant_id: filterTenantId.value || undefined,
      date_from: filterDateFrom.value || undefined,
      date_to: filterDateTo.value || undefined,
      page: currentPage.value,
      page_size: pageSize.value,
    })
    if (res.success) {
      items.value = res.items || []
      total.value = res.total || 0
    } else {
      toast.error(res.message || '加载充值记录失败')
      items.value = []
      total.value = 0
    }
  } catch (e: any) {
    toast.error(e.message || '加载充值记录失败')
    items.value = []
    total.value = 0
  }
}

function handleFilterChange() {
  currentPage.value = 1
  refresh()
}

function onPageChange(page: number, size: number) {
  currentPage.value = page
  pageSize.value = size
  refresh()
}

// 新增弹框
const showFormDialog = ref(false)

// 租户 autocomplete 关键词与下拉控制
const tenantSearch = ref('')
const showTenantList = ref(false)

const filteredTenants = computed(() => {
  const kw = tenantSearch.value.trim().toLowerCase()
  if (!kw) return allTenants.value
  return allTenants.value.filter(t =>
    (t.company_name || '').toLowerCase().includes(kw) ||
    (t.tenant_code || '').toLowerCase().includes(kw)
  )
})

function selectTenant(t: any) {
  formData.value.tenant_id = t.tenant_id
  tenantSearch.value = `${t.company_name} (${t.tenant_code})`
  showTenantList.value = false
}

function hideTenantListDelayed() {
  // 延迟关闭，让 mousedown 选中事件先触发
  setTimeout(() => { showTenantList.value = false }, 150)
}

// 生成 datetime-local 控件所需的 "YYYY-MM-DDTHH:MM" 格式（本地时间）
function getCurrentDateTimeLocal(): string {
  const now = new Date()
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
}

const defaultForm = () => ({
  tenant_id: '',
  amount_yuan: 0,
  credits: 0,
  created_at: getCurrentDateTimeLocal(),
  remark: '',
})
const formData = ref(defaultForm())

function openAddDialog() {
  formData.value = defaultForm()
  tenantSearch.value = ''
  showTenantList.value = false
  showFormDialog.value = true
}

async function handleSubmit() {
  if (!formData.value.tenant_id) {
    toast.error('请选择租户')
    return
  }
  // 正整数校验：amount_yuan 与 credits 都必须是 >= 1 的整数
  const amount = Number(formData.value.amount_yuan)
  const credits = Number(formData.value.credits)
  if (!Number.isInteger(amount) ) { //|| amount < 1 为调试方便，暂放宽限制
    toast.error('充值金额必须是正整数')
    return
  }
  if (!Number.isInteger(credits) ) { //|| credits < 1 为调试方便，暂放宽限制
    toast.error('转化积分必须是正整数')
    return
  }
  try {
    const res = await createRecharge({
      tenant_id: formData.value.tenant_id,
      amount_yuan: amount,
      credits: credits,
      created_at: formData.value.created_at || undefined,
      remark: formData.value.remark || undefined,
    })
    if (res.success) {
      toast.success('充值成功')
      showFormDialog.value = false
      await refresh()
    } else {
      toast.error(res.message || '充值失败')
    }
  } catch (e: any) {
    toast.error(e.message || '充值失败')
  }
}

async function handleDelete(row: any) {
  if (!confirm(`确定删除该充值记录？\n\n租户：${row.tenant_name || row.tenant_id}\n金额：¥${row.amount_yuan}\n积分：${row.credits}\n\n删除后租户余额将回扣 ${row.credits} 积分。`)) {
    return
  }
  try {
    const res = await deleteRecharge(row.id)
    if (res.success) {
      toast.success('删除成功')
      await refresh()
    } else {
      toast.error(res.message || '删除失败')
    }
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

// 表格列
const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'tenant_name', label: '租户' },
  { key: 'amount_yuan', label: '充值金额', width: '120px' },
  { key: 'credits', label: '转化积分', width: '120px' },
  { key: 'balance_after', label: '充值后积分余额', width: '140px' },
  { key: 'source', label: '来源', width: '100px' },
  { key: 'operator_name', label: '操作人', width: '120px' },
  { key: 'remark', label: '备注' },
  { key: 'created_at', label: '充值日期', width: '160px' },
  { key: 'actions', label: '操作', width: '100px' },
]

// 工具函数
function formatAmount(v: number | string): string {
  const n = Number(v) || 0
  return n.toFixed(2)
}

function formatDateTime(datetime: string): string {
  if (!datetime) return ''
  const date = new Date(datetime)
  return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function getSourceLabel(source: string): string {
  switch (source) {
    case 'manual': return '手动'
    case 'online_payment': return '在线支付'
    default: return source || '-'
  }
}

function getSourceBadgeClass(source: string): string {
  switch (source) {
    case 'manual': return 'bg-info-100 text-info-700'
    case 'online_payment': return 'bg-success-100 text-success-700'
    default: return 'bg-gray-100 text-gray-700'
  }
}

onMounted(async () => {
  await loadAllTenants()
  await refresh()
})
</script>

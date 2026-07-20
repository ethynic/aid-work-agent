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
          <select v-model="formData.tenant_id"
            class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400">
            <option value="">请选择租户</option>
            <option v-for="t in allTenants" :key="t.tenant_id" :value="t.tenant_id">
              {{ t.company_name }} ({{ t.tenant_code }})
            </option>
          </select>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">充值金额（元） <span class="text-danger-500">*</span></label>
            <input v-model.number="formData.amount_yuan" type="number" step="0.01" min="0.01"
              @input="recalculateCredits"
              class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">兑换系数</label>
            <input v-model.number="formData.rate" type="number" min="1" step="1"
              @input="recalculateCredits"
              class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400" />
          </div>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">转化积分</label>
          <input v-model.number="formData.credits" type="number" min="0" step="1"
            class="w-full px-3 py-2 border border-default rounded-lg text-default bg-surface focus:outline-none focus:border-primary-400" />
          <p class="text-xs text-muted mt-1">默认按金额 × 系数自动计算，可手动修改</p>
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
import { ref, onMounted, inject, watch } from 'vue'
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
const defaultForm = () => ({
  tenant_id: '',
  amount_yuan: 0,
  rate: 10,
  credits: 0,
  remark: '',
})
const formData = ref(defaultForm())
const creditsManuallyEdited = ref(false)

function recalculateCredits() {
  if (creditsManuallyEdited.value) return
  if (formData.value.amount_yuan && formData.value.rate) {
    formData.value.credits = Math.floor(formData.value.amount_yuan * formData.value.rate)
  }
}

function openAddDialog() {
  formData.value = defaultForm()
  creditsManuallyEdited.value = false
  showFormDialog.value = true
}

async function handleSubmit() {
  if (!formData.value.tenant_id) {
    toast.error('请选择租户')
    return
  }
  if (!formData.value.amount_yuan || formData.value.amount_yuan <= 0) {
    toast.error('充值金额必须大于 0')
    return
  }
  if (!formData.value.credits || formData.value.credits <= 0) {
    toast.error('转化积分必须大于 0')
    return
  }
  try {
    const res = await createRecharge({
      tenant_id: formData.value.tenant_id,
      amount_yuan: formData.value.amount_yuan,
      credits: formData.value.credits,
      rate: formData.value.rate,
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
  { key: 'rate', label: '兑换系数', width: '100px' },
  { key: 'source', label: '来源', width: '100px' },
  { key: 'operator_name', label: '操作人', width: '120px' },
  { key: 'remark', label: '备注' },
  { key: 'created_at', label: '创建时间', width: '160px' },
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

// 监听 credits 手动编辑
watch(() => formData.value.credits, (newVal, oldVal) => {
  // 用户主动修改 credits 时（不是 recalculate 触发）标记
  if (newVal !== oldVal && showFormDialog.value) {
    const expected = Math.floor(formData.value.amount_yuan * formData.value.rate)
    if (newVal !== expected) {
      creditsManuallyEdited.value = true
    }
  }
})

onMounted(async () => {
  await loadAllTenants()
  await refresh()
})
</script>

<template>
  <div class="page-container">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">外贸客户信息</h2>
      </div>
      <div class="page-toolbar-right">
        <BaseButton size="sm" :disabled="loading" @click="loadData">
          {{ loading ? '加载中...' : '刷新' }}
        </BaseButton>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div v-if="stats" class="grid grid-cols-4 gap-4 mb-4">
      <div class="rounded-xl border border-default bg-surface p-4 text-center">
        <div class="text-2xl font-bold text-default tabular-nums">{{ stats.total_customers }}</div>
        <div class="text-sm text-muted mt-1">匹配客户总数</div>
      </div>
      <div class="rounded-xl border border-default bg-surface p-4 text-center">
        <div class="text-2xl font-bold text-success-600 tabular-nums">{{ stats.success_emails }}</div>
        <div class="text-sm text-muted mt-1">成功发送邮件</div>
      </div>
      <div class="rounded-xl border border-default bg-surface p-4 text-center">
        <div class="text-2xl font-bold text-danger-600 tabular-nums">{{ stats.failed_emails }}</div>
        <div class="text-sm text-muted mt-1">发送失败</div>
      </div>
      <div class="rounded-xl border border-default bg-surface p-4 text-center">
        <div class="text-2xl font-bold text-info-600 tabular-nums">{{ stats.recent_customers }}</div>
        <div class="text-sm text-muted mt-1">本周新增客户</div>
      </div>
    </div>

    <!-- 错误提示 -->
    <div v-if="error" class="mb-4 p-4 bg-danger-50 border border-danger-200 rounded-lg text-danger-600 text-sm">
      <p class="m-0">{{ error }}</p>
      <p v-if="debug" class="mt-2 text-xs text-danger-700">{{ debug }}</p>
    </div>

    <!-- 客户列表 -->
    <template v-if="!loading && !error && customers.length > 0">
      <div class="table-scroll-wrapper">
        <BaseTable :columns="columns" :data="pagedCustomers" row-key="customer_id">
          <template #index="{ index }">{{ seqNumber(index) }}</template>
          <template #company_name="{ row }">
            <span class="font-medium text-default">{{ row.company_name || '-' }}</span>
          </template>
          <template #contact_name="{ row }">{{ row.contact_name || '-' }}</template>
          <template #email="{ row }">
            <span class="text-primary-600">{{ row.email || '-' }}</span>
          </template>
          <template #country="{ row }">
            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-info-100 text-info-700">{{ row.country || '-' }}</span>
          </template>
          <template #language="{ row }">
            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-primary-100 text-primary-700">{{ row.language || '-' }}</span>
          </template>
          <template #match_date="{ row }">{{ formatDate(row.match_date) }}</template>
          <template #industry="{ row }">{{ row.industry || '-' }}</template>
          <template #import_category="{ row }">{{ row.import_category || '-' }}</template>
          <template #company_size="{ row }">{{ row.company_size || '-' }}</template>
          <template #match_reason="{ row }">
            <span class="block max-w-[200px] truncate" :title="row.match_reason">{{ row.match_reason || '-' }}</span>
          </template>
          <template #email_status="{ row }">
            <span v-if="getCustomerEmailCount(row.customer_id) > 0" class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-success-100 text-success-700">
              已发 {{ getCustomerEmailCount(row.customer_id) }} 封
            </span>
            <span v-else class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-gray-100 text-muted">未发邮件</span>
          </template>
          <template #actions="{ row }">
            <BaseButton intent="ghost" size="sm" @click="toggleCustomerDetail(row.customer_id)">
              {{ expandedCustomerId === row.customer_id ? '收起' : '详情' }}
            </BaseButton>
          </template>
          <template #empty>暂无客户信息</template>
        </BaseTable>
      </div>

      <BasePagination
        :total="total"
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
      />
    </template>

    <!-- 加载状态 -->
    <div v-else-if="loading" class="text-center py-12 text-muted">
      <svg class="w-8 h-8 animate-spin mx-auto mb-3" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
      加载客户信息中...
    </div>

    <!-- 空状态 -->
    <div v-else-if="!error && customers.length === 0" class="text-center py-12 text-muted">
      <p class="text-lg mb-2">暂无客户信息</p>
      <p class="text-sm">请先在外贸智能体中匹配客户</p>
    </div>

    <!-- 邮件详情弹窗 -->
    <BaseModal v-model="showEmailModal" :title="'邮件详情 - ' + getExpandedCustomerName()" size="lg" mode="view">
      <div v-if="getCustomerEmails(expandedCustomerId || '').length > 0">
        <BaseTable :columns="emailColumns" :data="getCustomerEmails(expandedCustomerId || '')" row-key="email_id">
          <template #email_subject="{ row }">{{ row.email_subject || '-' }}</template>
          <template #email_language="{ row }">{{ row.email_language || '-' }}</template>
          <template #send_time="{ row }">{{ formatDateTime(row.send_time) }}</template>
          <template #send_status="{ row }">
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="emailStatusClass(row.send_status)">
              {{ row.send_status === 'success' ? '成功' : row.send_status === 'failed' ? '失败' : '进行中' }}
            </span>
          </template>
          <template #actions="{ row }">
            <BaseButton intent="ghost" size="sm" @click="toggleEmailDetail(row.email_id)">
              {{ expandedEmailId === row.email_id ? '收起' : '查看内容' }}
            </BaseButton>
          </template>
        </BaseTable>

        <div v-if="expandedEmailId" class="mt-4 p-4 bg-canvas rounded-lg border border-default">
          <h4 class="text-sm font-medium text-muted mb-2">邮件内容</h4>
          <pre class="text-sm text-default whitespace-pre-wrap break-words m-0 font-inherit max-h-[300px] overflow-y-auto">{{ getExpandedEmailBody() }}</pre>
        </div>
      </div>
      <div v-else class="text-center py-8 text-muted text-sm">暂无邮件发送记录</div>
      <template #footer>
        <BaseButton intent="secondary" @click="showEmailModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  listCustomers,
  getCustomer,
  getStats,
  type Customer,
  type CustomerEmail,
  type CustomerStats
} from '@/api/customer'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const { user: demoUser } = useDemoAuth()
const { admin: tenantAdmin } = useTenantAuth()

const loading = ref(false)
const error = ref('')
const debug = ref('')
const customers = ref<Customer[]>([])
const customerEmails = ref<Map<string, CustomerEmail[]>>(new Map())
const stats = ref<CustomerStats | null>(null)
const expandedCustomerId = ref<string | null>(null)
const expandedEmailId = ref<string | null>(null)
const showEmailModal = ref(false)
const total = ref(0)

// 优先用 URL query 中的 user_id（保留可覆盖能力），否则按路由从登录态取
const userId = computed(() => {
  const queryUserId = route.query.user_id as string
  if (queryUserId) return queryUserId
  // 租户前台模式 /t/:tenant_id/*
  if (route.path.startsWith('/t/')) {
    return tenantAdmin.value?.user_id || ''
  }
  // 演示模式 / 其他
  return demoUser.value?.user_id || ''
})

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'company_name', label: '公司名称' },
  { key: 'contact_name', label: '联系人' },
  { key: 'email', label: '邮箱' },
  { key: 'country', label: '国家' },
  { key: 'language', label: '语言' },
  { key: 'match_date', label: '匹配日期' },
  { key: 'industry', label: '行业' },
  { key: 'import_category', label: '进口品类' },
  { key: 'company_size', label: '公司规模' },
  { key: 'match_reason', label: '匹配原因' },
  { key: 'email_status', label: '邮件状态' },
  { key: 'actions', label: '操作', width: '80px' },
]

const emailColumns = [
  { key: 'email_subject', label: '邮件主题' },
  { key: 'email_language', label: '语言' },
  { key: 'send_time', label: '发送时间' },
  { key: 'send_status', label: '状态' },
  { key: 'actions', label: '操作', width: '100px' },
]

const { currentPage, pageSize, seqNumber } =
  usePageContext(async () => { await loadData() })

const pagedCustomers = computed(() => {
  total.value = customers.value.length
  const start = (currentPage.value - 1) * pageSize.value
  return customers.value.slice(start, start + pageSize.value)
})

function emailStatusClass(status: string) {
  const map: Record<string, string> = {
    success: 'bg-success-100 text-success-700',
    failed: 'bg-danger-100 text-danger-700',
    pending: 'bg-warning-100 text-warning-700',
  }
  return map[status] || 'bg-gray-100 text-muted'
}

async function loadData() {
  if (!userId.value) {
    error.value = '缺少用户ID参数，请从外贸智能体页面跳转'
    return
  }

  loading.value = true
  error.value = ''
  debug.value = ''

  try {
    const [customerRes, statsRes] = await Promise.all([
      listCustomers(userId.value),
      getStats(userId.value)
    ])

    if (customerRes.success && customerRes.data) {
      customers.value = customerRes.data.customers

      const emailPromises = customerRes.data.customers.map(async (customer) => {
        const detailRes = await getCustomer(customer.customer_id)
        if (detailRes.success && detailRes.data) {
          customerEmails.value.set(customer.customer_id, detailRes.data.emails || [])
        }
      })
      await Promise.all(emailPromises)
    } else {
      error.value = customerRes.error || '获取客户列表失败'
      debug.value = customerRes.debug || ''
    }

    if (statsRes.success && statsRes.data) {
      stats.value = statsRes.data
    }
  } catch (e: any) {
    error.value = '加载客户信息失败'
    debug.value = e.message || ''
  } finally {
    loading.value = false
  }
}

function getCustomerEmailCount(customerId: string): number {
  return customerEmails.value.get(customerId)?.length || 0
}

function getCustomerEmails(customerId: string): CustomerEmail[] {
  return customerEmails.value.get(customerId) || []
}

function toggleCustomerDetail(customerId: string) {
  if (expandedCustomerId.value === customerId) {
    expandedCustomerId.value = null
    expandedEmailId.value = null
    showEmailModal.value = false
  } else {
    expandedCustomerId.value = customerId
    expandedEmailId.value = null
    showEmailModal.value = true
  }
}

function toggleEmailDetail(emailId: string) {
  expandedEmailId.value = expandedEmailId.value === emailId ? null : emailId
}

function getExpandedCustomerName(): string {
  const customer = customers.value.find(c => c.customer_id === expandedCustomerId.value)
  return customer?.company_name || '-'
}

function getExpandedEmailBody(): string {
  const emails = getCustomerEmails(expandedCustomerId.value || '')
  const email = emails.find(e => e.email_id === expandedEmailId.value)
  return email?.email_body || '-'
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch { return dateStr }
}

function formatDateTime(dateStr: string): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return dateStr }
}

onMounted(() => { loadData() })
</script>

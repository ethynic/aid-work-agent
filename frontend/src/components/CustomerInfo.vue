<template>
  <div class="customer-info-container">
    <!-- 头部 -->
    <div class="header">
      <button v-if="false" class="back-btn" @click="goBack">
        <span>←</span> 返回
      </button>
      <h1>📋 外贸客户信息</h1>
      <div class="header-actions">
        <button class="refresh-btn" @click="loadData" :disabled="loading">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div v-if="stats" class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">{{ stats.total_customers }}</div>
        <div class="stat-label">匹配客户总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.success_emails }}</div>
        <div class="stat-label">成功发送邮件</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.failed_emails }}</div>
        <div class="stat-label">发送失败</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.recent_customers }}</div>
        <div class="stat-label">本周新增客户</div>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <p>加载客户信息中...</p>
    </div>

    <!-- 错误提示 -->
    <div v-else-if="error" class="error-message">
      <p>❌ {{ error }}</p>
      <p v-if="debug" class="debug-info">{{ debug }}</p>
    </div>

    <!-- 客户列表 - 表格形式 -->
    <div v-else-if="customers.length > 0" class="customer-table-section">
      <div class="table-header">
        <h2>📋 匹配客户列表 ({{ customers.length }})</h2>
      </div>

      <div class="table-wrapper">
        <table class="customer-table">
          <thead>
            <tr>
              <th>公司名称</th>
              <th>联系人</th>
              <th>邮箱</th>
              <th>国家</th>
              <th>语言</th>
              <th>匹配日期</th>
              <th>行业</th>
              <th>进口品类</th>
              <th>公司规模</th>
              <th>匹配原因</th>
              <th>邮件状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="customer in customers" :key="customer.customer_id">
              <td class="company-name">{{ customer.company_name || '-' }}</td>
              <td>{{ customer.contact_name || '-' }}</td>
              <td class="email-cell">{{ customer.email || '-' }}</td>
              <td>
                <span class="country-badge">{{ customer.country || '-' }}</span>
              </td>
              <td>
                <span class="language-badge">{{ customer.language || '-' }}</span>
              </td>
              <td>{{ formatDate(customer.match_date) }}</td>
              <td>{{ customer.industry || '-' }}</td>
              <td>{{ customer.import_category || '-' }}</td>
              <td>{{ customer.company_size || '-' }}</td>
              <td class="match-reason-cell" :title="customer.match_reason">
                {{ customer.match_reason || '-' }}
              </td>
              <td>
                <span v-if="getCustomerEmailCount(customer.customer_id) > 0" class="email-badge success">
                  ✉️ 已发 {{ getCustomerEmailCount(customer.customer_id) }} 封
                </span>
                <span v-else class="email-badge no-email">
                  ✉️ 未发邮件
                </span>
              </td>
              <td>
                <button class="detail-btn" @click="toggleCustomerDetail(customer.customer_id)">
                  {{ expandedCustomerId === customer.customer_id ? '收起' : '详情' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

    </div>

    <!-- 邮件详情弹窗 -->
    <div v-if="expandedCustomerId" class="modal-overlay" @click.self="expandedCustomerId = null">
      <div class="modal-content">
        <div class="modal-header">
          <h3>邮件详情 - {{ getExpandedCustomerName() }}</h3>
          <button class="close-btn" @click="expandedCustomerId = null">×</button>
        </div>

        <!-- 邮件列表 -->
        <div v-if="getCustomerEmails(expandedCustomerId).length > 0" class="modal-body">
          <table class="email-table">
            <thead>
              <tr>
                <th>邮件主题</th>
                <th>语言</th>
                <th>发送时间</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="email in getCustomerEmails(expandedCustomerId)" :key="email.email_id">
                <td>{{ email.email_subject || '-' }}</td>
                <td>{{ email.email_language || '-' }}</td>
                <td>{{ formatDate(email.send_time) }}</td>
                <td>
                  <span :class="['status-badge', email.send_status]">
                    {{ email.send_status === 'success' ? '✅ 成功' : email.send_status === 'failed' ? '❌ 失败' : '⏳ 进行中' }}
                  </span>
                </td>
                <td>
                  <button class="text-btn" @click="toggleEmailDetail(email.email_id)">
                    {{ expandedEmailId === email.email_id ? '收起' : '查看内容' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>

          <!-- 展开的邮件内容 -->
          <div v-if="expandedEmailId" class="email-body-section">
            <h4>邮件内容</h4>
            <pre class="email-body-content">{{ getExpandedEmailBody() }}</pre>
          </div>
        </div>

        <div v-else class="no-emails">
          <p>暂无邮件发送记录</p>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="empty-state">
      <p>📭 暂无客户信息</p>
      <p class="hint">请先在外贸智能体中匹配客户</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  listCustomers,
  getCustomer,
  getStats,
  type Customer,
  type CustomerEmail,
  type CustomerStats
} from '../api/customer'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const error = ref('')
const debug = ref('')
const customers = ref<Customer[]>([])
const customerEmails = ref<Map<string, CustomerEmail[]>>(new Map())
const stats = ref<CustomerStats | null>(null)
const expandedCustomerId = ref<string | null>(null)
const expandedEmailId = ref<string | null>(null)

// 获取 URL 参数
const userId = computed(() => route.query.user_id as string)
const sessionId = computed(() => route.query.session_id as string)

// 加载数据
async function loadData() {
  console.log('前端日志：loadData开始', { userId: userId.value, sessionId: sessionId.value })
  if (!userId.value) {
    error.value = '缺少用户ID参数，请从外贸智能体页面跳转'
    return
  }

  loading.value = true
  error.value = ''
  debug.value = ''

  try {
    // 并行加载客户列表和统计
    // 注意：不传 sessionId 以获取用户的所有客户（包括所有 session）
    console.log('前端日志：开始请求API', { userId: userId.value })
    const [customerRes, statsRes] = await Promise.all([
      listCustomers(userId.value),
      getStats(userId.value)
    ])
    console.log('前端日志：API响应', { customerRes, statsRes })

    if (customerRes.success && customerRes.data) {
      customers.value = customerRes.data.customers

      // 为每个客户加载邮件
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

// 返回上一页
function goBack() {
  router.back()
}

// 获取客户的邮件数量
function getCustomerEmailCount(customerId: string): number {
  return customerEmails.value.get(customerId)?.length || 0
}

// 获取客户的邮件列表
function getCustomerEmails(customerId: string): CustomerEmail[] {
  return customerEmails.value.get(customerId) || []
}

// 展开/收起客户详情
function toggleCustomerDetail(customerId: string) {
  expandedCustomerId.value = expandedCustomerId.value === customerId ? null : customerId
  // 收起时也要收起邮件详情
  if (expandedCustomerId.value === null) {
    expandedEmailId.value = null
  }
}

// 展开/收起邮件详情
function toggleEmailDetail(emailId: string) {
  expandedEmailId.value = expandedEmailId.value === emailId ? null : emailId
}

// 获取展开客户的名称
function getExpandedCustomerName(): string {
  const customer = customers.value.find(c => c.customer_id === expandedCustomerId.value)
  return customer?.company_name || '-'
}

// 获取展开邮件的内容
function getExpandedEmailBody(): string {
  const emails = getCustomerEmails(expandedCustomerId.value || '')
  const email = emails.find(e => e.email_id === expandedEmailId.value)
  return email?.email_body || '-'
}

// 格式化日期
function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  if (isNaN(date.getTime())) return dateStr
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  })
}

// 页面加载时获取数据
onMounted(() => {
  loadData()
})
</script>

<style scoped>
.customer-info-container {
  max-width: 1800px;
  margin: 0 auto;
  padding: 20px;
}

.header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e5e7eb;
}

.header h1 {
  flex: 1;
  font-size: 24px;
  margin: 0;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 16px;
  background: #f3f4f6;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}

.back-btn:hover {
  background: #e5e7eb;
}

.refresh-btn {
  padding: 8px 16px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}

.refresh-btn:hover:not(:disabled) {
  background: #2563eb;
}

.refresh-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
}

/* 统计卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 20px;
  border-radius: 12px;
  text-align: center;
}

.stat-value {
  font-size: 32px;
  font-weight: bold;
}

.stat-label {
  font-size: 14px;
  opacity: 0.9;
  margin-top: 4px;
}

/* 加载状态 */
.loading {
  text-align: center;
  padding: 60px 20px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #e5e7eb;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 错误提示 */
.error-message {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #dc2626;
  padding: 16px;
  border-radius: 8px;
  margin-bottom: 24px;
}

.debug-info {
  font-size: 12px;
  color: #991b1b;
  margin-top: 8px;
}

/* 表格区域 */
.customer-table-section {
  background: white;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.table-header {
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
}

.table-header h2 {
  margin: 0;
  font-size: 18px;
}

.table-wrapper {
  overflow-x: auto;
}

/* 客户表格 */
.customer-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.customer-table th {
  background: #f9fafb;
  padding: 12px 16px;
  text-align: left;
  font-weight: 600;
  color: #374151;
  border-bottom: 2px solid #e5e7eb;
  white-space: nowrap;
}

.customer-table td {
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
  color: #6b7280;
}

.customer-table tbody tr:hover {
  background: #f9fafb;
}

.customer-table tbody tr:last-child td {
  border-bottom: none;
}

.company-name {
  font-weight: 600;
  color: #111827 !important;
}

.email-cell {
  color: #3b82f6 !important;
}

.match-reason-cell {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 表格徽章 */
.country-badge,
.language-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.country-badge {
  background: #dbeafe;
  color: #1e40af;
}

.language-badge {
  background: #f3e8ff;
  color: #7c3aed;
}

.email-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.email-badge.success {
  background: #dcfce7;
  color: #166534;
}

.email-badge.no-email {
  background: #f3f4f6;
  color: #9ca3af;
}

/* 操作列 */
.customer-table th:last-child,
.customer-table td:last-child {
  width: 80px;
  text-align: center;
}

/* 操作按钮 */
.detail-btn {
  padding: 6px 16px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
}

.detail-btn:hover {
  background: #2563eb;
}

/* 模态框 */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: white;
  border-radius: 12px;
  width: 90%;
  max-width: 900px;
  max-height: 80vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
}

.modal-header h3 {
  margin: 0;
  font-size: 16px;
}

.close-btn {
  width: 32px;
  height: 32px;
  background: #e5e7eb;
  border: none;
  border-radius: 4px;
  font-size: 20px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.close-btn:hover {
  background: #d1d5db;
}

.modal-body {
  padding: 20px;
  overflow-y: auto;
  flex: 1;
}

/* 邮件表格 */
.email-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.email-table th {
  background: #f3f4f6;
  padding: 12px 16px;
  text-align: left;
  font-weight: 600;
  color: #374151;
  border-bottom: 2px solid #e5e7eb;
}

.email-table td {
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
  color: #6b7280;
}

.email-table tbody tr:hover {
  background: #f9fafb;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}

.status-badge.success {
  background: #dcfce7;
  color: #166534;
}

.status-badge.failed {
  background: #fee2e2;
  color: #991b1b;
}

.status-badge.pending {
  background: #fef3c7;
  color: #92400e;
}

.text-btn {
  background: none;
  border: none;
  color: #3b82f6;
  cursor: pointer;
  font-size: 12px;
}

.text-btn:hover {
  text-decoration: underline;
}

/* 邮件内容 */
.email-body-section {
  margin-top: 16px;
  padding: 16px;
  background: white;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}

.email-body-section h4 {
  margin: 0 0 12px 0;
  font-size: 14px;
}

.email-body-content {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-size: 13px;
  font-family: inherit;
  color: #374151;
  background: #f9fafb;
  padding: 12px;
  border-radius: 6px;
  max-height: 300px;
  overflow-y: auto;
}

/* 无邮件 */
.no-emails {
  text-align: center;
  padding: 40px;
  color: #9ca3af;
}

.no-emails p {
  margin: 0;
}

/* 空状态 */
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: #6b7280;
}

.empty-state p {
  margin: 0;
}

.hint {
  font-size: 14px;
  margin-top: 8px;
  color: #9ca3af;
}
</style>

<template>
  <div class="customer-info-container">
    <!-- 头部 -->
    <div class="header">
      <button class="back-btn" @click="goBack">
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

    <!-- 国家分布 -->
    <div v-if="stats?.country_distribution?.length" class="country-section">
      <h3>🌍 客户国家分布</h3>
      <div class="country-tags">
        <span
          v-for="item in stats.country_distribution"
          :key="item.country"
          class="country-tag"
        >
          {{ item.country }}: {{ item.count }}
        </span>
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

    <!-- 客户列表 -->
    <div v-else-if="customers.length > 0" class="customer-list">
      <div class="list-header">
        <h2>📋 匹配客户列表 ({{ customers.length }})</h2>
      </div>

      <div
        v-for="customer in customers"
        :key="customer.customer_id"
        class="customer-card"
        @click="toggleCustomerDetail(customer.customer_id)"
      >
        <div class="customer-header">
          <div class="customer-main">
            <h3>{{ customer.company_name }}</h3>
            <p class="contact">
              <span class="contact-name">{{ customer.contact_name }}</span>
              <span class="email">{{ customer.email }}</span>
            </p>
          </div>
          <div class="customer-meta">
            <span class="country-badge">{{ customer.country }}</span>
            <span class="language-badge">{{ customer.language }}</span>
          </div>
        </div>

        <div class="customer-info-row">
          <span class="info-item">📅 匹配日期: {{ formatDate(customer.match_date) }}</span>
          <span class="info-item">🏭 行业: {{ customer.industry }}</span>
          <span class="info-item">📦 进口品类: {{ customer.import_category }}</span>
          <span class="info-item">👥 规模: {{ customer.company_size }}</span>
        </div>

        <div v-if="customer.match_reason" class="match-reason">
          💡 {{ customer.match_reason }}
        </div>

        <!-- 邮件状态 -->
        <div class="email-status-section">
          <div class="email-status-header">
            <span v-if="getCustomerEmailCount(customer.customer_id) > 0">
              ✉️ 已发送 {{ getCustomerEmailCount(customer.customer_id) }} 封邮件
            </span>
            <span v-else class="no-email">
              ✉️ 暂无邮件发送记录
            </span>
          </div>

          <!-- 展开的邮件列表 -->
          <div v-if="expandedCustomerId === customer.customer_id && getCustomerEmails(customer.customer_id).length > 0" class="email-list">
            <div
              v-for="email in getCustomerEmails(customer.customer_id)"
              :key="email.email_id"
              class="email-item"
            >
              <div class="email-header">
                <span class="email-subject">{{ email.email_subject }}</span>
                <span :class="['email-status', email.send_status]">
                  {{ email.send_status === 'success' ? '✅' : email.send_status === 'failed' ? '❌' : '⏳' }}
                </span>
              </div>
              <div class="email-meta">
                <span>{{ email.email_language }}</span>
                <span>{{ formatDate(email.send_time) }}</span>
              </div>
              <div v-if="expandedEmailId === email.email_id" class="email-body">
                <pre>{{ email.email_body }}</pre>
              </div>
              <button
                v-if="expandedEmailId !== email.email_id"
                class="expand-email-btn"
                @click.stop="toggleEmailDetail(email.email_id)"
              >
                查看邮件内容
              </button>
              <button
                v-else
                class="collapse-email-btn"
                @click.stop="toggleEmailDetail(null)"
              >
                收起
              </button>
            </div>
          </div>
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

// 调试信息
console.log('前端日志：URL参数', { userId: userId.value, sessionId: sessionId.value })

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
    console.log('前端日志：开始请求API', { userId: userId.value })
    const [customerRes, statsRes] = await Promise.all([
      listCustomers(userId.value, sessionId.value || undefined),
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
}

// 展开/收起邮件详情
function toggleEmailDetail(emailId: string | null) {
  expandedEmailId.value = expandedEmailId.value === emailId ? null : emailId
}

// 格式化日期
function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  if (isNaN(date.getTime())) return dateStr
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

// 页面加载时获取数据
onMounted(() => {
  loadData()
})
</script>

<style scoped>
.customer-info-container {
  max-width: 1200px;
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

/* 国家分布 */
.country-section {
  margin-bottom: 24px;
}

.country-section h3 {
  margin: 0 0 12px 0;
  font-size: 16px;
}

.country-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.country-tag {
  background: #dbeafe;
  color: #1e40af;
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 13px;
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

/* 客户列表 */
.customer-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.list-header {
  margin-bottom: 8px;
}

.list-header h2 {
  margin: 0;
  font-size: 18px;
}

.customer-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 20px;
  cursor: pointer;
  transition: all 0.2s;
}

.customer-card:hover {
  border-color: #3b82f6;
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);
}

.customer-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.customer-main h3 {
  margin: 0 0 8px 0;
  font-size: 18px;
}

.contact {
  margin: 0;
  font-size: 14px;
  color: #6b7280;
}

.contact-name {
  margin-right: 12px;
}

.email {
  color: #3b82f6;
}

.customer-meta {
  display: flex;
  gap: 8px;
}

.country-badge,
.language-badge {
  padding: 4px 10px;
  border-radius: 16px;
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

.customer-info-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 12px;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.match-reason {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 12px;
}

/* 邮件状态 */
.email-status-section {
  border-top: 1px solid #e5e7eb;
  padding-top: 12px;
  margin-top: 12px;
}

.email-status-header {
  font-size: 14px;
  font-weight: 500;
}

.no-email {
  color: #9ca3af;
}

.email-list {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.email-item {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px;
}

.email-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.email-subject {
  font-weight: 500;
  color: #374151;
}

.email-status {
  font-size: 18px;
}

.email-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 8px;
}

.email-body {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 8px;
}

.email-body pre {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-size: 13px;
  font-family: inherit;
}

.expand-email-btn,
.collapse-email-btn {
  background: none;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  color: #3b82f6;
}

.expand-email-btn:hover,
.collapse-email-btn:hover {
  background: #f3f4f6;
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

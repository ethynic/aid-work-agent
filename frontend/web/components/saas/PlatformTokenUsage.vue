<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-default">平台积分消耗报表</h1>
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-2">
          <label class="text-sm text-default">选择月份:</label>
          <input type="month" v-model="selectedMonth" @change="loadData"
            class="px-3 py-1.5 border border-hover rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500">
        </div>
        <button @click="loadData" :disabled="loading"
          class="px-3 py-1.5 bg-primary-500 text-white rounded-lg text-sm hover:bg-primary-700 transition-colors disabled:opacity-50">
          刷新
        </button>
      </div>
    </div>

    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <template v-else>
      <!-- 汇总卡片 -->
      <div v-if="summary" class="mb-2 text-xs text-muted">汇总口径：仅统计真实租户（不含测试/演示租户）</div>
      <div v-if="summary" class="grid grid-cols-2 md:grid-cols-6 gap-4 mb-6">
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">真实租户数量</div>
          <div class="text-xl font-bold text-default mt-1">{{ summary.tenant_count }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">输入Token总数 (百万)</div>
          <div class="text-xl font-bold text-info-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_input_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">输出Token总数 (百万)</div>
          <div class="text-xl font-bold text-success-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_output_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">总对话次数</div>
          <div class="text-xl font-bold text-default mt-1">{{ summary.total_conversations }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">
            总成本 (元)
            <span v-if="summary.has_unpriced_tokens" class="text-amber-500 text-[10px] ml-1">含未计价模型</span>
          </div>
          <div class="text-xl font-bold text-amber-600 mt-1">{{ formatCost(summary.total_cost) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">总消耗积分</div>
          <div class="text-xl font-bold text-danger-600 mt-1">{{ formatCredit(summary.total_credit_cost) }}</div>
        </div>
      </div>

      <!-- 租户表格 -->
      <div class="bg-white rounded-xl shadow-sm border border-default overflow-hidden">
        <div class="px-5 py-3 border-b border-default">
          <h3 class="text-sm font-medium text-default">租户积分消耗明细 ({{ selectedMonth }})</h3>
        </div>
        <div v-if="data.length > 0">
          <table class="w-full">
            <thead class="bg-canvas">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">序号</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">租户代码</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">租户名称</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">类型</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">输入Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">输出Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">Token成本 (元)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">消耗积分</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">参考金额 (元)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">对话次数</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-default">
              <tr v-for="(item, index) in data" :key="item.tenant_id" class="hover:bg-surface-hover">
                <td class="px-4 py-2 text-sm text-default">{{ index + 1 }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ item.tenant_code }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ item.company_name }}</td>
                <td class="px-4 py-2">
                  <span :class="tenantTypeBadgeClass(item.tenant_type)"
                    class="px-2 py-0.5 rounded-full text-xs font-medium">{{ tenantTypeLabel(item.tenant_type) }}</span>
                </td>
                <td class="px-4 py-2 text-sm text-info-600">{{ formatTokensToMillionsThreeDecimals(item.input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-success-600">{{ formatTokensToMillionsThreeDecimals(item.output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-amber-600">
                  {{ formatCost(item.total_cost, item.has_unpriced_tokens) }}
                </td>
                <td class="px-4 py-2 text-sm text-danger-600 font-medium">
                  <a class="underline-offset-2 hover:underline cursor-pointer"
                     :title="`查看 ${item.company_name} ${selectedMonth} 每日积分用量`"
                     @click="openDailyUsageModal(item)">{{ formatCredit(item.credit_cost) }}</a>
                </td>
                <td class="px-4 py-2 text-sm text-default">
                  {{ formatReferenceAmount(item) }}
                  <span v-if="item.has_unrecharged_credits" class="text-amber-500 text-[10px] ml-1">含充值前消耗</span>
                </td>
                <td class="px-4 py-2 text-sm text-default">{{ item.conversation_count }}</td>
              </tr>
              <!-- 汇总行 -->
              <tr v-if="summary" class="bg-canvas font-medium">
                <td class="px-4 py-2 text-sm text-default" colspan="4">总计（仅真实租户）</td>
                <td class="px-4 py-2 text-sm text-info-600">{{ formatTokensToMillionsThreeDecimals(summary.total_input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-success-600">{{ formatTokensToMillionsThreeDecimals(summary.total_output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-amber-600">
                  {{ formatCost(summary.total_cost, summary.has_unpriced_tokens) }}
                </td>
                <td class="px-4 py-2 text-sm text-danger-600 font-medium">{{ formatCredit(summary.total_credit_cost) }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ formatSummaryReferenceAmount() }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ summary.total_conversations }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-center py-8 text-muted">暂无数据</div>
      </div>
    </template>

    <!-- 每日积分用量弹框（当前租户 + 所选月份），消耗积分列可继续下钻明细 -->
    <DailyCreditUsageModal
      v-model="showDailyUsageModal"
      :tenant-id="dailyUsageTenantId"
      :tenant-name="dailyUsageTenantName"
      :month="selectedMonth"
      @view-detail="openDetailModal"
    />

    <!-- 积分用量明细弹框（含 usage_breakdown 7 分项敏感数据，仅管理后台展示） -->
    <DailyCreditUsageDetailModal
      v-model="showDetailModal"
      :tenant-id="dailyUsageTenantId"
      :date="detailDate"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { getPlatformTokenUsage } from '@/api/adminReports'
import { TenantType, TenantTypeMap } from '@/api/enums'
import { formatTokensToMillionsThreeDecimals } from '@/utils/formatTokens'
import { formatCredit } from '@/utils/formatCredit'
import DailyCreditUsageModal from '@/components/saas/DailyCreditUsageModal.vue'
import DailyCreditUsageDetailModal from '@/components/saas/DailyCreditUsageDetailModal.vue'

const toast = useToast()

const loading = ref(true)
const selectedMonth = ref(getDefaultMonth())
const summary = ref<any>(null)
const data = ref<any[]>([])

function getDefaultMonth(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  return `${year}-${month}`
}

async function loadData() {
  loading.value = true
  try {
    const response = await getPlatformTokenUsage(selectedMonth.value)
    if (response.success) {
      summary.value = response.summary
      data.value = response.data
    } else {
      toast.error(response.message || '加载数据失败')
      summary.value = null
      data.value = []
    }
  } catch (error: any) {
    console.error('加载平台Token消耗报表失败:', error)
    toast.error(error.message || '加载数据失败')
    summary.value = null
    data.value = []
  } finally {
    loading.value = false
  }
}

function formatCost(cost: number, hasUnpricedTokens?: boolean): string {
  if (cost > 0) return cost.toFixed(2)
  if (hasUnpricedTokens) return '—'
  return '0.00'
}

function tenantTypeLabel(tenantType?: string): string {
  return (TenantTypeMap as Record<string, { label: string }>)[tenantType ?? TenantType.TEST]?.label ?? '测试租户'
}

function tenantTypeBadgeClass(tenantType?: string): string {
  const color = (TenantTypeMap as Record<string, { color: string }>)[tenantType ?? TenantType.TEST]?.color ?? 'gray'
  return color === 'green' ? 'bg-success-100 text-success-700' : 'bg-gray-100 text-gray-600'
}

function formatReferenceAmount(item: any): string {
  // null 兼容发布后 1h 内旧缓存条目（后端缺字段时传 null）
  if (item.reference_amount == null) return '—'
  if (item.reference_amount > 0) return item.reference_amount.toFixed(2)
  if (item.has_unrecharged_credits) return '—'
  return '0.00'
}

function formatSummaryReferenceAmount(): string {
  const total = summary.value?.total_reference_amount
  if (total == null) return '—'
  return total.toFixed(2)
}

onMounted(() => loadData())

// ============== 每日积分用量弹框 + 积分用量明细弹框（下钻） ==============

const showDailyUsageModal = ref(false)
const showDetailModal = ref(false)
const dailyUsageTenantId = ref('')
const dailyUsageTenantName = ref('')
const detailDate = ref('')

function openDailyUsageModal(item: any) {
  dailyUsageTenantId.value = item.tenant_id
  dailyUsageTenantName.value = item.company_name || item.tenant_code || item.tenant_id
  showDailyUsageModal.value = true
}

function openDetailModal(date: string) {
  detailDate.value = date
  showDetailModal.value = true
}
</script>
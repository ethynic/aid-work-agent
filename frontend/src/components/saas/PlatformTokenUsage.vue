<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-default">平台Token消耗报表</h1>
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
      <div v-if="summary" class="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <div class="bg-white rounded-xl shadow-sm p-4 border border-default">
          <div class="text-xs text-muted">租户数量</div>
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
      </div>

      <!-- 租户表格 -->
      <div class="bg-white rounded-xl shadow-sm border border-default overflow-hidden">
        <div class="px-5 py-3 border-b border-default">
          <h3 class="text-sm font-medium text-default">租户Token消耗明细 ({{ selectedMonth }})</h3>
        </div>
        <div v-if="data.length > 0">
          <table class="w-full">
            <thead class="bg-canvas">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">序号</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">租户代码</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">租户名称</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">输入Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">输出Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">Token成本 (元)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-muted">对话次数</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-default">
              <tr v-for="(item, index) in data" :key="item.tenant_id" class="hover:bg-surface-hover">
                <td class="px-4 py-2 text-sm text-default">{{ index + 1 }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ item.tenant_code }}</td>
                <td class="px-4 py-2 text-sm text-default">{{ item.company_name }}</td>
                <td class="px-4 py-2 text-sm text-info-600">{{ formatTokensToMillionsThreeDecimals(item.input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-success-600">{{ formatTokensToMillionsThreeDecimals(item.output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-amber-600">
                  {{ formatCost(item.total_cost, item.has_unpriced_tokens) }}
                </td>
                <td class="px-4 py-2 text-sm text-default">{{ item.conversation_count }}</td>
              </tr>
              <!-- 汇总行 -->
              <tr v-if="summary" class="bg-canvas font-medium">
                <td class="px-4 py-2 text-sm text-default" colspan="3">总计</td>
                <td class="px-4 py-2 text-sm text-info-600">{{ formatTokensToMillionsThreeDecimals(summary.total_input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-success-600">{{ formatTokensToMillionsThreeDecimals(summary.total_output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-amber-600">
                  {{ formatCost(summary.total_cost, summary.has_unpriced_tokens) }}
                </td>
                <td class="px-4 py-2 text-sm text-default">{{ summary.total_conversations }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-center py-8 text-muted">暂无数据</div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { getPlatformTokenUsage } from '@/api/adminReports'
import { formatTokensToMillionsThreeDecimals } from '@/utils/formatTokens'

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

onMounted(() => loadData())
</script>
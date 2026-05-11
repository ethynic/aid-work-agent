<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-slate-800">平台Token消耗报表</h1>
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-2">
          <label class="text-sm text-slate-600">选择月份:</label>
          <input type="month" v-model="selectedMonth" @change="loadData"
            class="px-3 py-1.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500">
        </div>
        <button @click="loadData" :disabled="loading"
          class="px-3 py-1.5 bg-cyan-500 text-white rounded-lg text-sm hover:bg-cyan-600 transition-colors disabled:opacity-50">
          刷新
        </button>
      </div>
    </div>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <template v-else>
      <!-- 汇总卡片 -->
      <div v-if="summary" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">租户数量</div>
          <div class="text-xl font-bold text-slate-800 mt-1">{{ summary.tenant_count }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">输入Token总数 (百万)</div>
          <div class="text-xl font-bold text-blue-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_input_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">输出Token总数 (百万)</div>
          <div class="text-xl font-bold text-green-600 mt-1">{{ formatTokensToMillionsThreeDecimals(summary.total_output_tokens) }}</div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-4 border border-slate-200">
          <div class="text-xs text-slate-500">总对话次数</div>
          <div class="text-xl font-bold text-slate-800 mt-1">{{ summary.total_conversations }}</div>
        </div>
      </div>

      <!-- 租户表格 -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div class="px-5 py-3 border-b border-slate-200">
          <h3 class="text-sm font-medium text-slate-700">租户Token消耗明细 ({{ selectedMonth }})</h3>
        </div>
        <div v-if="data.length > 0">
          <table class="w-full">
            <thead class="bg-slate-50">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">序号</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">租户代码</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">租户名称</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">输入Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">输出Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">对话次数</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="(item, index) in data" :key="item.tenant_id" class="hover:bg-slate-50">
                <td class="px-4 py-2 text-sm text-slate-600">{{ index + 1 }}</td>
                <td class="px-4 py-2 text-sm text-slate-800">{{ item.tenant_code }}</td>
                <td class="px-4 py-2 text-sm text-slate-800">{{ item.company_name }}</td>
                <td class="px-4 py-2 text-sm text-blue-600">{{ formatTokensToMillionsThreeDecimals(item.input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-green-600">{{ formatTokensToMillionsThreeDecimals(item.output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-slate-600">{{ item.conversation_count }}</td>
              </tr>
              <!-- 汇总行 -->
              <tr v-if="summary" class="bg-slate-50 font-medium">
                <td class="px-4 py-2 text-sm text-slate-800" colspan="3">总计</td>
                <td class="px-4 py-2 text-sm text-blue-600">{{ formatTokensToMillionsThreeDecimals(summary.total_input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-green-600">{{ formatTokensToMillionsThreeDecimals(summary.total_output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-slate-600">{{ summary.total_conversations }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-center py-8 text-slate-400">暂无数据</div>
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

onMounted(() => loadData())
</script>
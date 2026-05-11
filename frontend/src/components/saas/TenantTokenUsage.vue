<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-slate-800">我的Token消耗明细</h1>
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-2">
          <label class="text-sm text-slate-600">选择月份:</label>
          <input type="month" v-model="selectedMonth" @change="loadData(1)"
            class="px-3 py-1.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500">
        </div>
        <button @click="loadData(1)" :disabled="loading"
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
          <div class="text-xs text-slate-500">总对话次数</div>
          <div class="text-xl font-bold text-slate-800 mt-1">{{ summary.total_conversations }}</div>
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
          <div class="text-xs text-slate-500">平均每对话Token数</div>
          <div class="text-xl font-bold text-slate-800 mt-1">
            {{ summary.total_conversations > 0 ? formatTokensToMillionsThreeDecimals((summary.total_input_tokens + summary.total_output_tokens) / summary.total_conversations) : '0.000' }}
          </div>
        </div>
      </div>

      <!-- 对话明细表格 -->
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden mb-6">
        <div class="px-5 py-3 border-b border-slate-200">
          <h3 class="text-sm font-medium text-slate-700">对话明细 ({{ selectedMonth }})</h3>
        </div>
        <div v-if="data.length > 0">
          <table class="w-full">
            <thead class="bg-slate-50">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">序号</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">用户消息（前10字）</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">输入Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">输出Token数 (百万)</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">创建时间</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="(item, index) in data" :key="item.record_id" class="hover:bg-slate-50">
                <td class="px-4 py-2 text-sm text-slate-600">{{ getRowNumber(index) }}</td>
                <td class="px-4 py-2 text-sm text-slate-800" :title="item.user_message">{{ formatMessagePreview(item.user_message) }}</td>
                <td class="px-4 py-2 text-sm text-blue-600">{{ formatTokensToMillionsThreeDecimals(item.input_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-green-600">{{ formatTokensToMillionsThreeDecimals(item.output_tokens) }}</td>
                <td class="px-4 py-2 text-sm text-slate-600">{{ formatDateTime(item.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-center py-8 text-slate-400">暂无数据</div>
      </div>

      <!-- 分页控件 -->
      <div v-if="pagination && pagination.total_pages > 1" class="flex items-center justify-between">
        <div class="text-sm text-slate-500">
          共 {{ pagination.total_count }} 条记录，第 {{ pagination.page }} 页 / 共 {{ pagination.total_pages }} 页
        </div>
        <div class="flex items-center gap-1">
          <button @click="loadData(1)" :disabled="pagination.page === 1"
            class="px-3 py-1.5 text-sm border border-slate-300 rounded-lg hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed">
            首页
          </button>
          <button @click="loadData(pagination.page - 1)" :disabled="pagination.page === 1"
            class="px-3 py-1.5 text-sm border border-slate-300 rounded-lg hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed">
            上一页
          </button>
          <span class="px-3 py-1.5 text-sm text-slate-700">第 {{ pagination.page }} 页</span>
          <button @click="loadData(pagination.page + 1)" :disabled="pagination.page >= pagination.total_pages"
            class="px-3 py-1.5 text-sm border border-slate-300 rounded-lg hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed">
            下一页
          </button>
          <button @click="loadData(pagination.total_pages)" :disabled="pagination.page === pagination.total_pages"
            class="px-3 py-1.5 text-sm border border-slate-300 rounded-lg hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed">
            末页
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { getTenantTokenDetails } from '@/api/saasTenant'
import { formatTokensToMillionsThreeDecimals, formatMessagePreview } from '@/utils/formatTokens'

const toast = useToast()

const loading = ref(true)
const selectedMonth = ref(getDefaultMonth())
const summary = ref<any>(null)
const data = ref<any[]>([])
const pagination = ref<any>(null)

function getDefaultMonth(): string {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  return `${year}-${month}`
}

function formatDateTime(datetime: string): string {
  if (!datetime) return ''
  const date = new Date(datetime)
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function loadData(page: number = 1) {
  loading.value = true
  try {
    const response = await getTenantTokenDetails(selectedMonth.value, page, 100)
    if (response.success) {
      summary.value = response.summary
      data.value = response.data
      pagination.value = response.pagination
    } else {
      toast.error(response.message || '加载数据失败')
      summary.value = null
      data.value = []
      pagination.value = null
    }
  } catch (error: any) {
    console.error('加载租户Token消耗明细失败:', error)
    toast.error(error.message || '加载数据失败')
    summary.value = null
    data.value = []
    pagination.value = null
  } finally {
    loading.value = false
  }
}

function getRowNumber(index: number): number {
  if (!pagination.value) return index + 1
  const pageSize = 100 // 与 loadData 中调用 API 的 pageSize 一致
  return (pagination.value.page - 1) * pageSize + index + 1
}

onMounted(() => loadData(1))
</script>
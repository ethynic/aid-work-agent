<template>
  <div class="h-full flex flex-col bg-slate-50">
    <!-- 顶部操作栏 -->
    <div class="flex items-center justify-between px-6 py-4 bg-white border-b border-slate-200">
      <h1 class="text-xl font-bold text-slate-800">错误日志</h1>
      <div class="flex items-center gap-3">
        <!-- 状态筛选 -->
        <select v-model="filterStatus" @change="loadData"
          class="px-3 py-1.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500">
          <option value="">全部状态</option>
          <option value="unprocessed">未处理</option>
          <option value="processed">已处理</option>
          <option value="ignored">已忽略</option>
        </select>

        <!-- 清理旧日志按钮 -->
        <button @click="showCleanupConfirm = true"
          class="px-3 py-1.5 bg-red-500 text-white rounded-lg text-sm hover:bg-red-600 transition-colors">
          清理旧日志（30天）
        </button>

        <!-- 刷新按钮 -->
        <button @click="loadData" :disabled="loading"
          class="px-3 py-1.5 bg-cyan-500 text-white rounded-lg text-sm hover:bg-cyan-600 transition-colors disabled:opacity-50">
          刷新
        </button>
      </div>
    </div>

    <!-- 数据区域 -->
    <div class="flex-1 overflow-auto p-6">
      <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

      <template v-else>
        <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div v-if="logs.length > 0">
            <table class="w-full">
              <thead class="bg-slate-50 sticky top-0">
                <tr>
                  <th class="px-4 py-2 text-left text-xs font-medium text-slate-500 w-36">时间</th>
                  <th class="px-4 py-2 text-left text-xs font-medium text-slate-500 w-36">模块</th>
                  <th class="px-4 py-2 text-left text-xs font-medium text-slate-500 w-28">类型</th>
                  <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">消息</th>
                  <th class="px-4 py-2 text-left text-xs font-medium text-slate-500 w-20">状态</th>
                  <th class="px-4 py-2 text-left text-xs font-medium text-slate-500 w-44">操作</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr v-for="log in logs" :key="log.id" class="hover:bg-slate-50">
                  <td class="px-4 py-2 text-xs text-slate-500">{{ formatDateTime(log.timestamp) }}</td>
                  <td class="px-4 py-2 text-xs text-slate-600 font-mono">{{ log.module || '-' }}</td>
                  <td class="px-4 py-2 text-xs text-slate-600">{{ log.error_type || '-' }}</td>
                  <td class="px-4 py-2 text-sm text-slate-800 max-w-md">
                    <div class="truncate">{{ log.message }}</div>
                  </td>
                  <td class="px-4 py-2">
                    <span :class="getStatusClass(log.status)" class="px-2 py-0.5 rounded text-xs font-medium">
                      {{ getStatusLabel(log.status) }}
                    </span>
                  </td>
                  <td class="px-4 py-2">
                    <div class="flex items-center gap-2">
                      <button @click="showDetail(log)"
                        class="px-2 py-1 text-xs bg-cyan-100 text-cyan-700 rounded hover:bg-cyan-200 transition-colors">
                        详情
                      </button>
                      <template v-if="log.status === 'unprocessed'">
                        <button @click="markAsProcessed(log.id)"
                          class="px-2 py-1 text-xs bg-green-100 text-green-700 rounded hover:bg-green-200 transition-colors">
                          处理
                        </button>
                        <button @click="markAsIgnored(log.id)"
                          class="px-2 py-1 text-xs bg-slate-200 text-slate-600 rounded hover:bg-slate-300 transition-colors">
                          忽略
                        </button>
                      </template>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="text-center py-12 text-slate-400">暂无错误日志</div>

          <!-- 分页 -->
          <div v-if="totalPages > 1" class="px-4 py-3 border-t border-slate-200 flex items-center justify-between">
            <span class="text-sm text-slate-500">共 {{ total }} 条记录</span>
            <div class="flex items-center gap-2">
              <button @click="prevPage" :disabled="page <= 1"
                class="px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed">
                上一页
              </button>
              <span class="text-sm text-slate-600">{{ page }} / {{ totalPages }}</span>
              <button @click="nextPage" :disabled="page >= totalPages"
                class="px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed">
                下一页
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- 详情模态框 -->
    <div v-if="showModal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="showModal = false">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[80vh] overflow-hidden flex flex-col m-4">
        <div class="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h3 class="text-lg font-semibold text-slate-800">错误详情 #{{ selectedLog?.id }}</h3>
          <button @click="showModal = false" class="text-slate-400 hover:text-slate-600">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="flex-1 overflow-auto p-6">
          <div v-if="selectedLog" class="space-y-4">
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-xs font-medium text-slate-500 mb-1">时间</label>
                <div class="text-sm text-slate-800">{{ formatDateTime(selectedLog.timestamp) }}</div>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-500 mb-1">模块</label>
                <div class="text-sm text-slate-800 font-mono">{{ selectedLog.module || '-' }}</div>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-500 mb-1">错误类型</label>
                <div class="text-sm text-slate-800">{{ selectedLog.error_type || '-' }}</div>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-500 mb-1">状态</label>
                <span :class="getStatusClass(selectedLog.status)" class="inline-block px-2 py-0.5 rounded text-xs font-medium">
                  {{ getStatusLabel(selectedLog.status) }}
                </span>
              </div>
            </div>

            <div>
              <label class="block text-xs font-medium text-slate-500 mb-1">错误消息</label>
              <div class="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 whitespace-pre-wrap">
                {{ selectedLog.message }}
              </div>
            </div>

            <div v-if="selectedLog.traceback">
              <label class="block text-xs font-medium text-slate-500 mb-1">堆栈信息</label>
              <div class="p-3 bg-slate-900 border border-slate-700 rounded-lg text-xs text-green-400 overflow-auto max-h-64 whitespace-pre font-mono">
                {{ selectedLog.traceback }}
              </div>
            </div>

            <div v-if="selectedLog.processed_by" class="grid grid-cols-2 gap-4 pt-2 border-t border-slate-200">
              <div>
                <label class="block text-xs font-medium text-slate-500 mb-1">处理人</label>
                <div class="text-sm text-slate-800">{{ selectedLog.processed_by }}</div>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-500 mb-1">处理时间</label>
                <div class="text-sm text-slate-800">{{ formatDateTime(selectedLog.processed_at) }}</div>
              </div>
            </div>
          </div>
        </div>

        <div class="px-6 py-4 border-t border-slate-200 flex justify-end gap-3">
          <button @click="copyToClipboard"
            class="px-4 py-2 bg-cyan-500 text-white rounded-lg text-sm hover:bg-cyan-600 transition-colors">
            复制全部信息
          </button>
          <button @click="showModal = false"
            class="px-4 py-2 bg-slate-200 text-slate-700 rounded-lg text-sm hover:bg-slate-300 transition-colors">
            关闭
          </button>
        </div>
      </div>
    </div>

    <!-- 清理确认对话框 -->
    <div v-if="showCleanupConfirm" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-md p-6 m-4">
        <h3 class="text-lg font-semibold text-slate-800 mb-4">确认清理</h3>
        <p class="text-sm text-slate-600 mb-6">
          此操作将删除 30 天前的所有错误日志记录（包括数据库记录和日志文件）。<br><br>
          删除后无法恢复，确定继续吗？
        </p>
        <div class="flex justify-end gap-3">
          <button @click="showCleanupConfirm = false"
            class="px-4 py-2 bg-slate-200 text-slate-700 rounded-lg text-sm hover:bg-slate-300 transition-colors">
            取消
          </button>
          <button @click="doCleanup" :disabled="cleanupLoading"
            class="px-4 py-2 bg-red-500 text-white rounded-lg text-sm hover:bg-red-600 transition-colors disabled:opacity-50">
            {{ cleanupLoading ? '清理中...' : '确认清理' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import {
  getErrorLogs,
  updateErrorLogStatus,
  cleanupOldErrorLogs,
  ErrorLogItem
} from '@/api/error-logs'

const toast = useToast()

const loading = ref(false)
const cleanupLoading = ref(false)
const filterStatus = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const totalPages = ref(0)
const logs = ref<ErrorLogItem[]>([])

const showModal = ref(false)
const selectedLog = ref<ErrorLogItem | null>(null)

const showCleanupConfirm = ref(false)

async function loadData() {
  loading.value = true
  try {
    const response = await getErrorLogs(filterStatus.value || undefined, page.value, pageSize)
    if (response.success) {
      logs.value = response.data
      total.value = response.total
      totalPages.value = response.total_pages
    } else {
      toast.error(response.message || '加载数据失败')
      logs.value = []
      total.value = 0
      totalPages.value = 0
    }
  } catch (error: any) {
    console.error('加载错误日志失败:', error)
    toast.error(error.message || '加载数据失败')
    logs.value = []
    total.value = 0
    totalPages.value = 0
  } finally {
    loading.value = false
  }
}

function prevPage() {
  if (page.value > 1) {
    page.value--
    loadData()
  }
}

function nextPage() {
  if (page.value < totalPages.value) {
    page.value++
    loadData()
  }
}

function showDetail(log: ErrorLogItem) {
  selectedLog.value = log
  showModal.value = true
}

async function markAsProcessed(logId: number) {
  try {
    await updateErrorLogStatus(logId, 'processed')
    toast.success('状态已更新为「已处理」')
    loadData()
  } catch (error: any) {
    console.error('更新状态失败:', error)
    toast.error(error.message || '更新状态失败')
  }
}

async function markAsIgnored(logId: number) {
  try {
    await updateErrorLogStatus(logId, 'ignored')
    toast.success('状态已更新为「已忽略」')
    loadData()
  } catch (error: any) {
    console.error('更新状态失败:', error)
    toast.error(error.message || '更新状态失败')
  }
}

async function doCleanup() {
  cleanupLoading.value = true
  try {
    const result = await cleanupOldErrorLogs()
    toast.success(result.message || '清理完成')
    showCleanupConfirm.value = false
    loadData()
  } catch (error: any) {
    console.error('清理失败:', error)
    toast.error(error.message || '清理失败')
  } finally {
    cleanupLoading.value = false
  }
}

function copyToClipboard() {
  if (!selectedLog.value) return

  const text = `错误日志 #${selectedLog.value.id}
时间: ${formatDateTime(selectedLog.value.timestamp)}
模块: ${selectedLog.value.module || '-'}
类型: ${selectedLog.value.error_type || '-'}
状态: ${getStatusLabel(selectedLog.value.status)}

消息:
${selectedLog.value.message}

堆栈:
${selectedLog.value.traceback || '(无)'}

${selectedLog.value.processed_by ? `处理人: ${selectedLog.value.processed_by}\n处理时间: ${formatDateTime(selectedLog.value.processed_at)}` : ''}
`

  navigator.clipboard.writeText(text).then(() => {
    toast.success('已复制到剪贴板')
  }).catch(() => {
    toast.error('复制失败')
  })
}

function formatDateTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  try {
    const date = new Date(dateStr)
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  } catch {
    return dateStr
  }
}

function getStatusClass(status: string): string {
  switch (status) {
    case 'unprocessed':
      return 'bg-red-100 text-red-700'
    case 'processed':
      return 'bg-green-100 text-green-700'
    case 'ignored':
      return 'bg-slate-200 text-slate-600'
    default:
      return 'bg-slate-100 text-slate-600'
  }
}

function getStatusLabel(status: string): string {
  switch (status) {
    case 'unprocessed':
      return '未处理'
    case 'processed':
      return '已处理'
    case 'ignored':
      return '已忽略'
    default:
      return status
  }
}

onMounted(() => loadData())
</script>

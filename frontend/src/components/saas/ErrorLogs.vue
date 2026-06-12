<template>
  <div class="page-container">
    <!-- 工具栏 -->
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">错误日志</h2>
        <p class="text-sm text-muted mt-0.5 hidden sm:block">平台错误日志管理与追踪</p>
      </div>
      <div class="page-toolbar-right">
        <BaseSelect v-model="filterStatus" size="sm" class="w-28" @update:model-value="handleSearch('')">
          <option value="">全部状态</option>
          <option value="unprocessed">未处理</option>
          <option value="processed">已处理</option>
          <option value="ignored">已忽略</option>
        </BaseSelect>
        <BaseButton size="sm" intent="danger" @click="showCleanupConfirm = true">清理旧日志（30天）</BaseButton>
        <BaseButton size="sm" intent="secondary" @click="refresh">刷新</BaseButton>
      </div>
    </div>

    <!-- 表格 -->
    <div class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="logs" row-key="id">
        <template #index="{ index }">{{ seqNumber(index) }}</template>
        <template #timestamp="{ row }">{{ formatDateTime(row.timestamp) }}</template>
        <template #module="{ row }">
          <span class="font-mono text-xs">{{ row.module || '-' }}</span>
        </template>
        <template #error_type="{ row }">{{ row.error_type || '-' }}</template>
        <template #message="{ row }">
          <span class="block max-w-[300px] truncate text-muted">{{ row.message }}</span>
        </template>
        <template #status="{ row }">
          <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium" :class="getStatusClass(row.status)">
            {{ getStatusLabel(row.status) }}
          </span>
        </template>
        <template #actions="{ row }">
          <div class="flex items-center justify-center gap-1">
            <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="showDetail(row)">详情</BaseButton>
            <template v-if="row.status === 'unprocessed'">
              <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="markAsProcessed(row.id)">处理</BaseButton>
              <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="markAsIgnored(row.id)">忽略</BaseButton>
            </template>
          </div>
        </template>
        <template #empty>
          <div v-if="loading" class="flex items-center justify-center gap-2">
            <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            加载中...
          </div>
          <span v-else>暂无错误日志</span>
        </template>
      </BaseTable>
    </div>

    <!-- 分页器 -->
    <BasePagination
      :total="total"
      v-model:current-page="currentPage"
      v-model:page-size="pageSize"
      @change="loadData"
    />

    <!-- 详情 Modal -->
    <BaseModal v-model="showModal" title="错误详情 #{{ selectedLog?.id }}" size="xl" mode="view">
      <template v-if="selectedLog">
        <div class="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span class="text-muted">时间：</span>
            <span class="text-default tabular-nums">{{ formatDateTime(selectedLog.timestamp) }}</span>
          </div>
          <div>
            <span class="text-muted">模块：</span>
            <span class="text-default font-mono text-xs">{{ selectedLog.module || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">错误类型：</span>
            <span class="text-default">{{ selectedLog.error_type || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">状态：</span>
            <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium" :class="getStatusClass(selectedLog.status)">
              {{ getStatusLabel(selectedLog.status) }}
            </span>
          </div>
          <div v-if="selectedLog.processed_by">
            <span class="text-muted">处理人：</span>
            <span class="text-default">{{ selectedLog.processed_by }}</span>
          </div>
          <div v-if="selectedLog.processed_at">
            <span class="text-muted">处理时间：</span>
            <span class="text-default tabular-nums">{{ formatDateTime(selectedLog.processed_at) }}</span>
          </div>
        </div>

        <div class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-2 uppercase tracking-wider">错误消息</h3>
          <div class="p-3 bg-danger-50 border border-danger-200 rounded-lg text-sm text-danger-700 whitespace-pre-wrap">
            {{ selectedLog.message }}
          </div>
        </div>

        <div v-if="selectedLog.traceback" class="mt-4">
          <h3 class="text-sm font-medium text-muted mb-2 uppercase tracking-wider">堆栈信息</h3>
          <div class="p-3 bg-gray-900 border border-gray-700 rounded-lg text-xs text-success-400 overflow-auto max-h-64 whitespace-pre font-mono">
            {{ selectedLog.traceback }}
          </div>
        </div>
      </template>
      <template #footer>
        <BaseButton size="sm" intent="primary" @click="copyToClipboard">复制全部信息</BaseButton>
        <BaseButton size="sm" intent="secondary" @click="showModal = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- 清理确认 Modal -->
    <BaseModal v-model="showCleanupConfirm" title="确认清理" size="sm">
      <p class="text-sm text-default">
        此操作将删除 30 天前的所有错误日志记录（包括数据库记录和日志文件）。<br><br>
        删除后无法恢复，确定继续吗？
      </p>
      <template #footer>
        <BaseButton size="sm" intent="danger" :disabled="cleanupLoading" @click="doCleanup">
          {{ cleanupLoading ? '清理中...' : '确认清理' }}
        </BaseButton>
        <BaseButton size="sm" intent="secondary" @click="showCleanupConfirm = false">取消</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { usePageContext } from '@/composables/usePageContext'
import {
  getErrorLogs,
  updateErrorLogStatus,
  cleanupOldErrorLogs,
  type ErrorLogItem
} from '@/api/error-logs'

const toast = useToast()

// ------- Columns -------
const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'timestamp', label: '时间', width: '180px' },
  { key: 'module', label: '模块', width: '100px' },
  { key: 'error_type', label: '类型', width: '80px' },
  { key: 'message', label: '消息' },
  { key: 'status', label: '状态', width: '100px' },
  { key: 'actions', label: '操作', width: '160px', thAlign: 'center' as const },
]

// ------- State -------
const filterStatus = ref('')
const logs = ref<ErrorLogItem[]>([])
const total = ref(0)
const showModal = ref(false)
const selectedLog = ref<ErrorLogItem | null>(null)
const showCleanupConfirm = ref(false)
const cleanupLoading = ref(false)

// ------- Page Context -------
async function loadData() {
  try {
    const response = await getErrorLogs(filterStatus.value || undefined, currentPage.value, pageSize.value)
    if (response.success) {
      logs.value = response.data
      total.value = response.total
    } else {
      toast.error(response.message || '加载数据失败')
      logs.value = []
      total.value = 0
    }
  } catch (error: any) {
    // 前端日志：加载错误日志失败
    console.error('前端日志：加载错误日志失败:', error)
    toast.error(error.message || '加载数据失败')
    logs.value = []
    total.value = 0
  }
}

const { currentPage, pageSize, loading, seqNumber, handleSearch, refresh } =
  usePageContext(async () => { await loadData() })
pageSize.value = 20

// ------- Detail -------
function showDetail(log: Record<string, any>) {
  selectedLog.value = log as unknown as ErrorLogItem
  showModal.value = true
}

// ------- Status Actions -------
async function markAsProcessed(logId: number) {
  try {
    await updateErrorLogStatus(logId, 'processed')
    toast.success('状态已更新为「已处理」')
    refresh()
  } catch (error: any) {
    console.error('前端日志：更新状态失败:', error)
    toast.error(error.message || '更新状态失败')
  }
}

async function markAsIgnored(logId: number) {
  try {
    await updateErrorLogStatus(logId, 'ignored')
    toast.success('状态已更新为「已忽略」')
    refresh()
  } catch (error: any) {
    console.error('前端日志：更新状态失败:', error)
    toast.error(error.message || '更新状态失败')
  }
}

// ------- Cleanup -------
async function doCleanup() {
  cleanupLoading.value = true
  try {
    const result = await cleanupOldErrorLogs()
    toast.success(result.message || '清理完成')
    showCleanupConfirm.value = false
    refresh()
  } catch (error: any) {
    console.error('前端日志：清理失败:', error)
    toast.error(error.message || '清理失败')
  } finally {
    cleanupLoading.value = false
  }
}

// ------- Copy -------
function copyToClipboard() {
  if (!selectedLog.value) return
  const text = `模块: ${selectedLog.value.module || '-'}
消息:
${selectedLog.value.message}
堆栈:
${selectedLog.value.traceback || '-'}
`
  navigator.clipboard.writeText(text).then(() => {
    toast.success('已复制到剪贴板')
  }).catch(() => {
    toast.error('复制失败')
  })
}

// ------- Formatting -------
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
      return 'bg-danger-100 text-danger-700'
    case 'processed':
      return 'bg-success-100 text-success-700'
    case 'ignored':
      return 'bg-surface-hover text-muted'
    default:
      return 'bg-surface-hover text-default'
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

// ------- Init -------
onMounted(() => { refresh() })
</script>

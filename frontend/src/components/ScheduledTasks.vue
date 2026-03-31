<template>
  <div class="scheduled-tasks-container">
    <div class="header">
      <button class="back-btn" @click="goBack">
        <span>&larr;</span> 返回
      </button>
      <h1>&#x1F4CB; 我的定时任务</h1>
      <div class="header-actions">
        <button class="refresh-btn" @click="loadData" :disabled="loading">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div v-if="stats" class="stats-grid">
      <div class="stat-card active">
        <div class="stat-value">{{ stats.active_tasks }}</div>
        <div class="stat-label">活跃任务</div>
      </div>
      <div class="stat-card paused">
        <div class="stat-value">{{ stats.paused_tasks }}</div>
        <div class="stat-label">已暂停</div>
      </div>
      <div class="stat-card runs">
        <div class="stat-value">{{ stats.total_runs }}</div>
        <div class="stat-label">总执行次数</div>
      </div>
      <div class="stat-card success">
        <div class="stat-value">{{ stats.success_rate }}%</div>
        <div class="stat-label">成功率</div>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <p>加载中...</p>
    </div>

    <!-- 错误提示 -->
    <div v-else-if="error" class="error-message">
      <p>&#x274C; {{ error }}</p>
      <p v-if="debug" class="debug-info">{{ debug }}</p>
    </div>

    <!-- 任务列表 -->
    <div v-else-if="tasks.length > 0" class="task-list">
      <div v-for="task in tasks" :key="task.task_id" class="task-card" :class="task.status">
        <div class="task-header">
          <div class="task-name">
            <span class="status-dot" :class="task.status"></span>
            {{ task.name }}
          </div>
          <div class="task-status-badge" :class="task.status">
            {{ statusText(task.status) }}
          </div>
        </div>

        <div class="task-description">{{ task.description || '-' }}</div>

        <div class="task-meta">
          <span class="meta-item">
            &#x1F4C5; {{ formatSchedule(task.schedule_type, task.cron_expression) }}
          </span>
          <span class="meta-item">
            &#x1F504; 执行 {{ task.total_runs }} 次
            <span class="success-text">({{ task.success_count }} 成功</span>
            <span v-if="task.fail_count > 0" class="fail-text">, {{ task.fail_count }} 失败</span><span class="success-text">)</span>
          </span>
          <span v-if="task.last_run_at" class="meta-item">
            &#x1F552; 上次: {{ formatDate(task.last_run_at) }}
          </span>
        </div>

        <div class="task-actions">
          <button v-if="task.status === 'active'" class="action-btn pause" @click="handlePause(task.task_id)">
            &#x23F8; 暂停
          </button>
          <button v-if="task.status === 'paused'" class="action-btn resume" @click="handleResume(task.task_id)">
            &#x25B6; 恢复
          </button>
          <button v-if="task.status !== 'cancelled'" class="action-btn trigger" @click="handleTrigger(task.task_id)">
            &#x25B6;&#xFE0F; 立即执行
          </button>
          <button class="action-btn logs" @click="toggleLogs(task.task_id)">
            &#x1F4CB; 日志
          </button>
          <button v-if="task.status !== 'cancelled'" class="action-btn cancel" @click="handleCancel(task.task_id)">
            &#x1F5D1; 取消
          </button>
        </div>

        <!-- 展开的日志 -->
        <div v-if="expandedTaskId === task.task_id" class="task-logs">
          <div v-if="loadingLogs" class="logs-loading">加载日志...</div>
          <div v-else-if="taskLogs.length > 0" class="log-list">
            <div v-for="log in taskLogs" :key="log.log_id" class="log-item" :class="log.status">
              <span class="log-status">{{ log.status === 'success' ? '&#x2705;' : '&#x274C;' }}</span>
              <span class="log-time">{{ formatDate(log.started_at) }}</span>
              <span class="log-duration">{{ log.duration_ms }}ms</span>
              <span class="log-trigger">{{ log.trigger_type === 'manual' ? '&#x1F5B1;手动' : '&#x23F0;定时' }}</span>
              <span v-if="log.error_message" class="log-error" :title="log.error_message">
                &#x274C; {{ log.error_message.slice(0, 50) }}{{ log.error_message.length > 50 ? '...' : '' }}
              </span>
            </div>
          </div>
          <div v-else class="no-logs">暂无执行日志</div>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="empty-state">
      <p>&#x1F4ED; 暂无定时任务</p>
      <p class="hint">在对话中告诉智能体你想定时执行的任务即可创建</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  listScheduledTasks,
  getTaskStats,
  pauseTask,
  resumeTask,
  cancelTask,
  triggerTask,
  getTaskLogs,
  type ScheduledTask,
  type TaskLog,
  type TaskUserStats
} from '../api/scheduledTask'

const router = useRouter()

const loading = ref(false)
const loadingLogs = ref(false)
const error = ref('')
const debug = ref('')
const tasks = ref<ScheduledTask[]>([])
const stats = ref<TaskUserStats | null>(null)
const expandedTaskId = ref<string | null>(null)
const taskLogs = ref<TaskLog[]>([])

function goBack() {
  router.back()
}

async function loadData() {
  loading.value = true
  error.value = ''
  debug.value = ''

  try {
    const [tasksRes, statsRes] = await Promise.all([
      listScheduledTasks(),
      getTaskStats()
    ])

    if (tasksRes.success && tasksRes.data) {
      tasks.value = tasksRes.data.tasks
    } else {
      error.value = tasksRes.error || '获取任务列表失败'
      debug.value = tasksRes.debug || ''
    }

    if (statsRes.success && statsRes.data) {
      stats.value = statsRes.data
    }
  } catch (e: any) {
    error.value = '加载失败'
    debug.value = e.message || ''
  } finally {
    loading.value = false
  }
}

async function handlePause(taskId: string) {
  try {
    const res = await pauseTask(taskId)
    if (res.success) {
      await loadData()
    } else {
      alert(res.error || '暂停失败')
    }
  } catch (e: any) {
    alert('暂停失败: ' + e.message)
  }
}

async function handleResume(taskId: string) {
  try {
    const res = await resumeTask(taskId)
    if (res.success) {
      await loadData()
    } else {
      alert(res.error || '恢复失败')
    }
  } catch (e: any) {
    alert('恢复失败: ' + e.message)
  }
}

async function handleCancel(taskId: string) {
  if (!confirm('确定要取消此定时任务吗？')) return
  try {
    const res = await cancelTask(taskId)
    if (res.success) {
      await loadData()
    } else {
      alert(res.error || '取消失败')
    }
  } catch (e: any) {
    alert('取消失败: ' + e.message)
  }
}

async function handleTrigger(taskId: string) {
  if (!confirm('确定要立即执行此任务吗？')) return
  try {
    const res = await triggerTask(taskId)
    if (res.success) {
      alert('已触发执行，请稍后查看日志')
    } else {
      alert(res.error || '触发失败')
    }
  } catch (e: any) {
    alert('触发失败: ' + e.message)
  }
}

async function toggleLogs(taskId: string) {
  if (expandedTaskId.value === taskId) {
    expandedTaskId.value = null
    return
  }
  expandedTaskId.value = taskId
  loadingLogs.value = true
  taskLogs.value = []

  try {
    const res = await getTaskLogs(taskId)
    if (res.success && res.data) {
      taskLogs.value = res.data.logs
    }
  } catch (e: any) {
    console.error('前端日志：加载任务日志失败', e)
  } finally {
    loadingLogs.value = false
  }
}

function statusText(status: string): string {
  const map: Record<string, string> = {
    active: '活跃',
    paused: '已暂停',
    completed: '已完成',
    cancelled: '已取消'
  }
  return map[status] || status
}

function formatSchedule(type: string, cron: string): string {
  if (type === 'daily') {
    const parts = cron?.split(' ') || []
    return `每天 ${parts[1] || '0'}:${parts[0] || '0'}`
  } else if (type === 'weekly') {
    const parts = cron?.split(' ') || []
    const days = ['日', '一', '二', '三', '四', '五', '六']
    const day = days[parseInt(parts[4] || '1')] || '一'
    return `每周${day} ${parts[1] || '0'}:${parts[0] || '0'}`
  } else if (type === 'interval') {
    return `间隔执行`
  } else if (type === 'once') {
    return `一次性: ${cron || '-'}`
  }
  return type
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  try {
    const date = new Date(dateStr)
    if (isNaN(date.getTime())) return dateStr
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch {
    return dateStr
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.scheduled-tasks-container {
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

.back-btn:hover { background: #e5e7eb; }

.refresh-btn {
  padding: 8px 16px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}

.refresh-btn:hover:not(:disabled) { background: #2563eb; }
.refresh-btn:disabled { background: #9ca3af; cursor: not-allowed; }

/* 统计卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  color: white;
  padding: 20px;
  border-radius: 12px;
  text-align: center;
}

.stat-card.active { background: linear-gradient(135deg, #22c55e, #16a34a); }
.stat-card.paused { background: linear-gradient(135deg, #f59e0b, #d97706); }
.stat-card.runs { background: linear-gradient(135deg, #3b82f6, #2563eb); }
.stat-card.success { background: linear-gradient(135deg, #8b5cf6, #7c3aed); }

.stat-value { font-size: 32px; font-weight: bold; }
.stat-label { font-size: 14px; opacity: 0.9; margin-top: 4px; }

/* 加载/错误 */
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

@keyframes spin { to { transform: rotate(360deg); } }

.error-message {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #dc2626;
  padding: 16px;
  border-radius: 8px;
}

.debug-info { font-size: 12px; color: #991b1b; margin-top: 8px; }

/* 任务卡片 */
.task-list { display: flex; flex-direction: column; gap: 16px; }

.task-card {
  background: white;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  padding: 20px;
  border-left: 4px solid #e5e7eb;
  transition: box-shadow 0.2s;
}

.task-card:hover { box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); }
.task-card.active { border-left-color: #22c55e; }
.task-card.paused { border-left-color: #f59e0b; }
.task-card.cancelled { border-left-color: #9ca3af; opacity: 0.7; }

.task-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.task-name {
  font-size: 18px;
  font-weight: 600;
  color: #111827;
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}

.status-dot.active { background: #22c55e; }
.status-dot.paused { background: #f59e0b; }
.status-dot.cancelled { background: #9ca3af; }

.task-status-badge {
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.task-status-badge.active { background: #dcfce7; color: #166534; }
.task-status-badge.paused { background: #fef3c7; color: #92400e; }
.task-status-badge.cancelled { background: #f3f4f6; color: #6b7280; }

.task-description {
  font-size: 14px;
  color: #6b7280;
  margin-bottom: 12px;
}

.task-meta {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.meta-item {
  font-size: 13px;
  color: #9ca3af;
}

.success-text { color: #16a34a; }
.fail-text { color: #dc2626; }

.task-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.action-btn {
  padding: 6px 14px;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.2s;
}

.action-btn.pause { background: #fef3c7; color: #92400e; }
.action-btn.pause:hover { background: #fde68a; }
.action-btn.resume { background: #dcfce7; color: #166534; }
.action-btn.resume:hover { background: #bbf7d0; }
.action-btn.trigger { background: #dbeafe; color: #1e40af; }
.action-btn.trigger:hover { background: #bfdbfe; }
.action-btn.logs { background: #f3f4f6; color: #374151; }
.action-btn.logs:hover { background: #e5e7eb; }
.action-btn.cancel { background: #fee2e2; color: #991b1b; }
.action-btn.cancel:hover { background: #fecaca; }

/* 日志区域 */
.task-logs {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #e5e7eb;
}

.logs-loading { text-align: center; color: #9ca3af; padding: 16px; }

.log-list {
  max-height: 300px;
  overflow-y: auto;
}

.log-item {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  color: #6b7280;
}

.log-item:nth-child(odd) { background: #f9fafb; }
.log-item.success { color: #166534; }
.log-item.failed { color: #991b1b; }

.log-status { flex-shrink: 0; }
.log-time { flex-shrink: 0; font-family: monospace; }
.log-duration { flex-shrink: 0; }
.log-trigger { flex-shrink: 0; }
.log-error { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #dc2626; }

.no-logs { text-align: center; color: #9ca3af; padding: 16px; }

/* 空状态 */
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: #6b7280;
}

.empty-state p { margin: 0; }
.hint { font-size: 14px; margin-top: 8px; color: #9ca3af; }
</style>

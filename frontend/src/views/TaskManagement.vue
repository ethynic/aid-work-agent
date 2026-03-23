<template>
  <div class="task-management">
    <el-row :gutter="20" style="margin-bottom: 20px;">
      <el-col :span="6">
        <el-statistic title="待执行任务" :value="stats.pending" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="运行中" :value="stats.running" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="已完成" :value="stats.completed" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="失败" :value="stats.failed" />
      </el-col>
    </el-row>
    
    <el-card>
      <template #header>
        <div class="card-header">
          <span>任务列表</span>
          <div class="actions">
            <el-select v-model="filterStatus" placeholder="状态筛选" clearable style="width: 120px;">
              <el-option label="全部" value="" />
              <el-option label="等待中" value="pending" />
              <el-option label="运行中" value="running" />
              <el-option label="已完成" value="completed" />
              <el-option label="失败" value="failed" />
            </el-select>
            <el-button type="primary" @click="showCreateDialog = true">
              <el-icon><Plus /></el-icon>
              创建任务
            </el-button>
          </div>
        </div>
      </template>
      
      <el-table :data="tasks" stripe v-loading="loading">
        <el-table-column prop="id" label="任务ID" width="100" />
        <el-table-column prop="name" label="任务名称" />
        <el-table-column prop="type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small">{{ getTypeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="{ row }">
            <el-tag :type="getPriorityType(row.priority)" size="small">
              {{ getPriorityLabel(row.priority) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="viewTask(row)">查看</el-button>
            <el-button 
              v-if="row.status === 'pending'" 
              type="warning" 
              link 
              size="small" 
              @click="cancelTask(row)"
            >
              取消
            </el-button>
            <el-button 
              v-if="row.status === 'failed'" 
              type="primary" 
              link 
              size="small" 
              @click="retryTask(row)"
            >
              重试
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      
      <div class="pagination">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
        />
      </div>
    </el-card>
    
    <el-dialog v-model="showCreateDialog" title="创建任务" width="600px">
      <el-form label-width="100px">
        <el-form-item label="任务名称">
          <el-input v-model="taskForm.name" placeholder="输入任务名称" />
        </el-form-item>
        <el-form-item label="任务类型">
          <el-select v-model="taskForm.type" style="width: 100%;">
            <el-option label="LLM推理" value="llm_inference" />
            <el-option label="MCP工具" value="mcp_tool" />
            <el-option label="数据库查询" value="database_query" />
            <el-option label="数据处理" value="data_processing" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="taskForm.priority" style="width: 100%;">
            <el-option label="低" value="low" />
            <el-option label="中" value="medium" />
            <el-option label="高" value="high" />
            <el-option label="紧急" value="urgent" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务参数">
          <el-input
            v-model="taskParamsJson"
            type="textarea"
            :rows="4"
            placeholder="JSON格式参数"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createTask">创建</el-button>
      </template>
    </el-dialog>
    
    <el-dialog v-model="showDetailDialog" title="任务详情" width="700px">
      <el-descriptions :column="2" border v-if="currentTask">
        <el-descriptions-item label="任务ID">{{ currentTask.id }}</el-descriptions-item>
        <el-descriptions-item label="任务名称">{{ currentTask.name }}</el-descriptions-item>
        <el-descriptions-item label="类型">{{ getTypeLabel(currentTask.type) }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="getStatusType(currentTask.status)">{{ getStatusLabel(currentTask.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatTime(currentTask.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ formatTime(currentTask.started_at) }}</el-descriptions-item>
        <el-descriptions-item label="完成时间">{{ formatTime(currentTask.completed_at) }}</el-descriptions-item>
        <el-descriptions-item label="执行时间">{{ currentTask.execution_time }}s</el-descriptions-item>
      </el-descriptions>
      
      <el-tabs style="margin-top: 20px;">
        <el-tab-pane label="参数">
          <pre>{{ JSON.stringify(currentTask?.parameters, null, 2) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="结果" v-if="currentTask?.result">
          <pre>{{ JSON.stringify(currentTask.result, null, 2) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="错误" v-if="currentTask?.error">
          <el-alert :title="currentTask.error" type="error" />
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { taskAPI } from '@/services/api'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'

const loading = ref(false)
const tasks = ref([])
const stats = ref({ pending: 0, running: 0, completed: 0, failed: 0 })
const filterStatus = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)
const showCreateDialog = ref(false)
const showDetailDialog = ref(false)
const currentTask = ref(null)

const taskForm = ref({
  name: '',
  type: 'llm_inference',
  priority: 'medium',
  parameters: {}
})

const taskParamsJson = ref('{}')

const getTypeLabel = (type) => {
  const labels = {
    llm_inference: 'LLM推理',
    mcp_tool: 'MCP工具',
    database_query: '数据库',
    data_processing: '数据处理'
  }
  return labels[type] || type
}

const getPriorityLabel = (priority) => {
  const labels = { low: '低', medium: '中', high: '高', urgent: '紧急' }
  return labels[priority] || priority
}

const getPriorityType = (priority) => {
  const types = { low: 'info', medium: '', high: 'warning', urgent: 'danger' }
  return types[priority] || 'info'
}

const getStatusLabel = (status) => {
  const labels = {
    pending: '等待中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
  }
  return labels[status] || status
}

const getStatusType = (status) => {
  const types = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info'
  }
  return types[status] || 'info'
}

const formatTime = (time) => {
  return time ? dayjs(time).format('YYYY-MM-DD HH:mm:ss') : '-'
}

const loadTasks = async () => {
  loading.value = true
  try {
    const response = await taskAPI.list_tasks(filterStatus.value || null, pageSize.value)
    tasks.value = response.tasks || []
    total.value = tasks.value.length
  } catch (error) {
    ElMessage.error(`加载任务列表失败: ${error.message}`)
  } finally {
    loading.value = false
  }
}

const loadStats = async () => {
  try {
    const response = await taskAPI.get_statistics()
    stats.value = response.statistics || stats.value
  } catch (error) {
    console.error('加载统计数据失败:', error)
  }
}

const viewTask = async (task) => {
  try {
    const response = await taskAPI.get_task(task.id)
    currentTask.value = response.task
    showDetailDialog.value = true
  } catch (error) {
    ElMessage.error(`加载任务详情失败: ${error.message}`)
  }
}

const cancelTask = async (task) => {
  try {
    await taskAPI.cancel_task(task.id)
    ElMessage.success('任务已取消')
    loadTasks()
    loadStats()
  } catch (error) {
    ElMessage.error(`取消任务失败: ${error.message}`)
  }
}

const retryTask = async (task) => {
  try {
    const response = await taskAPI.retry_task(task.id)
    ElMessage.success(`新任务已创建: ${response.task_id}`)
    loadTasks()
  } catch (error) {
    ElMessage.error(`重试任务失败: ${error.message}`)
  }
}

const createTask = async () => {
  try {
    taskForm.value.parameters = JSON.parse(taskParamsJson.value || '{}')
    await taskAPI.create_task(taskForm.value)
    ElMessage.success('任务创建成功')
    showCreateDialog.value = false
    loadTasks()
    loadStats()
  } catch (error) {
    ElMessage.error(`创建任务失败: ${error.message}`)
  }
}

watch(filterStatus, loadTasks)
onMounted(() => {
  loadTasks()
  loadStats()
})
</script>

<style lang="scss" scoped>
.task-management {
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    
    .actions {
      display: flex;
      gap: 10px;
    }
  }
  
  .pagination {
    margin-top: 20px;
    display: flex;
    justify-content: flex-end;
  }
  
  pre {
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 15px;
    border-radius: 6px;
    overflow-x: auto;
  }
}
</style>

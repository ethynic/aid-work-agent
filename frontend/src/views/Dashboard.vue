<template>
  <div class="dashboard">
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card class="stat-card">
          <template #header>
            <div class="card-header">
              <span>运行任务</span>
              <el-icon><Loading /></el-icon>
            </div>
          </template>
          <div class="stat-value">{{ stats.runningTasks }}</div>
          <div class="stat-trend up">
            <el-icon><Top /></el-icon> 较昨日 +12%
          </div>
        </el-card>
      </el-col>
      
      <el-col :span="6">
        <el-card class="stat-card">
          <template #header>
            <div class="card-header">
              <span>完成任务</span>
              <el-icon><SuccessFilled /></el-icon>
            </div>
          </template>
          <div class="stat-value">{{ stats.completedTasks }}</div>
          <div class="stat-trend up">
            <el-icon><Top /></el-icon> 较昨日 +8%
          </div>
        </el-card>
      </el-col>
      
      <el-col :span="6">
        <el-card class="stat-card">
          <template #header>
            <div class="card-header">
              <span>活跃模型</span>
              <el-icon><Grid /></el-icon>
            </div>
          </template>
          <div class="stat-value">{{ stats.activeModels }}</div>
          <div class="stat-trend">已配置 {{ stats.totalModels }} 个模型</div>
        </el-card>
      </el-col>
      
      <el-col :span="6">
        <el-card class="stat-card">
          <template #header>
            <div class="card-header">
              <span>数据库连接</span>
              <el-icon><Connection /></el-icon>
            </div>
          </template>
          <div class="stat-value">{{ stats.activeConnections }}</div>
          <div class="stat-trend">{{ stats.totalTables }} 个数据表</div>
        </el-card>
      </el-col>
    </el-row>
    
    <el-row :gutter="20" style="margin-top: 20px;">
      <el-col :span="16">
        <el-card class="chart-card">
          <template #header>
            <div class="card-header">
              <span>任务执行趋势</span>
              <el-radio-group v-model="chartPeriod" size="small">
                <el-radio-button value="week">周</el-radio-button>
                <el-radio-button value="month">月</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <div ref="trendChartRef" class="chart-container"></div>
        </el-card>
      </el-col>
      
      <el-col :span="8">
        <el-card class="chart-card">
          <template #header>
            <span>模型使用分布</span>
          </template>
          <div ref="modelChartRef" class="chart-container"></div>
        </el-card>
      </el-col>
    </el-row>
    
    <el-row :gutter="20" style="margin-top: 20px;">
      <el-col :span="12">
        <el-card class="list-card">
          <template #header>
            <div class="card-header">
              <span>最近任务</span>
              <el-button text @click="$router.push('/tasks')">查看全部</el-button>
            </div>
          </template>
          <el-table :data="recentTasks" stripe v-memo="[recentTasks]">
            <el-table-column prop="name" label="任务名称" />
            <el-table-column prop="type" label="类型" width="100">
              <template #default="{ row }">
                <el-tag size="small">{{ getTypeLabel(row.type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="getStatusType(row.status)" size="small">
                  {{ getStatusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="createdAt" label="创建时间" width="160">
              <template #default="{ row }">
                {{ formatTime(row.createdAt) }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      
      <el-col :span="12">
        <el-card class="list-card">
          <template #header>
            <div class="card-header">
              <span>快捷操作</span>
            </div>
          </template>
          <div class="quick-actions">
            <el-button type="primary" @click="$router.push('/chat')">
              <el-icon><ChatDotRound /></el-icon>
              发起对话
            </el-button>
            <el-button type="success" @click="$router.push('/tools/image')">
              <el-icon><Picture /></el-icon>
              生成图像
            </el-button>
            <el-button type="warning" @click="$router.push('/database/query')">
              <el-icon><Search /></el-icon>
              SQL查询
            </el-button>
            <el-button type="info" @click="$router.push('/tasks')">
              <el-icon><List /></el-icon>
              查看任务
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { Loading, SuccessFilled, Grid, Connection, Top, ChatDotRound, Picture, Search, List } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { taskAPI, llmAPI, databaseAPI } from '@/services/api'
import dayjs from 'dayjs'

const chartPeriod = ref('week')
const trendChartRef = ref(null)
const modelChartRef = ref(null)
let trendChart = null
let modelChart = null

const stats = ref({
  runningTasks: 0,
  completedTasks: 0,
  activeModels: 0,
  totalModels: 0,
  activeConnections: 0,
  totalTables: 0
})

const recentTasks = ref([])

// 防抖函数
const debounce = (func, wait) => {
  let timeout
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout)
      func(...args)
    }
    clearTimeout(timeout)
    timeout = setTimeout(later, wait)
  }
}

const getTypeLabel = (type) => {
  const labels = {
    llm_inference: 'LLM推理',
    mcp_tool: 'MCP工具',
    database_query: '数据库',
    data_processing: '数据处理'
  }
  return labels[type] || type
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

const formatTime = (time) => {
  return dayjs(time).format('YYYY-MM-DD HH:mm:ss')
}

const loadStats = async () => {
  try {
    // 使用 Promise.all 并行请求，但添加错误处理
    const [taskStats, modelConfigs, dbStats] = await Promise.all([
      taskAPI.get_statistics().catch(() => ({ running: 0, completed: 0 })),
      llmAPI.get_config().catch(() => ({ configs: [] })),
      databaseAPI.list_connections().catch(() => ({ connections: [] }))
    ])
    
    stats.value = {
      runningTasks: taskStats.running || 0,
      completedTasks: taskStats.completed || 0,
      activeModels: modelConfigs.configs?.filter(c => c.enabled).length || 0,
      totalModels: modelConfigs.configs?.length || 0,
      activeConnections: dbStats.connections?.filter(c => c.enabled).length || 0,
      totalTables: 0
    }
  } catch (error) {
    console.error('加载统计数据失败:', error)
  }
}

const loadRecentTasks = async () => {
  try {
    const response = await taskAPI.list_tasks(null, 5).catch(() => ({ tasks: [] }))
    recentTasks.value = response.tasks || []
  } catch (error) {
    console.error('加载最近任务失败:', error)
  }
}

const initCharts = () => {
  // 确保 DOM 元素已渲染
  if (!trendChartRef.value || !modelChartRef.value) return
  
  trendChart = echarts.init(trendChartRef.value)
  modelChart = echarts.init(modelChartRef.value)
  
  const trendOption = {
    tooltip: { trigger: 'axis' },
    legend: { data: ['成功', '失败', '总计'] },
    xAxis: {
      type: 'category',
      data: ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    },
    yAxis: { type: 'value' },
    series: [
      { name: '成功', type: 'line', data: [65, 72, 86, 82, 90, 95, 100], smooth: true },
      { name: '失败', type: 'line', data: [5, 3, 8, 4, 6, 2, 4], smooth: true },
      { name: '总计', type: 'bar', data: [70, 75, 94, 86, 96, 97, 104] }
    ]
  }
  
  const modelOption = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      label: { show: false },
      data: [
        { value: 40, name: 'DeepSeek' },
        { value: 30, name: '豆包' },
        { value: 20, name: '千问' },
        { value: 10, name: 'GPT-4' }
      ]
    }]
  }
  
  trendChart.setOption(trendOption)
  modelChart.setOption(modelOption)
}

// 使用防抖优化 resize 事件
const handleResize = debounce(() => {
  trendChart?.resize()
  modelChart?.resize()
}, 200)

onMounted(() => {
  // 延迟加载，让页面先渲染
  nextTick(() => {
    loadStats()
    loadRecentTasks()
    // 再延迟初始化图表，避免阻塞页面渲染
    setTimeout(initCharts, 500)
  })
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (trendChart) {
    trendChart.dispose()
    trendChart = null
  }
  if (modelChart) {
    modelChart.dispose()
    modelChart = null
  }
})
</script>

<style lang="scss" scoped>
.dashboard {
  .stat-card {
    :deep(.el-card__header) {
      padding: 15px 20px;
    }
    
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    
    .stat-value {
      font-size: 36px;
      font-weight: bold;
      color: #303133;
    }
    
    .stat-trend {
      margin-top: 8px;
      font-size: 13px;
      color: #909399;
      
      &.up {
        color: #67c23a;
      }
    }
  }
  
  .chart-card, .list-card {
    height: 400px;
    
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    
    .chart-container {
      height: 320px;
    }
  }
  
  .quick-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 15px;
    padding: 20px 0;
    
    .el-button {
      width: 140px;
      height: 80px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }
  }
}
</style>

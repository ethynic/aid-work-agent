<template>
  <div class="mcp-tools">
    <el-row :gutter="20">
      <el-col :span="16">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>可用工具</span>
              <el-select v-model="selectedCategory" placeholder="全部类别" clearable style="width: 160px;">
                <el-option label="图像生成" value="image_generation" />
                <el-option label="视频生成" value="video_generation" />
                <el-option label="文档处理" value="document" />
                <el-option label="数据分析" value="analysis" />
                <el-option label="数据库" value="database" />
              </el-select>
            </div>
          </template>
          
          <el-table :data="filteredTools" stripe>
            <el-table-column prop="name" label="工具名称" width="150" />
            <el-table-column prop="description" label="描述" />
            <el-table-column prop="category" label="类别" width="120">
              <template #default="{ row }">
                <el-tag size="small">{{ getCategoryLabel(row.category) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="version" label="版本" width="80" />
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button type="primary" link size="small" @click="viewToolDetail(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      
      <el-col :span="8">
        <el-card>
          <template #header>
            <span>MCP服务器</span>
          </template>
          
          <el-table :data="servers" stripe>
            <el-table-column prop="name" label="服务器" />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="row.status === 'running' ? 'success' : 'info'" size="small">
                  {{ row.status === 'running' ? '运行中' : '已停止' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
          
          <el-button type="primary" style="width: 100%; margin-top: 15px;" @click="showAddServer = true">
            添加服务器
          </el-button>
        </el-card>
      </el-col>
    </el-row>
    
    <el-dialog v-model="showDetailDialog" :title="currentTool?.name" width="600px">
      <template v-if="currentTool">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="名称">{{ currentTool.name }}</el-descriptions-item>
          <el-descriptions-item label="版本">{{ currentTool.version }}</el-descriptions-item>
          <el-descriptions-item label="类别" :span="2">
            <el-tag>{{ getCategoryLabel(currentTool.category) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ currentTool.description }}</el-descriptions-item>
        </el-descriptions>
        
        <el-divider>参数定义</el-divider>
        
        <el-table :data="currentTool.parameters" size="small">
          <el-table-column prop="name" label="参数名" />
          <el-table-column prop="type" label="类型" width="80" />
          <el-table-column prop="description" label="描述" />
          <el-table-column prop="required" label="必填" width="60">
            <template #default="{ row }">
              <el-icon v-if="row.required" color="#f56c6c"><StarFilled /></el-icon>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>
    
    <el-dialog v-model="showAddServer" title="添加MCP服务器" width="500px">
      <el-form label-width="100px">
        <el-form-item label="服务器名称">
          <el-input v-model="serverForm.name" placeholder="输入服务器名称" />
        </el-form-item>
        <el-form-item label="服务器类型">
          <el-select v-model="serverForm.type" style="width: 100%;">
            <el-option label="HTTP" value="http" />
            <el-option label="WebSocket" value="websocket" />
          </el-select>
        </el-form-item>
        <el-form-item label="服务器地址">
          <el-input v-model="serverForm.url" placeholder="http://localhost:3000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddServer = false">取消</el-button>
        <el-button type="primary" @click="addServer">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { StarFilled } from '@element-plus/icons-vue'
import { mcpAPI } from '@/services/api'
import { ElMessage } from 'element-plus'

const tools = ref([])
const servers = ref([])
const selectedCategory = ref('')
const showDetailDialog = ref(false)
const showAddServer = ref(false)
const currentTool = ref(null)

const serverForm = ref({
  name: '',
  type: 'http',
  url: ''
})

const filteredTools = computed(() => {
  if (!selectedCategory.value) return tools.value
  return tools.value.filter(t => t.category === selectedCategory.value)
})

const getCategoryLabel = (category) => {
  const labels = {
    image_generation: '图像生成',
    video_generation: '视频生成',
    document: '文档处理',
    analysis: '数据分析',
    database: '数据库'
  }
  return labels[category] || category
}

const loadTools = async () => {
  try {
    const response = await mcpAPI.list_tools()
    tools.value = response.tools || []
  } catch (error) {
    console.error('加载工具列表失败:', error)
  }
}

const loadServers = async () => {
  try {
    const response = await mcpAPI.list_servers()
    servers.value = response.servers || []
  } catch (error) {
    console.error('加载服务器列表失败:', error)
  }
}

const viewToolDetail = async (tool) => {
  try {
    const response = await mcpAPI.get_tool_info(tool.name)
    currentTool.value = response.tool
    showDetailDialog.value = true
  } catch (error) {
    ElMessage.error(`加载工具详情失败: ${error.message}`)
  }
}

const addServer = async () => {
  try {
    await mcpAPI.configure_server(serverForm.value)
    ElMessage.success('服务器添加成功')
    showAddServer.value = false
    loadServers()
  } catch (error) {
    ElMessage.error(`添加服务器失败: ${error.message}`)
  }
}

onMounted(() => {
  loadTools()
  loadServers()
})
</script>

<style lang="scss" scoped>
.mcp-tools {
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
}
</style>

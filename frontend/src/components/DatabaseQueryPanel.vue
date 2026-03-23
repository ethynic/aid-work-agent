<template>
  <div class="database-query-panel">
    <el-form label-width="80px" size="default">
      <el-form-item label="连接">
        <el-select v-model="selectedConnection" placeholder="选择数据库连接" style="width: 100%;">
          <el-option
            v-for="conn in connections"
            :key="conn.name"
            :label="`${conn.name} (${conn.db_type})`"
            :value="conn.name"
          />
        </el-select>
      </el-form-item>
      
      <el-form-item label="SQL">
        <el-input
          v-model="sqlQuery"
          type="textarea"
          :rows="8"
          placeholder="SELECT * FROM table WHERE..."
        />
      </el-form-item>
      
      <el-form-item>
        <el-button type="primary" @click="executeQuery" :loading="executing">
          <el-icon><VideoPlay /></el-icon>
          执行查询
        </el-button>
        <el-button @click="generateSQL">
          <el-icon><MagicStick /></el-icon>
          智能生成
        </el-button>
        <el-button @click="insertCurrentQuery">
          <el-icon><DocumentCopy /></el-icon>
          插入对话
        </el-button>
      </el-form-item>
    </el-form>
    
    <el-tabs v-model="activeTab" v-if="queryResult">
      <el-tab-pane label="结果" name="result">
        <el-table :data="queryResult.rows" stripe max-height="400">
          <el-table-column
            v-for="col in queryResult.columns"
            :key="col"
            :prop="col"
            :label="col"
            min-width="120"
          />
        </el-table>
        <div class="query-info">
          <span>返回 {{ queryResult.row_count }} 行</span>
          <span>执行时间: {{ queryResult.execution_time.toFixed(3) }}s</span>
        </div>
      </el-tab-pane>
      
      <el-tab-pane label="JSON" name="json">
        <pre class="json-viewer">{{ JSON.stringify(queryResult, null, 2) }}</pre>
      </el-tab-pane>
    </el-tabs>
    
    <el-dialog v-model="showGenerateDialog" title="智能生成SQL" width="600px">
      <el-form label-width="80px">
        <el-form-item label="描述">
          <el-input
            v-model="sqlDescription"
            type="textarea"
            :rows="3"
            placeholder="描述您想要查询的数据，例如：查询最近7天的订单按状态分组统计"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showGenerateDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmGenerateSQL">生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { VideoPlay, MagicStick, DocumentCopy } from '@element-plus/icons-vue'
import { databaseAPI } from '@/services/api'
import { ElMessage } from 'element-plus'

const emit = defineEmits(['insert-query'])

const connections = ref([])
const selectedConnection = ref('')
const sqlQuery = ref('')
const queryResult = ref(null)
const executing = ref(false)
const activeTab = ref('result')
const showGenerateDialog = ref(false)
const sqlDescription = ref('')

const loadConnections = async () => {
  try {
    const response = await databaseAPI.list_connections()
    connections.value = response.connections || []
    if (connections.value.length > 0) {
      selectedConnection.value = connections.value[0].name
    }
  } catch (error) {
    console.error('加载连接列表失败:', error)
  }
}

const executeQuery = async () => {
  if (!selectedConnection.value || !sqlQuery.value.trim()) {
    ElMessage.warning('请选择数据库连接并输入SQL语句')
    return
  }
  
  executing.value = true
  try {
    const response = await databaseAPI.execute_query(selectedConnection.value, {
      sql: sqlQuery.value,
      limit: 100
    })
    queryResult.value = response
    activeTab.value = 'result'
  } catch (error) {
    ElMessage.error(`查询失败: ${error.message}`)
  } finally {
    executing.value = false
  }
}

const generateSQL = () => {
  showGenerateDialog.value = true
}

const confirmGenerateSQL = async () => {
  if (!sqlDescription.value.trim()) {
    ElMessage.warning('请输入查询描述')
    return
  }
  
  try {
    const response = await databaseAPI.generate_sql(selectedConnection.value, {
      description: sqlDescription.value,
      table_name: '',
      operation: 'SELECT'
    })
    sqlQuery.value = response.sql
    showGenerateDialog.value = false
    ElMessage.success('SQL生成成功')
  } catch (error) {
    ElMessage.error(`生成失败: ${error.message}`)
  }
}

const insertCurrentQuery = () => {
  if (sqlQuery.value.trim()) {
    emit('insert-query', sqlQuery.value)
    ElMessage.success('已插入到对话')
  }
}

onMounted(() => {
  loadConnections()
})
</script>

<style lang="scss" scoped>
.database-query-panel {
  padding: 10px;
  
  .query-info {
    margin-top: 10px;
    font-size: 13px;
    color: #909399;
    
    span {
      margin-right: 20px;
    }
  }
  
  .json-viewer {
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 15px;
    border-radius: 6px;
    overflow-x: auto;
    max-height: 400px;
  }
}
</style>

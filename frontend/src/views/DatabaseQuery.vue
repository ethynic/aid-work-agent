<template>
  <div class="database-query">
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card class="sidebar">
          <template #header>
            <span>连接选择</span>
          </template>
          <el-select v-model="selectedConnection" placeholder="选择连接" style="width: 100%;">
            <el-option 
              v-for="conn in connections" 
              :key="conn.name" 
              :label="conn.name" 
              :value="conn.name" 
            />
          </el-select>
          
          <el-divider />
          
          <div class="tables-quick">
            <small>常用表</small>
            <el-tag 
              v-for="table in recentTables" 
              :key="table"
              size="small"
              class="table-tag"
              @click="insertTable(table)"
            >
              {{ table }}
            </el-tag>
          </div>
        </el-card>
      </el-col>
      
      <el-col :span="18">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>SQL编辑器</span>
              <div class="actions">
                <el-button @click="formatSQL">
                  <el-icon><Edit /></el-icon>
                  格式化
                </el-button>
                <el-button @click="clearEditor">
                  <el-icon><Delete /></el-icon>
                  清空
                </el-button>
              </div>
            </div>
          </template>
          
          <el-input
            v-model="sqlCode"
            type="textarea"
            :rows="10"
            class="sql-editor"
            placeholder="SELECT * FROM table WHERE..."
            font-family="monospace"
          />
          
          <div class="editor-actions">
            <el-button type="primary" @click="executeQuery" :loading="executing">
              <el-icon><VideoPlay /></el-icon>
              执行查询
            </el-button>
            <el-button @click="showGenerateDialog = true">
              <el-icon><MagicStick /></el-icon>
              智能生成
            </el-button>
            <el-button @click="explainQuery">
              <el-icon><InfoFilled /></el-icon>
              分析执行计划
            </el-button>
          </div>
        </el-card>
        
        <el-card style="margin-top: 20px;" v-if="queryResult">
          <template #header>
            <div class="card-header">
              <span>查询结果</span>
              <span class="result-info">
                {{ queryResult.row_count }} 行 | {{ (queryResult.execution_time * 1000).toFixed(2) }}ms
              </span>
            </div>
          </template>
          
          <el-table :data="queryResult.rows" stripe max-height="400" v-if="queryResult.columns.length > 0">
            <el-table-column
              v-for="col in queryResult.columns"
              :key="col"
              :prop="col"
              :label="col"
              min-width="120"
              show-overflow-tooltip
            />
          </el-table>
          
          <el-empty v-else description="无数据返回" />
        </el-card>
        
        <el-card style="margin-top: 20px;" v-if="queryResult?.columns">
          <template #header>
            <span>结果统计</span>
          </template>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="返回行数">{{ queryResult.row_count }}</el-descriptions-item>
            <el-descriptions-item label="返回列数">{{ queryResult.columns.length }}</el-descriptions-item>
            <el-descriptions-item label="执行时间">{{ (queryResult.execution_time * 1000).toFixed(2) }}ms</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
    
    <el-dialog v-model="showGenerateDialog" title="智能生成SQL" width="600px">
      <el-form label-width="80px">
        <el-form-item label="描述">
          <el-input
            v-model="sqlDescription"
            type="textarea"
            :rows="4"
            placeholder="描述您想要查询的数据..."
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showGenerateDialog = false">取消</el-button>
        <el-button type="primary" @click="generateSQL" :loading="generating">生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Edit, Delete, VideoPlay, MagicStick, InfoFilled } from '@element-plus/icons-vue'
import { databaseAPI } from '@/services/api'
import { ElMessage } from 'element-plus'

const connections = ref([])
const selectedConnection = ref('')
const sqlCode = ref('SELECT * FROM ')
const queryResult = ref(null)
const executing = ref(false)
const generating = ref(false)
const showGenerateDialog = ref(false)
const sqlDescription = ref('')
const recentTables = ref(['users', 'orders', 'products'])

const loadConnections = async () => {
  try {
    const response = await databaseAPI.list_connections()
    connections.value = response.connections || []
    if (connections.value.length > 0) {
      selectedConnection.value = connections.value[0].name
    }
  } catch (error) {
    ElMessage.error(`加载连接列表失败: ${error.message}`)
  }
}

const executeQuery = async () => {
  if (!selectedConnection.value || !sqlCode.value.trim()) {
    ElMessage.warning('请输入SQL语句')
    return
  }
  
  executing.value = true
  try {
    const response = await databaseAPI.execute_query(selectedConnection.value, {
      sql: sqlCode.value,
      limit: 100
    })
    queryResult.value = response
    ElMessage.success('查询执行成功')
  } catch (error) {
    ElMessage.error(`查询失败: ${error.message}`)
    queryResult.value = null
  } finally {
    executing.value = false
  }
}

const formatSQL = () => {
  // 简单的SQL格式化
  let sql = sqlCode.value
  const keywords = ['SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT']
  
  keywords.forEach(keyword => {
    const regex = new RegExp(`\\b${keyword}\\b`, 'gi')
    sql = sql.replace(regex, '\n' + keyword)
  })
  
  sqlCode.value = sql.trim()
}

const clearEditor = () => {
  sqlCode.value = 'SELECT * FROM '
}

const insertTable = (tableName) => {
  sqlCode.value = `SELECT * FROM ${tableName} `
}

const generateSQL = async () => {
  if (!sqlDescription.value.trim()) {
    ElMessage.warning('请输入查询描述')
    return
  }
  
  generating.value = true
  try {
    const response = await databaseAPI.generate_sql(selectedConnection.value, {
      description: sqlDescription.value,
      table_name: '',
      operation: 'SELECT'
    })
    sqlCode.value = response.sql
    showGenerateDialog.value = false
    ElMessage.success('SQL生成成功')
  } catch (error) {
    ElMessage.error(`生成失败: ${error.message}`)
  } finally {
    generating.value = false
  }
}

const explainQuery = () => {
  sqlCode.value = `EXPLAIN ${sqlCode.value}`
  executeQuery()
}

onMounted(loadConnections)
</script>

<style lang="scss" scoped>
.database-query {
  .sidebar {
    .tables-quick {
      small {
        color: #909399;
        display: block;
        margin-bottom: 8px;
      }
      
      .table-tag {
        margin: 4px;
        cursor: pointer;
        
        &:hover {
          background: #409eff;
          color: #fff;
        }
      }
    }
  }
  
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    
    .actions {
      display: flex;
      gap: 8px;
    }
    
    .result-info {
      font-size: 13px;
      color: #909399;
    }
  }
  
  .sql-editor {
    :deep(textarea) {
      font-family: 'Fira Code', 'Consolas', monospace;
      font-size: 14px;
      line-height: 1.6;
    }
  }
  
  .editor-actions {
    margin-top: 15px;
    display: flex;
    gap: 10px;
  }
}
</style>

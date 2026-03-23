<template>
  <div class="database-connections">
    <el-row :gutter="20">
      <el-col :span="8" v-for="conn in connections" :key="conn.name">
        <el-card class="connection-card" :class="{ connected: conn.enabled }">
          <template #header>
            <div class="card-header">
              <div class="conn-info">
                <el-icon :size="24" :class="getDbIcon(conn.db_type)">
                  <Connection />
                </el-icon>
                <div>
                  <h4>{{ conn.name }}</h4>
                  <small>{{ conn.host }}:{{ conn.port }}</small>
                </div>
              </div>
              <el-switch v-model="conn.enabled" @change="toggleConnection(conn)" />
            </div>
          </template>
          
          <div class="card-content">
            <el-descriptions :column="1" size="small">
              <el-descriptions-item label="数据库类型">
                <el-tag size="small">{{ conn.db_type.toUpperCase() }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="数据库">{{ conn.database }}</el-descriptions-item>
            </el-descriptions>
            
            <div class="card-actions">
              <el-button type="primary" size="small" @click="viewTables(conn)">
                <el-icon><List /></el-icon>
                查看表
              </el-button>
              <el-button size="small" @click="testConnection(conn)">
                <el-icon><Connection /></el-icon>
                测试
              </el-button>
              <el-button type="danger" size="small" @click="deleteConnection(conn)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </div>
        </el-card>
      </el-col>
      
      <el-col :span="8">
        <el-card class="add-card" @click="showAddDialog = true">
          <div class="add-content">
            <el-icon :size="48"><Plus /></el-icon>
            <p>添加数据库连接</p>
          </div>
        </el-card>
      </el-col>
    </el-row>
    
    <el-dialog v-model="showAddDialog" title="添加数据库连接" width="500px">
      <el-form label-width="100px" :model="connectionForm" :rules="rules" ref="formRef">
        <el-form-item label="连接名称" prop="name">
          <el-input v-model="connectionForm.name" placeholder="输入连接名称" />
        </el-form-item>
        <el-form-item label="数据库类型" prop="db_type">
          <el-select v-model="connectionForm.db_type" style="width: 100%;">
            <el-option label="MySQL" value="mysql" />
            <el-option label="SQL Server" value="sqlserver" />
            <el-option label="Oracle" value="oracle" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机" prop="host">
          <el-input v-model="connectionForm.host" placeholder="localhost" />
        </el-form-item>
        <el-form-item label="端口" prop="port">
          <el-input-number v-model="connectionForm.port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="用户名" prop="username">
          <el-input v-model="connectionForm.username" placeholder="数据库用户名" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="connectionForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="数据库" prop="database">
          <el-input v-model="connectionForm.database" placeholder="数据库名称" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" @click="addConnection" :loading="testing">测试连接</el-button>
        <el-button type="success" @click="saveConnection" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Connection, List, Delete, Plus } from '@element-plus/icons-vue'
import { databaseAPI } from '@/services/api'
import { ElMessage } from 'element-plus'

const router = useRouter()
const connections = ref([])
const showAddDialog = ref(false)
const testing = ref(false)
const saving = ref(false)
const formRef = ref(null)

const connectionForm = ref({
  name: '',
  db_type: 'mysql',
  host: 'localhost',
  port: 3306,
  username: '',
  password: '',
  database: ''
})

const rules = {
  name: [{ required: true, message: '请输入连接名称', trigger: 'blur' }],
  db_type: [{ required: true, message: '请选择数据库类型', trigger: 'change' }],
  host: [{ required: true, message: '请输入主机地址', trigger: 'blur' }],
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  database: [{ required: true, message: '请输入数据库名称', trigger: 'blur' }]
}

const getDbIcon = (type) => {
  const icons = {
    mysql: 'mysql-icon',
    sqlserver: 'sqlserver-icon',
    oracle: 'oracle-icon'
  }
  return icons[type] || ''
}

const loadConnections = async () => {
  try {
    const response = await databaseAPI.list_connections()
    connections.value = response.connections || []
  } catch (error) {
    ElMessage.error(`加载连接列表失败: ${error.message}`)
  }
}

const testConnection = async (conn) => {
  try {
    const response = await databaseAPI.test_connection(conn.name)
    if (response.success) {
      ElMessage.success('连接测试成功')
    } else {
      ElMessage.error(`连接测试失败: ${response.message}`)
    }
  } catch (error) {
    ElMessage.error(`测试失败: ${error.message}`)
  }
}

const addConnection = async () => {
  await formRef.value?.validate()
  
  testing.value = true
  try {
    const response = await databaseAPI.add_connection(connectionForm.value)
    if (response.success) {
      ElMessage.success('连接测试成功')
    } else {
      ElMessage.error(`测试失败: ${response.message || '未知错误'}`)
    }
  } catch (error) {
    ElMessage.error(`测试失败: ${error.message}`)
  } finally {
    testing.value = false
  }
}

const saveConnection = async () => {
  await formRef.value?.validate()
  
  saving.value = true
  try {
    const response = await databaseAPI.add_connection(connectionForm.value)
    if (response.success) {
      ElMessage.success('连接保存成功')
      showAddDialog.value = false
      loadConnections()
    } else {
      ElMessage.error(`保存失败: ${response.message}`)
    }
  } catch (error) {
    ElMessage.error(`保存失败: ${error.message}`)
  } finally {
    saving.value = false
  }
}

const toggleConnection = async (conn) => {
  try {
    if (conn.enabled) {
      await testConnection(conn)
    }
  } catch (error) {
    conn.enabled = !conn.enabled
  }
}

const viewTables = (conn) => {
  router.push(`/database/schema?connection=${conn.name}`)
}

const deleteConnection = async (conn) => {
  try {
    await databaseAPI.remove_connection(conn.name)
    ElMessage.success('连接已删除')
    loadConnections()
  } catch (error) {
    ElMessage.error(`删除失败: ${error.message}`)
  }
}

onMounted(loadConnections)
</script>

<style lang="scss" scoped>
.database-connections {
  .connection-card {
    transition: all 0.3s;
    
    &.connected {
      border-color: #67c23a;
    }
    
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      
      .conn-info {
        display: flex;
        align-items: center;
        gap: 10px;
        
        h4 {
          margin: 0;
        }
        
        small {
          color: #909399;
        }
      }
    }
    
    .card-content {
      .card-actions {
        margin-top: 15px;
        display: flex;
        gap: 8px;
      }
    }
  }
  
  .add-card {
    cursor: pointer;
    height: 200px;
    display: flex;
    align-items: center;
    justify-content: center;
    
    &:hover {
      border-color: #409eff;
      background: #ecf5ff;
    }
    
    .add-content {
      text-align: center;
      color: #909399;
      
      p {
        margin-top: 10px;
      }
    }
  }
}
</style>

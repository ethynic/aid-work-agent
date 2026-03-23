<template>
  <div class="database-schema">
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card class="sidebar">
          <template #header>
            <span>数据库连接</span>
          </template>
          <el-select v-model="selectedConnection" placeholder="选择连接" style="width: 100%;" @change="loadSchema">
            <el-option 
              v-for="conn in connections" 
              :key="conn.name" 
              :label="conn.name" 
              :value="conn.name" 
            />
          </el-select>
          
          <el-divider />
          
          <div class="tables-list" v-if="schema">
            <div 
              v-for="table in schema.tables" 
              :key="table.table_name"
              class="table-item"
              :class="{ active: selectedTable === table.table_name }"
              @click="selectTable(table.table_name)"
            >
              <el-icon><Grid /></el-icon>
              <span>{{ table.table_name }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
      
      <el-col :span="18">
        <el-card v-if="selectedTableInfo">
          <template #header>
            <div class="card-header">
              <span>{{ selectedTable }}</span>
              <el-tag>{{ selectedTableInfo.column_count }} 个字段</el-tag>
            </div>
          </template>
          
          <el-table :data="selectedTableInfo.columns" stripe>
            <el-table-column prop="column_name" label="字段名" width="180" />
            <el-table-column prop="data_type" label="类型" width="120">
              <template #default="{ row }">
                <el-tag size="small" type="info">{{ row.data_type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="column_type" label="完整类型" width="180" />
            <el-table-column prop="is_nullable" label="可空" width="80">
              <template #default="{ row }">
                <el-icon v-if="row.is_nullable" color="#67c23a"><CircleCheck /></el-icon>
                <el-icon v-else color="#f56c6c"><CircleClose /></el-icon>
              </template>
            </el-table-column>
            <el-table-column prop="column_key" label="键" width="80">
              <template #default="{ row }">
                <el-tag v-if="row.column_key === 'PRI'" size="small" type="success">主键</el-tag>
                <el-tag v-if="row.column_key === 'MUL'" size="small" type="warning">外键</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="column_comment" label="注释" />
          </el-table>
        </el-card>
        
        <el-card style="margin-top: 20px;" v-if="schema">
          <template #header>
            <span>实体关系图</span>
          </template>
          <div ref="erdRef" class="erd-container"></div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { Grid, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { databaseAPI } from '@/services/api'
import { ElMessage } from 'element-plus'

const connections = ref([])
const selectedConnection = ref('')
const schema = ref(null)
const selectedTable = ref('')
const selectedTableInfo = ref(null)
const erdRef = ref(null)
let erdChart = null

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

const loadSchema = async () => {
  if (!selectedConnection.value) return
  
  try {
    const response = await databaseAPI.getSchema(selectedConnection.value)
    schema.value = response.schema
  } catch (error) {
    ElMessage.error(`加载数据库结构失败: ${error.message}`)
  }
}

const selectTable = async (tableName) => {
  selectedTable.value = tableName
  
  try {
    const response = await databaseAPI.get_table_info(selectedConnection.value, tableName)
    selectedTableInfo.value = response.table
  } catch (error) {
    console.error('加载表信息失败:', error)
  }
}

const initERD = async () => {
  if (!selectedConnection.value || !erdRef.value) return
  
  try {
    const response = await databaseAPI.generate_erd(selectedConnection.value)
    const erdData = response.erd
    
    if (!erdChart) {
      erdChart = echarts.init(erdRef.value)
    }
    
    const nodes = erdData.nodes.map(node => ({
      id: node.id,
      name: node.id,
      symbolSize: 50,
      label: {
        show: true
      }
    }))
    
    const edges = erdData.edges.map(edge => ({
      source: edge.from,
      target: edge.to
    }))
    
    const option = {
      nodes: nodes,
      edges: edges,
      layout: {
        type: 'force',
        force: {
          repulsion: 200
        }
      },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        label: {
          position: 'right'
        },
        edgeSymbol: ['circle', 'arrow'],
        edgeSymbolSize: [4, 10]
      }]
    }
    
    erdChart.setOption(option)
  } catch (error) {
    console.error('加载ER图失败:', error)
  }
}

watch(selectedConnection, () => {
  loadSchema()
  initERD()
})

onMounted(() => {
  loadConnections()
})
</script>

<style lang="scss" scoped>
.database-schema {
  .sidebar {
    height: calc(100vh - 120px);
    
    .tables-list {
      max-height: calc(100% - 80px);
      overflow-y: auto;
      
      .table-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        border-radius: 4px;
        cursor: pointer;
        transition: all 0.2s;
        
        &:hover {
          background: #f5f7fa;
        }
        
        &.active {
          background: #ecf5ff;
          color: #409eff;
        }
      }
    }
  }
  
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  
  .erd-container {
    height: 400px;
  }
}
</style>

<template>
  <div class="im-apps-container">
    <el-card class="im-apps-card">
      <template #header>
        <div class="card-header">
          <h2>IM应用配置</h2>
          <el-button type="primary" @click="showAddDialog">
            <el-icon><Plus /></el-icon>
            添加应用
          </el-button>
        </div>
      </template>
      
      <el-table :data="apps" v-loading="loading" stripe>
        <el-table-column prop="platform" label="平台" width="120">
          <template #default="{ row }">
            <el-tag :type="getPlatformTagType(row.platform)">
              {{ getPlatformName(row.platform) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="app_name" label="应用名称" width="180" />
        <el-table-column prop="app_id" label="App ID" width="200" />
        <el-table-column prop="agent_id" label="关联智能体" width="200" />
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-switch
              v-model="row.is_active"
              @change="updateStatus(row)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="150">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="editApp(row)">
              编辑
            </el-button>
            <el-button type="danger" link size="small" @click="deleteApp(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="600px"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
      >
        <el-form-item label="IM平台" prop="platform">
          <el-select v-model="form.platform" placeholder="请选择平台" :disabled="isEditing">
            <el-option label="飞书" value="feishu" />
            <el-option label="企业微信" value="wecom" />
            <el-option label="钉钉" value="dingtalk" />
          </el-select>
        </el-form-item>
        
        <el-form-item label="应用名称" prop="app_name">
          <el-input v-model="form.app_name" placeholder="请输入应用名称" />
        </el-form-item>
        
        <el-form-item label="App ID" prop="app_id">
          <el-input v-model="form.app_id" placeholder="请输入App ID" :disabled="isEditing" />
        </el-form-item>
        
        <el-form-item label="App Secret" prop="app_secret">
          <el-input
            v-model="form.app_secret"
            type="password"
            :placeholder="isEditing ? '不修改请留空' : '请输入App Secret'"
            show-password
          />
          <span v-if="isEditing" class="form-hint">当前已设置，不修改请留空</span>
        </el-form-item>
        
        <el-form-item label="Verification Token" prop="verification_token">
          <el-input
            v-model="form.verification_token"
            type="password"
            :placeholder="isEditing ? '不修改请留空' : '请输入事件回调验证Token（可选）'"
            show-password
          />
          <span v-if="isEditing" class="form-hint">当前已设置，不修改请留空</span>
        </el-form-item>
        
        <el-form-item label="关联智能体" prop="agent_id">
          <el-select v-model="form.agent_id" placeholder="请选择关联的智能体" v-loading="loadingAgents">
            <el-option
              v-for="agent in agents"
              :key="agent.agent_id"
              :label="agent.display_name || agent.name"
              :value="agent.agent_id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveApp" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { imAPI, employeeAPI } from '@/services/api'

const loading = ref(false)
const loadingAgents = ref(false)
const saving = ref(false)
const apps = ref([])
const agents = ref([])
const dialogVisible = ref(false)
const dialogTitle = ref('添加IM应用')
const formRef = ref(null)
const isEditing = ref(false)
const editingId = ref(null)

const form = reactive({
  platform: '',
  app_name: '',
  app_id: '',
  app_secret: '',
  verification_token: '',
  agent_id: ''
})

const rules = {
  platform: [
    { required: true, message: '请选择平台', trigger: 'change' }
  ],
  app_name: [
    { required: true, message: '请输入应用名称', trigger: 'blur' }
  ],
  app_id: [
    { required: true, message: '请输入App ID', trigger: 'blur' }
  ],
  app_secret: [
    { 
      validator: (rule, value, callback) => {
        if (!isEditing.value && !value) {
          callback(new Error('请输入App Secret'))
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ],
  agent_id: [
    { required: true, message: '请选择关联的智能体', trigger: 'change' }
  ]
}

const getPlatformName = (platform) => {
  const names = {
    feishu: '飞书',
    wecom: '企业微信',
    dingtalk: '钉钉'
  }
  return names[platform] || platform
}

const getPlatformTagType = (platform) => {
  const types = {
    feishu: 'primary',
    wecom: 'success',
    dingtalk: 'warning'
  }
  return types[platform] || 'info'
}

const formatDate = (dateStr) => {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN')
}

const loadApps = async () => {
  loading.value = true
  try {
    const response = await imAPI.get_apps()
    apps.value = response.apps || []
  } catch (error) {
    console.error('加载IM应用配置失败:', error)
    ElMessage.error('加载IM应用配置失败')
  } finally {
    loading.value = false
  }
}

const loadAgents = async () => {
  loadingAgents.value = true
  try {
    const response = await employeeAPI.get_employees()
    agents.value = response.agents || []
  } catch (error) {
    console.error('加载智能体列表失败:', error)
    ElMessage.error('加载智能体列表失败')
  } finally {
    loadingAgents.value = false
  }
}

const showAddDialog = () => {
  isEditing.value = false
  editingId.value = null
  dialogTitle.value = '添加IM应用'
  
  Object.assign(form, {
    platform: '',
    app_name: '',
    app_id: '',
    app_secret: '',
    verification_token: '',
    agent_id: ''
  })
  
  dialogVisible.value = true
}

const editApp = (app) => {
  isEditing.value = true
  editingId.value = app.id
  dialogTitle.value = '编辑IM应用'
  
  Object.assign(form, {
    platform: app.platform,
    app_name: app.app_name,
    app_id: app.app_id,
    app_secret: '',
    verification_token: '',
    agent_id: app.agent_id
  })
  
  dialogVisible.value = true
}

const saveApp = async () => {
  if (!formRef.value) return
  
  try {
    await formRef.value.validate()
    saving.value = true
    
    if (isEditing.value) {
      const updateData = {
        app_name: form.app_name,
        agent_id: form.agent_id
      }
      if (form.app_secret) {
        updateData.app_secret = form.app_secret
      }
      if (form.verification_token) {
        updateData.verification_token = form.verification_token
      }
      
      await imAPI.update_app(editingId.value, updateData)
      ElMessage.success('更新成功')
    } else {
      await imAPI.createApp({
        platform: form.platform,
        app_name: form.app_name,
        app_id: form.app_id,
        app_secret: form.app_secret,
        verification_token: form.verification_token || null,
        agent_id: form.agent_id
      })
      ElMessage.success('添加成功')
    }
    
    dialogVisible.value = false
    await loadApps()
  } catch (error) {
    console.error('保存IM应用配置失败:', error)
    ElMessage.error(error.response?.data?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const deleteApp = async (app) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除应用 "${app.app_name}" 吗？`,
      '删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'danger'
      }
    )
    
    await imAPI.deleteApp(app.id)
    ElMessage.success('删除成功')
    await loadApps()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除IM应用配置失败:', error)
      ElMessage.error('删除失败')
    }
  }
}

const updateStatus = async (app) => {
  try {
    await imAPI.update_app(app.id, { is_active: app.is_active })
    ElMessage.success('状态更新成功')
  } catch (error) {
    console.error('更新状态失败:', error)
    app.is_active = !app.is_active
    ElMessage.error('更新状态失败')
  }
}

onMounted(async () => {
  await loadAgents()
  await loadApps()
})
</script>

<style lang="scss" scoped>
.im-apps-container {
  padding: 20px;
  
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    
    h2 {
      margin: 0;
      font-size: 20px;
      color: #303133;
    }
  }
  
  .el-select {
    width: 100%;
  }
  
  .form-hint {
    font-size: 12px;
    color: #909399;
    margin-top: 4px;
    display: block;
  }
}
</style>

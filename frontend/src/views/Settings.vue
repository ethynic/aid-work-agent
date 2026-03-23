<template>
  <div class="settings">
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card class="settings-menu">
          <el-menu :default-active="activeMenu" @select="handleMenuSelect">
            <el-menu-item index="general">
              <el-icon><Setting /></el-icon>
              <span>通用设置</span>
            </el-menu-item>
            <el-menu-item index="users">
              <el-icon><User /></el-icon>
              <span>用户管理</span>
            </el-menu-item>
            <el-menu-item index="security">
              <el-icon><Lock /></el-icon>
              <span>安全设置</span>
            </el-menu-item>
            <el-menu-item index="api">
              <el-icon><Key /></el-icon>
              <span>API配置</span>
            </el-menu-item>
            <el-menu-item index="notifications">
              <el-icon><Bell /></el-icon>
              <span>通知设置</span>
            </el-menu-item>
            <el-menu-item index="about">
              <el-icon><InfoFilled /></el-icon>
              <span>关于系统</span>
            </el-menu-item>
          </el-menu>
        </el-card>
      </el-col>
      
      <el-col :span="18">
        <el-card v-if="activeMenu === 'general'">
          <template #header>
            <span>通用设置</span>
          </template>
          
          <el-form label-width="120px">
            <el-form-item label="系统名称">
              <el-input v-model="settings.app_name" />
            </el-form-item>
            <el-form-item label="默认语言">
              <el-select v-model="settings.language" style="width: 200px;">
                <el-option label="简体中文" value="zh-CN" />
                <el-option label="English" value="en-US" />
              </el-select>
            </el-form-item>
            <el-form-item label="时区">
              <el-select v-model="settings.timezone" style="width: 300px;">
                <el-option label="Asia/Shanghai (UTC+8)" value="Asia/Shanghai" />
                <el-option label="America/New_York (UTC-5)" value="America/New_York" />
                <el-option label="Europe/London (UTC+0)" value="Europe/London" />
              </el-select>
            </el-form-item>
            <el-form-item label="主题模式">
              <el-radio-group v-model="settings.theme">
                <el-radio-button value="light">浅色</el-radio-button>
                <el-radio-button value="dark">深色</el-radio-button>
                <el-radio-button value="auto">跟随系统</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveSettings">保存设置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
        
        <el-card v-if="activeMenu === 'security'">
          <template #header>
            <span>安全设置</span>
          </template>
          
          <el-form label-width="120px">
            <el-form-item label="会话超时">
              <el-select v-model="security.session_timeout" style="width: 200px;">
                <el-option label="30分钟" :value="30" />
                <el-option label="1小时" :value="60" />
                <el-option label="8小时" :value="480" />
                <el-option label="24小时" :value="1440" />
              </el-select>
            </el-form-item>
            <el-form-item label="密码策略">
              <el-checkbox-group v-model="security.password_policy">
                <el-checkbox label="length" value="length">最少8位</el-checkbox>
                <el-checkbox label="uppercase" value="uppercase">包含大写字母</el-checkbox>
                <el-checkbox label="numbers" value="numbers">包含数字</el-checkbox>
                <el-checkbox label="special" value="special">包含特殊字符</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="两因素认证">
              <el-switch v-model="security.two_factor_enabled" />
              <span class="form-tip">启用后登录需要验证码</span>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveSecuritySettings">保存设置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
        
        <el-card v-if="activeMenu === 'api'">
          <template #header>
            <span>API配置</span>
          </template>
          
          <el-form label-width="120px">
            <el-form-item label="API密钥">
              <el-input v-model="apiSettings.api_key" :type="showApiKey ? 'text' : 'password'">
                <template #append>
                  <el-button @click="showApiKey = !showApiKey">
                    <el-icon><View /></el-icon>
                  </el-button>
                </template>
              </el-input>
              <el-button size="small" style="margin-top: 10px;" @click="regenerateApiKey">
                重新生成密钥
              </el-button>
            </el-form-item>
            <el-form-item label="IP白名单">
              <el-input
                v-model="apiSettings.ip_whitelist"
                type="textarea"
                :rows="3"
                placeholder="每行一个IP地址，留空表示不限制"
              />
            </el-form-item>
            <el-form-item label="请求频率限制">
              <el-input-number v-model="apiSettings.rate_limit" :min="1" :max="1000" />
              <span class="form-tip">次/分钟</span>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveApiSettings">保存设置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
        
        <el-card v-if="activeMenu === 'users'">
          <template #header>
            <div class="card-header">
              <span>用户管理</span>
              <el-button type="primary" @click="openUserDialog">
                <el-icon><Plus /></el-icon>
                添加用户
              </el-button>
            </div>
          </template>
          
          <el-table v-loading="loading" :data="users" style="width: 100%">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="username" label="用户名" />
            <el-table-column prop="email" label="邮箱" />
            <el-table-column prop="full_name" label="真实姓名" />
            <el-table-column prop="is_active" label="状态" width="80">
              <template #default="scope">
                <el-switch v-model="scope.row.is_active" @change="updateUserStatus(scope.row)" />
              </template>
            </el-table-column>
            <el-table-column prop="is_superuser" label="角色" width="80">
              <template #default="scope">
                <el-tag type="success" v-if="scope.row.is_superuser">管理员</el-tag>
                <el-tag v-else>普通用户</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="scope">
                <el-button size="small" @click="openUserDialog(scope.row)">
                  <el-icon><Edit /></el-icon>
                  编辑
                </el-button>
                <el-button size="small" type="danger" @click="deleteUser(scope.row.id)">
                  <el-icon><Delete /></el-icon>
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          
          <div class="pagination">
            <el-pagination
              v-model:current-page="pagination.page"
              v-model:page-size="pagination.pageSize"
              :page-sizes="[10, 20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              :total="pagination.total"
              @size-change="handleSizeChange"
              @current-change="handleCurrentChange"
            />
          </div>
          
          <!-- 用户编辑对话框 -->
          <el-dialog
            v-model="dialogVisible"
            :title="isEdit ? '编辑用户' : '添加用户'"
            width="500px"
          >
            <el-form :model="userForm" :rules="userRules" ref="userFormRef" label-width="100px">
              <el-form-item label="用户名" prop="username">
                <el-input v-model="userForm.username" placeholder="请输入用户名" />
              </el-form-item>
              <el-form-item label="邮箱" prop="email">
                <el-input v-model="userForm.email" placeholder="请输入邮箱" type="email" />
              </el-form-item>
              <el-form-item label="真实姓名" prop="full_name">
                <el-input v-model="userForm.full_name" placeholder="请输入真实姓名" />
              </el-form-item>
              <el-form-item label="密码" prop="password" v-if="!isEdit">
                <el-input v-model="userForm.password" placeholder="请输入密码" type="password" show-password />
              </el-form-item>
              <el-form-item label="角色" prop="is_superuser">
                <el-switch v-model="userForm.is_superuser" />
                <span class="form-tip">开启后为管理员权限</span>
              </el-form-item>
            </el-form>
            <template #footer>
              <div class="dialog-footer">
                <el-button @click="dialogVisible = false">取消</el-button>
                <el-button type="primary" @click="saveUser" :loading="saving">保存</el-button>
              </div>
            </template>
          </el-dialog>
        </el-card>
        
        <el-card v-if="activeMenu === 'about'">
          <template #header>
            <span>关于系统</span>
          </template>
          
          <div class="about-info">
            <el-icon :size="64"><Brain /></el-icon>
            <h2>爱定义智能体系统</h2>
            <p>版本: {{ version }}</p>
            <p>构建时间: {{ buildTime }}</p>
            
            <el-divider />
            
            <h3>技术栈</h3>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="后端框架">FastAPI + Python</el-descriptions-item>
              <el-descriptions-item label="前端框架">Vue 3 + Element Plus</el-descriptions-item>
              <el-descriptions-item label="大模型适配">DeepSeek/豆包/千问等</el-descriptions-item>
              <el-descriptions-item label="数据库支持">MySQL/SQL Server/Oracle</el-descriptions-item>
              <el-descriptions-item label="工具协议">MCP</el-descriptions-item>
              <el-descriptions-item label="任务调度">Celery + Redis</el-descriptions-item>
            </el-descriptions>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Setting, Lock, Key, Bell, InfoFilled, View, User, Plus, Edit, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const activeMenu = ref('general')
const showApiKey = ref(false)
const version = ref('1.0.0')
const buildTime = ref('2024-01-14 12:00:00')

const settings = ref({
  app_name: '爱定义智能体系统',
  language: 'zh-CN',
  timezone: 'Asia/Shanghai',
  theme: 'light'
})

const security = ref({
  session_timeout: 60,
  password_policy: ['length', 'numbers'],
  two_factor_enabled: false
})

const apiSettings = ref({
  api_key: 'sk-xxxxxxxxxxxxxxxxxxxxxxxx',
  ip_whitelist: '',
  rate_limit: 100
})

// 用户管理相关状态
const users = ref([])
const loading = ref(false)
const pagination = ref({
  page: 1,
  pageSize: 10,
  total: 0
})
const dialogVisible = ref(false)
const isEdit = ref(false)
const saving = ref(false)
const userFormRef = ref(null)
const userForm = ref({
  id: '',
  username: '',
  email: '',
  full_name: '',
  password: '',
  is_active: true,
  is_superuser: false
})

// 用户表单验证规则
const userRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度在 3 到 20 个字符', trigger: 'blur' }
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入正确的邮箱格式', trigger: 'blur' }
  ],
  full_name: [
    { max: 50, message: '真实姓名长度不能超过 50 个字符', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 20, message: '密码长度在 6 到 20 个字符', trigger: 'blur' }
  ]
}

const handleMenuSelect = (index) => {
  activeMenu.value = index
  // 切换到用户管理时，获取用户列表
  if (index === 'users') {
    fetchUsers()
  }
}

// 页面加载时，如果当前是用户管理页面，获取用户列表
onMounted(() => {
  if (activeMenu.value === 'users') {
    fetchUsers()
  }
})

const saveSettings = () => {
  ElMessage.success('通用设置已保存')
}

const saveSecuritySettings = () => {
  ElMessage.success('安全设置已保存')
}

const saveApiSettings = () => {
  ElMessage.success('API配置已保存')
}

const regenerateApiKey = async () => {
  try {
    await ElMessageBox.confirm('确定要重新生成API密钥吗？旧密钥将立即失效。', '确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    apiSettings.value.api_key = 'sk-' + Math.random().toString(36).substr(2, 24)
    ElMessage.success('API密钥已重新生成')
  } catch {
    // 用户取消
  }
}

// 用户管理相关方法
const fetchUsers = async () => {
  loading.value = true
  try {
    // 模拟API请求获取用户列表
    // 实际项目中替换为真实的API调用
    setTimeout(() => {
      users.value = [
        {
          id: '1',
          username: 'admin',
          email: 'admin@example.com',
          full_name: '系统管理员',
          is_active: true,
          is_superuser: true,
          created_at: '2024-01-14 12:00:00'
        },
        {
          id: '2',
          username: 'user1',
          email: 'user1@example.com',
          full_name: '普通用户',
          is_active: true,
          is_superuser: false,
          created_at: '2024-01-14 13:00:00'
        }
      ]
      pagination.value.total = users.value.length
      loading.value = false
    }, 500)
  } catch (error) {
    console.error('获取用户列表失败:', error)
    ElMessage.error('获取用户列表失败')
    loading.value = false
  }
}

const openUserDialog = (user = null) => {
  if (user) {
    // 编辑模式
    isEdit.value = true
    userForm.value = { ...user }
  } else {
    // 添加模式
    isEdit.value = false
    userForm.value = {
      id: '',
      username: '',
      email: '',
      full_name: '',
      password: '',
      is_active: true,
      is_superuser: false
    }
  }
  dialogVisible.value = true
}

const saveUser = async () => {
  if (!userFormRef.value) return
  
  try {
    await userFormRef.value.validate()
    saving.value = true
    
    // 模拟API请求保存用户
    // 实际项目中替换为真实的API调用
    setTimeout(() => {
      if (isEdit.value) {
        // 更新现有用户
        const index = users.value.findIndex(u => u.id === userForm.value.id)
        if (index !== -1) {
          users.value[index] = { ...userForm.value }
        }
      } else {
        // 添加新用户
        const newUser = {
          ...userForm.value,
          id: Date.now().toString(),
          created_at: new Date().toLocaleString()
        }
        users.value.unshift(newUser)
        pagination.value.total++
      }
      
      dialogVisible.value = false
      ElMessage.success(isEdit.value ? '用户更新成功' : '用户添加成功')
      saving.value = false
    }, 500)
  } catch (error) {
    console.error('保存用户失败:', error)
    ElMessage.error('保存用户失败')
    saving.value = false
  }
}

const updateUserStatus = async (user) => {
  try {
    // 模拟API请求更新用户状态
    // 实际项目中替换为真实的API调用
    setTimeout(() => {
      ElMessage.success(`用户 ${user.username} 状态已更新`)
    }, 300)
  } catch (error) {
    console.error('更新用户状态失败:', error)
    ElMessage.error('更新用户状态失败')
    // 恢复原状态
    user.is_active = !user.is_active
  }
}

const deleteUser = async (userId) => {
  try {
    await ElMessageBox.confirm('确定要删除这个用户吗？', '确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    // 模拟API请求删除用户
    // 实际项目中替换为真实的API调用
    setTimeout(() => {
      const index = users.value.findIndex(u => u.id === userId)
      if (index !== -1) {
        users.value.splice(index, 1)
        pagination.value.total--
      }
      ElMessage.success('用户删除成功')
    }, 300)
  } catch {
    // 用户取消
  }
}

const handleSizeChange = (size) => {
  pagination.value.pageSize = size
  pagination.value.page = 1
  fetchUsers()
}

const handleCurrentChange = (page) => {
  pagination.value.page = page
  fetchUsers()
}
</script>

<style lang="scss" scoped>
.settings {
  .settings-menu {
    height: calc(100vh - 120px);
    
    :deep(.el-menu) {
      border-right: none;
    }
  }
  
  .form-tip {
    margin-left: 10px;
    font-size: 12px;
    color: #909399;
  }
  
  .about-info {
    text-align: center;
    
    h2 {
      margin: 15px 0 5px;
    }
    
    p {
      color: #909399;
      margin: 5px 0;
    }
    
    h3 {
      margin: 20px 0 15px;
      text-align: left;
    }
  }
  
  // 用户管理相关样式
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  
  .pagination {
    margin-top: 20px;
    display: flex;
    justify-content: flex-end;
  }
  
  // 确保表格在小屏幕上可以滚动
  :deep(.el-table) {
    margin-bottom: 10px;
    overflow-x: auto;
  }
  
  // 按钮间距
  :deep(.el-button + .el-button) {
    margin-left: 10px;
  }
}
</style>

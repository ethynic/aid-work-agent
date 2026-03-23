<template>
  <div class="ai-employees-container">
    <el-card class="ai-employees-card">
      <template #header>
        <div class="card-header">
          <h2>AI数字员工管理</h2>
          <el-button type="primary" @click="showAddEmployeeDialog">
            <el-icon><Plus /></el-icon>
            添加数字员工
          </el-button>
        </div>
      </template>
      
      <!-- 数字员工头像网格 -->
      <div class="employee-grid" v-loading="loading">
        <div
          v-for="employee in employees"
          :key="employee.agent_id"
          class="employee-item"
          @click="enterEmployeeChat(employee)"
        >
          <div class="employee-avatar-container">
            <el-avatar :size="100" :src="employee.avatar || defaultAvatar">
              {{ employee.display_name.charAt(0) }}
            </el-avatar>
          </div>
          <div class="employee-details">
            <h3 class="employee-name">{{ employee.name }}</h3>
            <p class="employee-display-name">{{ employee.display_name }}</p>
          </div>
          <div class="employee-actions">
            <el-button type="primary" link size="small" @click.stop="editEmployee(employee)">
              编辑
            </el-button>
            <el-button type="danger" link size="small" @click.stop="deleteEmployee(employee)">
              删除
            </el-button>
          </div>
        </div>
      </div>
    </el-card>
    
    <!-- 添加/编辑数字员工对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="700px"
    >
      <el-form
        ref="employeeFormRef"
        :model="employeeForm"
        :rules="employeeRules"
        label-position="top"
      >
        <el-form-item label="智能体名称" prop="name">
          <el-input v-model="employeeForm.name" placeholder="请输入智能体名称" />
        </el-form-item>
        
        <el-form-item label="岗位" prop="position">
          <el-select v-model="employeeForm.position" placeholder="请选择岗位">
            <el-option label="招聘" value="招聘" />
            <el-option label="入职" value="入职" />
            <el-option label="采购" value="采购" />
            <el-option label="助理" value="助理" />
          </el-select>
        </el-form-item>
        
        <el-form-item label="简介" prop="description">
          <el-input
            v-model="employeeForm.description"
            type="textarea"
            placeholder="请输入智能体简介"
            :rows="3"
          />
        </el-form-item>
        
        <el-form-item label="技能" prop="skills">
          <el-checkbox-group v-model="employeeForm.skills">
            <el-checkbox
              v-for="skill in availableSkills"
              :key="skill.skill_id"
              :label="skill.skill_id"
            >
              {{ skill.name }} ({{ skill.description }})
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        
        <el-form-item label="MCP" prop="mcps">
          <el-checkbox-group v-model="employeeForm.mcps">
            <el-checkbox
              v-for="mcp in availableMCPs"
              :key="mcp.mcp_id"
              :label="mcp.mcp_id"
            >
              {{ mcp.name }} ({{ mcp.description }})
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEmployee">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { employeeAPI } from '@/services/api'

const router = useRouter()
const loading = ref(false)
const employees = ref([])
const dialogVisible = ref(false)
const dialogTitle = ref('添加数字员工')
const employeeFormRef = ref(null)
const editingEmployee = ref(null)
const defaultAvatar = ref('https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=professional%20avatar%20for%20AI%20assistant&image_size=square')

// 可用的技能和MCP
const availableSkills = ref([])
const availableMCPs = ref([])

// 员工表单
const employeeForm = reactive({
  name: '',
  position: '',
  description: '',
  skills: [],
  mcps: []
})

// 表单验证规则
const employeeRules = {
  name: [
    { required: true, message: '请输入智能体名称', trigger: 'blur' }
  ],
  position: [
    { required: true, message: '请选择岗位', trigger: 'change' }
  ],
  description: [
    { required: true, message: '请输入智能体简介', trigger: 'blur' }
  ]
}

// 加载数字员工列表
const loadEmployees = async () => {
  loading.value = true
  try {
    const response = await employeeAPI.get_employees()
    employees.value = response.agents
  } catch (error) {
    console.error('加载数字员工失败:', error)
    ElMessage.error('加载数字员工失败')
  } finally {
    loading.value = false
  }
}

// 加载可用的技能和MCP
const loadAvailableSkillsAndMCPs = async () => {
  try {
    // 加载技能库
    const skillsResponse = await employeeAPI.get_skill_library()
    availableSkills.value = skillsResponse.skills
    
    // 加载MCP库
    const mcpsResponse = await employeeAPI.get_mcp_library()
    availableMCPs.value = mcpsResponse.mcps
  } catch (error) {
    console.error('加载技能和MCP失败:', error)
    ElMessage.error('加载技能和MCP失败')
  }
}

// 显示添加员工对话框
const showAddEmployeeDialog = () => {
  editingEmployee.value = null
  dialogTitle.value = '添加数字员工'
  
  // 重置表单
  Object.assign(employeeForm, {
    name: '',
    position: '',
    description: '',
    skills: [],
    mcps: []
  })
  
  dialogVisible.value = true
}

// 编辑员工
const editEmployee = (employee) => {
  editingEmployee.value = employee
  dialogTitle.value = '编辑数字员工'
  
  // 填充表单数据
  employeeForm.name = employee.name
  employeeForm.position = employee.position
  employeeForm.description = employee.description
  employeeForm.skills = employee.skills.map(skill => skill.skill_id)
  employeeForm.mcps = employee.mcps.map(mcp => mcp.mcp_id)
  
  dialogVisible.value = true
}

// 保存员工
const saveEmployee = async () => {
  if (!employeeFormRef.value) return
  
  try {
    await employeeFormRef.value.validate()
    
    if (editingEmployee.value) {
      // 更新员工
      await employeeAPI.update_employee(editingEmployee.value.agent_id, {
        name: employeeForm.name,
        description: employeeForm.description,
        skills: employeeForm.skills,
        mcps: employeeForm.mcps
      })
      ElMessage.success('更新数字员工成功')
    } else {
      // 添加员工
      await employeeAPI.create_employee({
        name: employeeForm.name,
        position: employeeForm.position,
        description: employeeForm.description,
        skills: employeeForm.skills,
        mcps: employeeForm.mcps
      })
      ElMessage.success('添加数字员工成功')
    }
    
    dialogVisible.value = false
    await loadEmployees()
  } catch (error) {
    console.error('保存数字员工失败:', error)
    ElMessage.error('保存数字员工失败')
  }
}

// 删除员工
const deleteEmployee = async (employee) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除数字员工 "${employee.name}" 吗？`,
      '删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'danger'
      }
    )
    
    await employeeAPI.delete_employee(employee.agent_id)
    ElMessage.success('删除数字员工成功')
    await loadEmployees()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除数字员工失败:', error)
      ElMessage.error('删除数字员工失败')
    }
  }
}

// 更新员工状态
const updateEmployeeStatus = async (employee) => {
  try {
    await employeeAPI.update_employee(employee.agent_id, {
      enabled: employee.enabled
    })
    ElMessage.success('更新状态成功')
  } catch (error) {
    console.error('更新状态失败:', error)
    employee.enabled = !employee.enabled // 恢复原状态
    ElMessage.error('更新状态失败')
  }
}

// 进入员工聊天页面
const enterEmployeeChat = (employee) => {
  // 跳转到聊天页面，只传递agentId参数
  router.push({
    path: '/chat',
    query: {
      agentId: employee.agent_id
    }
  })
}

// 初始化
onMounted(async () => {
  await loadEmployees()
  await loadAvailableSkillsAndMCPs()
})
</script>

<style lang="scss" scoped>
.ai-employees-container {
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
  
  .employee-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 24px;
    margin-top: 20px;
  }
  
  .employee-item {
    cursor: pointer;
    transition: all 0.3s ease;
    padding: 16px;
    border-radius: 12px;
    text-align: center;
    
    &:hover {
      background-color: #f5f7fa;
      transform: translateY(-2px);
    }
    
    .employee-avatar-container {
      display: flex;
      justify-content: center;
      margin-bottom: 12px;
      
      .el-avatar {
        border: 2px solid #ebeef5;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
      }
    }
    
    .employee-details {
      margin-bottom: 12px;
      
      .employee-name {
        margin: 0 0 4px;
        font-size: 16px;
        font-weight: 600;
        color: #303133;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      
      .employee-display-name {
        margin: 0;
        font-size: 13px;
        color: #606266;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }
    
    .employee-actions {
      display: flex;
      justify-content: center;
      gap: 8px;
      
      .el-button {
        font-size: 11px;
        padding: 0;
      }
    }
  }
}
</style>
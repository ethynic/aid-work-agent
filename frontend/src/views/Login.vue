<template>
  <div class="login-container">
    <el-card class="login-card">
      <template #header>
        <div class="login-title">
          <el-icon :size="48" class="login-icon"><Lock /></el-icon>
          <h2>爱定义智能体系统</h2>
        </div>
      </template>
      
      <el-form
        ref="loginFormRef"
        :model="loginForm"
        :rules="loginRules"
        label-position="top"
        @keydown.enter="handleLogin"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="请输入用户名"
            :prefix-icon="User"
            size="large"
            autocomplete="off"
          />
        </el-form-item>
        
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="请输入密码"
            :prefix-icon="Lock"
            size="large"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>
        
        <el-form-item>
          <el-button
            type="primary"
            :loading="isLoading"
            @click="handleLogin"
            size="large"
            block
          >
            <el-icon><CircleCheck /></el-icon>
            登录
          </el-button>
        </el-form-item>
        
        <div class="login-footer">
          <div class="login-tip">默认管理员账户：admin / admin123</div>
          <div class="login-links">
            <a href="#" @click.prevent="showForgotPassword">忘记密码？</a>
            <span class="login-separator">|</span>
            <a href="#" @click.prevent="showRegister">注册账户</a>
          </div>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, CircleCheck } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { authAPI } from '@/services/api'
import { useGlobalStore } from '@/stores'

const router = useRouter()
const globalStore = useGlobalStore()
const loginFormRef = ref(null)
const isLoading = ref(false)

const loginForm = reactive({
  username: '',
  password: ''
})

const loginRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度在 3 到 20 个字符', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 20, message: '密码长度在 6 到 20 个字符', trigger: 'blur' }
  ]
}

const handleLogin = async () => {
  if (!loginFormRef.value) return
  
  try {
    await loginFormRef.value.validate()
    isLoading.value = true
    
    const response = await authAPI.login(loginForm.username, loginForm.password)
    
    if (response.access_token) {
      // 保存token到全局状态管理
      globalStore.setToken(response.access_token)
      ElMessage.success('登录成功')
      // 跳转到仪表盘
      router.push('/dashboard')
    } else {
      ElMessage.error('登录失败，无效的响应')
    }
  } catch (error) {
    console.error('登录失败:', error)
    if (error.response) {
      ElMessage.error(`登录失败: ${error.response.data.detail || '用户名或密码错误'}`)
    } else {
      ElMessage.error(`登录失败: ${error.message || '网络错误'}`)
    }
  } finally {
    isLoading.value = false
  }
}

const showForgotPassword = () => {
  ElMessage.info('忘记密码功能开发中')
}

const showRegister = () => {
  ElMessage.info('注册功能开发中')
}
</script>

<style lang="scss" scoped>
.login-container {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
  
  .login-card {
    width: 100%;
    max-width: 400px;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
    backdrop-filter: blur(10px);
    
    .login-title {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 16px;
      margin-bottom: 20px;
      
      .login-icon {
        color: #667eea;
      }
      
      h2 {
        margin: 0;
        font-size: 24px;
        color: #303133;
      }
    }
    
    .login-footer {
      margin-top: 20px;
      text-align: center;
      
      .login-tip {
        margin-bottom: 10px;
        color: #909399;
        font-size: 13px;
      }
      
      .login-links {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 15px;
        color: #606266;
        font-size: 14px;
        
        a {
          color: #409eff;
          text-decoration: none;
          
          &:hover {
            color: #66b1ff;
          }
        }
        
        .login-separator {
          color: #dcdfe6;
        }
      }
    }
  }
}
</style>

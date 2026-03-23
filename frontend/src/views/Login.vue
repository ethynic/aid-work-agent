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
        <!-- 登录方式切换 -->
        <el-tabs v-model="loginMethod" class="login-tabs">
          <el-tab-pane label="手机号登录" name="phone">
            <el-form-item label="手机号" prop="phone">
              <el-input
                v-model="loginForm.phone"
                placeholder="请输入手机号"
                :prefix-icon="Phone"
                size="large"
                autocomplete="off"
              />
            </el-form-item>

            <el-form-item v-if="loginMethod === 'phone' && usePassword" label="密码" prop="password">
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

            <el-form-item v-else label="验证码" prop="code">
              <div class="code-input">
                <el-input
                  v-model="loginForm.code"
                  placeholder="请输入验证码"
                  :prefix-icon="Message"
                  size="large"
                  autocomplete="off"
                />
                <el-button
                  size="large"
                  :disabled="countdown > 0"
                  @click="sendCode"
                >
                  {{ countdown > 0 ? `${countdown}s` : '获取验证码' }}
                </el-button>
              </div>
            </el-form-item>

            <div class="login-toggle">
              <el-button link type="primary" @click="usePassword = !usePassword">
                {{ usePassword ? '使用验证码登录' : '使用密码登录' }}
              </el-button>
            </div>
          </el-tab-pane>

          <el-tab-pane label="账号登录" name="username">
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
          </el-tab-pane>
        </el-tabs>

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
          <div class="login-tip">演示账号：手机号 13800000000，验证码 888888</div>
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
import { ref, reactive, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, CircleCheck, Phone, Message } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { authAPI } from '@/services/api'
import { useGlobalStore } from '@/stores'

const router = useRouter()
const globalStore = useGlobalStore()
const loginFormRef = ref(null)
const isLoading = ref(false)
const loginMethod = ref('phone')
const usePassword = ref(false)
const countdown = ref(0)
let countdownTimer = null

const loginForm = reactive({
  phone: '',
  code: '',
  username: '',
  password: ''
})

const loginRules = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的手机号', trigger: 'blur' }
  ],
  code: [
    { required: true, message: '请输入验证码', trigger: 'blur' },
    { len: 6, message: '验证码为6位', trigger: 'blur' }
  ],
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度在 3 到 20 个字符', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 20, message: '密码长度在 6 到 20 个字符', trigger: 'blur' }
  ]
}

const sendCode = async () => {
  if (!loginForm.phone) {
    ElMessage.warning('请先输入手机号')
    return
  }

  try {
    const response = await authAPI.sendCode(loginForm.phone)
    if (response.success) {
      ElMessage.success('验证码已发送')
      countdown.value = 60
      countdownTimer = setInterval(() => {
        countdown.value--
        if (countdown.value <= 0) {
          clearInterval(countdownTimer)
        }
      }, 1000)
    } else {
      ElMessage.error(response.message || '发送失败')
    }
  } catch (error) {
    console.error('发送验证码失败:', error)
    ElMessage.error('发送失败，请重试')
  }
}

const handleLogin = async () => {
  if (!loginFormRef.value) return

  try {
    await loginFormRef.value.validate()
    isLoading.value = true

    let response

    if (loginMethod.value === 'phone') {
      // 手机号登录
      if (usePassword.value) {
        // 密码登录
        response = await authAPI.login(loginForm.phone, loginForm.password)
      } else {
        // 验证码登录
        response = await authAPI.phoneCodeLogin(loginForm.phone, loginForm.code)
      }
    } else {
      // 账号登录（使用手机号作为账号）
      ElMessage.info('账号登录功能开发中，请使用手机号登录')
      return
    }

    if (response.success && response.token) {
      // 保存token到全局状态管理
      globalStore.setToken(response.token)
      // 保存用户信息
      if (response.user) {
        globalStore.setUser(response.user)
      }
      ElMessage.success('登录成功')
      // 跳转到仪表盘
      router.push('/dashboard')
    } else {
      ElMessage.error(response.message || '登录失败')
    }
  } catch (error) {
    console.error('登录失败:', error)
    if (error.response) {
      ElMessage.error(error.response.data?.message || error.response.data?.detail || '登录失败')
    } else {
      ElMessage.error(error.message || '网络错误')
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

onUnmounted(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
  }
})
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
    max-width: 420px;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
    backdrop-filter: blur(10px);

    .login-title {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 16px;
      margin-bottom: 10px;

      .login-icon {
        color: #667eea;
      }

      h2 {
        margin: 0;
        font-size: 24px;
        color: #303133;
      }
    }

    .login-tabs {
      margin-bottom: 10px;
    }

    .code-input {
      display: flex;
      gap: 10px;

      .el-input {
        flex: 1;
      }
    }

    .login-toggle {
      text-align: right;
      margin-bottom: 15px;
    }

    .login-footer {
      margin-top: 15px;
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

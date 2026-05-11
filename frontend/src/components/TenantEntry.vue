<template>
  <div class="tenant-entry-page">
    <div class="container">
      <h1>租户登录</h1>
      <p class="subtitle">请输入您的租户代码以继续</p>

      <form @submit.prevent="handleSubmit" id="tenantForm">
        <div class="form-group">
          <label for="tenantCode">租户代码</label>
          <input type="text" id="tenantCode" v-model="tenantCode"
                 placeholder="例如：ALIBB" maxlength="8" autocomplete="off" autofocus
                 @input="validateFormat">
          <div id="formatError" class="error" v-if="formatErrorVisible">
            请输入4-8位字母数字组合
          </div>
        </div>
        <button type="submit" id="submitBtn" :disabled="isSubmitting || formatErrorVisible">
          {{ isSubmitting ? '验证中...' : '进入租户' }}
        </button>
      </form>

      <div id="result" class="result" :class="resultClass" v-if="resultMessage">
        <div v-html="resultMessage"></div>
        <a v-if="showRedirectLink" href="#" id="redirectLink" class="redirect-link" @click.prevent="redirect">
          如果未自动跳转，请点击此处
        </a>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
const tenantCode = ref('')
const isSubmitting = ref(false)
const formatErrorVisible = ref(false)
const resultMessage = ref('')
const resultType = ref<'success'|'error'>('error')
const redirectUrl = ref('')

// 计算属性
const resultClass = computed(() => {
  return resultType.value === 'success' ? 'success' : 'error-result'
})

const showRedirectLink = computed(() => {
  return resultType.value === 'success' && redirectUrl.value
})

// 格式验证：4-8位字母数字
function validateFormat(): boolean {
  const code = tenantCode.value.trim()
  const isValid = /^[A-Za-z0-9]{4,8}$/.test(code)
  formatErrorVisible.value = code !== '' && !isValid
  return isValid
}

// 表单提交
async function handleSubmit() {
  const code = tenantCode.value.trim().toUpperCase()

  if (!validateFormat()) {
    showError('请输入有效的租户代码（4-8位字母数字）')
    return
  }

  isSubmitting.value = true
  resultMessage.value = ''

  try {
    const response = await fetch('/api/tenant/enter', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_code: code }),
    })

    const data = await response.json()

    if (data.success && data.redirect_url) {
      redirectUrl.value = data.redirect_url
      showSuccess('验证成功，正在跳转到租户...')
      // 2秒后自动重定向
      setTimeout(() => {
        redirect()
      }, 2000)
    } else {
      showError(data.error || '租户代码无效或租户不可用')
    }
  } catch (err) {
    showError('网络错误，请稍后重试')
  } finally {
    isSubmitting.value = false
  }
}

// 显示错误消息
function showError(message: string) {
  resultMessage.value = `<strong>错误：</strong> ${message}`
  resultType.value = 'error'
}

// 显示成功消息
function showSuccess(message: string) {
  resultMessage.value = `<strong>成功：</strong> ${message}`
  resultType.value = 'success'
}

// 手动重定向
function redirect() {
  if (redirectUrl.value) {
    window.location.href = redirectUrl.value
  }
}
</script>

<style scoped>
.tenant-entry-page {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 20px;
}

.container {
  background: white;
  border-radius: 12px;
  padding: 40px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
  max-width: 400px;
  width: 100%;
  text-align: center;
}

h1 {
  color: #333;
  margin-bottom: 10px;
  font-size: 24px;
}

.subtitle {
  color: #666;
  margin-bottom: 30px;
  font-size: 14px;
}

.form-group {
  margin-bottom: 20px;
  text-align: left;
}

label {
  display: block;
  margin-bottom: 6px;
  color: #555;
  font-weight: 500;
}

input {
  width: 100%;
  padding: 12px 16px;
  border: 2px solid #ddd;
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 0.3s;
  box-sizing: border-box;
}

input:focus {
  outline: none;
  border-color: #667eea;
}

.error {
  color: #e74c3c;
  font-size: 14px;
  margin-top: 5px;
}

button {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 14px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
  transition: transform 0.2s, box-shadow 0.2s;
}

button:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.result {
  margin-top: 20px;
  padding: 12px;
  border-radius: 8px;
  display: block;
}

.success {
  background: #d4edda;
  color: #155724;
}

.error-result {
  background: #f8d7da;
  color: #721c24;
}

.redirect-link {
  display: inline-block;
  margin-top: 10px;
  color: #667eea;
  text-decoration: none;
  font-weight: 500;
}

.redirect-link:hover {
  text-decoration: underline;
}
</style>
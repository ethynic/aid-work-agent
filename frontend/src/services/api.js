import axios from 'axios'
import { useApiStore, useGlobalStore } from '@/stores'

// 本项目后端 API 基础路径（同源）
const API_BASE_URL = '/api'

// 导出基础URL（不含 /api 后缀）
export const API_BASE = window.location.origin

export { API_BASE_URL }
const globalStore = useGlobalStore()
const apiStore = useApiStore()

// 创建axios实例
const createApiInstance = () => {
  const api = axios.create({
    baseURL: API_BASE_URL,
    timeout: globalStore.config.timeout,
    headers: {
      'Content-Type': 'application/json'
    }
  })

  // 请求拦截器
  api.interceptors.request.use(
    (config) => {
      // 添加认证token
      const token = globalStore.token
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }

      // 添加请求ID
      config.headers['X-Request-ID'] = crypto.randomUUID()

      return config
    },
    (error) => {
      globalStore.setError(error)
      return Promise.reject(error)
    }
  )

  // 响应拦截器
  api.interceptors.response.use(
    (response) => {
      // 对于文本响应，返回整个response对象
      if (response.config.responseType === 'text') {
        return response
      }

      // 缓存成功响应
      if (response.config.method === 'get') {
        const endpoint = response.config.url.replace(API_BASE_URL, '')
        const params = response.config.params
        apiStore.cacheApiResponse(endpoint, params, response.data)
      }
      return response.data
    },
    (error) => {
      // 处理401错误
      if (error.response?.status === 401) {
        globalStore.logout()
        window.location.href = '/login'
      }

      globalStore.setError(error)
      return Promise.reject(error)
    }
  )

  return api
}

const api = createApiInstance()

// 带缓存的请求函数
const cachedRequest = async (method, url, config = {}) => {
  // 仅对GET请求使用缓存
  if (method === 'get') {
    const cached = apiStore.getCachedApiResponse(url, config.params)
    if (cached) {
      return cached
    }
  }

  // 检查是否有相同请求正在进行
  const requestKey = `${url}_${JSON.stringify(config.params || {})}`
  if (apiStore.isRequestInProgress(url, config.params)) {
    return apiStore.getRequest(url, config.params)
  }

  // 创建新请求
  let request = api[method](url, config)

  // 添加到请求队列
  apiStore.addRequestToQueue(url, config.params, request)

  try {
    return await request
  } finally {
    // 从队列移除
    apiStore.removeRequestFromQueue(url, config.params)
  }
}

// 重试函数
const retryRequest = async (fn, retries = 3, delay = 1000) => {
  try {
    return await fn()
  } catch (error) {
    if (retries > 0 && error.message.includes('Network Error')) {
      await new Promise(resolve => setTimeout(resolve, delay))
      return retryRequest(fn, retries - 1, delay * 2)
    }
    throw error
  }
}

// ==================== 认证 API ====================
export const authAPI = {
  // 手机号密码登录
  login: (phone, password) =>
    retryRequest(() => api.post('/auth/phone/login', { phone, password })),

  // 手机号验证码登录
  phoneCodeLogin: (phone, code) =>
    retryRequest(() => api.post('/auth/phone/code-login', { phone, code })),

  // 发送短信验证码
  sendCode: (phone) =>
    api.post('/auth/phone/send-code', { phone }),

  // 用户注册
  register: (phone, password, code) =>
    api.post('/auth/register', { phone, password, code }),

  // 获取当前用户信息
  getProfile: () => cachedRequest('get', '/auth/me'),

  // 登出
  logout: () => api.post('/auth/logout'),

  // 绑定手机号
  bindPhone: (userId, phone, code) =>
    api.post('/auth/bind-phone', { user_id: userId, phone, code })
}

// ==================== 聊天 API ====================
export const chatAPI = {
  // 普通聊天
  chat: (message, sessionId, userId = 'web_user') => {
    return api.post('/chat', {
      message,
      session_id: sessionId,
      user_id: userId
    })
  },

  // 流式聊天（SSE）
  chatStream: (message, sessionId, files = null) => {
    const requestData = {
      message,
      session_id: sessionId
    }
    if (files) {
      requestData.files = files
    }
    return fetch(`${API_BASE_URL}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${globalStore.token}`
      },
      body: JSON.stringify(requestData)
    })
  },

  // 获取聊天历史
  getHistory: (sessionId) => cachedRequest('get', `/chat/history/${sessionId}`),

  // 删除会话
  deleteSession: (sessionId) => api.delete(`/chat/session/${sessionId}`)
}

// ==================== 工具 API ====================
export const toolsAPI = {
  // 获取可用工具列表
  listTools: () => cachedRequest('get', '/tools')
}

// ==================== 会话 API ====================
export const sessionAPI = {
  // 获取会话列表
  listSessions: () => cachedRequest('get', '/sessions'),

  // 创建会话
  createSession: (data) => api.post('/sessions', data),

  // 获取会话详情
  getSession: (sessionId) => cachedRequest('get', `/sessions/${sessionId}`),

  // 更新会话
  updateSession: (sessionId, data) => api.patch(`/sessions/${sessionId}`, data),

  // 删除会话
  deleteSession: (sessionId) => api.delete(`/sessions/${sessionId}`),

  // 获取会话消息
  getMessages: (sessionId) => cachedRequest('get', `/sessions/${sessionId}/messages`),

  // 添加消息
  addMessage: (sessionId, role, content, metadata) =>
    api.post(`/sessions/${sessionId}/messages`, { role, content, metadata }),

  // 获取会话上下文
  getContext: (sessionId) => cachedRequest('get', `/sessions/${sessionId}/context`)
}

// ==================== 文件上传 ====================
export const uploadAPI = {
  upload_file: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  },

  upload_multiple_files: (files) => {
    const formData = new FormData()
    files.forEach(file => {
      formData.append('files', file)
    })
    return api.post('/upload_multiple', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  }
}

// ==================== 以下为兼容旧版本的API ====================
// 保留原有API名称以便现有代码兼容

export const llmAPI = {
  // 使用 chatAPI 进行流式聊天
  chat_stream: chatAPI.chatStream,
  agent_chat_stream: chatAPI.chatStream,

  // 获取工具列表
  get_providers: toolsAPI.listTools
}

export const employeeAPI = {
  // 本项目使用主智能体，暂不提供多智能体管理接口
  get_employees: () => Promise.resolve({ agents: [] }),
  get_employee: () => Promise.resolve(null),
  get_skill_library: () => Promise.resolve({ skills: [] }),
  get_mcp_library: () => Promise.resolve({ mcps: [] })
}

// ==================== 工具函数 ====================
export const apiUtils = {
  // 批量请求
  batchRequest: async (requests) => {
    try {
      const results = await Promise.allSettled(requests)
      return results.map(result =>
        result.status === 'fulfilled' ? result.value : null
      )
    } catch (error) {
      globalStore.setError(error)
      return []
    }
  },

  // 延迟请求
  debouncedRequest: (fn, delay = 300) => {
    let timeout
    return (...args) => {
      clearTimeout(timeout)
      return new Promise((resolve) => {
        timeout = setTimeout(async () => {
          const result = await fn(...args)
          resolve(result)
        }, delay)
      })
    }
  },

  // 限流请求
  throttledRequest: (fn, limit = 1000) => {
    let lastCall = 0
    return (...args) => {
      const now = Date.now()
      if (now - lastCall < limit) {
        return Promise.resolve(null)
      }
      lastCall = now
      return fn(...args)
    }
  }
}

// 保留旧API导出（兼容现有代码）
export const mcpAPI = {
  list_tools: () => Promise.resolve({ tools: [] })
}

export const databaseAPI = {
  list_connections: () => Promise.resolve({ connections: [] })
}

export const taskAPI = {
  list_tasks: () => Promise.resolve({ tasks: [] })
}

export const imAPI = {
  get_apps: () => Promise.resolve({ apps: [] })
}

export const knowledgeAPI = {
  list: () => Promise.resolve({ list: [] })
}

export default api

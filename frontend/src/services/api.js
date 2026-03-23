import axios from 'axios'
import { useApiStore, useGlobalStore } from '@/stores'

//前端网址如有端口号，将端口号替换为8002，然后拼接 /api/v1, 例如前端网址为 http://localhost:8082/ ，那么后端接口网址为 http://localhost:8002/api/v1
//前端网址如没有端口号，直接拼接 /api/v1，例如前端网址为 https://aicompany.aidingyi.cn/ ，那么后端接口网址为 https://aicompany.aidingyi.cn/api/v1
const API_BASE_URL = window.location.port
  ? `${window.location.protocol}//${window.location.hostname}:8002/api/v1`
  : `${window.location.protocol}//${window.location.hostname}/api/v1`;

// 导出基础URL（不含 /api/v1 后缀）
export const API_BASE = window.location.port
  ? `${window.location.protocol}//${window.location.hostname}:8002`
  : `${window.location.protocol}//${window.location.hostname}`;

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

export const authAPI = {
  login: (username, password) =>
    retryRequest(() => api.post('/auth/token', new URLSearchParams({
      username,
      password
    }), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })),

  register: (data) => api.post('/auth/register', data),

  get_profile: () => cachedRequest('get', '/auth/me'),

  refresh_token: (token) => api.post('/auth/refresh', { token }),

  change_password: (oldPassword, newPassword) =>
    api.post('/auth/change_password', { old_password: oldPassword, new_password: newPassword })
}

export const llmAPI = {
  get_providers: () => cachedRequest('get', '/llm/providers'),

  chat: (data) => api.post('/llm/chat', data),

  chat_stream: (data) => {
    return fetch(`${API_BASE_URL}/llm/chat_stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${globalStore.token}`
      },
      body: JSON.stringify(data)
    })
  },

  config: (data) => api.post('/llm/config', data),

  get_config: () => cachedRequest('get', '/llm/config'),

  delete_config: (modelName) => api.delete(`/llm/config/${modelName}`),

  analyze: (query, dataContext, model) =>
    api.post('/llm/analyze', { query, data_context: dataContext, model }),

  // 智能体相关API
  agents_chat: (data) => api.post('/llm/agents_chat', data),

  agent_chat_stream: (data) => {
    return fetch(`${API_BASE_URL}/llm/agent_chat_stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${globalStore.token}`
      },
      body: JSON.stringify(data)
    })
  },

  agent_learn_interface: (interfaceInfo) => api.post('/llm/agent/learn/interface', interfaceInfo),

  agent_learn_tool: (toolInfo) => api.post('/llm/agent/learn/tool', toolInfo)
}

export const mcpAPI = {
  list_tools: (category) => cachedRequest('get', '/mcp/tools', { params: { category } }),

  get_tool_info: (toolName) => cachedRequest('get', `/mcp/tools/${toolName}`),

  register_tool: (data) => api.post('/mcp/tools', data),

  unregister_tool: (toolName) => api.delete(`/mcp/tools/${toolName}`),

  call_tool: (data) => api.post('/mcp/call', data),

  list_servers: () => cachedRequest('get', '/mcp/servers'),

  configure_server: (data) => api.post('/mcp/servers', data),

  start_server: (serverName) => api.post(`/mcp/servers/${serverName}/start`),

  stop_server: (serverName) => api.post(`/mcp/servers/${serverName}/stop`),

  generate_image: (prompt, tool, options) =>
    api.post('/mcp/image/generate', { prompt, tool_name: tool, options }),

  generate_video: (prompt, tool, options) =>
    api.post('/mcp/video/generate', { prompt, tool_name: tool, options }),

  health_check: () => cachedRequest('get', '/mcp/health')
}

export const databaseAPI = {
  list_connections: () => cachedRequest('get', '/database/connections'),

  add_connection: (data) => api.post('/database/connections', data),

  remove_connection: (name) => api.delete(`/database/connections/${name}`),

  test_connection: (name) => api.post(`/database/connections/${name}/test`),

  list_tables: (connectionName) => cachedRequest('get', `/database/${connectionName}/tables`),

  get_table_info: (connectionName, tableName) =>
    cachedRequest('get', `/database/${connectionName}/tables/${tableName}`),

  get_columns: (connectionName, tableName) =>
    cachedRequest('get', `/database/${connectionName}/tables/${tableName}/columns`),

  get_schema: (connectionName) => cachedRequest('get', `/database/${connectionName}/schema`),

  get_relationships: (connectionName) =>
    cachedRequest('get', `/database/${connectionName}/relationships`),

  execute_query: (connectionName, data) =>
    api.post(`/database/${connectionName}/query`, data),

  generate_sql: (connectionName, data) =>
    api.post(`/database/${connectionName}/generate_sql`, data),

  analyze_database: (connectionName) => cachedRequest('get', `/database/${connectionName}/analyze`),

  get_sample_data: (connectionName, tableName, limit) =>
    cachedRequest('get', `/database/${connectionName}/tables/${tableName}/sample`, { params: { limit } }),

  generate_erd: (connectionName) => cachedRequest('get', `/database/${connectionName}/erd`),

  get_metadata: (connectionName) => cachedRequest('get', `/database/${connectionName}/metadata`)
}

export const taskAPI = {
  list_tasks: (status, limit) => cachedRequest('get', '/tasks/', { params: { status, limit } }),

  get_task: (taskId) => cachedRequest('get', `/tasks/${taskId}`),

  create_task: (data) => api.post('/tasks/', data),

  cancel_task: (taskId) => api.post(`/tasks/${taskId}/cancel`),

  retry_task: (taskId) => api.post(`/tasks/${taskId}/retry`),

  delete_task: (taskId) => api.delete(`/tasks/${taskId}`),

  get_task_result: (taskId) => cachedRequest('get', `/tasks/${taskId}/result`),

  create_batch_tasks: (tasks) => api.post('/tasks/batch', tasks),

  get_queue_status: () => cachedRequest('get', '/tasks/queues'),

  get_worker_status: () => cachedRequest('get', '/tasks/workers'),

  create_workflow: (name, tasks) => api.post('/tasks/workflow', { name, tasks }),

  list_workflows: () => cachedRequest('get', '/tasks/workflows'),

  get_workflow: (workflowId) => cachedRequest('get', `/tasks/workflows/${workflowId}`),

  schedule_task: (taskDef, cronExpression) =>
    api.post('/tasks/schedule', { ...taskDef, cron_expression: cronExpression }),

  list_scheduled_jobs: () => cachedRequest('get', '/tasks/scheduled'),

  remove_scheduled_job: (jobId) => api.delete(`/tasks/scheduled/${jobId}`),

  get_statistics: () => cachedRequest('get', '/tasks/statistics')
}

// 导出工具函数
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

export const employeeAPI = {
  get_employees: () => cachedRequest('get', '/llm/agents'),

  get_employee: (agentId) => cachedRequest('get', `/llm/agents/${agentId}`),

  create_employee: (data) => api.post('/llm/agents', data),

  update_employee: (agentId, data) => api.put(`/llm/agents/${agentId}`, data),

  delete_employee: (agentId) => api.delete(`/llm/agents/${agentId}`),

  get_skill_library: () => cachedRequest('get', '/llm/skills'),

  get_mcp_library: () => cachedRequest('get', '/llm/mcps')
}

export const imAPI = {
  get_apps: () => cachedRequest('get', '/im/apps'),

  get_app: (configId) => cachedRequest('get', `/im/apps/${configId}`),

  create_app: (data) => api.post('/im/apps', data),

  update_app: (configId, data) => api.put(`/im/apps/${configId}`, data),

  delete_app: (configId) => api.delete(`/im/apps/${configId}`),

  reload_apps: () => api.post('/im/apps/reload')
}

export const knowledgeAPI = {
  list: (params) => api.get('/knowledge/', {
    params: {
      page: params.page,
      page_size: params.page_size,
      keyword: params.keyword
    }
  }),

  get: (id) => api.get(`/knowledge/${id}`),

  create: (data) => api.post('/knowledge/', data),

  update: (id, data) => api.put(`/knowledge/${id}`, data),

  delete: (id) => api.delete(`/knowledge/${id}`),

  search: (query, limit) => api.get('/knowledge/search/results', {
    params: { q: query, limit }
  })
}

export default api

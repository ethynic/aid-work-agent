import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// 全局状态管理
export const useGlobalStore = defineStore('global', () => {
  // 应用状态
  const isLoading = ref(false)
  const loadingMessage = ref('')
  const isReady = ref(false)
  const error = ref(null)
  
  // 用户状态
  const user = ref(null)
  const token = ref(localStorage.getItem('token') || null)
  const isAuthenticated = computed(() => !!token.value)
  
  // 应用配置
  //前端网址如有端口号，将端口号替换为8002，然后拼接 /api/v1, 例如前端网址为 http://localhost:8082/ ，那么后端接口网址为 http://localhost:8002/api/v1
  //前端网址如没有端口号，直接拼接 /api/v1，例如前端网址为 https://aicompany.aidingyi.cn/ ，那么后端接口网址为 https://aicompany.aidingyi.cn/api/v1
  const config = ref({
    apiBaseUrl: window.location.port
      ? `${window.location.protocol}//${window.location.hostname}:8002/api/v1`
      : `${window.location.protocol}//${window.location.hostname}/api/v1`,
    timeout: 300000,  // 5分钟超时，足够智能体处理文件分析
    theme: 'light'
  })
  
  // 缓存数据
  const cache = ref(new Map())
  
  // 历史记录
  const history = ref([])
  
  // 设置加载状态
  const setLoading = (loading, message = '') => {
    isLoading.value = loading
    loadingMessage.value = message
  }
  
  // 设置错误
  const setError = (err) => {
    error.value = err
  }
  
  // 清除错误
  const clearError = () => {
    error.value = null
  }
  
  // 设置用户
  const setUser = (userData) => {
    user.value = userData
  }
  
  // 设置token
  const setToken = (newToken) => {
    token.value = newToken
    if (newToken) {
      localStorage.setItem('token', newToken)
    } else {
      localStorage.removeItem('token')
    }
  }
  
  // 登出
  const logout = () => {
    setToken(null)
    setUser(null)
    clearCache()
  }
  
  // 添加到缓存
  const addToCache = (key, value, expiration = 3600000) => { // 默认1小时过期
    const item = {
      value,
      timestamp: Date.now(),
      expiration
    }
    cache.value.set(key, item)
  }
  
  // 从缓存获取
  const getFromCache = (key) => {
    const item = cache.value.get(key)
    if (!item) return null
    
    // 检查是否过期
    if (Date.now() - item.timestamp > item.expiration) {
      cache.value.delete(key)
      return null
    }
    
    return item.value
  }
  
  // 清除缓存
  const clearCache = (key = null) => {
    if (key) {
      cache.value.delete(key)
    } else {
      cache.value.clear()
    }
  }
  
  // 添加到历史记录
  const addToHistory = (item) => {
    history.value.unshift({
      ...item,
      id: Date.now(),
      timestamp: Date.now()
    })
    
    // 限制历史记录长度
    if (history.value.length > 50) {
      history.value = history.value.slice(0, 50)
    }
  }
  
  // 清除历史记录
  const clearHistory = () => {
    history.value = []
  }
  
  return {
    // 状态
    isLoading,
    loadingMessage,
    isReady,
    error,
    user,
    token,
    isAuthenticated,
    config,
    cache,
    history,
    
    // 方法
    setLoading,
    setError,
    clearError,
    setUser,
    setToken,
    logout,
    addToCache,
    getFromCache,
    clearCache,
    addToHistory,
    clearHistory
  }
})

// API状态管理
export const useApiStore = defineStore('api', () => {
  const globalStore = useGlobalStore()
  
  // API缓存
  const apiCache = ref(new Map())
  
  // 请求队列
  const requestQueue = ref(new Map())
  
  // 缓存API响应
  const cacheApiResponse = (endpoint, params, response) => {
    const key = `${endpoint}_${JSON.stringify(params || {})}`
    globalStore.addToCache(key, response)
  }
  
  // 获取缓存的API响应
  const getCachedApiResponse = (endpoint, params) => {
    const key = `${endpoint}_${JSON.stringify(params || {})}`
    return globalStore.getFromCache(key)
  }
  
  // 检查请求是否正在进行中
  const isRequestInProgress = (endpoint, params) => {
    const key = `${endpoint}_${JSON.stringify(params || {})}`
    return requestQueue.value.has(key)
  }
  
  // 添加请求到队列
  const addRequestToQueue = (endpoint, params, promise) => {
    const key = `${endpoint}_${JSON.stringify(params || {})}`
    requestQueue.value.set(key, promise)
  }
  
  // 从队列移除请求
  const removeRequestFromQueue = (endpoint, params) => {
    const key = `${endpoint}_${JSON.stringify(params || {})}`
    requestQueue.value.delete(key)
  }
  
  // 获取请求
  const getRequest = (endpoint, params) => {
    const key = `${endpoint}_${JSON.stringify(params || {})}`
    return requestQueue.value.get(key)
  }

  // 清除特定端点的缓存（通过前缀匹配）
  const clearApiCache = (endpointPrefix) => {
    const keysToDelete = []
    for (const key of globalStore.cache.value.keys()) {
      if (key.startsWith(endpointPrefix)) {
        keysToDelete.push(key)
      }
    }
    keysToDelete.forEach(key => globalStore.cache.value.delete(key))
  }

  return {
    apiCache,
    requestQueue,
    cacheApiResponse,
    getCachedApiResponse,
    isRequestInProgress,
    addRequestToQueue,
    removeRequestFromQueue,
    getRequest,
    clearApiCache
  }
})

// 模型配置存储
export const useModelStore = defineStore('model', () => {
  const models = ref([])
  const activeModel = ref(null)
  
  const setModels = (modelList) => {
    models.value = modelList
  }
  
  const setActiveModel = (model) => {
    activeModel.value = model
  }
  
  return {
    models,
    activeModel,
    setModels,
    setActiveModel
  }
})

import { createRouter, createWebHistory } from 'vue-router'

// 预加载组件
const preloadComponents = () => {
  // 预加载核心组件
  import('@/views/Dashboard.vue')
  import('@/views/Chat.vue')
}

const routes = [
  {
    path: '/',
    redirect: '/login'
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', requiresAuth: false, keepAlive: false }
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '仪表盘', requiresAuth: true, keepAlive: true, cache: true }
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/Chat.vue'),
    meta: { title: '智能对话', requiresAuth: true, keepAlive: true, cache: true }
  },
  {
    path: '/tools/image',
    name: 'ImageGeneration',
    component: () => import('@/views/ImageGeneration.vue'),
    meta: { title: '图像生成', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/tools/video',
    name: 'VideoGeneration',
    component: () => import('@/views/VideoGeneration.vue'),
    meta: { title: '视频生成', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/tools/mcp',
    name: 'MCPTools',
    component: () => import('@/views/MCPTools.vue'),
    meta: { title: 'MCP工具', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/database/connections',
    name: 'DatabaseConnections',
    component: () => import('@/views/DatabaseConnections.vue'),
    meta: { title: '连接管理', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/database/schema',
    name: 'DatabaseSchema',
    component: () => import('@/views/DatabaseSchema.vue'),
    meta: { title: '结构分析', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/database/query',
    name: 'DatabaseQuery',
    component: () => import('@/views/DatabaseQuery.vue'),
    meta: { title: 'SQL查询', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/models',
    name: 'ModelConfig',
    component: () => import('@/views/ModelConfig.vue'),
    meta: { title: '模型配置', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/tasks',
    name: 'TaskManagement',
    component: () => import('@/views/TaskManagement.vue'),
    meta: { title: '任务管理', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/views/Settings.vue'),
    meta: { title: '系统设置', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/ai-employees',
    name: 'AIEmployees',
    component: () => import('@/views/AIEmployees.vue'),
    meta: { title: 'AI数字员工', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/im-apps',
    name: 'IMApps',
    component: () => import('@/views/IMApps.vue'),
    meta: { title: 'IM应用配置', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/knowledge',
    name: 'KnowledgeBase',
    component: () => import('@/views/KnowledgeBase.vue'),
    meta: { title: '企业知识库', requiresAuth: true, keepAlive: false }
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/Login.vue'),
    meta: { title: '页面不存在', requiresAuth: false }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) {
      return savedPosition
    } else {
      return { top: 0 }
    }
  }
})

// 路由守卫
router.beforeEach((to, from, next) => {
  // 设置页面标题
  document.title = `${to.meta.title || '企业智能体'} - 爱定义智能体系统`
  
  // 检查是否需要认证
  if (to.meta.requiresAuth !== false) {
    // 获取localStorage中的token
    const token = localStorage.getItem('token')
    
    if (!token) {
      // 没有token，跳转到登录页面
      next('/login')
      return
    }
  }
  
  next()
})

// 路由加载完成后预加载核心组件
router.isReady().then(() => {
  preloadComponents()
})

export default router

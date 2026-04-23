import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Toast from 'vue-toastification'
import 'vue-toastification/dist/index.css'
import App from './App.vue'
import './style.css'
import { useTheme } from './composables/useTheme'
import { useDemoAuth } from './composables/useDemoAuth'
import { useTenantAuth } from './composables/useTenantAuth'

// 客户信息页面
import CustomerInfo from './components/CustomerInfo.vue'

// 定时任务管理页面
import ScheduledTasks from './components/ScheduledTasks.vue'

// 知识库管理页面
import KnowledgeBase from './components/KnowledgeBase.vue'

// 初始化主题
const { initTheme } = useTheme()
initTheme()

// 创建路由
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: () => import('./components/ChatContainer.vue')
    },
    {
      path: '/chat/:subagent',
      name: 'chat-subagent',
      component: () => import('./components/ChatContainer.vue')
    },
    {
      path: '/customer-info',
      name: 'customer-info',
      component: CustomerInfo
    },
    {
      path: '/scheduled-tasks',
      name: 'scheduled-tasks',
      component: ScheduledTasks
    },
    {
      path: '/knowledge-base',
      name: 'knowledge-base',
      component: KnowledgeBase
    },
    {
      path: '/admin/subagents',
      name: 'admin-subagents',
      component: () => import('./components/DigitalEmployeeManager.vue')
    },
    {
      path: '/all-sessions',
      name: 'all-sessions',
      component: () => import('./components/AllSessions.vue')
    },
    // SaaS 租户管理 Portal（仅平台管理员）
    {
      path: '/portal/login',
      name: 'portal-login',
      component: () => import('./components/saas/TenantLogin.vue')
    },
    {
      path: '/portal/reset-password',
      name: 'portal-reset-password',
      component: () => import('./components/saas/ResetPassword.vue')
    },
    {
      path: '/portal',
      component: () => import('./components/saas/PortalLayout.vue'),
      children: [
        { path: '', name: 'portal-dashboard', component: () => import('./components/saas/TenantDashboard.vue') },
        { path: 'tenants', name: 'portal-tenants', component: () => import('./components/saas/TenantMgmt.vue') },
        { path: 'subagents', name: 'portal-subagents', component: () => import('./components/DigitalEmployeeManager.vue') },
      ]
    },
    // 租户入口 /t/:tenant_id（所有用户）
    {
      path: '/t/:tenant_id',
      component: () => import('./components/saas/PortalLayout.vue'),
      children: [
        { path: '', name: 'tenant-dashboard', component: () => import('./components/saas/TenantDashboard.vue') },
        { path: 'login', name: 'tenant-login', component: () => import('./components/saas/TenantLogin.vue') },
        { path: 'users', name: 'tenant-users', component: () => import('./components/saas/TenantUserManager.vue') },
        { path: 'knowledge', name: 'tenant-knowledge', component: () => import('./components/KnowledgeBase.vue') },
        { path: 'channels', name: 'tenant-channels', component: () => import('./components/saas/ChannelConfig.vue') },
        { path: 'settings', name: 'tenant-settings', component: () => import('./components/saas/TenantSettings.vue') },
        { path: 'chat', name: 'tenant-chat', component: () => import('./components/ChatContainer.vue') },
      ]
    }
  ]
})

// 全局路由守卫：确保 auth 状态在任何页面刷新时都能初始化
router.beforeEach(async () => {
  const path = window.location.pathname
  if (path.startsWith('/t/') || path.startsWith('/portal')) {
    const { init, isInitialized } = useTenantAuth()
    if (!isInitialized.value) {
      await init()
    }
  } else {
    const { init, isInitialized } = useDemoAuth()
    if (!isInitialized.value) {
      await init()
    }
  }
})

createApp(App).use(router).use(Toast, {
  position: 'top-center',
  timeout: 3000,
  closeOnClick: true,
  pauseOnFocusLoss: true,
  pauseOnHover: true,
  draggable: true,
  draggablePercent: 0.6,
  showCloseButtonOnHover: false,
  hideProgressBar: false,
  closeButton: 'button',
  icon: true,
  rtl: false,
  transition: {
    enter: 'fade-enter',
    exit: 'fade-exit',
    move: 'fade-move',
  }
}).mount('#app')

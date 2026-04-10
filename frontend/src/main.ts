import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'
import { useTheme } from './composables/useTheme'

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
    // SaaS 租户管理 Portal
    {
      path: '/portal/login',
      name: 'portal-login',
      component: () => import('./components/saas/TenantLogin.vue')
    },
    {
      path: '/portal',
      component: () => import('./components/saas/PortalLayout.vue'),
      children: [
        { path: '', name: 'portal-dashboard', component: () => import('./components/saas/TenantDashboard.vue') },
        { path: 'instances', name: 'portal-instances', component: () => import('./components/saas/InstanceManager.vue') },
        { path: 'channels', name: 'portal-channels', component: () => import('./components/saas/ChannelConfig.vue') },
        { path: 'users', name: 'portal-users', component: () => import('./components/saas/TenantUserManager.vue') },
        { path: 'skills', name: 'portal-skills', component: () => import('./components/saas/SkillManager.vue') },
        { path: 'reports', name: 'portal-reports', component: () => import('./components/saas/UsageReports.vue') },
        { path: 'billing', name: 'portal-billing', component: () => import('./components/saas/BillingView.vue') },
        { path: 'settings', name: 'portal-settings', component: () => import('./components/saas/TenantSettings.vue') },
      ]
    }
  ]
})

createApp(App).use(router).mount('#app')

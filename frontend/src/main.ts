import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import Toast from 'vue-toastification'
import 'vue-toastification/dist/index.css'
import App from './App.vue'
import './style.css'
import { useTheme } from './composables/useTheme'
import { useDemoAuth } from './composables/useDemoAuth'
import { useTenantAuth } from './composables/useTenantAuth'

// 静态导入的页面（始终需要或被多处引用）
import CustomerInfo from './components/CustomerInfo.vue'
import ScheduledTasks from './components/ScheduledTasks.vue'
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
      name: 'root',
      component: () => import.meta.env.VITE_DEMO_ENABLED === 'true'
        ? import('./components/ChatContainer.vue')
        : import('./components/UniversalLogin.vue')
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
      path: '/subagents',
      name: 'subagents',
      component: () => import('./components/DigitalEmployeeManager.vue')
    },
    {
      path: '/portal/subagents',
      name: 'portal-subagents',
      component: () => import('./components/DigitalEmployeeManager.vue')
    },
    {
      path: '/all-sessions',
      name: 'all-sessions',
      component: () => import('./components/AllSessions.vue')
    },
    {
      path: '/data-sources',
      name: 'data-sources',
      component: () => import('./pages/DataSourceManager.vue')
    },
    // 外贸获客智能体业务数据页面
    {
      path: '/trade-specialist',
      name: 'trade-specialist',
      component: () => import('./components/BaseBusinessLayout.vue'),
      children: [
        {
          path: 'customers',
          name: 'trade-specialist-customers',
          component: CustomerInfo
        },
      ]
    },
    // 旅游咨询顾问业务数据页面
    {
      path: '/travel-consultant',
      name: 'travel-consultant',
      component: () => import('./components/BaseBusinessLayout.vue'),
      children: [
        { path: 'vehicles', name: 'travel-consultant-vehicles', component: () => import('./components/travel/VehicleManager.vue') },
        { path: 'attractions', name: 'travel-consultant-attractions', component: () => import('./components/travel/AttractionManager.vue') },
        { path: 'hotels', name: 'travel-consultant-hotels', component: () => import('./components/travel/HotelManager.vue') },
        { path: 'meals', name: 'travel-consultant-meals', component: () => import('./components/travel/MealManager.vue') },
        { path: 'guides', name: 'travel-consultant-guides', component: () => import('./components/travel/GuideManager.vue') },
        { path: 'fees', name: 'travel-consultant-fees', component: () => import('./components/travel/FeeManager.vue') },
      ]
    },
    // 客户跟进智能体业务数据页面
    {
      path: '/customer-followup',
      name: 'customer-followup',
      component: () => import('./components/BaseBusinessLayout.vue'),
      children: [
        {
          path: 'leads',
          name: 'customer-followup-leads',
          component: () => import('./components/followup/LeadManager.vue')
        },
        {
          path: 'followup-records',
          name: 'customer-followup-followup-records',
          component: () => import('./components/followup/FollowupRecords.vue')
        },
        {
          path: 'sales-reps',
          name: 'customer-followup-sales-reps',
          component: () => import('./components/followup/SalesRepManager.vue')
        },
      ]
    },
    // 投诉处理智能体业务数据页面
    {
      path: '/complaint',
      name: 'complaint',
      component: () => import('./components/BaseBusinessLayout.vue'),
      children: [
        {
          path: 'list',
          name: 'complaint-list',
          component: () => import('./components/complaint/ComplaintList.vue')
        },
        {
          path: 'stats',
          name: 'complaint-stats',
          component: () => import('./components/complaint/ComplaintStats.vue')
        },
      ]
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
        { path: 'agent-definitions', name: 'portal-agent-definitions', component: () => import('./components/AgentDefinitionManager.vue') },
        { path: 'token-usage', name: 'portal-token-usage', component: () => import('./components/saas/PlatformTokenUsage.vue') },
        { path: 'error-logs', name: 'portal-error-logs', component: () => import('./components/saas/ErrorLogs.vue') },
        { path: 'reply-styles', name: 'portal-reply-styles', component: () => import('./components/saas/SystemReplyStyleManager.vue') },
        { path: 'monitoring', name: 'portal-monitoring', component: () => import('./components/saas/TraceBrowser.vue') },
        { path: 'monitoring/:session_id', name: 'portal-session-traces', component: () => import('./components/saas/SessionTraces.vue') },
        { path: 'monitoring/trace/:trace_id', name: 'portal-trace-detail', component: () => import('./components/saas/TraceDetail.vue') },
      ]
    },
    // 租户入口 /t/:tenant_id（所有用户）
    {
      path: '/t/:tenant_id',
      component: () => import('./components/saas/PortalLayout.vue'),
      children: [
        { path: '', name: 'tenant-chat', component: () => import('./components/ChatContainer.vue') },
        { path: 'login', name: 'tenant-login', component: () => import('./components/saas/TenantLogin.vue') },
        { path: 'reset-password', name: 'tenant-reset-password', component: () => import('./components/saas/ResetPassword.vue') },
        { path: 'users', name: 'tenant-users', component: () => import('./components/saas/TenantUserManager.vue') },
        { path: 'knowledge', name: 'tenant-knowledge', component: () => import('./components/KnowledgeBase.vue') },
        { path: 'channels', name: 'tenant-channels', component: () => import('./components/saas/ChannelConfig.vue') },
        { path: 'settings', name: 'tenant-settings', component: () => import('./components/saas/TenantSettings.vue') },
        { path: 'chat', name: 'tenant-chat-explicit', component: () => import('./components/ChatContainer.vue') },
        { path: 'chat/:subagent', name: 'tenant-chat-subagent', component: () => import('./components/ChatContainer.vue') },
        { path: 'all-sessions', name: 'tenant-all-sessions', component: () => import('./components/AllSessions.vue') },
        { path: 'token-usage', name: 'tenant-token-usage', component: () => import('./components/saas/TenantTokenUsage.vue') },
        { path: 'reply-styles', name: 'tenant-reply-styles', component: () => import('./components/saas/ReplyStyleManager.vue') },
        { path: 'external-customers', name: 'tenant-external-customers', component: () => import('./components/saas/ExternalCustomerService.vue') },
        { path: 'data-sources', name: 'tenant-data-sources', component: () => import('./pages/DataSourceManager.vue') },
        // 外贸获客智能体业务数据页面
        {
          path: 'trade-specialist',
          name: 'tenant-trade-specialist',
          component: () => import('./components/BaseBusinessLayout.vue'),
          children: [
            {
              path: 'customers',
              name: 'tenant-trade-specialist-customers',
              component: CustomerInfo
            },
          ]
        },
        // 旅游咨询顾问业务数据页面
        {
          path: 'travel-consultant',
          name: 'tenant-travel-consultant',
          component: () => import('./components/BaseBusinessLayout.vue'),
          children: [
            { path: 'vehicles', name: 'tenant-travel-consultant-vehicles', component: () => import('./components/travel/VehicleManager.vue') },
            { path: 'attractions', name: 'tenant-travel-consultant-attractions', component: () => import('./components/travel/AttractionManager.vue') },
            { path: 'hotels', name: 'tenant-travel-consultant-hotels', component: () => import('./components/travel/HotelManager.vue') },
            { path: 'meals', name: 'tenant-travel-consultant-meals', component: () => import('./components/travel/MealManager.vue') },
            { path: 'guides', name: 'tenant-travel-consultant-guides', component: () => import('./components/travel/GuideManager.vue') },
            { path: 'fees', name: 'tenant-travel-consultant-fees', component: () => import('./components/travel/FeeManager.vue') },
          ]
        },
        // 客户跟进智能体业务数据页面
        {
          path: 'customer-followup',
          name: 'tenant-customer-followup',
          component: () => import('./components/BaseBusinessLayout.vue'),
          children: [
            {
              path: 'leads',
              name: 'tenant-customer-followup-leads',
              component: () => import('./components/followup/LeadManager.vue')
            },
            {
              path: 'followup-records',
              name: 'tenant-customer-followup-followup-records',
              component: () => import('./components/followup/FollowupRecords.vue')
            },
            {
              path: 'sales-reps',
              name: 'tenant-customer-followup-sales-reps',
              component: () => import('./components/followup/SalesRepManager.vue')
            },
          ]
        },
        // 投诉处理智能体业务数据页面
        {
          path: 'complaint',
          name: 'tenant-complaint',
          component: () => import('./components/BaseBusinessLayout.vue'),
          children: [
            {
              path: 'list',
              name: 'tenant-complaint-list',
              component: () => import('./components/complaint/ComplaintList.vue')
            },
            {
              path: 'stats',
              name: 'tenant-complaint-stats',
              component: () => import('./components/complaint/ComplaintStats.vue')
            },
          ]
        },
      ]
    }
  ]
})

// 全局路由守卫：确保 auth 状态在任何页面刷新时都能初始化
router.beforeEach(async (to) => {
  const path = to.path
  // 登录页和重置密码页不需要验证 auth 状态
  if (path.endsWith('/login') || path.endsWith('/reset-password')) {
    return
  }
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
  timeout: 5000,
  maxToasts: 3,
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

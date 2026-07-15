import type { RouteRecordRaw } from 'vue-router'

import CustomerInfo from '@/components/CustomerInfo.vue'
import EmailRecords from '@/components/EmailRecords.vue'
import KnowledgeBase from '@/components/KnowledgeBase.vue'
import MatchStats from '@/components/MatchStats.vue'
import ScheduledTasks from '@/components/ScheduledTasks.vue'

export const agentRoutes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'root',
    component: () => import.meta.env.VITE_DEMO_ENABLED === 'true'
      ? import('@/components/ChatContainer.vue')
      : import('@/components/UniversalLogin.vue')
  },
  { path: '/chat/:subagent', name: 'chat-subagent', component: () => import('@/components/ChatContainer.vue') },
  { path: '/customer-info', name: 'customer-info', component: CustomerInfo },
  { path: '/scheduled-tasks', name: 'scheduled-tasks', component: ScheduledTasks },
  { path: '/knowledge-base', name: 'knowledge-base', component: KnowledgeBase },
  { path: '/my-agents', name: 'my-agents', component: () => import('@/components/MyDigitalEmployees.vue') },
  { path: '/all-sessions', name: 'all-sessions', component: () => import('@/components/AllSessions.vue') },
  { path: '/data-sources', name: 'data-sources', component: () => import('@/pages/DataSourceManager.vue') },
  { path: '/social-media', name: 'social-media', component: () => import('@/components/social-media/SocialMediaWorkbench.vue') },
  {
    path: '/trade-specialist',
    name: 'trade-specialist',
    component: () => import('@/components/BaseBusinessLayout.vue'),
    children: [
      { path: 'customers', name: 'trade-specialist-customers', component: CustomerInfo },
      { path: 'email-records', name: 'trade-specialist-email-records', component: EmailRecords },
      { path: 'match-stats', name: 'trade-specialist-match-stats', component: MatchStats },
    ]
  },
  {
    path: '/travel-consultant',
    name: 'travel-consultant',
    component: () => import('@/components/BaseBusinessLayout.vue'),
    children: [
      { path: 'vehicles', name: 'travel-consultant-vehicles', component: () => import('@/components/travel/VehicleManager.vue') },
      { path: 'attractions', name: 'travel-consultant-attractions', component: () => import('@/components/travel/AttractionManager.vue') },
      { path: 'hotels', name: 'travel-consultant-hotels', component: () => import('@/components/travel/HotelManager.vue') },
      { path: 'meals', name: 'travel-consultant-meals', component: () => import('@/components/travel/MealManager.vue') },
      { path: 'guides', name: 'travel-consultant-guides', component: () => import('@/components/travel/GuideManager.vue') },
      { path: 'fees', name: 'travel-consultant-fees', component: () => import('@/components/travel/FeeManager.vue') },
    ]
  },
  {
    path: '/customer-followup',
    name: 'customer-followup',
    component: () => import('@/components/BaseBusinessLayout.vue'),
    children: [
      { path: 'leads', name: 'customer-followup-leads', component: () => import('@/components/followup/LeadManager.vue') },
      { path: 'followup-records', name: 'customer-followup-followup-records', component: () => import('@/components/followup/FollowupRecords.vue') },
      { path: 'sales-reps', name: 'customer-followup-sales-reps', component: () => import('@/components/followup/SalesRepManager.vue') },
    ]
  },
  {
    path: '/complaint',
    name: 'complaint',
    component: () => import('@/components/BaseBusinessLayout.vue'),
    children: [
      { path: 'list', name: 'complaint-list', component: () => import('@/components/complaint/ComplaintList.vue') },
      { path: 'stats', name: 'complaint-stats', component: () => import('@/components/complaint/ComplaintStats.vue') },
    ]
  },
  {
    path: '/after-sales',
    name: 'after-sales',
    component: () => import('@/components/BaseBusinessLayout.vue'),
    children: [
      { path: 'tickets', name: 'after-sales-tickets', component: () => import('@/components/after-sales/TicketList.vue') },
      { path: 'returns', name: 'after-sales-returns', component: () => import('@/components/after-sales/ReturnList.vue') },
    ]
  },
  {
    path: '/t/:tenant_id',
    component: () => import('@/components/saas/TenantLayout.vue'),
    children: [
      { path: '', name: 'tenant-chat', component: () => import('@/components/ChatContainer.vue') },
      { path: 'login', name: 'tenant-login', component: () => import('@/components/saas/TenantLogin.vue') },
      { path: 'reset-password', name: 'tenant-reset-password', component: () => import('@/components/saas/ResetPassword.vue') },
      { path: 'users', name: 'tenant-users', component: () => import('@/components/saas/TenantUserManager.vue') },
      { path: 'knowledge', name: 'tenant-knowledge', component: () => import('@/components/KnowledgeBase.vue') },
      { path: 'channels', name: 'tenant-channels', component: () => import('@/components/saas/ChannelConfig.vue') },
      { path: 'wecom-personal-rpa', name: 'tenant-wecom-personal-rpa', component: () => import('@/components/saas/WecomPersonalRpaManager.vue') },
      { path: 'settings', name: 'tenant-settings', component: () => import('@/components/saas/TenantSettings.vue') },
      { path: 'my-agents', name: 'tenant-my-agents', component: () => import('@/components/MyDigitalEmployees.vue') },
      { path: 'agent/:subagent_name/prompt', name: 'tenant-agent-prompt', component: () => import('@/components/TenantPromptEditor.vue') },
      { path: 'chat', name: 'tenant-chat-explicit', component: () => import('@/components/ChatContainer.vue') },
      { path: 'chat/:subagent', name: 'tenant-chat-subagent', component: () => import('@/components/ChatContainer.vue') },
      { path: 'all-sessions', name: 'tenant-all-sessions', component: () => import('@/components/AllSessions.vue') },
      { path: 'token-usage', name: 'tenant-token-usage', component: () => import('@/components/saas/TenantTokenUsage.vue') },
      { path: 'reply-styles', name: 'tenant-reply-styles', component: () => import('@/components/saas/ReplyStyleManager.vue') },
      { path: 'external-customers', name: 'tenant-external-customers', component: () => import('@/components/saas/ExternalCustomerService.vue') },
      { path: 'data-sources', name: 'tenant-data-sources', component: () => import('@/pages/DataSourceManager.vue') },
      { path: 'social-media', name: 'tenant-social-media', component: () => import('@/components/social-media/SocialMediaWorkbench.vue') },
      {
        path: 'trade-specialist',
        name: 'tenant-trade-specialist',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'customers', name: 'tenant-trade-specialist-customers', component: CustomerInfo },
          { path: 'email-records', name: 'tenant-trade-specialist-email-records', component: EmailRecords },
          { path: 'match-stats', name: 'tenant-trade-specialist-match-stats', component: MatchStats },
        ]
      },
      {
        path: 'travel-consultant',
        name: 'tenant-travel-consultant',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'vehicles', name: '车辆价格', component: () => import('@/components/travel/VehicleManager.vue') },
          { path: 'attractions', name: '景点门票', component: () => import('@/components/travel/AttractionManager.vue') },
          { path: 'hotels', name: '酒店房型', component: () => import('@/components/travel/HotelManager.vue') },
          { path: 'meals', name: '餐标价格', component: () => import('@/components/travel/MealManager.vue') },
          { path: 'guides', name: '导游费用', component: () => import('@/components/travel/GuideManager.vue') },
          { path: 'fees', name: '其他费用', component: () => import('@/components/travel/FeeManager.vue') },
        ]
      },
      {
        path: 'customer-followup',
        name: 'tenant-customer-followup',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'leads', name: 'tenant-customer-followup-leads', component: () => import('@/components/followup/LeadManager.vue') },
          { path: 'followup-records', name: 'tenant-customer-followup-followup-records', component: () => import('@/components/followup/FollowupRecords.vue') },
          { path: 'sales-reps', name: 'tenant-customer-followup-sales-reps', component: () => import('@/components/followup/SalesRepManager.vue') },
        ]
      },
      {
        path: 'complaint',
        name: 'tenant-complaint',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'list', name: 'tenant-complaint-list', component: () => import('@/components/complaint/ComplaintList.vue') },
          { path: 'stats', name: 'tenant-complaint-stats', component: () => import('@/components/complaint/ComplaintStats.vue') },
        ]
      },
      {
        path: 'after-sales',
        name: 'tenant-after-sales',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'tickets', name: '售后工单', component: () => import('@/components/after-sales/TicketList.vue') },
          { path: 'returns', name: '退换货记录', component: () => import('@/components/after-sales/ReturnList.vue') },
        ]
      },
    ]
  }
]

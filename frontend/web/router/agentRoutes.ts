import type { RouteRecordRaw } from 'vue-router'

import CustomerInfo from '@/components/CustomerInfo.vue'
import EmailRecords from '@/components/EmailRecords.vue'
import MatchStats from '@/components/MatchStats.vue'

export const agentRoutes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'root',
    component: () => import('@/components/UniversalLogin.vue')
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
      { path: 'connections', name: 'tenant-connections', component: () => import('@/components/connections/ConnectionCenter.vue') },
      { path: 'channels', name: 'tenant-channels', component: () => import('@/components/saas/ChannelConfig.vue') },
      { path: 'wecom-personal-rpa', name: 'tenant-wecom-personal-rpa', component: () => import('@/components/saas/WecomPersonalRpaManager.vue') },
      { path: 'settings', name: 'tenant-settings', component: () => import('@/components/saas/TenantSettings.vue') },
      { path: 'local-tools', name: 'tenant-local-tools', component: () => import('@/components/saas/LocalToolDevices.vue') },
      { path: 'daily-report', name: 'tenant-daily-report', component: () => import('@/components/reports/PersonalDailyReport.vue') },
      { path: 'work-outcomes', name: 'tenant-work-outcomes', component: () => import('@/components/reports/WorkOutcomes.vue') },
      { path: 'my-agents', name: 'tenant-my-agents', component: () => import('@/components/MyDigitalEmployees.vue') },
      { path: 'extras', name: 'tenant-extras', component: () => import('@/components/TenantExtraManager.vue') },
      { path: 'agent/:subagent_name/prompt', name: 'tenant-agent-prompt', component: () => import('@/components/TenantPromptEditor.vue') },
      { path: 'chat', name: 'tenant-chat-explicit', component: () => import('@/components/ChatContainer.vue') },
      { path: 'chat/:subagent', name: 'tenant-chat-subagent', component: () => import('@/components/ChatContainer.vue') },
      { path: 'all-sessions', name: 'tenant-all-sessions', component: () => import('@/components/AllSessions.vue') },
      { path: 'behavior-logs', name: 'tenant-behavior-logs', component: () => import('@/components/saas/BehaviorLogs.vue'), props: { scope: 'tenant' } },
      { path: 'scheduled-tasks', name: 'tenant-scheduled-tasks', component: () => import('@/components/ScheduledTasks.vue') },
      { path: 'token-usage', name: 'tenant-token-usage', component: () => import('@/components/saas/TenantTokenUsage.vue') },
      { path: 'recharge-records', name: 'tenant-recharge-records', component: () => import('@/components/saas/TenantRechargeRecords.vue') },
      { path: 'reply-styles', name: 'tenant-reply-styles', component: () => import('@/components/saas/ReplyStyleManager.vue') },
      { path: 'external-customers', name: 'tenant-external-customers', component: () => import('@/components/saas/ExternalCustomerService.vue') },
      { path: 'data-sources', name: 'tenant-data-sources', component: () => import('@/pages/DataSourceManager.vue') },
      { path: 'social-media', name: 'tenant-social-media', component: () => import('@/components/social-media/VideoCreationWorkbench.vue') },
      { path: 'assets', name: 'tenant-video-agent-assets', component: () => import('@/components/video-agent/AssetLibrary.vue') },
      { path: 'videos', name: 'tenant-video-agent-videos', component: () => import('@/components/video-agent/VideoLibrary.vue') },
      { path: 'prompts', name: 'tenant-video-agent-prompts', component: () => import('@/components/video-agent/PromptLibrary.vue') },
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
        path: 'recruiting-operator',
        name: 'tenant-recruiting-operator',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'resumes', name: 'tenant-recruiting-operator-resumes', component: () => import('@/components/recruiting/ResumeLibrary.vue') },
          { path: 'resumes/:resumeId', name: 'tenant-recruiting-operator-resume-detail', component: () => import('@/components/recruiting/ResumeDetail.vue') },
          { path: 'jobs', name: 'tenant-recruiting-operator-jobs', component: () => import('@/components/recruiting/JobLibrary.vue') },
          { path: 'jobs/:jobId', name: 'tenant-recruiting-operator-job-detail', component: () => import('@/components/recruiting/JobDetail.vue') },
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

import type { RouteRecordRaw } from 'vue-router'

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
      { path: 'knowledge/tdoc-imports', name: 'tenant-knowledge-tdoc-imports', component: () => import('@/components/knowledge/TencentDocsImport.vue') },
      { path: 'connections', name: 'tenant-connections', component: () => import('@/components/connections/ConnectionCenter.vue') },
      { path: 'connections/external-systems', name: 'tenant-external-systems', component: () => import('@/components/connections/ExternalSystems.vue') },
      { path: 'channels', name: 'tenant-channels', component: () => import('@/components/saas/ChannelConfig.vue') },
      { path: 'wechat-mp', name: 'tenant-wechat-mp', component: () => import('@/components/saas/WechatMpContent.vue') },
      { path: 'hongtao-shop', name: 'tenant-hongtao-shop', component: () => import('@/components/saas/HongtaoShopSync.vue') },
      { path: 'wecom-personal-rpa', name: 'tenant-wecom-personal-rpa', component: () => import('@/components/saas/WecomPersonalRpaManager.vue') },
      { path: 'settings', name: 'tenant-settings', component: () => import('@/components/saas/TenantSettings.vue') },
      { path: 'local-tools', name: 'tenant-local-tools', component: () => import('@/components/saas/LocalToolDevices.vue') },
      { path: 'daily-report', name: 'tenant-daily-report', component: () => import('@/components/reports/PersonalDailyReport.vue') },
      { path: 'work-outcomes', name: 'tenant-work-outcomes', component: () => import('@/components/reports/WorkOutcomes.vue') },
      { path: 'my-agents', name: 'tenant-my-agents', component: () => import('@/components/MyDigitalEmployees.vue') },
      { path: 'agent/:subagent_name/prompt', name: 'tenant-agent-prompt', redirect: to => ({ name: 'tenant-my-agents', params: { tenant_id: (to.params as any).tenant_id } }) },
      { path: 'chat', name: 'tenant-chat-explicit', component: () => import('@/components/ChatContainer.vue') },
      { path: 'chat/:subagent', name: 'tenant-chat-subagent', component: () => import('@/components/ChatContainer.vue') },
      { path: 'all-sessions', name: 'tenant-all-sessions', component: () => import('@/components/AllSessions.vue') },
      { path: 'behavior-logs', name: 'tenant-behavior-logs', component: () => import('@/components/saas/BehaviorLogs.vue'), props: { scope: 'tenant' } },
      // 定时任务 Phase 1 清场：路由暂时下线，Phase 5 以任务视图重挂（见 plan-scheduled-task-runner-refactor.md）
      { path: 'token-usage', name: 'tenant-token-usage', component: () => import('@/components/saas/TenantTokenUsage.vue') },
      { path: 'recharge-records', name: 'tenant-recharge-records', component: () => import('@/components/saas/TenantRechargeRecords.vue') },
      { path: 'reply-styles', name: 'tenant-reply-styles', component: () => import('@/components/saas/ReplyStyleManager.vue') },
      { path: 'external-customers', name: 'tenant-external-customers', component: () => import('@/components/saas/ExternalCustomerService.vue') },
      { path: 'abuse-center', name: 'tenant-abuse-center', component: () => import('@/components/saas/AbuseCenter.vue') },
      { path: 'office-sessions', name: 'tenant-office-sessions', component: () => import('@/components/saas/OfficeSessions.vue') },
      { path: 'web-sessions', name: 'tenant-web-sessions', component: () => import('@/components/saas/WebSessions.vue') },
      { path: 'data-sources', name: 'tenant-data-sources', component: () => import('@/pages/DataSourceManager.vue') },
      { path: 'social-media', name: 'tenant-social-media', component: () => import('@/components/social-media/VideoCreationWorkbench.vue') },
      { path: 'assets', name: 'tenant-video-agent-assets', component: () => import('@/components/video-agent/AssetLibrary.vue') },
      { path: 'videos', name: 'tenant-video-agent-videos', component: () => import('@/components/video-agent/VideoLibrary.vue') },
      { path: 'prompts', name: 'tenant-video-agent-prompts', component: () => import('@/components/video-agent/PromptLibrary.vue') },
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
          { path: 'returns', name: '退换货记录', component: () => import('@/components/after-sales/ReturnList.vue') }
        ]
      },
      {
        // 微信营销自动化工作台（P3-A2；page_metadata 状态 developing，验收后 published）
        path: 'weixin-marketing',
        name: 'tenant-weixin-marketing',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'automations', name: 'tenant-weixin-marketing-automations', component: () => import('@/components/weixinMarketing/AutomationList.vue') },
          { path: 'session-tasks', name: 'tenant-weixin-session-tasks', component: () => import('@/components/sessionTasks/TaskList.vue') },
          { path: 'session-tasks/:taskId', name: 'tenant-weixin-session-task-detail', component: () => import('@/components/sessionTasks/TaskDetail.vue') },
          { path: 'runs/:runId', name: 'tenant-weixin-marketing-run-detail', component: () => import('@/components/weixinMarketing/RunDetail.vue') },
          { path: 'event-sources', name: 'tenant-weixin-marketing-event-sources', component: () => import('@/components/weixinMarketing/EventSourceSettings.vue') },
        ]
      },
      {
        // AI获客业务数据页面（只读列表；page_metadata 状态 planned，验收后 published）
        path: 'ai-lead-gen',
        name: 'tenant-ai-lead-gen',
        component: () => import('@/components/BaseBusinessLayout.vue'),
        children: [
          { path: 'campaigns', name: '获客活动', component: () => import('@/components/aiLeadGen/CampaignList.vue') },
          { path: 'leads', name: '线索公司', component: () => import('@/components/aiLeadGen/LeadList.vue') },
          { path: 'contacts', name: '联系人', component: () => import('@/components/aiLeadGen/ContactList.vue') },
          { path: 'emails', name: '邮件记录', component: () => import('@/components/aiLeadGen/EmailList.vue') },
          { path: 'replies', name: '回复记录', component: () => import('@/components/aiLeadGen/ReplyList.vue') },
        ]
      },
    ]
  }
]

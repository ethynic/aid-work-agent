/**
 * MSW 请求处理器
 *
 * 定义所有 API 端点的 mock 行为
 */
import { http, HttpResponse } from 'msw'

export const handlers = [
  // 认证端点
  http.post('/api/auth/login', async ({ request }) => {
    const body = await request.json() as any
    if (body.phone && body.code === '1234') {
      return HttpResponse.json({
        success: true,
        token: 'test_token_123',
        user: { user_id: 'u1', username: 'TestUser', phone: '13800138000' }
      })
    }
    return HttpResponse.json({ success: false, error: 'Invalid credentials' }, { status: 401 })
  }),

  http.post('/api/auth/sms-code', () => {
    return HttpResponse.json({ success: true })
  }),

  http.get('/api/auth/me', ({ request }) => {
    const auth = request.headers.get('Authorization')
    if (auth === 'Bearer test_token_123') {
      return HttpResponse.json({
        user_id: 'u1', username: 'TestUser', phone: '13800138000'
      })
    }
    return HttpResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }),

  http.post('/api/auth/admin_logout', () => {
    return HttpResponse.json({ success: true })
  }),

  // 会话端点
  http.get('/api/sessions', ({ request }) => {
    const auth = request.headers.get('Authorization')
    if (!auth) {
      return HttpResponse.json({ error: 'Unauthorized' }, { status: 401 })
    }
    return HttpResponse.json({
      sessions: [
        {
          session_id: 's1',
          user_id: 'u1',
          title: 'Test Session',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        }
      ]
    })
  }),

  http.post('/api/sessions', async ({ request }) => {
    const body = await request.json() as any
    return HttpResponse.json({
      session_id: 'new_session_id',
      user_id: 'u1',
      title: body.title || 'New Session',
      created_at: '2026-04-07T00:00:00Z',
      updated_at: '2026-04-07T00:00:00Z',
    })
  }),

  http.delete('/api/sessions/:id', () => {
    return HttpResponse.json({ success: true })
  }),

  // 定时任务端点（Runner 化 Phase 5：侧栏任务计划分组 / 任务视图）
  http.get('/api/scheduled-tasks', ({ request }) => {
    const auth = request.headers.get('Authorization')
    if (!auth) {
      return HttpResponse.json({ error: 'Unauthorized' }, { status: 401 })
    }
    return HttpResponse.json({
      success: true,
      data: {
        tasks: [{
          task_id: 'sched_mock1', user_id: 'u1', name: 'Mock 定时任务',
          task_prompt: '每天汇总', attachments: [], profile_id: 'main',
          schedule_type: 'daily', cron_expression: '0 9 * * *',
          session_id: 's_sched_1', status: 'active',
          next_due_at: '2026-01-02T01:00:00Z', total_runs: 3,
          success_count: 3, fail_count: 0,
          created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
        }],
        total: 1,
      },
    })
  }),

  // 文件上传端点
  http.post('/api/upload', () => {
    return HttpResponse.json({
      success: true,
      file_id: 'file_123',
      name: 'test.pdf',
      size: 1024,
      mime_type: 'application/pdf',
      type: 'file',
    })
  }),

  // 工具列表端点
  http.get('/api/tools', () => {
    return HttpResponse.json({
      tools: [
        { name: 'web_search', description: '搜索', category: 'search' },
        { name: 'email_send', description: '发送邮件', category: 'email' },
      ]
    })
  }),

  // 租户列表（list_tenants）
  http.get('/api/saas/tenants/list_tenants', () => {
    return HttpResponse.json({
      success: true,
      tenants: [
        { tenant_id: 'tenant_A', company_name: '租户A', status: 'active' },
        { tenant_id: 'tenant_B', company_name: '租户B', status: 'active' },
        { tenant_id: 'tenant_C', company_name: '已停用租户', status: 'suspended' },
      ],
      total: 3,
      page: 1,
      page_size: 500,
    })
  }),

  // 可用数字员工列表（按租户订阅；后端返回 {agent_id, name} 对象数组）
  http.get('/api/saas/channels/available-subagents', ({ request }) => {
    const tid = request.headers.get('X-Tenant-Id')
    // 不同租户返回不同订阅，用于测试
    if (tid === 'tenant_A') {
      return HttpResponse.json({
        success: true,
        subagents: [
          { agent_id: 'ai-lead-gen', name: 'AI获客智能体' },
          { agent_id: 'travel-consultant', name: '旅游咨询顾问' },
        ],
      })
    }
    if (tid === 'tenant_B') {
      return HttpResponse.json({
        success: true,
        subagents: [{ agent_id: 'travel-consultant', name: '旅游咨询顾问' }],
      })
    }
    return HttpResponse.json({ success: true, subagents: [] })
  }),

  // 数据源授权清单（连接中心菜单门控；默认空 = 未开通任何数据源）
  http.get('/api/saas/connection-sources', () => {
    return HttpResponse.json({ success: true, sources: [] })
  }),
]

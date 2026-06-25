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

  // ========== 企业微信个人账号 RPA 管理端点（/api/saas/wecom-personal-rpa/*） ==========

  // 跨租户列出所有 RPA 客户端（平台后台 RpaBindingPanel 用，一行一 client 聚合视图）
  // 自 2026-06 改造后以 wecom_rpa_clients 为基准，确保创建 client 后即可看到
  http.get('/api/saas/wecom-personal-rpa/all_bindings', ({ request }) => {
    const url = new URL(request.url)
    const status = url.searchParams.get('status')
    // 用本地时间字符串（无 Z 后缀，对齐后端 TIMESTAMP 行为，前端按本地时区解析）
    // 见 frontend_dev.md 时间显示规范
    const formatLocal = (d: Date): string => {
      const pad = (n: number) => String(n).padStart(2, '0')
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    }
    const now = new Date()
    const recent = new Date(now.getTime() - 5 * 1000) // 5 秒前 → 在线
    const stale = new Date(now.getTime() - 5 * 60 * 1000) // 5 分钟前 → 离线
    let rows = [
      {
        client_id: 'rpa_client_a',
        tenant_id: 'tenant_A',
        client_name: '客户端A',
        client_status: 'active',
        agent_base_url: 'https://prod.example.com',
        last_heartbeat_at: formatLocal(recent),
        min_version: '1.0.0',
        created_at: '2026-06-01T10:00:00',
        updated_at: '2026-06-24T10:00:00',
        account_count: 2,
        binding_count: 3,
        last_account_name: '张三',
      },
      {
        client_id: 'rpa_client_b',
        tenant_id: 'tenant_B',
        client_name: '客户端B',
        client_status: 'disabled',
        agent_base_url: 'https://agent.example.com', // 占位地址（应触发警告）
        last_heartbeat_at: null, // 从未连上来 → 未连接
        min_version: '1.0.0',
        created_at: '2026-06-02T10:00:00',
        updated_at: '2026-06-24T10:00:00',
        account_count: 0,
        binding_count: 0,
        last_account_name: null,
      },
      {
        client_id: 'rpa_client_c',
        tenant_id: 'tenant_A',
        client_name: '客户端C-离线',
        client_status: 'active',
        agent_base_url: null,
        last_heartbeat_at: formatLocal(stale), // 离线
        min_version: '1.0.0',
        created_at: '2026-06-03T10:00:00',
        updated_at: '2026-06-24T10:00:00',
        account_count: 1,
        binding_count: 1,
        last_account_name: '李四',
      },
    ]
    if (status === 'needs_review_only') {
      // 排除 active，显示其他状态
      rows = rows.filter(r => r.client_status !== 'active')
    } else if (status) {
      rows = rows.filter(r => r.client_status === status)
    }
    return HttpResponse.json({ success: true, data: rows })
  }),

  // 客户端级暂停（POST /clients/{clientId}/pause）
  http.post('/api/saas/wecom-personal-rpa/clients/:clientId/pause', ({ params }) => {
    return HttpResponse.json({
      success: true,
      data: { client_id: params.clientId, client_status: 'disabled' },
    })
  }),

  // 客户端级恢复（POST /clients/{clientId}/resume）
  http.post('/api/saas/wecom-personal-rpa/clients/:clientId/resume', ({ params }) => {
    return HttpResponse.json({
      success: true,
      data: { client_id: params.clientId, client_status: 'active' },
    })
  }),

  // 暂停（account/conversation/tenant）— 租户后台 WecomPersonalRpaManager 仍在用
  http.post('/api/saas/wecom-personal-rpa/pause', async ({ request }) => {
    const body = await request.json() as any
    return HttpResponse.json({
      success: true,
      data: { scope: body.scope, action: 'pause', affected: [body.account_id || body.conversation_id] },
    })
  }),

  // 恢复（account/conversation/tenant）— 租户后台 WecomPersonalRpaManager 仍在用
  http.post('/api/saas/wecom-personal-rpa/resume', async ({ request }) => {
    const body = await request.json() as any
    return HttpResponse.json({
      success: true,
      data: { scope: body.scope, action: 'resume', affected: [body.account_id || body.conversation_id] },
    })
  }),

  // 复核确认 — 租户后台 WecomPersonalRpaManager 仍在用
  http.post('/api/saas/wecom-personal-rpa/bindings/:bindingId/confirm', ({ params }) => {
    return HttpResponse.json({
      success: true,
      data: { binding_id: params.bindingId, status: 'active' },
    })
  }),

  // 更新 agent_base_url
  http.patch('/api/saas/wecom-personal-rpa/clients/:clientId/agent_base_url', async ({ params, request }) => {
    const body = await request.json() as any
    return HttpResponse.json({
      success: true,
      data: { client_id: params.clientId, agent_base_url: body.agent_base_url || null },
    })
  }),

  // 注册客户端（POST /clients）— 返回 client_id + 明文 client_secret（仅一次）
  http.post('/api/saas/wecom-personal-rpa/clients', async ({ request }) => {
    const body = (await request.json()) as any
    // 透传 X-Tenant-Id 用于断言「平台管理员代管理」是否生效
    const tenantId = request.headers.get('X-Tenant-Id') || 'tenant_default'
    return HttpResponse.json({
      success: true,
      data: {
        client_id: 'rpa_client_new_test',
        client_secret: 'plain_secret_once_only_xyz',
        min_version: body?.min_version || '1.0.0',
        name: body?.name || '',
        secret_warning: 'client_secret 仅本次响应返回一次，请立即妥善保存。',
        // 测试辅助字段：断言平台后台代管理时 X-Tenant-Id 被正确传递
        _mock_tenant_id: tenantId,
      },
    })
  }),

  // 轮换密钥（POST /clients/{clientId}/rotate-secret）— 返回新明文 secret（仅一次）
  http.post('/api/saas/wecom-personal-rpa/clients/:clientId/rotate-secret', ({ params, request }) => {
    const tenantId = request.headers.get('X-Tenant-Id') || ''
    return HttpResponse.json({
      success: true,
      data: {
        client_id: params.clientId,
        client_secret: 'rotated_new_secret_abc123',
        secret_warning: '新 client_secret 仅本次响应返回一次，请立即妥善保存。',
        // 测试辅助字段：断言平台后台代管理时 X-Tenant-Id 被正确传递
        _mock_tenant_id: tenantId,
      },
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

  // 可用数字员工列表（按租户订阅）
  http.get('/api/saas/channels/available-subagents', ({ request }) => {
    const tid = request.headers.get('X-Tenant-Id')
    // 不同租户返回不同订阅，用于测试
    if (tid === 'tenant_A') {
      return HttpResponse.json({ success: true, subagents: ['trade-specialist', 'travel-consultant'] })
    }
    if (tid === 'tenant_B') {
      return HttpResponse.json({ success: true, subagents: ['travel-consultant'] })
    }
    return HttpResponse.json({ success: true, subagents: [] })
  }),
]

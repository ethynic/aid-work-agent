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
]

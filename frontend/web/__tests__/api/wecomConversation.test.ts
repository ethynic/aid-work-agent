/**
 * 企微会话业务 API 接缝测试：后端统一返回 {success:true, data:{...}}，
 * 前端必须解包 data 才能取到会话列表/报告/runner_id；success:false 抛中文错误。
 */
import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { listConversations, dailyReports, generateDailyReport } from '@/api/wecomConversation'

describe('wecomConversation API 接缝（响应解包）', () => {
  it('listConversations 解包 data，页面可直接遍历会话数组', async () => {
    server.use(
      http.get('/api/wecom-conversations/conversations', () =>
        HttpResponse.json({
          success: true,
          data: { conversations: [{ id: 7, name: '售前客服群', timezone: 'Asia/Shanghai', enroll_reasons: ['agent_replied'] }] },
        })),
    )
    const value = await listConversations()
    expect(Array.isArray(value.conversations)).toBe(true)
    expect(value.conversations[0]?.name).toBe('售前客服群')
  })

  it('dailyReports 按 business_date 查询并解包 reports（含缺失会话）', async () => {
    server.use(
      http.get('/api/wecom-conversations/daily-reports', ({ request }) => {
        const url = new URL(request.url)
        expect(url.searchParams.get('business_date')).toBe('2026-11-02')
        return HttpResponse.json({
          success: true,
          data: { business_date: '2026-11-02', reports: [
            { conversation_id: 7, name: '售前客服群', summary: { overview: '当日概览' }, coverage: null },
            { conversation_id: 8, name: 'VIP 客户', summary: null, coverage: null },
          ] },
        })
      }),
    )
    const value = await dailyReports('2026-11-02')
    expect(value.reports).toHaveLength(2)
    expect(value.reports[1]?.summary).toBeNull()
  })

  it('success:false 抛出后端 error 文案', async () => {
    server.use(
      http.post('/api/wecom-conversations/daily-reports/generate', () =>
        HttpResponse.json({ success: false, error: '会话不存在或无权访问', code: 'CONVERSATION_NOT_FOUND' })),
    )
    await expect(generateDailyReport({ business_date: '2026-11-02', conversation_ids: [1] }))
      .rejects.toThrow('会话不存在或无权访问')
  })
})

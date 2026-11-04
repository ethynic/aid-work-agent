/**
 * 企微会话业务 API 接缝测试：后端统一返回 {success:true, data:{...}}，
 * 前端必须解包 data 才能取到会话列表/报告/runner_id；success:false 抛中文错误。
 */
import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { addConversations, listConversations, dailyReports, generateDailyReport, listDirectory, saveDailyPlan } from '@/api/wecomConversation'

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
    await expect(generateDailyReport({ conversation_ids: [1] }))
      .rejects.toThrow('会话不存在或无权访问')
  })

  it('目录 cursor 与专用计划 body 只走各自受信入口', async () => {
    server.use(
      http.get('/api/wecom-conversations/directory', ({ request }) => {
        const query = new URL(request.url).searchParams
        expect(query.get('cursor')).toBe('wdir1.next')
        expect(query.get('query')).toBe('同名')
        return HttpResponse.json({ success: true, data: { device_id: 'device-a', self_user_id: 'account-a',
          source_generation: 'generation-a', conversations: [], next_cursor: null } })
      }),
      http.post('/api/wecom-conversations/daily-plans', async ({ request }) => {
        expect(await request.json()).toEqual({ target_day: 'yesterday', scope_mode: 'all',
          conversation_ids: [], hour: 2, minute: 0 })
        return HttpResponse.json({ success: true, data: { plan: { task_id: 'sched-one' } } })
      }),
    )
    expect((await listDirectory({ query: '同名', cursor: 'wdir1.next' })).self_user_id).toBe('account-a')
    expect((await saveDailyPlan({ target_day: 'yesterday', scope_mode: 'all',
      conversation_ids: [], hour: 2, minute: 0 }, false)).plan.task_id).toBe('sched-one')
  })

  it('添加携带目录来源预期值供服务端拒绝选定设备变化', async () => {
    server.use(http.post('/api/wecom-conversations/conversations/add', async ({ request }) => {
      expect(await request.json()).toEqual({ sessions: ['ws1.locator'], expected_device_id: 'device-a',
        expected_source_generation: 'generation-a' })
      return HttpResponse.json({ success: true, data: { conversations: [], added_count: 1 } })
    }))
    expect((await addConversations(['ws1.locator'], { expected_device_id: 'device-a',
      expected_source_generation: 'generation-a' })).added_count).toBe(1)
  })
})

/**
 * 招聘操作智能体 API 客户端测试（简历 job_id 硬关联 + 简历时间线）
 *
 * 验证点：
 * - listResumes：job_id 参数拼接到 query（未传时不出现 job_id 键）
 * - createResume：body 携带 job_id（硬关联，后端校验属本租户并回填职位名）
 * - updateResume：job_id 三态在 body 中的形状——不传=键缺失 / 空串=清除关联 / 非空=重新关联
 * - 沟通记录/邀约记录六函数（第④期）：URL 形状（子资源路径/独立资源路径）+ body 形状
 *
 * 为什么重要：简历-职位从 job_name 文本弱关联改为 job_id 强关联后，
 * 前端传参形状（尤其 update 三态）直接决定后端是修改、清除还是保留关联；
 * 时间线函数则必须命中正确的子资源/独立资源路径，否则 404。
 */
import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import {
  listResumes,
  createResume,
  updateResume,
  listCommLogs,
  createCommLog,
  deleteCommLog,
  listInvitations,
  createInvitation,
  updateInvitation,
  deleteInvitation,
  listResumeNotifyLogs,
} from '@/api/recruitingOperator'

// 捕获最近一次请求的 url（含 query）与 body
function captureJsonRequest(): { url: URL; bodies: Record<string, unknown>[] } {
  const state: { url: URL | null; bodies: Record<string, unknown>[] } = { url: null, bodies: [] }
  server.use(
    http.get('/api/recruiting-operator/resumes', ({ request }) => {
      state.url = new URL(request.url)
      return HttpResponse.json({ success: true, data: { items: [], total: 0, page: 1, page_size: 20 } })
    }),
    http.post('/api/recruiting-operator/resumes', async ({ request }) => {
      state.url = new URL(request.url)
      state.bodies.push((await request.json()) as Record<string, unknown>)
      return HttpResponse.json({ success: true, data: { id: 1 } })
    }),
    http.patch('/api/recruiting-operator/resumes/:resumeId', async ({ request }) => {
      state.url = new URL(request.url)
      state.bodies.push((await request.json()) as Record<string, unknown>)
      return HttpResponse.json({ success: true, data: { id: 1 } })
    }),
  )
  return {
    get url() {
      return state.url as URL
    },
    bodies: state.bodies,
  }
}

describe('recruitingOperator api - listResumes job_id 筛选', () => {
  it('传 job_id 时拼接到 query', async () => {
    const cap = captureJsonRequest()
    await listResumes({ page: 1, page_size: 20, job_id: 'd2b1a0c3-0000-4000-8000-000000000001' })
    expect(cap.url.searchParams.get('job_id')).toBe('d2b1a0c3-0000-4000-8000-000000000001')
  })

  it('不传 job_id 时 query 不含 job_id 键', async () => {
    const cap = captureJsonRequest()
    await listResumes({ page: 1, keyword: '张三' })
    expect(cap.url.searchParams.has('job_id')).toBe(false)
    expect(cap.url.searchParams.get('keyword')).toBe('张三')
  })
})

describe('recruitingOperator api - create/update body 带 job_id', () => {
  it('createResume body 携带 job_id', async () => {
    const cap = captureJsonRequest()
    await createResume({
      candidate_name: '张三',
      job_id: 'd2b1a0c3-0000-4000-8000-000000000002',
      source: 'manual',
    })
    expect(cap.bodies[0]).toMatchObject({
      candidate_name: '张三',
      job_id: 'd2b1a0c3-0000-4000-8000-000000000002',
      source: 'manual',
    })
  })

  it('updateResume 不传 job_id：body 不含 job_id 键（后端语义=不修改关联）', async () => {
    const cap = captureJsonRequest()
    await updateResume(1, { status: 'viewed' })
    expect(cap.url.pathname).toBe('/api/recruiting-operator/resumes/1')
    expect(cap.bodies[0]).toEqual({ status: 'viewed' })
    expect('job_id' in cap.bodies[0]).toBe(false)
  })

  it('updateResume 传空串：body job_id 为空串（后端语义=清除关联）', async () => {
    const cap = captureJsonRequest()
    await updateResume(1, { job_id: '' })
    expect(cap.bodies[0]).toEqual({ job_id: '' })
  })

  it('updateResume 传非空：body 携带新 job_id（后端语义=校验后硬关联）', async () => {
    const cap = captureJsonRequest()
    await updateResume(1, { job_id: 'd2b1a0c3-0000-4000-8000-000000000003' })
    expect(cap.bodies[0]).toEqual({ job_id: 'd2b1a0c3-0000-4000-8000-000000000003' })
  })
})

describe('recruitingOperator api - 简历时间线六函数（第④期）', () => {
  // 捕获最近一次时间线请求的 method/url/body
  function captureTimelineRequest(): { method: string; url: URL; body: Record<string, unknown> } {
    const state: { method: string; url: URL | null; body: Record<string, unknown> } = {
      method: '', url: null, body: {},
    }
    server.use(
      http.get('/api/recruiting-operator/resumes/:resumeId/comm-logs', ({ request }) => {
        state.method = 'GET'; state.url = new URL(request.url); state.body = {}
        return HttpResponse.json({ success: true, data: { items: [] } })
      }),
      http.post('/api/recruiting-operator/resumes/:resumeId/comm-logs', async ({ request }) => {
        state.method = 'POST'; state.url = new URL(request.url)
        state.body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ success: true, data: { id: 1 } })
      }),
      http.delete('/api/recruiting-operator/comm-logs/:logId', ({ request }) => {
        state.method = 'DELETE'; state.url = new URL(request.url); state.body = {}
        return HttpResponse.json({ success: true })
      }),
      http.get('/api/recruiting-operator/resumes/:resumeId/invitations', ({ request }) => {
        state.method = 'GET'; state.url = new URL(request.url); state.body = {}
        return HttpResponse.json({ success: true, data: { items: [] } })
      }),
      http.post('/api/recruiting-operator/resumes/:resumeId/invitations', async ({ request }) => {
        state.method = 'POST'; state.url = new URL(request.url)
        state.body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ success: true, data: { id: 1 } })
      }),
      http.patch('/api/recruiting-operator/invitations/:invitationId', async ({ request }) => {
        state.method = 'PATCH'; state.url = new URL(request.url)
        state.body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ success: true, data: { id: 1 } })
      }),
      http.delete('/api/recruiting-operator/invitations/:invitationId', ({ request }) => {
        state.method = 'DELETE'; state.url = new URL(request.url); state.body = {}
        return HttpResponse.json({ success: true })
      }),
      http.get('/api/recruiting-operator/resumes/:resumeId/notify-logs', ({ request }) => {
        state.method = 'GET'; state.url = new URL(request.url); state.body = {}
        return HttpResponse.json({ success: true, data: { items: [] } })
      }),
    )
    return {
      get method() { return state.method },
      get url() { return state.url as URL },
      get body() { return state.body },
    }
  }

  it('listCommLogs：GET 简历子资源路径', async () => {
    const cap = captureTimelineRequest()
    await listCommLogs(12)
    expect(cap.method).toBe('GET')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/resumes/12/comm-logs')
  })

  it('createCommLog：POST 子资源路径，body 带 direction/channel/content', async () => {
    const cap = captureTimelineRequest()
    await createCommLog(12, { direction: 'out', channel: 'wecom', content: '已联系候选人' })
    expect(cap.method).toBe('POST')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/resumes/12/comm-logs')
    expect(cap.body).toEqual({ direction: 'out', channel: 'wecom', content: '已联系候选人' })
  })

  it('deleteCommLog：DELETE 独立 comm-logs 路径', async () => {
    const cap = captureTimelineRequest()
    await deleteCommLog(7)
    expect(cap.method).toBe('DELETE')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/comm-logs/7')
  })

  it('listInvitations：GET 简历子资源路径', async () => {
    const cap = captureTimelineRequest()
    await listInvitations(12)
    expect(cap.method).toBe('GET')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/resumes/12/invitations')
  })

  it('createInvitation：POST 子资源路径，body 带邀约字段（时间含秒的 ISO）', async () => {
    const cap = captureTimelineRequest()
    await createInvitation(12, {
      interview_at: '2026-09-01T14:00:00',
      interviewer: '王经理',
      method: '视频面试',
      status: 'pending',
    })
    expect(cap.method).toBe('POST')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/resumes/12/invitations')
    expect(cap.body).toEqual({
      interview_at: '2026-09-01T14:00:00',
      interviewer: '王经理',
      method: '视频面试',
      status: 'pending',
    })
  })

  it('updateInvitation：PATCH 独立 invitations 路径，仅传的键出现在 body', async () => {
    const cap = captureTimelineRequest()
    await updateInvitation(33, { status: 'confirmed' })
    expect(cap.method).toBe('PATCH')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/invitations/33')
    expect(cap.body).toEqual({ status: 'confirmed' })
  })

  it('deleteInvitation：DELETE 独立 invitations 路径', async () => {
    const cap = captureTimelineRequest()
    await deleteInvitation(33)
    expect(cap.method).toBe('DELETE')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/invitations/33')
  })

  it('listResumeNotifyLogs：GET 简历子资源 notify-logs 路径', async () => {
    const cap = captureTimelineRequest()
    await listResumeNotifyLogs(12)
    expect(cap.method).toBe('GET')
    expect(cap.url.pathname).toBe('/api/recruiting-operator/resumes/12/notify-logs')
  })
})

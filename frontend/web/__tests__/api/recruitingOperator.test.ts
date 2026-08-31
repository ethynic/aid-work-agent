/**
 * 招聘操作智能体 API 客户端测试（简历 job_id 硬关联）
 *
 * 验证点：
 * - listResumes：job_id 参数拼接到 query（未传时不出现 job_id 键）
 * - createResume：body 携带 job_id（硬关联，后端校验属本租户并回填职位名）
 * - updateResume：job_id 三态在 body 中的形状——不传=键缺失 / 空串=清除关联 / 非空=重新关联
 *
 * 为什么重要：简历-职位从 job_name 文本弱关联改为 job_id 强关联后，
 * 前端传参形状（尤其 update 三态）直接决定后端是修改、清除还是保留关联。
 */
import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { listResumes, createResume, updateResume } from '@/api/recruitingOperator'

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

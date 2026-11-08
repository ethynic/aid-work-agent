/**
 * 企微代理任务 API 接缝测试：后端统一返回 {success:true, data:{...}}，
 * 前端必须解包 data；success:false 抛 WecomProxyApiError（含 code），
 * proxyErrorText 在服务端 message 缺省时按码映射可读文案（客户端需升级等）。
 */
import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import {
  createProxyTask, deleteProxyTask, editProxyTask, getProxyTask, listProxyTasks,
  proxyErrorText, startProxyTask, stopProxyTask, WecomProxyApiError,
} from '@/api/wecomProxy'

const task = { id: 7, title: '售前群代理', status: 'stopped', active_target_count: 2 }

describe('wecomProxy API 接缝（响应解包与错误码）', () => {
  it('listProxyTasks 解包 tasks，include_deleted 透传查询参数', async () => {
    server.use(
      http.get('/api/wecom-proxy-tasks', ({ request }) => {
        const query = new URL(request.url).searchParams
        expect(query.get('include_deleted')).toBe('true')
        expect(query.get('limit')).toBe('200')
        return HttpResponse.json({ success: true, data: { tasks: [task] } })
      }),
    )
    const value = await listProxyTasks({ includeDeleted: true, limit: 200 })
    expect(value.tasks).toHaveLength(1)
    expect(value.tasks[0]?.title).toBe('售前群代理')
  })

  it('createProxyTask POST 七字段创建体并解包 201 任务', async () => {
    server.use(
      http.post('/api/wecom-proxy-tasks', async ({ request }) => {
        expect(await request.json()).toEqual({
          title: '售前群代理', instruction: '有咨询就回答', agent_id: 'agent-a',
          sessions: ['ws1.acc.R%3Aone'], profile_id: 'main',
        })
        return HttpResponse.json({ success: true, data: { task: { ...task, status: 'stopped' } } }, { status: 201 })
      }),
    )
    const value = await createProxyTask({
      title: '售前群代理', instruction: '有咨询就回答', agent_id: 'agent-a',
      sessions: ['ws1.acc.R%3Aone'], profile_id: 'main',
    })
    expect(value.task.id).toBe(7)
  })

  it('getProxyTask 解包详情（含 targets 与 batches 投影）', async () => {
    server.use(
      http.get('/api/wecom-proxy-tasks/7', () =>
        HttpResponse.json({ success: true, data: { task: { ...task, targets: [{ id: 1, status: 'active', wb1_cursor: 'wb1.x' }], batches: [] } } })),
    )
    const value = await getProxyTask(7)
    expect(value.task.targets?.[0]?.wb1_cursor).toBe('wb1.x')
  })

  it('editProxyTask PUT 只携带变更字段，stop/start/delete 走各自子路径', async () => {
    server.use(
      http.put('/api/wecom-proxy-tasks/7', async ({ request }) => {
        expect(await request.json()).toEqual({ title: '新标题', add_sessions: ['ws1.acc.R%3Atwo'] })
        return HttpResponse.json({ success: true, data: { task } })
      }),
      http.post('/api/wecom-proxy-tasks/7/start', () =>
        HttpResponse.json({ success: true, data: { task: { ...task, status: 'running' } } })),
      http.post('/api/wecom-proxy-tasks/7/stop', () =>
        HttpResponse.json({ success: true, data: { task: { ...task, status: 'stopped', stop_settled: true } } })),
      http.delete('/api/wecom-proxy-tasks/7', () =>
        HttpResponse.json({ success: true, data: { task: { ...task, status: 'deleted' } } })),
    )
    expect((await editProxyTask(7, { title: '新标题', add_sessions: ['ws1.acc.R%3Atwo'] })).task.id).toBe(7)
    expect((await startProxyTask(7)).task.status).toBe('running')
    expect((await stopProxyTask(7)).task.stop_settled).toBe(true)
    expect((await deleteProxyTask(7)).task.status).toBe('deleted')
  })

  it('success:false 优先透出服务端 message，并保留 code 与 field_errors', async () => {
    server.use(
      http.post('/api/wecom-proxy-tasks', () =>
        HttpResponse.json({ success: false, error: '同一企微账号的会话已由运行中的任务 #3 监控',
          code: 'PROXY_SESSION_OWNED', field_errors: [] })),
    )
    const error = await createProxyTask({
      title: 't', instruction: 'i', agent_id: 'a', sessions: ['ws1.x'],
    }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(WecomProxyApiError)
    expect((error as WecomProxyApiError).code).toBe('PROXY_SESSION_OWNED')
    expect(proxyErrorText(error)).toContain('任务 #3')
  })

  it('服务端 message 缺省时按错误码映射可读文案（客户端需升级等）', () => {
    expect(proxyErrorText(new WecomProxyApiError('', 'PROXY_CLIENT_UPGRADE', 409))).toContain('客户端需升级')
    expect(proxyErrorText(new WecomProxyApiError('', 'PROXY_CAPABILITY_MISSING', 409))).toContain('缺少所需能力')
    expect(proxyErrorText(new WecomProxyApiError('', 'DEVICE_OFFLINE', 409))).toContain('离线')
    expect(proxyErrorText(new WecomProxyApiError('', 'UNKNOWN_CODE', 409))).toBe('操作失败（UNKNOWN_CODE）')
    expect(proxyErrorText(new Error('网络错误'))).toBe('网络错误')
  })
})

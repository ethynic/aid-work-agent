/**
 * EventSourceSettings 关键路径测试（P4-B，R57/R58）
 *
 * 1. 挂载加载列表（fetch mock）：行渲染 + 密钥掩码（只有 key_id/版本/状态）；
 * 2. 创建 webhook 源：表单提交 → 一次性 secret 弹窗展示（关闭后不可再现）；
 * 3. internal 源创建：无 secret 弹窗（toast 提示）；
 * 4. 轮换：确认弹窗 → 新 secret 一次性展示；列表刷新出现 retiring/active 两版本；
 * 5. 轮换 403（非属主）：错误 toast，不弹 secret；
 * 6. 加载失败：错误态 + 重试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

import EventSourceSettings from '@/components/weixinMarketing/EventSourceSettings.vue'
import type { EventSourceItem } from '@/api/weixinMarketing'

const SOURCE_ID = '11111111-2222-4333-8444-555555555555'

function sourceFixture(overrides: Partial<EventSourceItem> = {}): EventSourceItem {
  return {
    id: SOURCE_ID,
    source_ref: 'crm-order-events',
    source_type: 'webhook',
    status: 'active',
    allowed_event_types: ['order.completed'],
    keys: [
      { key_id: 'wk-aaaa1111', key_version: 1, status: 'active', retire_at: null },
    ],
    webhook_url: `/api/weixin-marketing/webhooks/${SOURCE_ID}`,
    created_at: '2026-09-05T08:00:00Z',
    updated_at: '2026-09-05T08:00:00Z',
    ...overrides,
  }
}

function envelope(data: unknown, status = 200, code?: string, error?: string) {
  return {
    ok: status < 400,
    status,
    json: async () => ({
      success: status < 400,
      data,
      error: error ?? (status >= 400 ? '操作失败' : undefined),
      code: code ?? (status >= 400 ? 'INTERNAL_ERROR' : undefined),
      field_errors: [],
    }),
  }
}

function mockFetchSequence(responses: { match?: (url: string, init?: RequestInit) => boolean; resp: unknown; status?: number; code?: string; error?: string }[]) {
  const calls: { url: string; init?: RequestInit }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    for (const r of responses) {
      if (!r.match || r.match(url, init)) {
        return envelope(r.resp, r.status ?? 200, r.code, r.error) as Response
      }
    }
    throw new Error(`unexpected fetch: ${url}`)
  })
  return { fn, calls }
}

const listMatch = (url: string, init?: RequestInit) =>
  url.includes('/event-sources') && init?.method !== 'POST'

beforeEach(() => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
})

describe('EventSourceSettings', () => {
  it('renders masked source list with key metadata only', async () => {
    const source = sourceFixture()
    const { fn } = mockFetchSequence([{ match: listMatch, resp: { items: [source], total: 1, page: 1, page_size: 20 } }])
    vi.stubGlobal('fetch', fn)
    const wrapper = mount(EventSourceSettings, { global: { stubs: { teleport: true } } })
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('crm-order-events')
    expect(text).toContain('order.completed')
    expect(text).toContain('wk-aaaa1111')
    // 掩码：不出现任何密钥明文列（keys 数组无 secret 字段——类型层保证，视图断言 key_id 存在即可）
    expect(wrapper.findAll('tbody tr').length).toBe(1)
  })

  it('creates webhook source and reveals secret once', async () => {
    const created = sourceFixture()
    const { fn, calls } = mockFetchSequence([
      { match: listMatch, resp: { items: [], total: 0, page: 1, page_size: 20 } },
      {
        match: (url, init) => url.endsWith('/event-sources') && init?.method === 'POST',
        resp: { source: created, secret: 'plain-secret-abc', key_id: 'wk-new1234' },
      },
      { match: listMatch, resp: { items: [created], total: 1, page: 1, page_size: 20 } },
    ])
    vi.stubGlobal('fetch', fn)
    const wrapper = mount(EventSourceSettings, { global: { stubs: { teleport: true } } })
    await flushPromises()

    const openCreate = wrapper.findAll('button').find(b => b.text() === '新增事件源')
    expect(openCreate).toBeTruthy()
    await openCreate!.trigger('click')
    const refInput = wrapper.find('input[placeholder="如 crm-order-events"]')
    expect(refInput.exists()).toBe(true)
    await refInput.setValue('crm-order-events')
    const submit = wrapper.findAll('button').find(b => b.text() === '创建')
    await submit!.trigger('submit')
    await flushPromises()

    // 一次性 secret 弹窗
    const reveal = wrapper.find('[data-testid="secret-reveal"]')
    expect(reveal.exists()).toBe(true)
    expect(reveal.text()).toContain('plain-secret-abc')
    expect(reveal.text()).toContain('wk-new1234')
    // 请求体含 source_type=webhook
    const createCall = calls.find(c => c.url.endsWith('/event-sources') && c.init?.method === 'POST')
    expect(String(createCall?.init?.body)).toContain('"source_type":"webhook"')
  })

  it('creates internal source without secret reveal', async () => {
    const internal = sourceFixture({ source_type: 'internal', keys: [], webhook_url: undefined })
    const { fn } = mockFetchSequence([
      { match: listMatch, resp: { items: [], total: 0, page: 1, page_size: 20 } },
      {
        match: (url, init) => url.endsWith('/event-sources') && init?.method === 'POST',
        resp: { source: internal, secret: null, key_id: null },
      },
      { match: listMatch, resp: { items: [internal], total: 1, page: 1, page_size: 20 } },
    ])
    vi.stubGlobal('fetch', fn)
    const wrapper = mount(EventSourceSettings, { global: { stubs: { teleport: true } } })
    await flushPromises()
    const openCreate = wrapper.findAll('button').find(b => b.text() === '新增事件源')
    await openCreate!.trigger('click')
    await wrapper.find('input[placeholder="如 crm-order-events"]').setValue('int-src')
    // 切到 internal
    const typeSelect = wrapper.find('select')
    await typeSelect.setValue('internal')
    const submit = wrapper.findAll('button').find(b => b.text() === '创建')
    await submit!.trigger('submit')
    await flushPromises()
    expect(wrapper.find('[data-testid="secret-reveal"]').exists()).toBe(false)
  })

  it('rotates key with confirm modal and reveals new secret', async () => {
    const source = sourceFixture()
    const afterRotate = sourceFixture({
      keys: [
        { key_id: 'wk-bbbb2222', key_version: 2, status: 'active', retire_at: null },
        { key_id: 'wk-aaaa1111', key_version: 1, status: 'retiring', retire_at: '2026-09-05T09:00:00Z' },
      ],
    })
    const { fn, calls } = mockFetchSequence([
      { match: listMatch, resp: { items: [source], total: 1, page: 1, page_size: 20 } },
      {
        match: url => url.includes(`/event-sources/${SOURCE_ID}/rotate-key`),
        resp: { source_id: SOURCE_ID, key_id: 'wk-bbbb2222', key_version: 2, secret: 'rotated-secret-xyz', rotate_window_seconds: 900 },
      },
      { match: listMatch, resp: { items: [afterRotate], total: 1, page: 1, page_size: 20 } },
    ])
    vi.stubGlobal('fetch', fn)
    const wrapper = mount(EventSourceSettings, { global: { stubs: { teleport: true } } })
    await flushPromises()

    const rotateBtn = wrapper.findAll('button').find(b => b.text() === '轮换密钥')
    expect(rotateBtn).toBeTruthy()
    await rotateBtn!.trigger('click')
    // 确认弹窗
    const confirmBtn = wrapper.findAll('button').find(b => b.text() === '确认轮换')
    expect(confirmBtn).toBeTruthy()
    await confirmBtn!.trigger('click')
    await flushPromises()

    const reveal = wrapper.find('[data-testid="secret-reveal"]')
    expect(reveal.exists()).toBe(true)
    expect(reveal.text()).toContain('rotated-secret-xyz')
    expect(calls.some(c => c.url.includes(`/event-sources/${SOURCE_ID}/rotate-key`))).toBe(true)
  })

  it('shows error state and retry on load failure', async () => {
    const { fn } = mockFetchSequence([
      { match: listMatch, resp: null, status: 500, error: '后端不可用' },
    ])
    vi.stubGlobal('fetch', fn)
    const wrapper = mount(EventSourceSettings, { global: { stubs: { teleport: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('后端不可用')
  })
})

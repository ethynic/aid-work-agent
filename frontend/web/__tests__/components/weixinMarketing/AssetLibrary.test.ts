/**
 * AssetLibrary 关键路径测试（P4-A，R57/R58）
 *
 * 1. 打开加载列表（fetch mock）：网格渲染 + images_enabled 开关展示；
 * 2. images_enabled=false：上传禁用与提示（门禁明示，不静默）；
 * 3. 上传成功（FakeXHR）：进度回调 → 完成态 → 列表刷新；
 * 4. 取消上传：abort 移除队列项（无孤儿资源——服务端只在校验通过后落盘）；
 * 5. 上传失败：错误态 + 服务端错误文案展示；
 * 6. 引用保护删除：409 ASSET_IN_USE → 错误文案展示（不关闭确认框）；
 * 7. 选择模式：点击素材回传 selected 并关闭。
 *
 * 列表/预览走 fetch mock；上传走 FakeXMLHttpRequest（进度/取消需 XHR）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

import AssetLibrary from '@/components/weixinMarketing/AssetLibrary.vue'
import type { AssetItem } from '@/api/weixinMarketing'

const ASSET_A = 'aaaaaaaa-1111-4111-8111-111111111111'
const ASSET_B = 'bbbbbbbb-2222-4222-8222-222222222222'

function assetFixture(id: string, overrides: Partial<AssetItem> = {}): AssetItem {
  return {
    id,
    mime: 'image/png',
    size: 2048,
    width: 120,
    height: 80,
    status: 'active',
    reference_count: 0,
    created_at: '2026-09-01T08:00:00Z',
    retention_until: '2026-12-01T08:00:00Z',
    ...overrides,
  }
}

function envelope(data: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => ({
      success: status < 400,
      data,
      error: status >= 400 ? '素材正被 1 个内容版本引用（草稿或已发布），请先在任务中移除引用后再删除' : undefined,
      code: status >= 400 ? 'ASSET_IN_USE' : undefined,
      field_errors: [],
    }),
  } as unknown as Response
}

function blobResponse() {
  return { ok: true, status: 200, blob: async () => new Blob(['x'], { type: 'image/png' }) } as unknown as Response
}

// ==================== FakeXHR（进度/取消）====================

class FakeXMLHttpRequest {
  static instances: FakeXMLHttpRequest[] = []
  status = 0
  response: unknown = null
  responseType = ''
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = {
    onprogress: null,
  }
  // 直接持有 on* 属性（与真实 XHR 一致：组件对 xhr.onload/onerror/onabort 赋值）
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  sent = false
  aborted = false
  requestHeaders: Record<string, string> = {}

  constructor() {
    FakeXMLHttpRequest.instances.push(this)
  }

  open() {}
  setRequestHeader(key: string, value: string) {
    this.requestHeaders[key] = value
  }
  send() {
    this.sent = true
  }
  abort() {
    this.aborted = true
    this.status = 0
    this.onabort?.()
  }
  simulateProgressAndSuccess(body: unknown, status = 200) {
    this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 })
    this.status = status
    this.response = body
    this.onload?.()
  }
  simulateError() {
    this.onerror?.()
  }
}

const xhrInstances = () => FakeXMLHttpRequest.instances

beforeEach(() => {
  FakeXMLHttpRequest.instances = []
  vi.stubGlobal('XMLHttpRequest', FakeXMLHttpRequest as unknown as typeof XMLHttpRequest)
  vi.stubGlobal(
    'URL',
    Object.assign(URL, {
      createObjectURL: vi.fn(() => `blob:mock-${Math.random().toString(16).slice(2)}`),
      revokeObjectURL: vi.fn(),
    }),
  )
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openLibrary(listBody: unknown = {
  items: [assetFixture(ASSET_A), assetFixture(ASSET_B, { reference_count: 2 })],
  total: 2, page: 1, page_size: 12, images_enabled: true,
}) {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any, init?: any) => {
    const url = String(input)
    if (url.includes('/assets/') && url.includes('/content')) return blobResponse()
    // DELETE 默认模拟引用保护 409（ASSET_IN_USE）——删除用例断言该路径
    if (init?.method === 'DELETE') return envelope(listBody, 409)
    return envelope(listBody)
  })
  const wrapper = mount(AssetLibrary, {
      props: { modelValue: false, selectMode: true },
      global: { stubs: { teleport: true } },
    })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  return { wrapper, fetchMock }
}

describe('AssetLibrary', () => {
  it('打开加载列表并渲染网格（引用计数展示）', async () => {
    const { wrapper } = await openLibrary()
    const tiles = wrapper.findAll('[data-testid="asset-tile"]')
    expect(tiles).toHaveLength(2)
    expect(wrapper.text()).toContain('120×80')
    expect(wrapper.find('[data-testid="asset-referenced"]').text()).toContain('2 个版本引用')
  })

  it('images_enabled=false：上传禁用并明示提示', async () => {
    const { wrapper } = await openLibrary({
      items: [], total: 0, page: 1, page_size: 12, images_enabled: false,
    })
    expect(wrapper.text()).toContain('图片内容未启用')
    const chooseBtn = wrapper.findAll('button').find(b => b.text().includes('选择文件'))!
    expect(chooseBtn.attributes('disabled')).toBeDefined()
  })

  it('上传成功：进度回调 → 完成 → 列表刷新', async () => {
    let listCalls = 0
    const uploaded = assetFixture(ASSET_A)
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.includes('/weixin-marketing/assets')) {
        listCalls += 1
        return envelope({
          items: listCalls > 1 ? [uploaded] : [],
          total: listCalls > 1 ? 1 : 0,
          page: 1, page_size: 12, images_enabled: true,
        })
      }
      if (url.includes('/content')) return blobResponse()
      return envelope({})
    })
    // 打开（初始加载）后上传
    const wrapper = mount(AssetLibrary, {
      props: { modelValue: false },
      global: { stubs: { teleport: true } },
    })
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(listCalls).toBe(1)

    const file = new File([new Uint8Array([1, 2, 3])], 'pic.png', { type: 'image/png' })
    // 触发内部上传（onFilesChosen 经隐藏 input 的 change）
    const input = wrapper.find('input[type="file"]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    await input.dispatchEvent(new Event('change'))
    await flushPromises()

    expect(xhrInstances().length).toBe(1)
    const xhr = xhrInstances()[0]
    expect(xhr.sent).toBe(true)
    xhr.simulateProgressAndSuccess({ success: true, data: uploaded })
    await flushPromises()

    expect(wrapper.text()).toContain('完成')
    // 成功后刷新列表（初始 1 次 + 上传成功 1 次）
    expect(listCalls).toBeGreaterThanOrEqual(2)
    expect(fetchMock).toHaveBeenCalled()
    expect(wrapper.findAll('[data-testid="asset-tile"]').length).toBe(1)
  })

  it('取消上传：abort 移除队列项（取消回收）', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      envelope({ items: [], total: 0, page: 1, page_size: 12, images_enabled: true }),
    )
    const wrapper = mount(AssetLibrary, {
      props: { modelValue: true },
      global: { stubs: { teleport: true } },
    })
    await flushPromises()

    const file = new File([new Uint8Array([1])], 'pic.png', { type: 'image/png' })
    const input = wrapper.find('input[type="file"]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    await input.dispatchEvent(new Event('change'))
    await flushPromises()

    expect(wrapper.find('[data-testid="upload-queue"]').exists()).toBe(true)
    const cancelBtn = wrapper.findAll('button').find(b => b.text() === '取消')!
    await cancelBtn.trigger('click')
    await flushPromises()
    expect(xhrInstances()[0].aborted).toBe(true)
    expect(wrapper.find('[data-testid="upload-queue"]').exists()).toBe(false)
  })

  it('上传失败：服务端错误文案展示（伪 mime/超限等由服务端权威判定）', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      envelope({ items: [], total: 0, page: 1, page_size: 12, images_enabled: true }),
    )
    const wrapper = mount(AssetLibrary, {
      props: { modelValue: true },
      global: { stubs: { teleport: true } },
    })
    await flushPromises()

    const file = new File([new Uint8Array([1])], 'pic.png', { type: 'image/png' })
    const input = wrapper.find('input[type="file"]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    await input.dispatchEvent(new Event('change'))
    await flushPromises()

    xhrInstances()[0].simulateProgressAndSuccess(
      { success: false, error: '图片数据损坏或无法解码，已拒绝', code: 'VALIDATION_FAILED' },
      422,
    )
    await flushPromises()
    expect(wrapper.text()).toContain('失败')
    // 重试按钮可用（同一文件重传）
    expect(wrapper.findAll('button').some(b => b.text() === '重试')).toBe(true)
  })

  it('删除 409 ASSET_IN_USE：错误文案展示且不关闭确认框', async () => {
    const { wrapper } = await openLibrary()
    const deleteBtn = wrapper.findAll('button').find(b => b.text() === '删除')!
    await deleteBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('确认删除素材')

    const confirmBtn = wrapper.findAll('button').find(b => b.text() === '确认删除')!
    await confirmBtn.trigger('click')
    await flushPromises()

    // 409 → 服务端文案直出（引用保护），确认框保留供用户阅读
    expect(wrapper.find('[data-testid="delete-error"]').text()).toContain('请先在任务中移除引用')
    expect(wrapper.text()).toContain('确认删除素材')
  })

  it('选择模式：点击素材回传 selected 并关闭', async () => {
    const { wrapper } = await openLibrary()
    const firstTileButton = wrapper.find('[data-testid="asset-tile"] button')
    await firstTileButton.trigger('click')
    const selected = wrapper.emitted('selected')
    expect(selected).toBeTruthy()
    expect((selected![0][0] as AssetItem).id).toBe(ASSET_A)
    const closeEvents = wrapper.emitted('update:modelValue') || []; expect(closeEvents[closeEvents.length - 1]).toEqual([false])
  })

  it('并发上限 3：第 4 个文件排队', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      envelope({ items: [], total: 0, page: 1, page_size: 12, images_enabled: true }),
    )
    const wrapper = mount(AssetLibrary, {
      props: { modelValue: true },
      global: { stubs: { teleport: true } },
    })
    await flushPromises()

    const files = [1, 2, 3, 4, 5].map(
      i => new File([new Uint8Array([i])], `pic${i}.png`, { type: 'image/png' }),
    )
    const input = wrapper.find('input[type="file"]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: files, configurable: true })
    await input.dispatchEvent(new Event('change'))
    await flushPromises()

    // 同时只允许 3 个 XHR 发出；其余排队
    expect(xhrInstances().filter(x => x.sent).length).toBe(3)
    expect(wrapper.text()).toContain('排队中')
  })
})

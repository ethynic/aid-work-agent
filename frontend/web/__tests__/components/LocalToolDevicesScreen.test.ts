/**
 * 「本地工具」页实时画面入口测试
 *
 * 验证点（设计 6.3 / D12）：
 * - 设备行操作列新增「实时画面」按钮；
 * - 离线、已撤销、Runtime 不支持（screen_stream 缺省 false）时禁用并给出原因提示；
 * - 在线且支持时点击打开观看弹框（传入对应设备）；
 * - 页面容器放宽到 max-w-6xl。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import LocalToolDevices from '@/components/saas/LocalToolDevices.vue'
import ScreenViewerModal from '@/components/saas/ScreenViewerModal.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { tenant_id: 'tenant' } }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ admin: ref({ user_id: 'user' }), isLoggedIn: ref(true), logout: vi.fn() }) }))

const DEVICES = [
  { device_id: 'ok-1', name: '在线支持', platform: 'windows', runtime_version: '1.2.0', selected: true, status: 'active', online: true, screen_stream: true, last_seen_at: null, created_at: null },
  { device_id: 'noscreen-1', name: '在线不支持', platform: 'windows', runtime_version: '1.1.0', selected: false, status: 'active', online: true, screen_stream: false, last_seen_at: null, created_at: null },
  { device_id: 'offline-1', name: '离线', platform: 'windows', runtime_version: '1.2.0', selected: false, status: 'active', online: false, screen_stream: true, last_seen_at: null, created_at: null },
  { device_id: 'revoked-1', name: '已撤销', platform: 'windows', runtime_version: '1.2.0', selected: false, status: 'revoked', online: false, screen_stream: true, last_seen_at: null, created_at: null },
]

vi.mock('@/api/localTools', () => ({
  listDevices: vi.fn(async () => DEVICES.map((device) => ({ ...device }))),
  createPairingTicket: vi.fn(),
  createReplacementPairingTicket: vi.fn(),
  selectDevice: vi.fn(),
  revokeDevice: vi.fn(),
}))

let wrapper: ReturnType<typeof mount> | undefined

async function render() {
  wrapper = mount(LocalToolDevices, {
    global: { stubs: { AppHeader: true, ScreenViewerModal: true }, provide: { sidebarCollapsed: ref(false), toggleSidebar: vi.fn() } },
  })
  await flushPromises()
  return wrapper
}

/** 按设备名称找到所在行，再取该行的「实时画面」按钮 */
function screenButton(deviceName: string) {
  const row = wrapper!.findAll('tr').find((tr) => tr.text().includes(deviceName))!
  return row.findAll('button').find((b) => b.text().trim() === '实时画面')!
}

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => { wrapper?.unmount(); wrapper = undefined })

describe('实时画面入口', () => {
  it('设备行操作列出现「实时画面」，页面容器放宽到 max-w-6xl', async () => {
    const view = await render()
    expect(view.find('.max-w-6xl').exists()).toBe(true)
    expect(view.findAll('button').filter((b) => b.text().trim() === '实时画面')).toHaveLength(4)
  })

  it('在线且支持 → 可用；点击打开弹框并传入该设备', async () => {
    const view = await render()
    const modal = view.findComponent(ScreenViewerModal)
    expect(modal.props('device')).toBe(null)
    await screenButton('在线支持').trigger('click')
    await flushPromises()
    const modalAfter = view.findComponent(ScreenViewerModal)
    expect(modalAfter.props('modelValue')).toBe(true)
    expect(modalAfter.props('device')?.device_id).toBe('ok-1')
  })

  it('离线设备禁用并提示原因', async () => {
    await render()
    const button = screenButton('离线')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('title')).toBe('设备离线，无法观看实时画面')
  })

  it('Runtime 不支持（screen_stream=false）禁用并提示升级', async () => {
    await render()
    const button = screenButton('在线不支持')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('title')).toBe('当前 Runtime 版本不支持实时画面')
  })

  it('已撤销设备禁用并提示原因', async () => {
    await render()
    const button = screenButton('已撤销')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('title')).toBe('设备已撤销，无法观看实时画面')
  })
})

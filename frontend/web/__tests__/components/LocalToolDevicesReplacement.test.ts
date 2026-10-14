import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import LocalToolDevices from '@/components/saas/LocalToolDevices.vue'
import { createPairingTicket, createReplacementPairingTicket, revokeDevice, selectDevice } from '@/api/localTools'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { tenant_id: 'tenant' } }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ admin: ref({ user_id: 'user' }), isLoggedIn: ref(true), logout: vi.fn() }) }))
vi.mock('@/api/localTools', () => ({
  listDevices: vi.fn(async () => [{ device_id: 'old-one', name: '同名电脑', status: 'active', online: true }, { device_id: 'old-two', name: '同名电脑', status: 'revoked', online: false }]),
  createPairingTicket: vi.fn(async () => ({ code: 'NORMAL23', expires_at: '2099-01-01T00:00:00' })),
  createReplacementPairingTicket: vi.fn(async (id: string) => ({ code: 'REPLACE2', expires_at: '2099-01-01T00:00:00', replacement_device_id: id })),
  selectDevice: vi.fn(), revokeDevice: vi.fn(),
}))
let wrapper: ReturnType<typeof mount> | undefined
const confirm = vi.fn(() => true), alert = vi.fn()
beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('confirm', confirm); vi.stubGlobal('alert', alert); confirm.mockReturnValue(true) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals() })
async function render() { wrapper = mount(LocalToolDevices, { global: { stubs: { AppHeader: true }, provide: { sidebarCollapsed: ref(false), toggleSidebar: vi.fn() } } }); await flushPromises(); return wrapper }
function button(label: string, index = 0) { return wrapper!.findAll('button').filter(x => x.text() === label)[index]! }

it('explicit replacement confirms old id, issues bound ticket and never revokes or selects early', async () => {
  await render(); await button('更换此设备').trigger('click'); await flushPromises()
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('old-one'))
  expect(createReplacementPairingTicket).toHaveBeenCalledExactlyOnceWith('old-one')
  expect(wrapper!.text()).toContain('旧设备编号：old-one')
  expect(wrapper!.text()).toContain('新版 Runtime')
  expect(wrapper!.text()).toContain('旧执行资源交接')
  expect(wrapper!.text()).not.toContain('agent-tool-runtime pair')
  expect(wrapper!.text()).toContain('已撤销')
  expect(button('选定', 1).attributes()).toHaveProperty('disabled')
  expect(revokeDevice).not.toHaveBeenCalled(); expect(selectDevice).not.toHaveBeenCalled()
})
it('regeneration keeps same explicit old target; normal mode is an explicit separate choice', async () => {
  await render(); await button('更换此设备', 1).trigger('click'); await flushPromises(); await button('重新生成').trigger('click'); await flushPromises()
  expect(createReplacementPairingTicket).toHaveBeenNthCalledWith(1, 'old-two'); expect(createReplacementPairingTicket).toHaveBeenNthCalledWith(2, 'old-two')
  expect(createPairingTicket).not.toHaveBeenCalled()
  await button('改为添加普通设备').trigger('click'); await button('生成配对码').trigger('click'); await flushPromises()
  expect(createPairingTicket).toHaveBeenCalledOnce(); expect(wrapper!.text()).not.toContain('旧设备编号：')
})
it('cancelled confirmation sends nothing', async () => {
  confirm.mockReturnValue(false); await render(); await button('更换此设备').trigger('click'); await flushPromises()
  expect(createReplacementPairingTicket).not.toHaveBeenCalled(); expect(revokeDevice).not.toHaveBeenCalled()
})
it('failed re-generation keeps target but discards stale code; retry still uses same old id', async () => {
  await render(); await button('更换此设备').trigger('click'); await flushPromises()
  vi.mocked(createReplacementPairingTicket).mockRejectedValueOnce(new Error('服务端暂不支持设备替换，请更新'))
  await button('重新生成').trigger('click'); await flushPromises()
  expect(wrapper!.text()).toContain('旧设备编号：old-one'); expect(wrapper!.text()).not.toContain('REPLACE2'); expect(alert).toHaveBeenCalledWith(expect.stringContaining('更新'))
  await button('生成配对码').trigger('click'); await flushPromises()
  expect(createReplacementPairingTicket).toHaveBeenLastCalledWith('old-one'); expect(createPairingTicket).not.toHaveBeenCalled()
})

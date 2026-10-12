import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import RuntimeManagementPage from '../RuntimeManagementPage.vue'
import type { ManagementRequest, RuntimeManagementPort } from '../contracts'

const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.forEach((wrapper) => wrapper.unmount()); wrappers.length = 0; localStorage.clear(); document.body.innerHTML = '' })
function mountPage(options: { features?: string[]; state?: string; ready?: boolean; version?: string; pairReject?: boolean; recovery?: boolean } = {}) {
  const requests: ManagementRequest[] = []
  const port: RuntimeManagementPort = {
    request: vi.fn(async (request) => {
      requests.push(request)
      const base = { request_id: request.request_id, method: request.method, code: 0, error: '' }
      if (request.method === 'describe') return { ...base, result: { api_major: 1, host_version: '0.2.14', features: options.features ?? ['events', 'first_party_plugins'], instance_id: 'host-one', revision: 1 } }
      if (request.method === 'getState') return { ...base, result: { instance_id: 'host-one', revision: 1, state: options.state ?? 'stopped', supervisor: 'runtime_app', connection: 'offline', device: { device_id: 'device-one', name: '测试设备' }, ...(options.state === 'reconciling' ? { blocked_reason: '历史执行事实缺少结案证明' } : {}) } }
      if (request.method === 'plugins.list') return { ...base, result: { instance_id: 'host-one', revision: 1, plugins: [{ installation_id: 'installed-one', plugin_id: 'weixin', display_name: '微信', release_id: 'opaque-release', enabled: true, ready: options.ready ?? false, reason: '请登录微信', ...(options.version ? { version: options.version } : {}) }] } }
      if (request.method === 'pair' && options.pairReject) return { ...base, code: 11, error: '历史执行事实缺少结案证明', result: null }
      return { ...base, result: { operation_id: 'operation-one', operation: request.method.replace('plugins.', ''), status: 'succeeded', code: 0, error: '' } }
    }),
    observe: vi.fn(() => vi.fn()),
    choosePackage: vi.fn(async () => ({ selection_ref: 'selection-one', label: '微信离线包.zip' })),
  }
  if (options.recovery) {
    port.diagnoseConnection = vi.fn(async () => ({ version: 1, category: 'CONFIG_INVALID', summary: '原连接配置格式损坏', steps: ['重新配置并使用新配对码。', '完成后在网页选定新设备。'], service_verified: false, can_preserve: false, can_reconfigure: true, requires_reboot: false }))
    port.reconfigureConnection = vi.fn(async () => ({ version: 1, status: 'action_required', new_device: false, diagnosis: { version: 1, category: 'NETWORK_FAILED', summary: '无法连接企业服务', steps: ['检查网络与服务地址，恢复后检查连接。'], service_verified: false, can_preserve: false, can_reconfigure: true, requires_reboot: false } }))
  }
  const wrapper = mount(RuntimeManagementPage, { props: { port }, attachTo: document.body })
  wrappers.push(wrapper)
  return { wrapper, port, requests }
}
function button(wrapper: VueWrapper, text: string) { const found = wrapper.findAll('button').find((element) => element.text() === text); if (!found) throw new Error(`Missing button ${text}`); return found }

describe('Runtime management page', () => {
  it('offers explicit recovery independently of blocked normal pairing and shows concrete instructions', async () => {
    const { wrapper, port } = mountPage({ state: 'blocked', recovery: true }); await flushPromises()
    expect(port.diagnoseConnection).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('原连接配置格式损坏')
    expect(wrapper.text()).toContain('完成后在网页选定新设备')
    expect(wrapper.get('#runtime-pairing-code').attributes()).toHaveProperty('disabled')
    expect(button(wrapper, '重新配置连接').attributes()).not.toHaveProperty('disabled')
    expect(wrapper.text()).not.toContain('已收到服务器心跳响应')
  })
  it('requires confirmation, clears recovery code immediately, and does not replay a failed request', async () => {
    const { wrapper, port } = mountPage({ state: 'blocked', recovery: true }); await flushPromises()
    let finish!: (value: unknown) => void
    port.reconfigureConnection = vi.fn(() => new Promise(resolve => { finish = resolve }))
    await button(wrapper, '重新配置连接').trigger('click')
    await wrapper.get('#recovery-server').setValue('https://service.example')
    await wrapper.get('#recovery-name').setValue('原设备名称')
    await wrapper.get('#recovery-code').setValue('recovery-otp-test')
    expect(button(wrapper, '确认处理').attributes()).toHaveProperty('disabled')
    await wrapper.get('.runtime-confirm input').setValue(true)
    await wrapper.findAll('form')[1]!.trigger('submit')
    await flushPromises()
    expect(port.reconfigureConnection).toHaveBeenCalledWith({ mode: 'new_device', confirmed: true, server: 'https://service.example', device_name: '原设备名称', pairing_code: 'recovery-otp-test' })
    expect(wrapper.find('#recovery-code').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('recovery-otp-test'); expect(localStorage.length).toBe(0)
    finish({ version: 1, status: 'action_required', new_device: true, diagnosis: { version: 1, category: 'PAIRING_CODE_INVALID', summary: '配对码已过期', steps: ['生成新配对码再提交。'], service_verified: false, can_preserve: false, can_reconfigure: true, requires_reboot: false } })
    await flushPromises(); expect(wrapper.text()).toContain('生成新配对码再提交'); expect(port.reconfigureConnection).toHaveBeenCalledTimes(1)
  })
  it('distinguishes enabled from ready and never derives version from release identifier', async () => {
    const { wrapper } = mountPage(); await flushPromises()
    expect(wrapper.text()).toContain('已启用')
    expect(wrapper.text()).toContain('未就绪')
    expect(wrapper.text()).toContain('请登录微信')
    expect(wrapper.text()).toContain('版本未知')
    expect(wrapper.text()).not.toContain('opaque-release')
    expect(wrapper.findAll('input').every((input) => wrapper.find(`label[for="${input.attributes('id')}"]`).exists())).toBe(true)
  })
  it('requires explicit confirmation before uninstall and preserves opaque installation identity', async () => {
    const { wrapper, requests } = mountPage(); await flushPromises()
    await button(wrapper, '卸载').trigger('click')
    expect(requests.some((request) => request.method === 'plugins.uninstall')).toBe(false)
    await button(wrapper, '确认卸载').trigger('click'); await flushPromises()
    expect(requests.find((request) => request.method === 'plugins.uninstall')?.params).toMatchObject({ installation_id: 'installed-one', request_key: expect.any(String) })
  })
  it('clears submitted pairing code, displays precise rejection and does not persist it', async () => {
    const { wrapper, requests } = mountPage({ pairReject: true }); await flushPromises()
    await wrapper.get('#runtime-server').setValue('https://service.example')
    await wrapper.get('#runtime-device-name').setValue('设备名称')
    await wrapper.get('#runtime-pairing-code').setValue('one-time-sensitive')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(requests.find((request) => request.method === 'pair')?.params.pairing_code).toBe('one-time-sensitive')
    expect((wrapper.get('#runtime-pairing-code').element as HTMLInputElement).value).toBe('')
    expect(wrapper.get('[role="alert"]').text()).toContain('代码 11')
    expect(localStorage.length).toBe(0)
    expect(wrapper.text()).not.toContain('one-time-sensitive')
  })
  it('shows draining as a wait condition and prevents starting or pairing', async () => {
    const { wrapper } = mountPage({ state: 'draining' }); await flushPromises()
    expect(wrapper.text()).toContain('等待已接受的任务收尾')
    expect(button(wrapper, '启动执行').attributes()).toHaveProperty('disabled')
    expect(button(wrapper, '停止执行').attributes()).toHaveProperty('disabled')
    expect(wrapper.get('#runtime-pairing-code').attributes()).toHaveProperty('disabled')
  })
  it('offers the existing stop check while reconciling without allowing start or identity replacement', async () => {
    const { wrapper, requests } = mountPage({ state: 'reconciling' }); await flushPromises()
    expect(button(wrapper, '启动执行').attributes()).toHaveProperty('disabled')
    expect(wrapper.get('#runtime-pairing-code').attributes()).toHaveProperty('disabled')
    expect(button(wrapper, '停止执行').attributes()).not.toHaveProperty('disabled')
    await button(wrapper, '停止执行').trigger('click'); await flushPromises()
    expect(requests.some(request => request.method === 'stop')).toBe(true)
    expect(requests.some(request => request.method === 'start' || request.method === 'pair')).toBe(false)
    expect(button(wrapper, '重新连接管理服务').exists()).toBe(true)
  })
  it('removes installation controls when the producer has not negotiated the capability', async () => {
    const { wrapper } = mountPage({ features: ['events'] }); await flushPromises()
    expect(wrapper.text()).toContain('尚未提供第一方插件管理能力')
    expect(wrapper.findAll('button').some((element) => element.text() === '安装 / 升级离线包')).toBe(false)
    expect(wrapper.findAll('button').some((element) => element.text() === '卸载')).toBe(false)
  })
  it('page unmount only unsubscribes observation and never requests Host stop', async () => {
    const { wrapper, requests, port } = mountPage({ state: 'running' }); await flushPromises(); wrapper.unmount()
    expect(vi.mocked(port.observe)).toHaveBeenCalledOnce()
    expect(requests.some((request) => request.method === 'stop')).toBe(false)
  })
})

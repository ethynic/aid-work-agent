import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import DesktopApp from '@desktop/DesktopApp.vue'
import { desktopControllerKey } from '@desktop/app/context'

describe('Desktop update-required blocker', () => {
  it('moves through check, download and install actions and releases the update listener', async () => {
    let listener: ((state: DesktopUpdateState) => void) | undefined
    const unsubscribe = vi.fn()
    const updates = {
      getState: vi.fn(async () => ({ status: 'idle', currentVersion: '1.0.0' } as DesktopUpdateState)),
      onState: vi.fn((callback: (state: DesktopUpdateState) => void) => { listener = callback; return unsubscribe }),
      check: vi.fn(async () => undefined),
      download: vi.fn(async () => undefined),
      restartAndInstall: vi.fn(async () => undefined),
    }
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: { runtime: { platform: 'win32' }, updates },
    })
    const controller = {
      state: { phase: 'update-required', connectivity: 'online', session: null, currentVersion: '1.0.0', minimumVersion: '2.0.0' },
    }
    const wrapper = mount(DesktopApp, { global: { provide: { [desktopControllerKey as symbol]: controller } } })
    await flushPromises()
    await wrapper.get('button').trigger('click')
    expect(updates.check).toHaveBeenCalledOnce()
    listener?.({ status: 'available', currentVersion: '1.0.0', availableVersion: '2.0.0' })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('button').text()).toBe('下载更新')
    await wrapper.get('button').trigger('click')
    expect(updates.download).toHaveBeenCalledOnce()
    listener?.({ status: 'downloaded', currentVersion: '1.0.0', availableVersion: '2.0.0' })
    await wrapper.vm.$nextTick()
    await wrapper.get('button').trigger('click')
    expect(updates.restartAndInstall).toHaveBeenCalledOnce()
    wrapper.unmount()
    expect(unsubscribe).toHaveBeenCalledOnce()
  })

  it('does not let a stale initial updater snapshot overwrite a newer event', async () => {
    let listener: ((state: DesktopUpdateState) => void) | undefined
    let resolveInitial: ((state: DesktopUpdateState) => void) | undefined
    const updates = {
      getState: vi.fn(() => new Promise<DesktopUpdateState>((resolve) => { resolveInitial = resolve })),
      onState: vi.fn((callback: (state: DesktopUpdateState) => void) => { listener = callback; return vi.fn() }),
      check: vi.fn(), download: vi.fn(), restartAndInstall: vi.fn(),
    }
    Object.defineProperty(window, 'agentDesktop', { configurable: true, value: { runtime: { platform: 'win32' }, updates } })
    const controller = { state: { phase: 'update-required', connectivity: 'online', session: null, currentVersion: '1.0.0', minimumVersion: '2.0.0' } }
    const wrapper = mount(DesktopApp, { global: { provide: { [desktopControllerKey as symbol]: controller } } })
    listener?.({ status: 'available', currentVersion: '1.0.0', availableVersion: '2.0.0' })
    resolveInitial?.({ status: 'idle', currentVersion: '1.0.0' })
    await flushPromises()
    expect(wrapper.get('button').text()).toBe('下载更新')
    wrapper.unmount()
  })
})

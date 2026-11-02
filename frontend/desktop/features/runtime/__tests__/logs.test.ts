import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import RuntimeLogsPanel from '../RuntimeLogsPanel.vue'
import type { RuntimeLogsPort, RuntimeLogSnapshot } from '../logContracts'

const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers.length = 0 })
const snapshot: RuntimeLogSnapshot = { version: 1, generatedAt: '2026-11-02T01:00:00Z', hours: 24,
  range: { from: '2026-11-01T01:00:00Z', to: '2026-11-02T01:00:00Z', observedFrom: null, observedTo: null },
  versions: { app: '0.0.33', host: null, core: null }, entries: [], sources: [{ name: 'runtime', status: 'unreadable', bytesRead: 0, totalBytes: 0, skippedLines: 0, partialLines: 0, truncated: false }], health: null, truncated: false }
function render(logs?: RuntimeLogsPort) { const w = mount(RuntimeLogsPanel, { props: { logs } }); wrappers.push(w); return w }
describe('Runtime logs', () => {
  it('reads and exports local evidence independently of a Host or connection port, with gaps visible', async () => {
    const logs: RuntimeLogsPort = { read: vi.fn(async () => snapshot), export: vi.fn(async () => ({ canceled: false, fileName: 'Runtime日志.zip' })) }
    const w = render(logs); await flushPromises()
    expect(logs.read).toHaveBeenCalledWith({ hours: 24 })
    expect(w.text()).toContain('部分历史日志不可用')
    expect(w.text()).not.toContain('全部采集完成')
    await w.get('select').setValue(1); await flushPromises()
    expect(logs.read).toHaveBeenLastCalledWith({ hours: 1 })
    await w.findAll('button').find(b => b.text() === '导出日志')!.trigger('click'); await flushPromises()
    expect(logs.export).toHaveBeenCalledWith({ hours: 1 })
    expect(w.text()).toContain('已导出：Runtime日志.zip')
  })
  it('allows export after a read failure and treats cancel as a non-error', async () => {
    const logs: RuntimeLogsPort = { read: vi.fn(async () => { throw Error('unreadable') }), export: vi.fn(async () => ({ canceled: true })) }
    const w = render(logs); await flushPromises()
    expect(w.text()).toContain('日志暂时无法读取')
    const save = w.findAll('button').find(b => b.text() === '导出日志')!
    expect(save.attributes()).not.toHaveProperty('disabled')
    await save.trigger('click'); await flushPromises()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.text()).not.toContain('已导出')
  })
  it('keeps an older bridge usable when the optional logs capability is absent', () => {
    const w = render()
    expect(w.text()).toContain('尚未提供日志功能')
    expect(w.findAll('button')[0]!.attributes()).toHaveProperty('disabled')
  })
})

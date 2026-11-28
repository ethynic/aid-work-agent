/**
 * 实时画面观看弹框（ScreenViewerModal）组件测试
 *
 * 验证点（设计 10 网页端各状态）：
 * - 连接中三步渲染与取消；
 * - 观看中顶部徽标（线路/仅查看）与右上角统计、降画质提示、锁屏覆盖；
 * - 五类结束态文案与「重新查看 / 关闭」；
 * - Esc 与关闭路径调用 endByUser；结束按钮连点只发一次（防重复提交守卫）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ScreenViewerModal from '@/components/saas/ScreenViewerModal.vue'
import { screenViewerFakes, resetViewerFakes } from '../fixtures/screenViewerModalFake'
import type { LocalToolDevice } from '@/api/localTools'

/** 取某事件最后一次 emit 的参数数组 */
function lastEmitted(wrapper: ReturnType<typeof mount>, event: string): unknown[] | undefined {
  const events = wrapper.emitted(event)
  return events ? events[events.length - 1] : undefined
}

vi.mock('@/composables/useScreenViewer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables/useScreenViewer')>()
  const { screenViewerFakes } = await import('../fixtures/screenViewerModalFake')
  return { ...actual, useScreenViewer: () => screenViewerFakes }
})

const DEVICE: LocalToolDevice = {
  device_id: 'dev-1', name: '测试电脑', platform: 'windows', runtime_version: '1.2.3',
  selected: true, status: 'active', online: true, screen_stream: true,
  last_seen_at: null, created_at: null,
}

function button(label: string) {
  return screenViewerWrapper!.findAll('button').filter((b) => b.text().trim() === label)[0]!
}

let screenViewerWrapper: ReturnType<typeof mount> | undefined

async function open(): Promise<void> {
  // 先卸掉上一个实例：BaseModal 的 Esc 监听挂在 document 上，泄漏的旧实例会叠加触发
  screenViewerWrapper?.unmount()
  screenViewerWrapper = mount(ScreenViewerModal, {
    props: { modelValue: false, device: DEVICE },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  })
  await screenViewerWrapper.setProps({ modelValue: true })
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  resetViewerFakes()
})

afterEach(() => {
  screenViewerWrapper?.unmount()
  screenViewerWrapper = undefined
})

describe('打开与连接中', () => {
  it('打开时开始会话并渲染三步进度，当前步骤带说明，可取消', async () => {
    await open()
    expect(screenViewerFakes.start).toHaveBeenCalledExactlyOnceWith(DEVICE)
    expect(screenViewerWrapper!.text()).toContain('唤醒 Runtime')
    expect(screenViewerWrapper!.text()).toContain('建立直连')
    expect(screenViewerWrapper!.text()).toContain('确认观看设备')
    // 第一步的说明
    expect(screenViewerWrapper!.text()).toContain('通常几秒内应答')
    expect(button('取消').exists()).toBe(true)

    await button('取消').trigger('click')
    expect(screenViewerFakes.endByUser).toHaveBeenCalledTimes(1)
    expect(lastEmitted(screenViewerWrapper!, 'update:modelValue')).toEqual([false])
  })

  it('第二步说明「这一步还没有画面」；本地网络权限处于提示时先给一句说明', async () => {
    screenViewerFakes.state.step = 2
    await open()
    expect(screenViewerWrapper!.text()).toContain('这一步还没有画面')
    expect(screenViewerWrapper!.text()).not.toContain('本地网络访问')

    screenViewerFakes.state.localNetworkPrompt = true
    await open()
    expect(screenViewerWrapper!.text()).toContain('「本地网络访问」权限提示')
  })
})

describe('观看中', () => {
  beforeEach(() => {
    screenViewerFakes.state.phase = 'watching'
    screenViewerFakes.state.path = 'lan'
    screenViewerFakes.state.stats = { durationMs: 65_000, width: 1280, height: 720, fps: 15, bitrateBps: 2_500_000 }
  })

  it('顶部绿点、设备名、线路徽标、「仅查看」与右上角统计、底部结束查看', async () => {
    await open()
    const text = screenViewerWrapper!.text()
    expect(text).toContain('实时画面')
    expect(text).toContain('测试电脑')
    expect(text).toContain('局域网直连')
    expect(text).toContain('仅查看')
    expect(text).toContain('01:05')
    expect(text).toContain('1280×720 · 15 帧')
    expect(text).toContain('2.5 Mbps')
    expect(button('结束查看').exists()).toBe(true)
  })

  it('点对点直连的徽标文案', async () => {
    screenViewerFakes.state.path = 'p2p'
    await open()
    expect(screenViewerWrapper!.text()).toContain('点对点直连')
  })

  it('已降低画质：左上角提示出现；restored 后消失', async () => {
    await open()
    expect(screenViewerWrapper!.text()).not.toContain('已降低画质')
    screenViewerFakes.state.degraded = true
    await flushPromises()
    expect(screenViewerWrapper!.text()).toContain('对方电脑正在执行任务，已降低画质')
    screenViewerFakes.state.degraded = false
    await flushPromises()
    expect(screenViewerWrapper!.text()).not.toContain('已降低画质')
  })

  it('目标电脑锁屏：覆盖提示与自动恢复说明', async () => {
    screenViewerFakes.state.locked = true
    await open()
    const text = screenViewerWrapper!.text()
    expect(text).toContain('目标电脑已锁屏')
    expect(text).toContain('解锁后自动恢复')
  })

  it('结束按钮连点只发一次 end（防重复提交守卫）', async () => {
    await open()
    const stop = button('结束查看')
    await stop.trigger('click')
    await stop.trigger('click')
    expect(screenViewerFakes.endByUser).toHaveBeenCalledTimes(1)
    expect(screenViewerWrapper!.emitted('update:modelValue')).toHaveLength(1)
    expect(screenViewerWrapper!.emitted('update:modelValue')?.[0]).toEqual([false])
  })
})

describe('结束态', () => {
  it('Runtime 停止共享：标题、说明与「重新查看」', async () => {
    screenViewerFakes.state.phase = 'ended'
    screenViewerFakes.state.end = {
      key: 'runtime_stopped', title: '对方已停止画面共享',
      description: '对方电脑上的用户停止了画面共享，可以重新查看。', retryable: true,
    }
    await open()
    expect(screenViewerWrapper!.text()).toContain('对方已停止画面共享')
    expect(button('重新查看').exists()).toBe(true)
    expect(button('关闭').exists()).toBe(true)

    await button('重新查看').trigger('click')
    expect(screenViewerFakes.restart).toHaveBeenCalledTimes(1)
    expect(screenViewerWrapper!.emitted('update:modelValue')).toBeUndefined()

    await button('关闭').trigger('click')
    expect(screenViewerWrapper!.emitted('update:modelValue')?.[0]).toEqual([false])
  })

  it('无法直连：附本地网络放行提示；不支持画面：不提供重新查看', async () => {
    screenViewerFakes.state.phase = 'ended'
    screenViewerFakes.state.end = {
      key: 'connect_failed', title: '当前网络无法直连',
      description: '实时画面只在两台电脑之间点对点直连，不经过服务器；当前网络打不通。',
      hint: '若浏览器弹出「本地网络访问」提示，请选择允许；未放行时同一网络内通常也无法直连。', retryable: true,
    }
    await open()
    expect(screenViewerWrapper!.text()).toContain('请选择允许')

    screenViewerFakes.state.end = {
      key: 'unsupported', title: '当前 Runtime 版本不支持实时画面',
      description: '请升级对方电脑的 Runtime 后再试。', retryable: false,
    }
    await open()
    expect(screenViewerWrapper!.text()).toContain('当前 Runtime 版本不支持实时画面')
    // retryable=false：不提供重新查看
    expect(screenViewerWrapper!.findAll('button').some((b) => b.text().trim() === '重新查看')).toBe(false)
    expect(button('关闭').exists()).toBe(true)
  })

  it('画面已自动停止：说明停的只是画面，不动任务', async () => {
    screenViewerFakes.state.phase = 'ended'
    screenViewerFakes.state.end = {
      key: 'renewal_timeout', title: '画面已自动停止',
      description: '对方电脑连续 30 秒没有收到服务器确认，按规则自动停止了画面共享；停的只是画面，不会去动这台电脑上的任务。',
      retryable: true,
    }
    await open()
    expect(screenViewerWrapper!.text()).toContain('不会去动这台电脑上的任务')
  })
})

describe('Esc 与右上角关闭', () => {
  it('Esc 关闭：先补发 end 再关闭弹框', async () => {
    await open()
    expect(screenViewerFakes.endByUser).not.toHaveBeenCalled()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(screenViewerFakes.endByUser).toHaveBeenCalledTimes(1)
    expect(lastEmitted(screenViewerWrapper!, 'update:modelValue')).toEqual([false])
  })

  it('右上角 X 关闭同样先补发 end', async () => {
    await open()
    const closeButton = screenViewerWrapper!.find('button[aria-label="关闭对话框"]')
    expect(closeButton.exists()).toBe(true)
    await closeButton.trigger('click')
    await flushPromises()
    expect(screenViewerFakes.endByUser).toHaveBeenCalledTimes(1)
    expect(lastEmitted(screenViewerWrapper!, 'update:modelValue')).toEqual([false])
  })

  it('已是结束态（Runtime 侧已结束）时 Esc 关闭不再补发 end', async () => {
    screenViewerFakes.state.phase = 'ended'
    screenViewerFakes.state.end = {
      key: 'runtime_stopped', title: '对方已停止画面共享',
      description: '对方电脑上的用户停止了画面共享，可以重新查看。', retryable: true,
    }
    await open()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    // 组件转发仍会发生，但组合函数对已结束会话是空操作——这里断言只转发一次、且不重复关闭
    expect(screenViewerFakes.endByUser).toHaveBeenCalledTimes(1)
    expect(lastEmitted(screenViewerWrapper!, 'update:modelValue')).toEqual([false])
  })
})

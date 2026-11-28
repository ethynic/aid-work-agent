/**
 * ScreenViewerModal 组件测试用的 useScreenViewer 假实现。
 *
 * 组件内通过 useScreenViewer() 拿到的就是这个对象；测试直接改 state 字段来驱动各状态渲染，
 * 并用 start / endByUser / restart / dispose 的调用记录断言用户动作转发。
 */
import { reactive, shallowRef } from 'vue'
import { vi } from 'vitest'
import type { ScreenViewerState } from '@/composables/useScreenViewer'

export const screenViewerFakes = {
  start: vi.fn(),
  endByUser: vi.fn(),
  restart: vi.fn(),
  dispose: vi.fn(),
  state: reactive<ScreenViewerState>({
    phase: 'connecting',
    step: 1,
    deviceName: '测试电脑',
    path: null,
    locked: false,
    degraded: false,
    localNetworkPrompt: false,
    stats: { durationMs: 0, width: null, height: null, fps: null, bitrateBps: null },
    end: null,
  }),
  track: shallowRef<MediaStreamTrack | null>(null),
  stream: shallowRef<MediaStream | null>(null),
}

export function resetViewerFakes(): void {
  const fake = screenViewerFakes
  fake.start.mockClear()
  fake.endByUser.mockClear()
  fake.restart.mockClear()
  fake.dispose.mockClear()
  fake.state.phase = 'connecting'
  fake.state.step = 1
  fake.state.deviceName = '测试电脑'
  fake.state.path = null
  fake.state.locked = false
  fake.state.degraded = false
  fake.state.localNetworkPrompt = false
  fake.state.stats = { durationMs: 0, width: null, height: null, fps: null, bitrateBps: null }
  fake.state.end = null
  fake.track.value = null
  fake.stream.value = null
}

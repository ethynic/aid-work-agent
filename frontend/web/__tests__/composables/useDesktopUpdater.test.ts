import { beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.resetModules()
  vi.restoreAllMocks()
  Reflect.deleteProperty(window, 'agentDesktop')
})

describe('useDesktopUpdater', () => {
  it('Web 环境无 Desktop bridge 时不显示更新入口也不发起网络请求', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch')
    const { useDesktopUpdater } = await import('@/composables/useDesktopUpdater')
    const updater = useDesktopUpdater()

    expect(updater.state.value.status).toBe('disabled')
    expect(updater.isVisible.value).toBe(false)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('Desktop 只订阅一次状态并按状态触发下载或确认安装', async () => {
    let listener: ((state: AgentDesktopUpdateState) => void) | undefined
    const download = vi.fn().mockResolvedValue(undefined)
    const restartAndInstall = vi.fn().mockResolvedValue(undefined)
    const onState = vi.fn((callback: (state: AgentDesktopUpdateState) => void) => {
      listener = callback
      return vi.fn()
    })
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: {
        version: 2,
        updates: {
          getState: vi.fn().mockResolvedValue({ status: 'idle', currentVersion: '0.0.2' }),
          onState,
          check: vi.fn().mockResolvedValue(undefined),
          download,
          restartAndInstall,
        },
      },
    })
    const { useDesktopUpdater } = await import('@/composables/useDesktopUpdater')
    const first = useDesktopUpdater()
    useDesktopUpdater()
    expect(onState).toHaveBeenCalledTimes(1)

    listener?.({ status: 'available', currentVersion: '0.0.2', availableVersion: '0.0.3' })
    expect(first.isVisible.value).toBe(true)
    await first.activate()
    expect(download).toHaveBeenCalledTimes(1)

    listener?.({ status: 'downloaded', currentVersion: '0.0.2', availableVersion: '0.0.3', percent: 100 })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await first.activate()
    expect(restartAndInstall).toHaveBeenCalledTimes(1)
  })

  it('初始化快照不得覆盖订阅后收到的新版本事件', async () => {
    let resolveInitialState!: (state: AgentDesktopUpdateState) => void
    let listener: ((state: AgentDesktopUpdateState) => void) | undefined
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: {
        version: 2,
        updates: {
          getState: vi.fn(() => new Promise<AgentDesktopUpdateState>((resolve) => { resolveInitialState = resolve })),
          onState: vi.fn((callback: (state: AgentDesktopUpdateState) => void) => {
            listener = callback
            return vi.fn()
          }),
          check: vi.fn().mockResolvedValue(undefined),
          download: vi.fn().mockResolvedValue(undefined),
          restartAndInstall: vi.fn().mockResolvedValue(undefined),
        },
      },
    })
    const { useDesktopUpdater } = await import('@/composables/useDesktopUpdater')
    const updater = useDesktopUpdater()
    listener?.({ status: 'available', currentVersion: '0.0.2', availableVersion: '0.0.3' })
    resolveInitialState({ status: 'idle', currentVersion: '0.0.2' })
    await Promise.resolve()

    expect(updater.state.value.status).toBe('available')
    expect(updater.state.value.availableVersion).toBe('0.0.3')
  })
})

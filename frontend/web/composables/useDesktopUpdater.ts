import { computed, readonly, ref } from 'vue'

const disabledState: AgentDesktopUpdateState = { status: 'disabled', currentVersion: '' }
const state = ref<AgentDesktopUpdateState>(disabledState)
let initialized = false

function initialize(): void {
  if (initialized) return
  initialized = true
  const updates = window.agentDesktop?.updates
  if (!updates) return
  void updates.getState().then((value) => {
    // Do not let the initial snapshot overwrite a newer event delivered while
    // the invoke response was in flight.
    if (state.value.status === 'disabled' && state.value.currentVersion === '') state.value = value
  }).catch(() => undefined)
  updates.onState((value) => { state.value = value })
}

export function useDesktopUpdater() {
  initialize()
  const isVisible = computed(() => ['available', 'downloading', 'downloaded', 'error'].includes(state.value.status))
  const label = computed(() => {
    switch (state.value.status) {
      case 'available': return `发现新版本 ${state.value.availableVersion ?? ''}`.trim()
      case 'downloading': return `正在下载 ${Math.round(state.value.percent ?? 0)}%`
      case 'downloaded': return '更新已就绪，点击重启安装'
      case 'error': return '更新失败，点击重试'
      default: return ''
    }
  })
  const activate = async (): Promise<void> => {
    const updates = window.agentDesktop?.updates
    if (!updates) return
    if (state.value.status === 'available') await updates.download()
    else if (state.value.status === 'downloaded') {
      if (window.confirm('更新已下载完成。现在重启并安装新版本吗？')) await updates.restartAndInstall()
    } else if (state.value.status === 'error') await updates.check()
  }
  return { state: readonly(state), isVisible, label, activate }
}

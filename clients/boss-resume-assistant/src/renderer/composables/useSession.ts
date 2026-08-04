/**
 * 会话状态 composable：拉取当前状态 + 订阅主进程事件推送。
 * LoginGate / RunConsole 共用，避免重复实现订阅与清理逻辑。
 */
import { ref, onMounted, onUnmounted } from 'vue'
import type { SessionStatusPayload, SessionEventPayload } from '@shared/ipc'

export function useSession() {
  const status = ref<SessionStatusPayload | null>(null)
  const events = ref<SessionEventPayload[]>([])
  let unsubscribe: (() => void) | null = null

  async function refresh() {
    status.value = await window.bossResume.session.status()
  }

  onMounted(async () => {
    await refresh()
    unsubscribe = window.bossResume.session.onEvent((e) => {
      events.value = [e, ...events.value].slice(0, 200)
      if (e.type === 'state') {
        // 状态推进事件到达后刷新完整状态（含统计）
        void refresh()
      }
    })
  })

  onUnmounted(() => {
    unsubscribe?.()
  })

  return { status, events, refresh }
}

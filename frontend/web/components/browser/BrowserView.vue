<template>
  <section class="overflow-hidden rounded-xl border border-slate-200 bg-slate-950 shadow-sm">
    <header class="flex items-center justify-between border-b border-white/10 px-3 py-2 text-xs text-slate-300">
      <div class="flex items-center gap-2">
        <span :class="['h-2 w-2 rounded-full', connected ? 'bg-emerald-400' : 'bg-amber-400']"></span>
        <span>服务端浏览器</span>
        <span class="text-slate-500">{{ connected ? '画面已连接' : '正在连接' }}</span>
      </div>
      <span class="rounded bg-white/5 px-2 py-1">{{ controlEnabled ? '人工控制' : '只读观察' }}</span>
    </header>
    <p v-if="notice" class="px-3 py-2 text-xs text-gray-300" role="status">{{ notice }}</p>
    <div
      ref="surface"
      class="relative aspect-video bg-slate-900 outline-none"
      :tabindex="controlEnabled ? 0 : -1"
      @click="sendPointer('click', $event)"
      @mousemove="sendPointer('move', $event)"
      @wheel.prevent="sendWheel"
      @keydown.prevent="sendKey"
    >
      <img v-if="frameUrl" :src="frameUrl" alt="浏览器实时画面" class="h-full w-full select-none object-contain" draggable="false" />
      <div v-else class="absolute inset-0 grid place-items-center text-sm text-slate-500">
        等待浏览器画面…
      </div>
      <div v-if="!controlEnabled" class="absolute inset-0 cursor-default" aria-label="当前为只读观察模式"></div>
    </div>
    <footer class="flex items-center justify-between px-3 py-2 text-[11px] text-slate-500">
      <span>画面不会保存到服务器</span>
      <span v-if="controlEnabled">点击画面后可使用键盘</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { browserApi } from '@/api/agent'

const props = defineProps<{ runId: string; assistanceId?: string; authHeaders: Record<string, string>; controlEnabled: boolean }>()
const connected = ref(false)
const frameUrl = ref('')
const surface = ref<HTMLElement | null>(null)
let socket: WebSocket | null = null
let awaitingFrame = false
let lastPointerMoveAt = 0
const notice = ref('')
const identity = computed(() => JSON.stringify([props.runId, props.assistanceId, Object.entries(props.authHeaders).sort()]))
let mounted = false
let generation = 0

function detach() {
  generation++
  if (socket) {
    socket.onopen = socket.onclose = socket.onmessage = socket.onerror = null
    socket.close()
    socket = null
  }
  connected.value = false
  awaitingFrame = false
  if (frameUrl.value) URL.revokeObjectURL(frameUrl.value)
  frameUrl.value = ''
}

async function connect() {
  detach()
  if (!mounted) return
  const currentGeneration = generation
  const original = identity.value
  const runId = props.runId
  const headers = { ...props.authHeaders }
  const current = () => mounted && generation === currentGeneration && identity.value === original
  notice.value = ''
  try {
    const { ticket } = await browserApi.viewTicket(runId, headers)
    if (!current()) return
    const active = new WebSocket(browserApi.viewWebSocketUrl(runId, ticket))
    socket = active
    active.binaryType = 'blob'
    active.onopen = () => { if (current()) connected.value = true }
    active.onclose = () => { if (current()) connected.value = false }
    active.onerror = () => { if (current()) notice.value = '画面暂时无法连接，请稍后重试。' }
    active.onmessage = (event) => {
      if (!current() || socket !== active) return
      if (typeof event.data === 'string') {
        try {
          const message = JSON.parse(event.data)
          awaitingFrame = message.type === 'frame'
          if (message.type === 'input_rejected') notice.value = '当前操作未获许可，请核对接管状态。'
        } catch { awaitingFrame = false }
        return
      }
      if (awaitingFrame && event.data instanceof Blob) {
        awaitingFrame = false
        const next = URL.createObjectURL(event.data)
        if (frameUrl.value) URL.revokeObjectURL(frameUrl.value)
        frameUrl.value = next
      }
    }
  } catch {
    if (current()) {
      connected.value = false
      notice.value = '画面暂时无法连接，请稍后重试。'
    }
  }
}

watch(identity, () => { void connect() }, { flush: 'sync' })
watch(() => props.controlEnabled, () => { notice.value = '' })
onMounted(() => { mounted = true; void connect() })

onBeforeUnmount(() => {
  mounted = false
  detach()
})

function point(event: MouseEvent) {
  const rect = surface.value!.getBoundingClientRect()
  return { x: ((event.clientX - rect.left) / rect.width) * 1280, y: ((event.clientY - rect.top) / rect.height) * 720 }
}

function sendPointer(action: 'click' | 'move', event: MouseEvent) {
  if (!props.controlEnabled || socket?.readyState !== WebSocket.OPEN || !surface.value) return
  if (action === 'move') {
    const now = performance.now()
    if (now - lastPointerMoveAt < 50 || socket.bufferedAmount > 64 * 1024) return
    lastPointerMoveAt = now
  }
  if (action === 'click') surface.value.focus()
  socket.send(JSON.stringify({ type: 'pointer', action, ...point(event) }))
}

function sendWheel(event: WheelEvent) {
  if (!props.controlEnabled || socket?.readyState !== WebSocket.OPEN) return
  socket.send(JSON.stringify({ type: 'pointer', action: 'wheel', delta_x: event.deltaX, delta_y: event.deltaY }))
}

function sendKey(event: KeyboardEvent) {
  if (!props.controlEnabled || socket?.readyState !== WebSocket.OPEN) return
  const modifiers = [event.ctrlKey && 'Control', event.altKey && 'Alt', event.shiftKey && 'Shift', event.metaKey && 'Meta'].filter(Boolean)
  socket.send(JSON.stringify({ type: 'keyboard', key: [...modifiers, event.key].join('+') }))
}
</script>

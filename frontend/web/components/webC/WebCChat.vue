<template>
  <div class="h-screen flex flex-col bg-canvas" :class="{ 'embed-mode': isEmbed }">
    <!-- 页头（iframe embed 模式隐藏，设计 §4.5） -->
    <header v-if="!isEmbed" class="flex items-center gap-3 px-4 py-3 bg-surface border-b border-default">
      <div class="w-9 h-9 rounded-full bg-primary-50 text-primary-600 flex items-center justify-center shrink-0">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" class="w-5 h-5">
          <path d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="min-w-0 flex-1">
        <div class="text-sm font-semibold text-default truncate">{{ headerTitle }}</div>
        <div class="text-xs text-muted">智能助手 · 官网客服</div>
      </div>
      <button
        v-if="showHistoryEnabled && identityMode !== 'ext'"
        class="text-xs text-muted hover:text-default border border-default rounded-md px-2 py-1"
        @click="toggleHistory"
      >
        历史会话
      </button>
    </header>

    <div class="flex-1 flex min-h-0">
      <!-- 历史会话侧栏（anon 模式本浏览器历史；入口开关关闭时不渲染） -->
      <aside v-if="showHistoryEnabled && showHistory && identityMode !== 'ext'" class="w-64 border-r border-default bg-surface flex flex-col max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-10">
        <div class="px-3 py-2 border-b border-default flex items-center justify-between gap-2">
          <span class="text-xs text-muted">本浏览器历史会话</span>
          <button
            class="text-xs text-primary-600 hover:text-primary-700 border border-primary-200 rounded-md px-2 py-1 shrink-0"
            @click="startNewSession"
          >+ 新会话</button>
        </div>
        <div class="flex-1 overflow-y-auto">
          <button
            v-for="s in sessions"
            :key="s.session_id"
            class="w-full text-left px-3 py-2 border-b border-default hover:bg-surface-hover"
            :class="{ 'bg-primary-50 text-primary-700': s.session_id === currentSessionId }"
            @click="switchSession(s.session_id)"
          >
            <div class="text-xs text-default truncate">{{ sessionTitle(s) }}</div>
            <div class="text-[11px] text-muted mt-0.5">{{ formatTime(s.updated_at || s.last_message_at) }}</div>
          </button>
          <div v-if="!sessions.length" class="px-3 py-6 text-xs text-muted text-center">暂无历史会话</div>
        </div>
      </aside>

      <!-- 消息流 -->
      <div class="flex-1 flex flex-col min-w-0">
        <div ref="scrollContainer" class="flex-1 overflow-y-auto px-4 py-4 space-y-4" @scroll.passive="onScroll">
          <!-- 开场卡 -->
          <div v-if="!messages.length && !streamingText && !isLoading" class="max-w-2xl mx-auto">
            <div class="bg-surface border border-default rounded-xl p-4 text-sm text-default leading-relaxed">
              {{ greeting }}
            </div>
          </div>
          <div v-if="isLoading" class="text-center text-xs text-muted py-8">加载中...</div>

          <template v-for="(msg, index) in messages" :key="msg.message_id || index">
            <!-- 用户消息 -->
            <div v-if="msg.role === 'user'" class="flex justify-end">
              <div class="max-w-[78%] space-y-2">
                <div v-if="msg.attachments && msg.attachments.length" class="flex flex-wrap gap-2 justify-end">
                  <span
                    v-for="(att, i) in msg.attachments"
                    :key="i"
                    class="bg-surface border border-default rounded-lg px-2 py-1 text-xs text-default max-w-[200px] truncate"
                  >{{ String((att as Record<string, unknown>).name || '附件') }}</span>
                </div>
                <div v-if="msg.content" class="bg-primary-500 text-white rounded-2xl rounded-br-sm px-4 py-2 text-sm whitespace-pre-wrap break-words">{{ msg.content }}</div>
                <div v-if="msg.created_at" class="text-xs text-muted mt-1 mr-2 text-right tabular-nums">{{ formatMsgTime(msg.created_at) }}</div>
              </div>
            </div>
            <!-- AI 消息 -->
            <div v-else class="flex justify-start">
              <div class="max-w-[85%] space-y-2">
                <div v-if="msg.images && msg.images.length" class="max-w-md">
                  <ImageGallery :images="msg.images" />
                </div>
                <div
                  v-if="msg.content"
                  class="bg-surface border border-default rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm leading-relaxed markdown-body"
                  v-html="renderMarkdown(msg.content)"
                ></div>
                <div v-if="msg.files && msg.files.length" class="space-y-2">
                  <DownloadFileCard v-for="(f, i) in msg.files" :key="i" :file="f" />
                </div>
                <div v-if="msg.created_at" class="ml-2 mt-1 text-xs text-muted tabular-nums">{{ formatMsgTime(msg.created_at) }}</div>
              </div>
            </div>
          </template>

          <!-- 流式 AI 回复 -->
          <div v-if="streamingText || waitingText || streamFailed" class="flex justify-start">
            <div class="max-w-[85%] space-y-2">
              <div
                v-if="streamingText"
                class="bg-surface border border-default rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm leading-relaxed markdown-body"
                v-html="renderMarkdown(streamingText)"
              ></div>
              <div v-else-if="waitingText" class="bg-surface border border-default rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">
                {{ waitingText }}
                <div class="mt-2 text-xs text-muted">请直接回复文字继续</div>
              </div>
              <div v-else-if="streamFailed" class="text-xs text-muted">本轮对话未完成，请重试</div>
              <div v-if="streamingText && !isTerminal" class="flex items-center gap-1 text-primary-500">
                <span class="w-1.5 h-1.5 rounded-full bg-primary-500 animate-pulse"></span>
                <span class="text-xs text-muted">正在输入…</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 待发送附件 -->
        <div v-if="pendingAttachments.length" class="px-4 pb-1 flex flex-wrap gap-2">
          <div v-for="(att, i) in pendingAttachments" :key="i" class="flex items-center gap-1.5 bg-surface border border-default rounded-lg px-2 py-1 text-xs text-default">
            <span class="max-w-[160px] truncate">{{ att.name }}</span>
            <button class="text-muted hover:text-danger-500" @click="removePending(i)">×</button>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="border-t border-default bg-surface px-4 py-3">
          <div class="max-w-3xl mx-auto flex items-end gap-2">
            <label v-if="allowAttachment" class="shrink-0 cursor-pointer text-muted hover:text-primary-600 p-2" title="发送文件">
              <input type="file" class="hidden" multiple @change="onFileChosen" />
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" class="w-5 h-5">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </label>
            <textarea
              v-model="inputText"
              class="flex-1 resize-none border border-default rounded-xl px-3 py-2 text-sm text-default focus:outline-none focus:ring-1 focus:ring-primary-400 bg-canvas"
              :rows="1"
              :disabled="submitting"
              placeholder="输入消息…"
              @paste="onPaste"
              @keydown.enter.exact.prevent="submit"
            ></textarea>
            <BaseButton v-if="!submitting && !streamingActive" :disabled="!canSubmit" @click="submit">发送</BaseButton>
            <BaseButton v-else intent="secondary" @click="cancel">停止</BaseButton>
          </div>
        </div>
      </div>
    </div>

    <!-- 全局错误提示 -->
    <div v-if="errorMessage" class="fixed bottom-20 left-1/2 -translate-x-1/2 bg-danger-50 text-danger-700 text-xs px-3 py-2 rounded-lg shadow z-20">
      {{ errorMessage }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import {
  buildIdentityQuery, getBootstrap, postChat, uploadFile, cancelRunner,
  getRunner, getSessionMessages, listSessions, webCFileUrl, runnerEventsUrl,
  type IdentityQuery, type UploadRef, type RunnerSnapshot, type SessionSummary,
  type ChannelMessage
} from '@/api/webC'
import { renderMarkdown } from '@/utils/markdown'
import ImageGallery from '@/components/ui/ImageGallery.vue'
import DownloadFileCard from '@/components/DownloadFileCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

interface ChatMessage {
  message_id?: string
  role: 'user' | 'assistant'
  content: string
  created_at?: string
  attachments: Record<string, unknown>[]
  images: import('@/types').ImageRef[]
  files: import('@/types').DownloadableFile[]
}

const route = useRoute()
const tenantId = computed(() => String(route.params.tenant_id || ''))
const configId = computed(() => String(route.params.config_id || ''))
const isEmbed = computed(() => route.query.embed === '1')

const identity = ref<IdentityQuery>({ uid: '' })
const identityMode = ref('')
const identityName = ref('')
const greeting = ref('')
const headerTitle = ref('官网客服')
const subagentName = ref('')
const allowAttachment = ref(false)
const showHistoryEnabled = ref(false)
const sessions = ref<SessionSummary[]>([])
const currentSessionId = ref('')
const messages = ref<ChatMessage[]>([])
const isLoading = ref(true)
const showHistory = ref(false)
const errorMessage = ref('')

const inputText = ref('')
const pendingAttachments = ref<UploadRef[]>([])
const submitting = ref(false)
const streamingText = ref('')
const waitingText = ref('')
const streamFailed = ref(false)
const isTerminal = ref(true)
const streamingActive = ref(false)
const scrollContainer = ref<HTMLElement | null>(null)

let observeAbort = new AbortController()
const activeRunnerId = ref('')

onBeforeUnmount(() => {
  // 组件卸载：中止进行中的 SSE/轮询连接与刷新循环（防泄漏）
  observeAbort.abort()
  submitting.value = false
  streamingActive.value = false
})

const canSubmit = computed(() => (inputText.value.trim().length > 0 || pendingAttachments.value.length > 0))

onMounted(async () => {
  identity.value = buildIdentityQuery(configId.value, route.query as Record<string, unknown>)
  try {
    const data = await getBootstrap(tenantId.value, configId.value, identity.value)
    greeting.value = data.greeting
    identityMode.value = data.identity?.mode || ''
    identityName.value = data.identity?.display_name || ''
    subagentName.value = data.subagent?.name || ''
    allowAttachment.value = data.allow_attachment === true
    showHistoryEnabled.value = data.show_history === true
    headerTitle.value = subagentName.value ? `${subagentName.value}` : '官网客服'
    sessions.value = data.sessions || []
    if (sessions.value.length) {
      await openSession(sessions.value[0].session_id)
    } else {
      isLoading.value = false
    }
  } catch (e) {
    isLoading.value = false
    errorMessage.value = e instanceof Error ? e.message : '渠道不可用'
    // ext-only 渠道用匿名打开会被 403：给出可理解的提示
    if (!identity.value.u && !identity.value.uid) errorMessage.value = '请从官网聊天入口进入'
  }
})

function sessionTitle(s: SessionSummary): string {
  const prefix = subagentName.value ? `[${subagentName.value}] ` : ''
  return `${prefix}会话 ${formatTime(s.updated_at || s.created_at || '')}`
}

function formatTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** 消息时间戳：MM-DD HH:mm:ss（与主聊天页 MessageItem 一致） */
function formatMsgTime(value?: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

// ============== 消息加载与渲染 ==============

function newClientId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `wc_${crypto.randomUUID()}`
  }
  return `wc_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`
}

/** 本地乐观渲染消息的时间戳（服务端权威校准以 reload 为准） */
function nowIso(): string {
  return new Date().toISOString()
}

async function openSession(sessionId: string) {
  currentSessionId.value = sessionId
  await reloadMessages()
}

async function switchSession(sessionId: string) {
  if (sessionId === currentSessionId.value) return
  showHistory.value = false
  resetStreamState()
  await openSession(sessionId)
}

/** 新会话：本地进入空白聊天，首条消息发送时才调后端惰性创建（避免空会话垃圾数据） */
function startNewSession() {
  showHistory.value = false
  resetStreamState()
  currentSessionId.value = ''
  messages.value = []
  isLoading.value = false
  errorMessage.value = ''
}

/** 打开侧栏时刷新会话列表（新会话/其他标签页产生的会话在此同步） */
function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value) void refreshSessions()
}

async function refreshSessions() {
  try {
    const data = await listSessions(tenantId.value, configId.value, identity.value, 20)
    sessions.value = (data.sessions || []).slice().sort((a, b) =>
      (Date.parse(b.updated_at || b.created_at || '') || 0) - (Date.parse(a.updated_at || a.created_at || '') || 0))
  } catch {
    /* 列表刷新失败保持现状 */
  }
}

function resetStreamState() {
  observeAbort.abort()
  observeAbort = new AbortController()
  streamingText.value = ''
  waitingText.value = ''
  streamFailed.value = false
  isTerminal.value = true
  streamingActive.value = false
  submitting.value = false
}

async function reloadMessages() {
  if (!currentSessionId.value) return
  try {
    const data = await getSessionMessages(tenantId.value, configId.value, identity.value, currentSessionId.value, 1, 200)
    // 后端按 id 倒序返回，这里统一按时间升序渲染（与主流聊天软件一致）
    messages.value = (data.messages || []).map(toChatMessage)
      .sort((a, b) => msgTimeValue(a) - msgTimeValue(b))
  } catch {
    /* 历史加载失败不阻断输入 */
  }
  isLoading.value = false
  await nextTick()
  scrollToBottom()
}

function msgTimeValue(m: ChatMessage): number {
  const t = Date.parse(m.created_at || '')
  return Number.isNaN(t) ? 0 : t
}

function toChatMessage(m: ChannelMessage): ChatMessage {
  const attachments = Array.isArray(m.attachments) ? m.attachments : []
  const images = attachments.filter(a => (a as Record<string, unknown>).type === 'image') as unknown as import('@/types').ImageRef[]
  const files = attachments
    .filter(a => (a as Record<string, unknown>).type !== 'image')
    .map(a => toDownloadable(a as Record<string, unknown>))
  return {
    message_id: m.message_id,
    role: m.role === 'user' ? 'user' : 'assistant',
    content: m.content || '',
    created_at: m.created_at,
    attachments: attachments.filter(a => (a as Record<string, unknown>).type !== 'image'),
    images,
    files
  }
}

function toDownloadable(att: Record<string, unknown>): import('@/types').DownloadableFile {
  const fileId = String(att.file_id || '')
  return {
    file_id: fileId,
    file_name: String(att.name || att.file_name || fileId),
    file_size: Number(att.size || att.file_size || 0),
    download_url: String(att.download_url || webCFileUrl(tenantId.value, configId.value, identity.value, fileId)),
    mime_type: att.mime_type ? String(att.mime_type) : undefined
  }
}

function scrollToBottom() {
  const el = scrollContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

function onScroll() {
  /* 消息量按需分页：当前一次拉取 200 条，预留滚动加载扩展点 */
}

// ============== 附件 ==============

async function onFileChosen(event: Event) {
  if (!allowAttachment.value) return
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  for (const file of files) {
    if (pendingAttachments.value.length >= 10) {
      errorMessage.value = '单次最多 10 个附件'
      break
    }
    try {
      const ref = await uploadFile(tenantId.value, configId.value, identity.value, file)
      pendingAttachments.value.push(ref)
    } catch (e) {
      errorMessage.value = e instanceof Error ? e.message : '上传失败'
    }
  }
}

function onPaste(event: ClipboardEvent) {
  if (!allowAttachment.value) return
  const files = Array.from(event.clipboardData?.files || [])
  if (files.length) {
    event.preventDefault()
    void onFileChosen({ target: { files } } as unknown as Event)
  }
}

function removePending(index: number) {
  pendingAttachments.value.splice(index, 1)
}

// ============== 发送与流式渲染 ==============

async function submit() {
  if (!canSubmit.value || submitting.value) return
  const text = inputText.value.trim()
  const attachments = pendingAttachments.value.slice()
  inputText.value = ''
  pendingAttachments.value = []
  errorMessage.value = ''
  resetStreamState()

  // 本地先渲染用户消息（服务端落库后由 reload 校准）
  messages.value.push({
    role: 'user',
    content: text,
    created_at: nowIso(),
    attachments: attachments.map(a => ({ type: a.type, file_id: a.file_id, name: a.name, mime_type: a.mime_type, size: a.size })),
    images: [],
    files: []
  })
  scrollToBottom()
  submitting.value = true
  streamingActive.value = true
  isTerminal.value = false

  try {
    const isNewSession = !currentSessionId.value
    const reply = await postChat(tenantId.value, configId.value, identity.value, {
      text,
      client_request_id: newClientId(),
      attachments,
      session_id: currentSessionId.value || undefined,
      new_session: isNewSession || undefined
    })
    currentSessionId.value = reply.session_id
    if (isNewSession) void refreshSessions()
    if (reply.direct_reply) {
      messages.value.push({ role: 'assistant', content: reply.direct_reply, created_at: nowIso(), attachments: [], images: [], files: [] })
      submitting.value = false
      streamingActive.value = false
      isTerminal.value = true
      return
    }
    if (reply.runner_id) {
      activeRunnerId.value = reply.runner_id
      waitingText.value = reply.waiting_text || ''
      await observeRunner(reply.runner_id)
    } else {
      submitting.value = false
      streamingActive.value = false
      isTerminal.value = true
    }
  } catch (e) {
    submitting.value = false
    streamingActive.value = false
    isTerminal.value = true
    streamFailed.value = !waitingText.value
    errorMessage.value = e instanceof Error ? e.message : '发送失败'
  }
}

async function cancel() {
  // 停止按钮：服务端 cancel 终止当前执行，本地复位输入状态
  const runnerId = activeRunnerId.value
  resetStreamState()
  if (runnerId) {
    try {
      await cancelRunner(tenantId.value, configId.value, identity.value, runnerId)
    } catch {
      /* 取消失败由终态刷新/下次会话拉取兜底 */
    }
  }
}

function applySnapshot(runner: RunnerSnapshot) {
  if (runner.waiting_text) {
    waitingText.value = runner.waiting_text
    streamingText.value = ''
    return
  }
  waitingText.value = ''
  const output = runner.result?.output || runner.snapshot?.output || ''
  streamingText.value = output
  scrollToBottom()
}

async function refreshOnce(runnerId: string): Promise<'running' | 'terminal'> {
  const { runner } = await getRunner(tenantId.value, configId.value, identity.value, runnerId)
  applySnapshot(runner)
  if (['completed', 'failed', 'cancelled'].includes(runner.status)) {
    isTerminal.value = true
    if (runner.status !== 'completed') streamFailed.value = true
    return 'terminal'
  }
  return 'running'
}

/** SSE 事件流消费：事件无载荷，每帧触发快照刷新；流结束 = 执行已终态结算。
 * cursor 在 observeRunner 作用域持有（cursorRef），重连续传不重放。 */
async function consumeEvents(runnerId: string, signal: AbortSignal, cursorRef: { seq: number }): Promise<void> {
  for (;;) {
    const response = await fetch(runnerEventsUrl(tenantId.value, configId.value, identity.value, runnerId, cursorRef.seq), { signal })
    if (!response.ok || !response.body) throw new Error('SSE unavailable')
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let terminalHit = false
    let refreshTimer: ReturnType<typeof setTimeout> | null = null
    const scheduleRefresh = () => {
      if (refreshTimer || terminalHit) return
      refreshTimer = setTimeout(() => {
        refreshTimer = null
        void refreshOnce(runnerId)
          .then(state => { if (state === 'terminal') terminalHit = true })
          .catch(() => { /* 单次刷新失败容忍，等下一帧 */ })
      }, 120)
    }
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done || terminalHit || signal.aborted) break
        buffer += decoder.decode(value, { stream: true })
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) >= 0 && !terminalHit) {
          const frame = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          const seqMatch = frame.match(/^id:\s*(\d+)/m)
          if (seqMatch) cursorRef.seq = Math.max(cursorRef.seq, parseInt(seqMatch[1], 10))
          if (frame.includes('event:')) scheduleRefresh()
        }
        if (terminalHit) break
      }
    } finally {
      if (refreshTimer) clearTimeout(refreshTimer)
      void reader.cancel().catch(() => {})
    }
    if (terminalHit || signal.aborted) return
    // 流正常结束（上游在终态结算后关闭）
    return
  }
}

/** SSE 连续失败时的轮询兜底 */
async function pollUntilTerminal(runnerId: string, signal: AbortSignal) {
  for (let i = 0; i < 600; i++) {
    if (signal.aborted) return
    try {
      if (await refreshOnce(runnerId) === 'terminal') return
    } catch {
      /* 网络抖动容忍 */
    }
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
}

async function observeRunner(runnerId: string) {
  const signal = observeAbort.signal
  const cursorRef = { seq: 0 }
  let failures = 0
  try {
    while (!signal.aborted) {
      try {
        await consumeEvents(runnerId, signal, cursorRef)
        break // 流结束 = 已结算
      } catch {
        if (signal.aborted) return
        failures++
        if (failures >= 3) {
          await pollUntilTerminal(runnerId, signal)
          break
        }
        await new Promise(resolve => setTimeout(resolve, 1000 * failures))
      }
    }
  } finally {
    if (!signal.aborted) {
      try {
        await refreshOnce(runnerId)
      } catch {
        /* 终态刷新失败时由 reload 校准 */
      }
    }
    if (streamFailed.value === false && streamingText.value) {
      messages.value.push({ role: 'assistant', content: streamingText.value, created_at: nowIso(), attachments: [], images: [], files: [] })
    }
    streamingText.value = ''
    waitingText.value = ''
    streamingActive.value = false
    submitting.value = false
    // 服务端权威校准（同时触发终态补写）
    await reloadMessages()
  }
}
</script>

<style scoped>
.embed-mode {
  background: transparent;
}
.markdown-body :deep(p) {
  margin: 0.25rem 0;
}
.markdown-body :deep(pre) {
  background: var(--bg-canvas, #f5f6f8);
  border-radius: 8px;
  padding: 8px;
  overflow-x: auto;
  font-size: 12px;
}
.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: 8px;
}
</style>

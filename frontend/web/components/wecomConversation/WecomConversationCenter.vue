<template>
  <div :class="embedded ? 'h-full' : 'h-screen'" class="flex flex-col bg-canvas">
    <AppHeader
      title="企微会话"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button v-if="!embedded" class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="goDaily(); closeMenu()">
          日报工作台
        </button>
        <button v-if="!embedded" class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="goProxy(); closeMenu()">
          代理任务
        </button>
        <button class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="refreshAll(); closeMenu()">
          刷新
        </button>
      </template>
    </AppHeader>

    <main class="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
      <!-- 左：记录范围会话列表（自动纳入；无人工开关） -->
      <aside class="max-h-[35%] min-h-0 md:max-h-none md:w-72 lg:w-96 md:shrink-0 border-b md:border-b-0 md:border-r border-default bg-surface flex flex-col">
        <div class="p-3 border-b border-default">
          <p class="text-xs text-muted leading-relaxed">
            需要日报或智能体已回复的会话自动记录全部参与者消息；无人工开启/关闭流程。
          </p>
        </div>
        <div class="flex-1 overflow-y-auto" data-testid="conversation-list">
          <p v-if="!conversations.length && !loadingConversations" class="p-4 text-sm text-muted">
            暂无记录会话。智能体成功回复或在日报计划纳入会话后自动出现。
          </p>
          <button
            v-for="item in conversations"
            :key="item.id"
            class="w-full text-left px-4 py-3 flex items-center gap-3 border-b border-b-default border-l-4"
            :class="item.id === selectedId ? 'bg-primary-50 border-l-primary-500' : 'border-l-transparent hover:bg-surface-hover'"
            :aria-current="item.id === selectedId ? 'true' : undefined"
            @click="selectConversation(item)"
          >
            <span
              class="w-10 h-10 shrink-0 rounded-full flex items-center justify-center text-sm font-medium"
              :class="item.kind === 'group' ? 'bg-primary-100 text-primary-700' : 'bg-success-100 text-success-700'"
            >{{ avatarText(item) }}</span>
            <span class="flex-1 min-w-0">
              <span class="flex items-center gap-1.5">
                <span class="text-sm font-medium text-default truncate">{{ item.name || '未命名会话' }}</span>
              </span>
              <span class="block text-xs text-muted mt-0.5 truncate">
                {{ item.kind === 'group' ? '群聊' : '单聊' }} · {{ syncLabel(item) }}
              </span>
            </span>
          </button>
        </div>
      </aside>

      <!-- 右：聊天时间线（与列表同页；进入定位最新，向上滚动加载） -->
      <section class="flex-1 flex flex-col min-h-0 min-w-0">
        <div class="px-4 py-3 border-b border-default bg-surface flex items-center gap-3">
          <span
            v-if="selected"
            aria-hidden="true"
            class="w-10 h-10 shrink-0 rounded-full flex items-center justify-center text-sm font-medium"
            :class="selected.kind === 'group' ? 'bg-primary-100 text-primary-700' : 'bg-success-100 text-success-700'"
          >{{ avatarText(selected) }}</span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center flex-wrap gap-x-2 gap-y-0.5">
              <h2 class="min-w-0 truncate text-base font-medium text-default m-0">{{ selected ? selected.name || '未命名会话' : '请选择会话' }}</h2>
              <BaseBadge v-if="selected" intent="neutral">{{ selected.kind === 'group' ? '群聊' : '单聊' }} · {{ selected.timezone }}</BaseBadge>
            </div>
            <p v-if="selected" class="text-xs text-muted truncate">
              账号 {{ shortAccount(selected) }}<template v-if="selected.device_id"> · 设备 {{ shortDevice(selected) }}</template>
            </p>
          </div>
          <BaseButton class="shrink-0" size="sm" intent="secondary" :disabled="!selected" @click="reloadMessages()">刷新当前</BaseButton>
        </div>

        <div
          ref="timelineEl"
          class="flex-1 overflow-y-auto p-4 space-y-4"
          data-testid="message-timeline"
          @scroll="onScroll"
        >
          <p v-if="reachedBrowseLimit" class="text-xs text-muted text-center">
            已达前端浏览上限（最近 {{ BROWSE_LIMIT }} 条）；更早记录仍在保存中，可通过日报按日查看。
          </p>
          <p v-if="locateHint" class="text-xs text-warning-700 text-center">{{ locateHint }}</p>
          <p v-if="!selected" class="text-sm text-muted text-center mt-10">左侧选择会话查看聊天记录</p>
          <p v-else-if="loadingMessages && !messages.length" class="text-sm text-muted text-center mt-10">加载中…</p>
          <p v-else-if="!messages.length" class="text-sm text-muted text-center mt-10">暂无已保存消息；后续成功读取会自动保存</p>
          <template v-for="message in messages" :key="message.id">
            <!-- 系统/时间线事件独立呈现 -->
            <div v-if="message.sender_side === 'timeline'" class="flex justify-center">
              <span class="px-3 py-1 rounded-full bg-surface border border-default text-xs text-muted max-w-[80%] text-center">
                {{ message.text || '[系统事件]' }} · {{ timeText(message) }}
              </span>
            </div>
            <!-- 普通气泡 -->
            <div v-else class="flex gap-2" :class="message.sender_side === 'self' ? 'justify-end' : 'justify-start'">
              <span v-if="message.sender_side !== 'self'" class="w-8 h-8 shrink-0 rounded-full bg-gray-100 border border-default flex items-center justify-center text-xs text-muted">{{ avatarChar(message) }}</span>
              <div
                class="max-w-[85%] md:max-w-[70%] min-w-0 flex flex-col"
                :data-source-msg-id="message.source_message_id"
                :class="[
                  message.sender_side === 'self' ? 'items-end' : 'items-start',
                  { 'ring-2 ring-primary-400 rounded-xl': message.source_message_id === highlightMsgId },
                ]"
              >
                <div class="max-w-full break-words text-xs text-muted mb-1" :class="message.sender_side === 'self' ? 'text-right' : ''">
                  <span class="font-medium">{{ displaySender(message) }}</span>
                  <span v-if="message.sender_platform === 'wechat'" class="ml-1">（微信用户）</span>
                  <BaseBadge v-if="message.sender_side === 'self' && message.send_origin === 'agent'" intent="info" class="ml-1">Agent代发</BaseBadge>
                  <BaseBadge v-else-if="message.sender_side === 'self'" intent="warning" class="ml-1">本人，来源未确认</BaseBadge>
                </div>
                <div
                  class="max-w-full px-4 py-2 rounded-2xl text-sm [overflow-wrap:anywhere] cursor-pointer"
                  :class="message.sender_side === 'self'
                    ? 'bg-primary-500 text-white rounded-br-sm'
                    : 'bg-surface border border-default text-default rounded-bl-sm shadow-sm'"
                  @click="openDetail(message)"
                >
                  <!-- 消息类型互斥链：Markdown → 图片/文件元数据占位 → 文本 → 未解析 -->
                  <div v-if="message.text_format === 'markdown' && message.text" class="markdown-body overflow-x-auto" v-html="renderMarkdown(message.text)" />
                  <div v-else-if="message.kind === 'image' || message.kind === 'file'" class="flex items-center gap-2">
                    <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.6">
                      <path v-if="message.kind === 'image'" stroke-linecap="round" stroke-linejoin="round" d="M4 6a2 2 0 012-2h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm2 10l3-3 3 3 3-3 3 3" />
                      <path v-else stroke-linecap="round" stroke-linejoin="round" d="M14 3v5h5M6 3h9l5 5v13a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1z" />
                    </svg>
                    <span>{{ attachmentLabel(message) }}</span>
                  </div>
                  <span v-else-if="message.text" class="whitespace-pre-wrap">{{ message.text }}</span>
                  <span v-else class="opacity-70">[未解析内容]</span>
                </div>
                <span class="mt-1 px-1 text-xs text-muted">{{ timeText(message) }}</span>
              </div>
            </div>
          </template>
        </div>

        <div class="px-4 py-2 border-t border-default bg-surface flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted">
          <span v-if="selected">
            最近保存 {{ selected.last_archived_at ? new Date(selected.last_archived_at).toLocaleString() : '—' }}
            <template v-if="logError(selected)">；最近错误：{{ logError(selected) }}</template>
          </span>
          <span>{{ loadedCount }}/{{ BROWSE_LIMIT }} 条（每批 {{ PAGE_SIZE }}）</span>
        </div>
      </section>
    </main>

    <!-- 消息详情 -->
    <BaseModal v-model="detailOpen" title="消息详情" size="md">
      <div v-if="detail" class="space-y-2 text-sm text-default">
        <p v-if="detail.text" class="whitespace-pre-wrap border border-default rounded-lg p-2 bg-surface">{{ detail.text }}</p>
        <dl class="grid grid-cols-[110px_1fr] gap-y-1.5 text-xs">
          <dt class="text-muted">发言人</dt><dd>{{ detail.sender_name || '（名称未同步）' }} {{ detail.sender_id ? `· ${detail.sender_id}` : '' }}</dd>
          <dt class="text-muted">名称来源</dt><dd>{{ detail.name_source || '—' }}</dd>
          <dt class="text-muted">方向</dt><dd>{{ sideLabel(detail.sender_side) }}</dd>
          <template v-if="detail.sender_platform === 'wechat'"><dt class="text-muted">用户类型</dt><dd>微信用户</dd></template>
          <dt class="text-muted">操作来源</dt><dd>{{ originLabel(detail) }}</dd>
          <dt class="text-muted">原消息ID</dt><dd class="break-all">{{ detail.source_message_id }}（{{ detail.source_generation }}）</dd>
          <dt class="text-muted">server_id</dt><dd class="break-all">{{ detail.server_id || '—' }}</dd>
          <dt class="text-muted">发送时间</dt><dd>{{ detail.sent_at ? new Date(detail.sent_at).toLocaleString() : `未取得（原文：${detail.source_time_text || '—'}）` }}</dd>
          <dt class="text-muted">解析可信</dt><dd>{{ detail.parse_trusted ? '可信正文' : '不可信/兜底解析' }}</dd>
        </dl>
        <div v-if="detail.attachment_ref" class="border border-default rounded-lg p-2 text-xs">
          <p class="font-medium mb-1">附件元数据（暂不支持原件读取/预览）</p>
          <p>{{ attachmentLabel(detail) }}</p>
          <p class="text-muted mt-1">类型 {{ detail.attachment_ref.meta?.mime_type || '—' }} · 大小 {{ sizeText(detail) }} · 状态 {{ detail.attachment_ref.meta?.status || '—' }}</p>
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
// 企业微信Agent 工作区内嵌模式（WecomAgentHub Tab）：根容器 h-full，页内跳转入口由统一 Tab 取代
defineProps<{ embedded?: boolean }>()

import { computed, inject, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { renderMarkdown } from '@/utils/markdown'
import { useTenantAuth } from '@/composables/useTenantAuth'
import {
  listConversations, listMessages, messagePageKey,
  type WecomConversation, type WecomMessage,
} from '@/api/wecomConversation'

/** 前端浏览上限只限制页面（设计 §6.1）：每批 100 条、最多最近 1000 条。 */
const PAGE_SIZE = 100
const BROWSE_LIMIT = 1000

const route = useRoute()
const router = useRouter()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : null)

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
function handleLogout() {
  tenantLogout()
}
function goDaily() {
  router.push({ name: 'tenant-wecom-daily' })
}
function goProxy() {
  router.push({ name: 'tenant-wecom-proxy' })
}

const conversations = ref<WecomConversation[]>([])
const selectedId = ref<number | null>(null)
const selected = computed(() => conversations.value.find(item => item.id === selectedId.value) ?? null)
const loadingConversations = ref(false)

const messages = ref<WecomMessage[]>([])
const loadingMessages = ref(false)
const oldest = ref<{ sent: string; id: number } | null>(null)
const detailOpen = ref(false)
const detail = ref<WecomMessage | null>(null)
const timelineEl = ref<HTMLElement | null>(null)
const loadingOlder = ref(false)
const highlightMsgId = ref<number | null>(null)
const locateHint = ref('')

/** 异步响应绑定发起时选择（评审 P2-18）：旧请求晚到不得覆盖当前内容。 */
let requestSeq = 0

const loadedCount = computed(() => messages.value.length)
const reachedBrowseLimit = computed(() => loadedCount.value >= BROWSE_LIMIT)

function avatarText(item: WecomConversation) {
  return (item.name || '?').slice(0, 1)
}
function avatarChar(message: WecomMessage) {
  return (message.sender_name || message.sender_platform || '?').slice(0, 1)
}
function displaySender(message: WecomMessage) {
  if (message.sender_side === 'self') return '本人'
  return message.sender_name || (message.sender_platform === 'wechat' ? '微信用户' : '发言人')
}
function timeText(message: WecomMessage) {
  if (message.sent_at) return new Date(message.sent_at).toLocaleString()
  return message.source_time_text || '时间未知'
}
function sideLabel(side: string) {
  return side === 'self' ? '本人' : side === 'peer' ? '对方' : '系统/时间线'
}
function originLabel(message: WecomMessage) {
  if (message.sender_side !== 'self') return '—'
  return message.send_origin === 'agent' ? 'Agent代发（发送回执确认）' : '本人，来源未确认'
}
function syncLabel(item: WecomConversation) {
  if (!item.last_archived_at) return '尚未保存'
  return `保存于 ${new Date(item.last_archived_at).toLocaleString()}`
}
function logError(item: WecomConversation): string {
  const coverage = (item.coverage || {}) as { last_log_error?: string }
  return coverage.last_log_error || ''
}
function shortAccount(item: WecomConversation) {
  return (item.source_account_id || '').slice(-6)
}
function shortDevice(item: WecomConversation) {
  return (item.device_id || '').slice(0, 8)
}
function attachmentLabel(message: WecomMessage) {
  const meta = message.attachment_ref?.meta
  if (message.kind === 'image') return meta?.name ? `图片：${meta.name}` : '图片消息（仅元数据）'
  if (message.kind === 'file') return meta?.name ? `文件：${meta.name}` : '文件消息（仅元数据）'
  return '[附件]'
}
function sizeText(message: WecomMessage) {
  const size = message.attachment_ref?.meta?.size_bytes
  if (!size) return '—'
  return size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(size / 1024))}KB`
}

async function loadConversations() {
  loadingConversations.value = true
  try {
    const value = await listConversations()
    conversations.value = value.conversations
    const wanted = Number(route.query.conversation)
    const initial = conversations.value.find(item => item.id === wanted) ?? conversations.value[0]
    if (initial) await selectConversation(initial)
  } finally {
    loadingConversations.value = false
  }
}

async function selectConversation(item: WecomConversation) {
  selectedId.value = item.id
  highlightMsgId.value = Number(route.query.msg) || null
  await reloadMessages()
}

async function reloadMessages() {
  if (!selected.value) return
  const seq = ++requestSeq
  const conversationId = selected.value.id
  loadingMessages.value = true
  messages.value = []
  oldest.value = null
  locateHint.value = ''
  try {
    const page = await listMessages(conversationId, { limit: PAGE_SIZE })
    if (seq !== requestSeq || selectedId.value !== conversationId) return  // 评审 P2-18：晚到丢弃
    messages.value = [...page.messages].reverse()
    // 评审 P1-6：接口返回降序页，页尾键取实际最早项（page.messages 末位）
    oldest.value = messagePageKey(page.messages[page.messages.length - 1] ?? null)
    await nextTick()
    scrollToBottom()
    // 先解除首页加载护栏再做定位（评审二 P2-8）：locateHighlight 依赖
    // loadOlder 续页，loadOlder 的 loadingMessages 护栏会与首页加载互斥死锁。
    if (seq === requestSeq) loadingMessages.value = false
    if (highlightMsgId.value) await locateHighlight(conversationId, seq)
  } finally {
    if (seq === requestSeq) loadingMessages.value = false
  }
}

async function locateHighlight(conversationId: number, seq: number) {
  const target = highlightMsgId.value
  if (!target) return
  if (messages.value.some(message => message.source_message_id === target)) {
    scrollToMessage(target)
    return
  }
  // 在最近 1000 条范围内向上加载定位；超出范围如实提示（设计 §6.2）
  while (oldest.value && loadedCount.value < BROWSE_LIMIT
         && !messages.value.some(message => message.source_message_id === target)) {
    const progressed = await loadOlder(conversationId, seq)
    if (!progressed) break
  }
  if (seq !== requestSeq) return
  if (messages.value.some(message => message.source_message_id === target)) {
    scrollToMessage(target)
  } else {
    locateHint.value = `消息 ${target} 超出最近 ${BROWSE_LIMIT} 条浏览范围；更早记录请通过日报按日查看。`
  }
}

function scrollToBottom() {
  const el = timelineEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function scrollToMessage(sourceMessageId: number) {
  const el = timelineEl.value
  const node = el?.querySelector(`[data-source-msg-id="${sourceMessageId}"]`)
  if (node) {
    node.scrollIntoView({ block: 'center' })
    locateHint.value = `已定位消息 ${sourceMessageId}（高亮显示）。`
  } else {
    locateHint.value = `消息 ${sourceMessageId} 不在当前已加载范围内。`
  }
}

/** 返回是否推进了分页键；用于定位循环。 */
async function loadOlder(conversationId?: number, seq?: number): Promise<boolean> {
  const targetId = conversationId ?? selected.value?.id
  const mySeq = seq ?? requestSeq
  if (!targetId || !oldest.value || loadingOlder.value || loadingMessages.value) return false
  if (reachedBrowseLimit.value) return false
  loadingOlder.value = true
  try {
    const el = timelineEl.value
    const previousHeight = el?.scrollHeight ?? 0
    const page = await listMessages(targetId, { before: oldest.value, limit: PAGE_SIZE })
    if (mySeq !== requestSeq || selectedId.value !== targetId) return false  // 评审 P2-18
    if (!page.messages.length) return false
    messages.value = [...[...page.messages].reverse(), ...messages.value].slice(-BROWSE_LIMIT)
    // 评审 P1-6：页尾键取本页实际最早项（降序页末位），跨页不重不漏
    oldest.value = messagePageKey(page.messages[page.messages.length - 1] ?? null)
    await nextTick()
    if (el) el.scrollTop = el.scrollHeight - previousHeight
    return true
  } finally {
    loadingOlder.value = false
  }
}

function onScroll() {
  const el = timelineEl.value
  if (el && el.scrollTop < 80) loadOlder()
}

function openDetail(message: WecomMessage) {
  detail.value = message
  detailOpen.value = true
}

async function refreshAll() {
  await loadConversations()
}

onMounted(loadConversations)
</script>

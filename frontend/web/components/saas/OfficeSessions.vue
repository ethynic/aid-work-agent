<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="办公软件会话"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <!-- 顶部渠道 Tab：按租户已配置的办公软件渠道动态显示 -->
    <div class="border-b border-default bg-surface px-6">
      <div class="flex items-center">
        <div class="flex gap-6">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            :class="[
              'py-3 text-sm font-medium border-b-2 transition-colors',
              activeChannel === tab.key
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover'
            ]"
            @click="switchChannel(tab.key)"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="tabs.length === 0" class="flex-1 flex items-center justify-center">
      <div class="text-center">
        <svg class="w-16 h-16 mx-auto text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0H5m4-4h.01M12 17h.01M16 17h.01M8 13h.01M12 13h.01M16 13h.01" />
        </svg>
        <p class="text-muted">租户尚未配置企业微信 / 钉钉 / 飞书渠道</p>
      </div>
    </div>

    <div v-else class="flex-1 overflow-hidden flex relative">
      <!-- 左侧：会话用户列表 -->
      <div class="w-96 border-r border-default bg-surface flex flex-col">
        <!-- 搜索栏 -->
        <div class="p-4 border-b border-default">
          <div class="flex gap-2">
            <BaseInput
              v-model="searchUsername"
              placeholder="搜索用户名"
              size="sm"
              clearable
              class="w-full"
              @keyup.enter="handleSearch"
            />
            <BaseButton size="sm" @click="handleSearch">搜索</BaseButton>
          </div>
        </div>

        <!-- 用户列表 -->
        <div class="flex-1 overflow-y-auto">
          <div v-if="loadingUsers" class="flex items-center justify-center h-32">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="userList.length === 0" class="flex items-center justify-center h-32">
            <span class="text-muted">暂无数据</span>
          </div>
          <div v-else>
            <div
              v-for="user in userList"
              :key="user.user_id"
              class="p-4 border-b border-default cursor-pointer transition-all"
              :class="selectedUserId === user.user_id ? 'bg-primary-50 border-l-4 border-l-primary-500' : 'hover:bg-surface-hover'"
              @click="selectUser(user)"
            >
              <div class="flex items-center gap-3">
                <img
                  :src="user.avatar_url || defaultAvatar"
                  class="w-12 h-12 rounded-full object-cover bg-gray-100 ring-2 ring-gray-200"
                  alt="头像"
                />
                <div class="flex-1 min-w-0">
                  <div class="font-medium text-default truncate">{{ user.nickname || user.username || '未知用户' }}</div>
                  <div class="text-xs text-muted mt-1">
                    {{ formatSessionDateRange(user) }}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 用户分页 -->
        <div class="p-3 border-t border-default">
          <BasePagination
            :total="userTotal"
            v-model:current-page="userPage"
            :page-size="userPageSize"
            :show-size-changer="false"
          />
        </div>
      </div>

      <!-- 右侧：聊天记录 -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- 聊天记录头部 -->
        <div v-if="selectedUser" class="p-4 border-b border-default bg-surface">
          <div class="flex items-center gap-3">
            <img
              :src="selectedUser.avatar_url || defaultAvatar"
              class="w-10 h-10 rounded-full object-cover bg-gray-100"
              alt="头像"
            />
            <div>
              <div class="font-medium text-default">{{ selectedUser.nickname || selectedUser.username || '未知用户' }}</div>
              <div class="text-xs text-muted">创建于 {{ formatDate(selectedUser.created_at) }}</div>
            </div>
          </div>
        </div>

        <!-- 消息区域 -->
        <div class="flex-1 overflow-y-auto p-4" ref="messageContainerRef">
          <div v-if="!selectedUserId" class="flex items-center justify-center h-full">
            <div class="text-center">
              <svg class="w-16 h-16 mx-auto text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <p class="text-muted">请选择左侧用户查看聊天记录</p>
            </div>
          </div>
          <div v-else-if="loadingMessages" class="flex items-center justify-center h-full">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="messageList.length === 0" class="flex items-center justify-center h-full">
            <div class="text-center">
              <svg class="w-16 h-16 mx-auto text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <p class="text-muted">暂无聊天记录</p>
            </div>
          </div>
          <div v-else class="space-y-4">
            <div
              v-for="msg in visibleMessages"
              :key="msg.message_id"
              class="flex flex-col"
              :class="msg.role === 'system' ? 'items-center' : (isUserSide(msg) ? 'items-end' : 'items-start')"
            >
              <!-- 系统提示条：居中灰色小字 -->
              <div v-if="msg.role === 'system'" class="text-xs text-muted bg-gray-100 rounded-full px-3 py-1">
                {{ systemHintText(msg) }}
              </div>
              <template v-else>
              <!-- 撤回徽章：整条撤回 / 部分撤回 -->
              <div v-if="isRecalled(msg) || isPartiallyRecalled(msg)" class="mb-1 px-1">
                <BaseBadge :intent="isRecalled(msg) ? 'danger' : 'warning'">
                  {{ isRecalled(msg) ? '已撤回' : '部分已撤回' }}
                </BaseBadge>
              </div>
              <div class="max-w-[70%] rounded-2xl px-4 py-2 text-sm"
                :class="[
                  isUserSide(msg)
                    ? 'bg-primary-500 text-white rounded-br-sm'
                    : 'bg-white border border-default text-default rounded-bl-sm shadow-sm',
                  isRecalled(msg) ? 'opacity-60 line-through' : '',
                ]"
              >
                <!-- 用户消息：文本 -->
                <template v-if="msg.role === 'user' && msg.content && !hasUserAttachment(msg)">
                  <div class="whitespace-pre-wrap">{{ msg.content }}</div>
                </template>
                <!-- 用户消息：附件 -->
                <template v-if="msg.role === 'user' && hasUserAttachment(msg)">
                  <!-- 合并后的文本内容：只显示一次 -->
                  <div
                    v-if="msg.content && msg.content !== '[语音消息]' && hasUserVoice(msg)"
                    class="text-xs opacity-80 mb-2 whitespace-pre-wrap"
                  >
                    {{ msg.content }}
                  </div>
                  <!-- 语音：自定义播放按钮 -->
                  <div v-if="hasUserVoice(msg)" class="flex flex-wrap gap-2">
                    <button
                      v-for="att in getUserVoiceAttachments(msg)"
                      :key="att.media_id"
                      type="button"
                      :disabled="amrPlayer.isLoading(att.media_id)"
                      class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm transition-colors disabled:opacity-60"
                      :class="amrPlayer.isPlaying(att.media_id)
                        ? 'bg-primary-500 text-white hover:bg-primary-600'
                        : 'bg-gray-100 text-default hover:bg-gray-200'"
                      @click="onPlayVoice(att)"
                    >
                      <span v-if="amrPlayer.isLoading(att.media_id)">加载中…</span>
                      <template v-else>
                        <span class="text-base leading-none">{{ amrPlayer.isPlaying(att.media_id) ? '⏸' : '▶' }}</span>
                        <span>{{ amrPlayer.isPlaying(att.media_id) ? '正在播放' : '点击播放' }}</span>
                        <span v-if="att.duration" class="text-xs opacity-80">{{ att.duration }}"</span>
                      </template>
                    </button>
                  </div>
                  <!-- 图片：预览 -->
                  <template v-for="att in getUserAttachments(msg)" :key="'img-' + att.media_id">
                    <div v-if="att.type === 'image'" class="space-y-1">
                      <img :src="getAttachmentDownloadUrl(att)" class="max-w-[240px] max-h-[240px] rounded-lg cursor-pointer" @click="previewImage(getAttachmentDownloadUrl(att))" alt="用户图片" />
                      <div class="text-xs opacity-80">{{ att.file_name }} · {{ formatFileSize(att.file_size) }}</div>
                    </div>
                  </template>
                  <!-- 视频：播放器 -->
                  <template v-for="att in getUserAttachments(msg)" :key="'vid-' + att.media_id">
                    <div v-if="att.type === 'video'" class="space-y-1">
                      <video controls :src="getAttachmentDownloadUrl(att)" class="max-w-[320px] max-h-[240px] rounded-lg" preload="metadata"></video>
                      <div class="text-xs opacity-80">{{ att.file_name }} · {{ formatFileSize(att.file_size) }}</div>
                    </div>
                  </template>
                  <!-- 文件：下载卡片 -->
                  <template v-for="att in getUserAttachments(msg)" :key="'file-' + att.media_id">
                    <div v-if="att.type === 'file'" class="mt-3">
                      <AttachmentCard :attachment="att" :download-url="getAttachmentDownloadUrl(att)" />
                    </div>
                  </template>
                </template>
                <!-- 用户消息：无内容也无附件 -->
                <template v-if="msg.role === 'user' && !msg.content && !hasUserAttachment(msg)">
                  <div class="text-xs opacity-80 italic">[非文本消息]</div>
                </template>
                <!-- AI 消息：文本 -->
                <template v-if="msg.role === 'assistant' && msg.content">
                  <div class="whitespace-pre-wrap">{{ msg.content }}</div>
                </template>
                <!-- AI 消息：图片（ImageRef），点击放大 -->
                <div v-if="msg.role === 'assistant' && getAssistantImages(msg).length > 0" class="mt-2 flex flex-wrap gap-2">
                  <img
                    v-for="img in getAssistantImages(msg)"
                    :key="img.file_id"
                    :src="img.download_url"
                    :alt="img.display_name || '图片'"
                    loading="lazy"
                    class="max-w-[240px] max-h-[240px] rounded-lg cursor-pointer border border-default"
                    @click="previewImage(img.download_url)"
                  />
                </div>
                <!-- AI 消息：可下载文件 -->
                <div v-if="msg.role === 'assistant' && getDownloadableFiles(msg).length > 0" class="mt-3 flex flex-wrap gap-2">
                  <DownloadFileCard
                    v-for="file in getDownloadableFiles(msg)"
                    :key="file.file_id"
                    :file="file"
                  />
                </div>
              </div>
              <div class="text-xs text-muted mt-1 px-1">
                {{ formatTime(msg.created_at) }}
              </div>
              </template>
            </div>
          </div>
        </div>

        <!-- 消息分页 -->
        <div v-if="selectedUserId" class="p-3 border-t border-default bg-surface">
          <BasePagination
            :total="messageTotal"
            v-model:current-page="messagePage"
            :page-size="messagePageSize"
            :show-size-changer="false"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick, inject } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import DownloadFileCard from '@/components/DownloadFileCard.vue'
import AttachmentCard from './AttachmentCard.vue'
import { listExternalUsers, getUserSessions, getSessionMessages } from '@/api/externalCustomers'
import { listChannels } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAmrPlayer } from '@/composables/useAmrPlayer'
import { formatFileSize } from '@/utils/file'
import type { DownloadableFile } from '@/types'
import { useToast } from 'vue-toastification'

const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init, isInitialized, logout: tenantLogout } = useTenantAuth()
const amrPlayer = useAmrPlayer()
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const messageContainerRef = ref<HTMLElement | null>(null)

// 办公软件渠道定义：key 与 channel_sessions.channel_type 一致
const OFFICE_CHANNELS = [
  { key: 'wecom', label: '企业微信' },
  { key: 'dingtalk', label: '钉钉' },
  { key: 'feishu', label: '飞书' },
] as const

type OfficeChannelKey = (typeof OFFICE_CHANNELS)[number]['key']

// 租户已配置的办公软件渠道 Tab（未配置的渠道不显示）
const tabs = ref<Array<{ key: string; label: string }>>([])
const activeChannel = ref<OfficeChannelKey | ''>('')

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 搜索条件
const searchUsername = ref('')

// 用户列表
const userList = ref<any[]>([])
const userTotal = ref(0)
const userPage = ref(1)
const userPageSize = ref(20)
const loadingUsers = ref(false)

// 消息列表
const messageList = ref<any[]>([])
const messageTotal = ref(0)
const messagePage = ref(1)
const messagePageSize = ref(50)
const loadingMessages = ref(false)

// 当前选中的用户
const selectedUserId = ref('')
const selectedUser = ref<any>(null)
const selectedSessionId = ref('')

// 默认头像
const defaultAvatar = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMjAiIGhlaWdodD0iMTIwIiB2aWV3Qm94PSIwIDAgMTIwIDEyMCI+PGNpcmNsZSBjeD0iNjAiIGN5PSI2MCIgcj0iNjAiIGZpbGw9IiMwN0MxNjAiLz48Y2lyY2xlIGN4PSI2MCIgY3k9IjQ0IiByPSIxNiIgZmlsbD0iI2ZmZiIvPjxlbGxpcHNlIGN4PSI2MCIgY3k9Ijg2IiByeD0iMjgiIHJ5PSIyMiIgZmlsbD0iI2ZmZiIvPjwvc3ZnPg=='

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  const tenantRoot = window.location.pathname.match(/^\/t\/[^/]+/)?.[0]
  window.location.href = tenantRoot ? `${tenantRoot}/login` : '/portal/login'
}

function switchChannel(key: string) {
  activeChannel.value = key as OfficeChannelKey
}

async function handleSearch() {
  // 翻回第 1 页会触发 watch(userPage) -> loadUsers，避免双重请求
  if (userPage.value !== 1) {
    userPage.value = 1
  } else {
    await loadUsers()
  }
}

// 请求序号：渠道切换/搜索后丢弃过期响应，防止慢响应覆盖新数据
const loadUsersSeq = ref(0)

async function loadUsers() {
  if (!activeChannel.value) return
  const seq = ++loadUsersSeq.value
  loadingUsers.value = true
  try {
    const res = await listExternalUsers({
      username: searchUsername.value || undefined,
      channel_type: activeChannel.value,
      page: userPage.value,
      page_size: userPageSize.value,
    })
    if (seq !== loadUsersSeq.value) return
    if (res.success) {
      userList.value = res.users || []
      userTotal.value = res.total || 0

      // 默认选中第一个用户
      if (userList.value.length > 0 && !selectedUserId.value) {
        await selectUser(userList.value[0])
      }
    } else {
      toast.error(res.message || '获取用户列表失败')
    }
  } catch (e: any) {
    if (seq === loadUsersSeq.value) {
      toast.error(e.message || '获取用户列表失败')
    }
  } finally {
    if (seq === loadUsersSeq.value) {
      loadingUsers.value = false
    }
  }
}

async function selectUser(user: any) {
  if (selectedUserId.value === user.user_id) return
  selectedUserId.value = user.user_id
  selectedUser.value = user
  messageList.value = []
  messageTotal.value = 0
  messagePage.value = 1
  selectedSessionId.value = ''
  amrPlayer.stopAll()
  await loadUserSessions()
}

async function loadUserSessions() {
  if (!selectedUserId.value || !activeChannel.value) return
  try {
    const res = await getUserSessions({
      user_id: selectedUserId.value,
      channel_type: activeChannel.value,
      page: 1,
      page_size: 100,
    })
    if (res.success) {
      const sessions = res.sessions || []
      // 默认选中第一个会话（按 updated_at 降序，最近会话在前）
      if (sessions.length > 0) {
        selectedSessionId.value = sessions[0].session_id
        await loadSessionMessages()
      }
    } else {
      toast.error(res.message || '获取会话列表失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取会话列表失败')
  }
}

// 过滤掉工具调用等中间消息，只展示对用户可见的对话
const visibleMessages = computed(() =>
  messageList.value.filter(msg => {
    if (msg.role === 'tool') return false
    if (msg.role === 'assistant' && !msg.content) return false
    return true
  }),
)

// 系统提示条文案：转人工标记显示简洁提示，其余去掉内容开头的 [xxx] 标记前缀
function systemHintText(msg: any): string {
  if (msg.metadata?.kind === 'transfer_to_human_marker') {
    return '已转人工'
  }
  return String(msg.content || '').replace(/^\[[^\]]*\]\s*/, '')
}

function isUserSide(msg: any): boolean {
  return msg.role === 'user'
}

// 撤回状态判断
const isRecalled = (msg: any) => msg.is_recalled === true
const isPartiallyRecalled = (msg: any) => {
  const recalled = msg.metadata?.recalled_part_msgids
  return Array.isArray(recalled) && recalled.length > 0
}

async function loadSessionMessages() {
  if (!selectedSessionId.value) return
  loadingMessages.value = true
  try {
    const res = await getSessionMessages({
      session_id: selectedSessionId.value,
      page: messagePage.value,
      page_size: messagePageSize.value,
    })
    if (res.success) {
      messageList.value = res.messages || []
      messageTotal.value = res.total || 0
      // 滚动到顶部
      nextTick(() => {
        if (messageContainerRef.value) {
          messageContainerRef.value.scrollTop = 0
        }
      })
    } else {
      toast.error(res.message || '获取聊天记录失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取聊天记录失败')
  } finally {
    loadingMessages.value = false
  }
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  const datePart = date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const timePart = date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
  return `${datePart} ${timePart}`
}

/**
 * 格式化会话日期区间（后端聚合 first_session_at / last_session_at）
 */
function formatSessionDateRange(user: any): string {
  const first = user?.first_session_at
  const last = user?.last_session_at

  if (first && last) {
    return `${formatDate(first)} ~ ${formatDate(last)}`
  }
  if (last) {
    return formatDate(last)
  }
  if (first) {
    return formatDate(first)
  }
  return ''
}

function formatTime(timeStr: string | null): string {
  if (!timeStr) return ''
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function getDownloadableFiles(msg: any): DownloadableFile[] {
  return msg?.metadata?.downloadableFiles || []
}

function getAssistantImages(msg: any): any[] {
  return msg?.metadata?.images || []
}

function getUserAttachments(msg: any): any[] {
  return msg?.attachments || []
}

function hasUserAttachment(msg: any): boolean {
  return getUserAttachments(msg).length > 0
}

function getUserVoiceAttachments(msg: any): any[] {
  return getUserAttachments(msg).filter(att => att.type === 'voice')
}

function hasUserVoice(msg: any): boolean {
  return getUserVoiceAttachments(msg).length > 0
}

function getAttachmentDownloadUrl(att: any): string {
  if (!att.local_path || !selectedSessionId.value) return ''
  // local_path 格式: storage/tenants/{tenant_id}/conversation/{filename}
  const parts = att.local_path.split('/')
  const filename = parts[parts.length - 1]
  const params = new URLSearchParams({
    session_id: selectedSessionId.value,
    filename,
  })
  return `/api/saas/external-customers/attachments/download?${params.toString()}`
}

async function onPlayVoice(att: any) {
  const url = getAttachmentDownloadUrl(att)
  if (!url) return
  try {
    await amrPlayer.toggle(att.media_id, url)
  } catch (e: any) {
    toast.error(`语音播放失败: ${e?.message || e}`)
  }
}

function previewImage(url: string) {
  window.open(url, '_blank')
}

// 切换渠道 Tab：重置选中状态并加载该渠道用户
watch(activeChannel, (val, oldVal) => {
  if (!val || val === oldVal) return
  selectedUserId.value = ''
  selectedUser.value = null
  selectedSessionId.value = ''
  messageList.value = []
  messageTotal.value = 0
  messagePage.value = 1
  amrPlayer.stopAll()
  loadUsersSeq.value++ // 使在途请求失效
  // 翻回第 1 页会触发 watch(userPage) -> loadUsers，避免双重请求
  if (userPage.value !== 1) {
    userPage.value = 1
  } else {
    loadUsers()
  }
})

// 监听分页
watch(userPage, () => loadUsers())
watch(messagePage, () => {
  if (selectedSessionId.value) {
    loadSessionMessages()
  }
})

// 初始化：加载租户渠道配置，确定显示哪些渠道 Tab
onMounted(async () => {
  if (!isInitialized.value) {
    await init()
  }
  try {
    const res = await listChannels()
    const configured = new Set((res.channels || []).map((c: any) => c.channel_type))
    tabs.value = OFFICE_CHANNELS.filter(ch => configured.has(ch.key)).map(ch => ({ ...ch }))
  } catch (e: any) {
    toast.error(e.message || '获取渠道配置失败')
  }
  if (tabs.value.length > 0) {
    // 置 activeChannel 由 watch 触发首次 loadUsers，避免重复请求
    activeChannel.value = tabs.value[0].key as OfficeChannelKey
  }
})

onUnmounted(() => amrPlayer.stopAll())
</script>

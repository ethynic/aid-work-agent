<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="网页端会话"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="flex-1 overflow-hidden flex relative">
      <!-- 左侧：用户列表 -->
      <div class="w-96 border-r border-default bg-surface flex flex-col">
        <!-- 搜索栏 -->
        <div class="p-4 border-b border-default">
          <div class="flex gap-2">
            <BaseInput
              v-model="searchKeyword"
              placeholder="搜索用户名"
              size="sm"
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
                    {{ user.session_count }} 个会话 · 最近 {{ formatTime(user.last_active_at) }}
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

      <!-- 中间：会话列表 -->
      <div class="w-80 border-r border-default bg-surface flex flex-col">
        <!-- 智能体筛选下拉框 -->
        <div class="p-4 border-b border-default">
          <BaseSelect
            v-model="selectedAgentId"
            size="sm"
            class="w-full"
            :disabled="!selectedUserId"
            @change="handleAgentChange"
          >
            <option value="">全部智能体</option>
            <option v-for="agent in agentOptions" :key="agent.agent_id" :value="agent.agent_id">
              {{ agent.agent_name }}
            </option>
          </BaseSelect>
        </div>

        <!-- 会话列表 -->
        <div class="flex-1 overflow-y-auto">
          <div v-if="loadingSessions" class="flex items-center justify-center h-32">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="!selectedUserId" class="flex items-center justify-center h-32">
            <span class="text-muted">请选择左侧用户</span>
          </div>
          <div v-else-if="sessionList.length === 0" class="flex items-center justify-center h-32">
            <span class="text-muted">暂无会话</span>
          </div>
          <div v-else>
            <div
              v-for="session in sessionList"
              :key="session.session_id"
              class="px-4 py-3 border-b border-default cursor-pointer transition-all"
              :class="selectedSessionId === session.session_id ? 'bg-primary-50 border-l-4 border-l-primary-500' : 'hover:bg-surface-hover'"
              @click="selectSession(session)"
            >
              <div class="flex items-start justify-between gap-2">
                <div class="flex-1 min-w-0">
                  <div class="text-sm text-default truncate">{{ session.title || '新会话' }}</div>
                  <div class="text-xs text-primary-600 truncate mt-0.5">{{ session.subagent_name || '主智能体' }}</div>
                </div>
                <div class="text-xs text-muted whitespace-nowrap">{{ formatTime(session.updated_at) }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- 会话分页 -->
        <div v-if="selectedUserId" class="p-3 border-t border-default">
          <BasePagination
            :total="sessionTotal"
            v-model:current-page="sessionPage"
            :page-size="sessionPageSize"
            :show-size-changer="false"
          />
        </div>
      </div>

      <!-- 右侧：聊天记录 -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- 聊天记录头部 -->
        <div v-if="selectedSession" class="p-4 border-b border-default bg-surface">
          <div class="flex items-center gap-3">
            <img
              :src="selectedUser?.avatar_url || defaultAvatar"
              class="w-10 h-10 rounded-full object-cover bg-gray-100"
              alt="头像"
            />
            <div class="min-w-0">
              <div class="font-medium text-default truncate">{{ selectedSession.title || '新会话' }}</div>
              <div class="text-xs text-muted">
                {{ selectedUser?.nickname || selectedUser?.username || '未知用户' }} · {{ selectedSession.subagent_name || '主智能体' }}
              </div>
            </div>
          </div>
        </div>

        <!-- 消息区域 -->
        <div class="flex-1 overflow-y-auto p-4" ref="messageContainerRef">
          <div v-if="!selectedSessionId" class="flex items-center justify-center h-full">
            <div class="text-center">
              <svg class="w-16 h-16 mx-auto text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <p class="text-muted">请选择会话查看聊天记录</p>
            </div>
          </div>
          <div v-else-if="loadingMessages" class="flex items-center justify-center h-full">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="visibleMessages.length === 0" class="flex items-center justify-center h-full">
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
              :class="isUserSide(msg) ? 'items-end' : 'items-start'"
            >
              <div class="max-w-[70%] rounded-2xl px-4 py-2 text-sm"
                :class="isUserSide(msg)
                  ? 'bg-primary-500 text-white rounded-br-sm'
                  : 'bg-white border border-default text-default rounded-bl-sm shadow-sm'"
              >
                <!-- 用户消息：文本 -->
                <template v-if="msg.role === 'user'">
                  <div v-if="msg.content" class="whitespace-pre-wrap">{{ msg.content }}</div>
                  <div v-else class="text-xs opacity-80 italic">[非文本消息]</div>
                </template>
                <!-- AI 消息：文本 -->
                <template v-if="msg.role === 'assistant' && msg.content">
                  <div class="whitespace-pre-wrap">{{ msg.content }}</div>
                </template>
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
            </div>
          </div>
        </div>

        <!-- 消息分页 -->
        <div v-if="selectedSessionId" class="p-3 border-t border-default bg-surface">
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
import { ref, computed, onMounted, watch, nextTick, inject } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import DownloadFileCard from '@/components/DownloadFileCard.vue'
import { listWebSessionUsers, listUserSessionAgents, getUserWebSessions, getWebSessionMessages } from '@/api/webSessions'
import { useTenantAuth } from '@/composables/useTenantAuth'
import type { DownloadableFile } from '@/types'
import { useToast } from 'vue-toastification'

const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init, isInitialized, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const messageContainerRef = ref<HTMLElement | null>(null)

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 左栏：用户列表
const searchKeyword = ref('')
const userList = ref<any[]>([])
const userTotal = ref(0)
const userPage = ref(1)
const userPageSize = ref(20)
const loadingUsers = ref(false)

// 中栏：会话列表
const selectedAgentId = ref('')
const agentOptions = ref<{ agent_id: string; agent_name: string; session_count: number }[]>([])
const sessionList = ref<any[]>([])
const sessionTotal = ref(0)
const sessionPage = ref(1)
const sessionPageSize = ref(20)
const loadingSessions = ref(false)

// 右栏：消息列表
const messageList = ref<any[]>([])
const messageTotal = ref(0)
const messagePage = ref(1)
const messagePageSize = ref(50)
const loadingMessages = ref(false)

// 当前选中
const selectedUserId = ref('')
const selectedUser = ref<any>(null)
const selectedSessionId = ref('')
const selectedSession = ref<any>(null)

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

async function handleSearch() {
  // 翻回第 1 页会触发 watch(userPage) -> loadUsers，避免双重请求
  if (userPage.value !== 1) {
    userPage.value = 1
  } else {
    await loadUsers()
  }
}

async function handleAgentChange() {
  // 翻回第 1 页会触发 watch(sessionPage) -> loadUserSessions，避免双重请求
  if (sessionPage.value !== 1) {
    sessionPage.value = 1
  } else {
    await loadUserSessions()
  }
}

async function loadAgentOptions(userId: string) {
  try {
    const res = await listUserSessionAgents(userId)
    agentOptions.value = res.success ? res.agents || [] : []
  } catch (e: any) {
    agentOptions.value = []
    toast.error(e.message || '获取智能体列表失败')
  }
}

// 请求序号：搜索/翻页后丢弃过期响应，防止慢响应覆盖新数据
const loadUsersSeq = ref(0)
const loadSessionsSeq = ref(0)
const loadMessagesSeq = ref(0)

async function loadUsers() {
  const seq = ++loadUsersSeq.value
  loadingUsers.value = true
  try {
    const res = await listWebSessionUsers({
      keyword: searchKeyword.value || undefined,
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
  sessionList.value = []
  sessionTotal.value = 0
  selectedAgentId.value = ''
  loadAgentOptions(user.user_id)
  selectedSessionId.value = ''
  selectedSession.value = null
  messageList.value = []
  messageTotal.value = 0
  loadSessionsSeq.value++ // 使在途会话/消息请求失效
  loadMessagesSeq.value++
  // 翻回第 1 页会触发 watch(sessionPage) -> loadUserSessions，避免双重请求
  if (sessionPage.value !== 1) {
    sessionPage.value = 1
  } else {
    await loadUserSessions()
  }
}

async function loadUserSessions() {
  if (!selectedUserId.value) return
  const seq = ++loadSessionsSeq.value
  loadingSessions.value = true
  try {
    const res = await getUserWebSessions({
      user_id: selectedUserId.value,
      subagent_id: selectedAgentId.value || undefined,
      page: sessionPage.value,
      page_size: sessionPageSize.value,
    })
    if (seq !== loadSessionsSeq.value) return
    if (res.success) {
      sessionList.value = res.sessions || []
      sessionTotal.value = res.total || 0

      // 默认选中第一个会话（按 updated_at 降序，最近会话在前）
      if (sessionList.value.length > 0) {
        await selectSession(sessionList.value[0])
      } else {
        selectedSessionId.value = ''
        selectedSession.value = null
        messageList.value = []
        messageTotal.value = 0
      }
    } else {
      toast.error(res.message || '获取会话列表失败')
    }
  } catch (e: any) {
    if (seq === loadSessionsSeq.value) {
      toast.error(e.message || '获取会话列表失败')
    }
  } finally {
    if (seq === loadSessionsSeq.value) {
      loadingSessions.value = false
    }
  }
}

async function selectSession(session: any) {
  if (selectedSessionId.value === session.session_id) {
    selectedSession.value = session
    return
  }
  selectedSessionId.value = session.session_id
  selectedSession.value = session
  messageList.value = []
  messageTotal.value = 0
  loadMessagesSeq.value++ // 使在途消息请求失效
  // 翻回第 1 页会触发 watch(messagePage) -> loadSessionMessages，避免双重请求
  if (messagePage.value !== 1) {
    messagePage.value = 1
  } else {
    await loadSessionMessages()
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

async function loadSessionMessages() {
  if (!selectedSessionId.value) return
  const seq = ++loadMessagesSeq.value
  loadingMessages.value = true
  try {
    const res = await getWebSessionMessages({
      session_id: selectedSessionId.value,
      page: messagePage.value,
      page_size: messagePageSize.value,
    })
    if (seq !== loadMessagesSeq.value) return
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
    if (seq === loadMessagesSeq.value) {
      toast.error(e.message || '获取聊天记录失败')
    }
  } finally {
    if (seq === loadMessagesSeq.value) {
      loadingMessages.value = false
    }
  }
}

function isUserSide(msg: any): boolean {
  return msg.role === 'user'
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

// 监听分页
watch(userPage, () => loadUsers())
watch(sessionPage, () => loadUserSessions())
watch(messagePage, () => {
  if (selectedSessionId.value) {
    loadSessionMessages()
  }
})

onMounted(async () => {
  if (!isInitialized.value) {
    await init()
  }
  await loadUsers()
})
</script>

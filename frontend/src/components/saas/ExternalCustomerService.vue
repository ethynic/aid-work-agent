<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="外部接待客户"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="flex-1 overflow-hidden flex">
      <!-- 左侧：客户列表 -->
      <div class="w-96 border-r border-default bg-surface flex flex-col">
        <!-- 搜索栏 -->
        <div class="p-4 border-b border-default space-y-3">
          <div>
            <label class="text-sm text-muted mb-1 block">用户名</label>
            <BaseInput
              v-model="searchUsername"
              placeholder="搜索用户名"
              size="sm"
              clearable
            />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">创建时间</label>
            <div class="flex gap-2">
              <BaseInput
                v-model="startDate"
                type="date"
                size="sm"
                class="flex-1"
              />
              <span class="text-muted self-center">至</span>
              <BaseInput
                v-model="endDate"
                type="date"
                size="sm"
                class="flex-1"
              />
            </div>
          </div>
          <div class="flex gap-2">
            <BaseButton size="sm" class="flex-1" @click="handleSearch">搜索</BaseButton>
            <BaseButton size="sm" intent="secondary" class="flex-1" @click="handleReset">重置</BaseButton>
          </div>
        </div>

        <!-- 客户列表 -->
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
                  :src="user.avatar || defaultAvatar"
                  class="w-12 h-12 rounded-full object-cover bg-gray-100 ring-2 ring-gray-200"
                  alt="头像"
                />
                <div class="flex-1 min-w-0">
                  <div class="font-medium text-default truncate">{{ user.username || '未知用户' }}</div>
                  <div class="flex items-center gap-2 mt-1">
                    <span class="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-muted">{{ user.source || '未知来源' }}</span>
                    <span class="text-xs text-muted">{{ formatDate(user.created_at) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 客户分页 -->
        <div class="p-3 border-t border-default">
          <BasePagination
            :total="userTotal"
            v-model:current-page="userPage"
            :page-size="userPageSize"
            :show-page-size="false"
          />
        </div>
      </div>

      <!-- 右侧：聊天记录 -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- 聊天记录头部 -->
        <div v-if="selectedUser" class="p-4 border-b border-default bg-surface">
          <div class="flex items-center gap-3">
            <img
              :src="selectedUser.avatar || defaultAvatar"
              class="w-10 h-10 rounded-full object-cover bg-gray-100"
              alt="头像"
            />
            <div>
              <div class="font-medium text-default">{{ selectedUser.username || '未知用户' }}</div>
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
              <p class="text-muted">请选择左侧客户查看聊天记录</p>
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
              v-for="msg in messageList"
              :key="msg.message_id"
              class="flex flex-col"
              :class="msg.role === 'user' ? 'items-end' : 'items-start'"
            >
              <div class="max-w-[70%] rounded-2xl px-4 py-2 text-sm"
                :class="msg.role === 'user'
                  ? 'bg-primary-500 text-white rounded-br-sm'
                  : 'bg-white border border-default text-default rounded-bl-sm shadow-sm'"
              >
                <div v-if="msg.content" class="whitespace-pre-wrap">{{ msg.content }}</div>
                <div v-else class="text-muted italic">[图片/文件消息]</div>
              </div>
              <div class="text-xs text-muted mt-1 px-1">
                {{ formatTime(msg.created_at) }}
              </div>
            </div>
          </div>
        </div>

        <!-- 消息分页 -->
        <div v-if="selectedUserId && messageTotal > 0" class="p-3 border-t border-default bg-surface">
          <BasePagination
            :total="messageTotal"
            v-model:current-page="messagePage"
            :page-size="messagePageSize"
            :show-page-size="false"
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
import BaseButton from '@/components/ui/BaseButton.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { listExternalUsers, getUserSessions, getSessionMessages } from '@/api/externalCustomers'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useToast } from 'vue-toastification'

const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init, isInitialized } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const messageContainerRef = ref<HTMLElement | null>(null)

// 响应式状态
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
const startDate = ref('')
const endDate = ref('')

// 客户列表
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

// 会话列表
const sessionList = ref<any[]>([])
const selectedSessionId = ref('')

// 选中状态
const selectedUserId = ref('')
const selectedUser = ref<any>(null)

// 默认头像
const defaultAvatar = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMjAiIGhlaWdodD0iMTIwIj48Y2lyY2xlIGN4PSI2MCIgY3k9IjYwIiByPSI1OCIgZmlsbD0iI2YzNGQ1YiIvPjxjaXJjbGUgY3g9IjYwIiBjeT0iNDQiIHI9IjE0IiBmaWxsPSIjOTA5YWNhIi8+PC9zdmc+'

// 方法
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

function handleLogout() {
  localStorage.removeItem('portal_token')
  window.location.href = '/portal/login'
}

async function handleSearch() {
  userPage.value = 1
  await loadUsers()
}

async function handleReset() {
  searchUsername.value = ''
  startDate.value = ''
  endDate.value = ''
  userPage.value = 1
  await loadUsers()
}

async function loadUsers() {
  loadingUsers.value = true
  try {
    const res = await listExternalUsers({
      username: searchUsername.value || undefined,
      page: userPage.value,
      page_size: userPageSize.value,
    })
    if (res.success) {
      userList.value = res.users || []
      userTotal.value = res.total || 0

      // 默认选中第一个客户
      if (userList.value.length > 0 && !selectedUserId.value) {
        await selectUser(userList.value[0])
      }
    } else {
      toast.error(res.message || '获取客户列表失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取客户列表失败')
  } finally {
    loadingUsers.value = false
  }
}

async function selectUser(user: any) {
  selectedUserId.value = user.user_id
  selectedUser.value = user
  messageList.value = []
  messageTotal.value = 0
  messagePage.value = 1
  sessionList.value = []
  selectedSessionId.value = ''
  await loadUserSessions()
}

async function loadUserSessions() {
  if (!selectedUserId.value) return
  try {
    const res = await getUserSessions({
      user_id: selectedUserId.value,
      page: 1,
      page_size: 100,
    })
    if (res.success) {
      sessionList.value = res.sessions || []
      // 默认选中第一个会话
      if (sessionList.value.length > 0) {
        selectedSessionId.value = sessionList.value[0].session_id
        await loadSessionMessages()
      }
    } else {
      toast.error(res.message || '获取会话列表失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取会话列表失败')
  }
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
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
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

// 监听分页
watch(userPage, () => loadUsers())
watch(messagePage, () => {
  if (selectedSessionId.value) {
    loadSessionMessages()
  }
})

// 初始化
onMounted(async () => {
  if (!isInitialized.value) {
    await init()
  }
  await loadUsers()
})
</script>

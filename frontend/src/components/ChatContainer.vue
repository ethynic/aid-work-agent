<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Main Content -->
    <main class="flex-1 flex overflow-hidden">
      <!-- Session Sidebar -->
      <SessionSidebar
        :is-collapsed="isSidebarCollapsed"
        @collapse="isSidebarCollapsed = true"
      />

      <!-- Right Content Area -->
      <div class="flex-1 flex flex-col min-w-0">
        <!-- Header Bar -->
        <AppHeader
          :title="pageTitle"
          :is-online="isOnline"
          :is-logged-in="isLoggedIn"
          :user="user"
          @toggle-sidebar="isSidebarCollapsed = !isSidebarCollapsed"
          @logout="handleLogout"
        >
          <template #menu-items="{ closeMenu }">
            <button
              @click="handleNewSession(); closeMenu()"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
              </svg>
              新会话
            </button>
            <button
              @click="openCustomerInfo"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
              我的客户
            </button>
            <button
              @click="showCredentialManager = true; closeMenu()"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
              </svg>
              凭据管理
            </button>
            <button
              @click="openScheduledTasks"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              我的定时任务
            </button>
            <button
              @click="showSettingsDialog = true; closeMenu()"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              设置
            </button>
          </template>
        </AppHeader>

        <!-- Messages Area -->
        <div class="flex-1 overflow-hidden">
          <MessageList
            :messages="messages"
            :is-processing="isProcessing"
          />
        </div>

        <!-- Input Area -->
        <div class="flex-shrink-0 border-t border-gray-200 bg-white p-4">
          <ChatInput
            @send="handleSend"
            @upload="handleUpload"
            @remove="handleRemoveFile"
            :disabled="isProcessing"
            :is-processing="isProcessing"
            :files="currentFiles"
          />
        </div>
      </div>
    </main>

    <!-- Login Modal -->
    <LoginModal
      :visible="showLoginModal"
      @close="showLoginModal = false"
      @success="handleLoginSuccess"
    />

    <!-- Credential Manager -->
    <CredentialManager
      v-if="showCredentialManager"
      @close="showCredentialManager = false"
    />

    <!-- Settings Dialog -->
    <SettingsDialog
      :visible="showSettingsDialog"
      @close="showSettingsDialog = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, computed } from 'vue'
import { useRoute } from 'vue-router'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'
import LoginModal from './LoginModal.vue'
import AppHeader from './AppHeader.vue'
import SessionSidebar from './SessionSidebar.vue'
import CredentialManager from './CredentialManager.vue'
import SettingsDialog from './SettingsDialog.vue'
import { useAgent } from '@/composables/useAgent'
import { useAuth } from '@/composables/useAuth'
import { useSession } from '@/composables/useSession'

const {
  messages,
  isProcessing,
  currentFiles,
  sendMessage,
  clearSession,
  switchSession,
  clearSessionCache,
  uploadAttachment,
  removeAttachment,
  clearAttachments,
  sessionId: agentSessionId
} = useAgent()

const { user, isLoggedIn, init: initAuth, logout: doLogout } = useAuth()
const { currentSessionId, sessions, createNewSession, loadSessions, loadLatestSession, selectSession, renameSession } = useSession()

const route = useRoute()
const subagentName = computed<string | null>(() =>
  route.name === 'chat-subagent' ? (route.params.subagent as string) : null
)

const isOnline = ref(true)
const isSidebarCollapsed = ref(false)
const showLoginModal = ref(false)
const showCredentialManager = ref(false)
const showSettingsDialog = ref(false)
// 标志位：避免 selectSession + 手动 switchSession 与 watcher 重复执行
const skipNextSwitch = ref(false)

// 计算页面标题
const pageTitle = computed(() => {
  const prefix = subagentName.value ? `${subagentName.value} - ` : ''
  if (!currentSessionId.value) {
    return `${prefix}新会话`
  }
  const session = sessions.value.find(s => s.session_id === currentSessionId.value)
  if (session?.title) {
    return `${prefix}历史会话：${session.title}`
  }
  return `${prefix}新会话`
})

// 跳转到客户信息页面
function openCustomerInfo() {
  // 使用 user_id 和 currentSessionId 构建 URL
  const userId = user.value?.user_id
  const sessionId = currentSessionId.value
  if (userId) {
    const params = new URLSearchParams()
    params.append('user_id', userId)
    if (sessionId) {
      params.append('session_id', sessionId)
    }
    window.open(`/customer-info?${params.toString()}`, '_blank')
  } else {
    // 如果没有用户信息，提示登录
    alert('请先登录')
    showLoginModal.value = true
  }
}

// 跳转到定时任务页面
function openScheduledTasks() {
  window.open('/scheduled-tasks', '_blank')
}

// 模拟在线状态检测
let heartbeatInterval: number | null = null

onMounted(async () => {
  // 初始化认证状态
  await initAuth()

  // 检查登录状态
  if (!isLoggedIn.value) {
    showLoginModal.value = true
  } else {
    // 已登录，加载会话列表
    await loadSessions()
    // 子智能体模式下不加载主智能体最近会话
    if (!subagentName.value) {
      const hasSession = await loadLatestSession()
      if (hasSession && currentSessionId.value) {
        agentSessionId.value = currentSessionId.value
      }
    }
  }

  // 简化版：假设一直在线
  isOnline.value = true
})

onUnmounted(() => {
  if (heartbeatInterval) {
    clearInterval(heartbeatInterval)
  }
})

async function handleSend(content: string) {
  if (!isLoggedIn.value) {
    showLoginModal.value = true
    return
  }

  // 如果没有当前会话，自动创建一个（默认标题"新会话"，发送消息后更新）
  if (!currentSessionId.value) {
    const newSession = await createNewSession(undefined, subagentName.value)
    if (newSession) {
      skipNextSwitch.value = true
      selectSession(newSession.session_id)
      agentSessionId.value = newSession.session_id
      // 手动等待 switchSession 完成，避免 watcher 异步覆盖后续 sendMessage 的消息
      await switchSession(newSession.session_id)
    }
  }

  const sid = currentSessionId.value
  if (!sid) return

  // 如果是当前会话的首条用户消息（标题还是默认的"新会话"），自动用前10个字更新标题
  const session = sessions.value.find(s => s.session_id === sid)
  if (session && (!session.title || session.title === '新会话')) {
    const title = content.slice(0, 10).trim() || '新会话'
    await renameSession(sid, title)
  }

  await sendMessage(content, subagentName.value)

  // 发送成功后清空附件
  clearAttachments()
}

async function handleUpload(file: File) {
  try {
    await uploadAttachment(file)
  } catch (error) {
    console.error('文件上传失败:', error)
  }
}

function handleRemoveFile(file_id: string) {
  removeAttachment(file_id)
}

async function handleNewSession() {
  if (!isLoggedIn.value) {
    showLoginModal.value = true
    return
  }
  const newSession = await createNewSession(undefined, subagentName.value)
  if (newSession) {
    // 选中新会话并同步到useAgent
    selectSession(newSession.session_id)
    agentSessionId.value = newSession.session_id
    // 创建新会话后，清空当前消息，开始新对话
    clearSession()
    clearAttachments()
    // 展开侧边栏
    isSidebarCollapsed.value = false
  }
}

async function handleLogout() {
  await doLogout()
  showLoginModal.value = true
  // 清空会话列表
}

function handleLoginSuccess() {
  showLoginModal.value = false
  // 登录成功后加载会话列表并自动打开最近会话
  loadSessions().then(async () => {
    const hasSession = await loadLatestSession()
    if (hasSession && currentSessionId.value) {
      // 同步会话ID到useAgent
      agentSessionId.value = currentSessionId.value
    }
  })
}

// 监听登录状态变化
watch(isLoggedIn, async (loggedIn) => {
  if (!loggedIn) {
    showLoginModal.value = true
  } else {
    await loadSessions()
  }
})

// 监听当前会话变化，通过 switchSession 保存/恢复消息
watch(currentSessionId, async (newSessionId) => {
  if (skipNextSwitch.value) {
    skipNextSwitch.value = false
    return
  }
  if (newSessionId) {
    await switchSession(newSessionId)
  } else {
    messages.value = []
  }
})

// SSE 完成后清除该会话的内存缓存，确保下次切回时从数据库加载最新数据
watch(isProcessing, (processing, wasProcessing) => {
  if (wasProcessing && !processing) {
    clearSessionCache()
  }
})

// 切换子智能体时清空当前会话和消息
watch(subagentName, () => {
  selectSession(null)
  messages.value = []
  clearSession()
  clearAttachments()
})
</script>

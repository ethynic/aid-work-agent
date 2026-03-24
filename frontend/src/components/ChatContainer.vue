<template>
  <div class="h-screen flex flex-col bg-slate-50">
    <!-- Header -->
    <header class="flex-shrink-0 border-b border-slate-200 bg-white/80 backdrop-blur-sm">
      <div class="max-w-7xl mx-auto px-4 py-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <!-- Toggle Sidebar Button -->
            <button
              @click="isSidebarCollapsed = !isSidebarCollapsed"
              class="p-2 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
              title="切换侧边栏"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h7" />
              </svg>
            </button>

            <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center">
              <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <div>
              <h1 class="text-xl font-semibold text-slate-800">AID Work Agent</h1>
              <p class="text-sm text-slate-500">智能工作助手</p>
            </div>
          </div>

          <div class="flex items-center gap-4">
            <!-- User Info / Login Button -->
            <div v-if="isLoggedIn" class="flex items-center gap-3">
              <span class="text-sm text-slate-600">{{ user?.username }}</span>
              <button
                @click="handleLogout"
                class="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
              >
                退出
              </button>
            </div>

            <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-200/50 text-sm">
              <span :class="isOnline ? 'bg-green-500' : 'bg-slate-400'" class="w-2 h-2 rounded-full"></span>
              <span class="text-slate-600">{{ isOnline ? '在线' : '离线' }}</span>
            </div>
            <button
              @click="handleNewSession"
              class="px-4 py-2 text-sm text-slate-600 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
            >
              新会话
            </button>
          </div>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 flex overflow-hidden">
      <!-- Session Sidebar -->
      <SessionSidebar
        :is-collapsed="isSidebarCollapsed"
        @collapse="isSidebarCollapsed = true"
      />

      <div class="flex-1 flex flex-col max-w-7xl mx-auto w-full">
        <!-- Messages Area -->
        <div class="flex-1 overflow-hidden">
          <MessageList
            :messages="messages"
            :is-processing="isProcessing"
          />
        </div>

        <!-- Progress Panel (show during processing or if has messages) -->
        <ProgressPanel
          v-if="isProcessing || progressMessages.length > 0"
          :messages="progressMessages"
          :is-processing="isProcessing"
        />

        <!-- Input Area -->
        <div class="flex-shrink-0 border-t border-slate-200 bg-white p-4">
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
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import MessageList from './MessageList.vue'
import ProgressPanel from './ProgressPanel.vue'
import ChatInput from './ChatInput.vue'
import LoginModal from './LoginModal.vue'
import SessionSidebar from './SessionSidebar.vue'
import { useAgent } from '@/composables/useAgent'
import { useAuth } from '@/composables/useAuth'
import { useSession } from '@/composables/useSession'

const {
  messages,
  progressMessages,
  isProcessing,
  currentFiles,
  sendMessage,
  clearSession,
  uploadAttachment,
  removeAttachment,
  clearAttachments,
  sessionId: agentSessionId
} = useAgent()

const { user, isLoggedIn, init: initAuth, logout: doLogout } = useAuth()
const { currentSessionId, createNewSession, loadSessions, loadLatestSession, saveMessage, selectSession } = useSession()

const isOnline = ref(true)
const isSidebarCollapsed = ref(false)
const showLoginModal = ref(false)

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

  // 如果有当前会话，保存用户消息
  if (currentSessionId.value) {
    await saveMessage(currentSessionId.value, 'user', content)
  }

  await sendMessage(content)

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
  const newSession = await createNewSession()
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

// 监听当前会话变化，加载会话历史并同步到useAgent
watch(currentSessionId, async (sessionId) => {
  if (sessionId) {
    // 同步会话ID到useAgent，这样发送消息时会发送到正确的会话
    agentSessionId.value = sessionId

    // 加载会话消息
    const { getSessionMessages } = await import('@/api/session')
    const result = await getSessionMessages(sessionId)
    // 将历史消息填充到 messages（包含执行详情）
    messages.value = result.messages?.map(m => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
      timestamp: new Date(m.created_at).getTime(),
      progressMessages: m.metadata?.progressMessages || []  // 从 metadata 中提取执行详情
    })) || []
  } else {
    // 没有选中会话，清空消息
    messages.value = []
  }
})
</script>

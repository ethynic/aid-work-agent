<template>
  <div class="h-screen flex flex-col bg-slate-900">
    <!-- Header -->
    <header class="flex-shrink-0 border-b border-slate-700 bg-slate-800/50 backdrop-blur-sm">
      <div class="max-w-7xl mx-auto px-4 py-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center">
              <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <div>
              <h1 class="text-xl font-semibold text-white">AID Work Agent</h1>
              <p class="text-sm text-slate-400">智能工作助手</p>
            </div>
          </div>

          <div class="flex items-center gap-4">
            <!-- User Info / Login Button -->
            <div v-if="isLoggedIn" class="flex items-center gap-3">
              <span class="text-sm text-slate-300">{{ user?.username }}</span>
              <button
                @click="handleLogout"
                class="px-3 py-1.5 text-sm text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
              >
                退出
              </button>
            </div>

            <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-700/50 text-sm">
              <span :class="isOnline ? 'bg-green-500' : 'bg-slate-500'" class="w-2 h-2 rounded-full"></span>
              <span class="text-slate-300">{{ isOnline ? '在线' : '离线' }}</span>
            </div>
            <button
              @click="handleClearSession"
              class="px-4 py-2 text-sm text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
            >
              新会话
            </button>
          </div>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 flex overflow-hidden">
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
        <div class="flex-shrink-0 border-t border-slate-700 bg-slate-800/50 p-4">
          <ChatInput
            @send="handleSend"
            :disabled="isProcessing"
            :is-processing="isProcessing"
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
import { useAgent } from '@/composables/useAgent'
import { useAuth } from '@/composables/useAuth'

const {
  messages,
  progressMessages,
  isProcessing,
  sessionId,
  sendMessage,
  clearSession
} = useAgent()

const { user, isLoggedIn, init: initAuth, logout: doLogout } = useAuth()

const isOnline = ref(true)
const showLoginModal = ref(false)

// 模拟在线状态检测
let heartbeatInterval: number | null = null

onMounted(async () => {
  // 初始化认证状态
  await initAuth()

  // 检查登录状态
  if (!isLoggedIn.value) {
    showLoginModal.value = true
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
  await sendMessage(content)
}

function handleClearSession() {
  clearSession()
}

async function handleLogout() {
  await doLogout()
  showLoginModal.value = true
}

function handleLoginSuccess() {
  showLoginModal.value = false
}

// 监听登录状态变化
watch(isLoggedIn, (loggedIn) => {
  if (!loggedIn) {
    showLoginModal.value = true
  }
})
</script>

<template>
  <aside
    :class="[
      'h-full bg-white border-r border-slate-200 flex flex-col transition-all duration-300',
      isCollapsed ? 'w-0 overflow-hidden' : 'w-72'
    ]"
  >
    <!-- Header -->
    <div class="flex-shrink-0 p-4 border-b border-slate-200">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold text-slate-800">会话列表</h2>
        <button
          @click="$emit('collapse')"
          class="p-1.5 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
          title="收起侧边栏"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>
      </div>

      <!-- New Session Button -->
      <button
        @click="handleNewSession"
        :disabled="isCreating"
        class="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-cyan-600 hover:bg-cyan-500 disabled:bg-cyan-600/50 text-white rounded-lg transition-colors"
      >
        <svg v-if="!isCreating" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        <svg v-else class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>新建会话</span>
      </button>
    </div>

    <!-- Session List -->
    <div class="flex-1 overflow-y-auto">
      <div v-if="isLoading" class="p-4 text-center text-slate-500">
        <svg class="w-6 h-6 mx-auto animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <p class="mt-2 text-sm">加载中...</p>
      </div>

      <div v-else-if="sessions.length === 0" class="p-4 text-center text-slate-500">
        <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <p class="text-sm">暂无会话记录</p>
        <p class="text-xs mt-1">点击上方按钮开始新对话</p>
      </div>

      <div v-else class="p-2 space-y-1">
        <div
          v-for="session in sessions"
          :key="session.session_id"
          :class="[
            'group relative p-3 rounded-lg cursor-pointer transition-colors',
            currentSessionId === session.session_id
              ? 'bg-cyan-100 border border-cyan-200'
              : 'hover:bg-slate-100'
          ]"
          @click="handleSelectSession(session.session_id)"
        >
          <!-- Session Title -->
          <div class="flex items-start gap-2">
            <svg class="w-4 h-4 mt-0.5 flex-shrink-0 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <div class="flex-1 min-w-0">
              <p class="text-sm font-medium text-slate-800 truncate">
                {{ session.title || '新会话' }}
              </p>
              <p class="text-xs text-slate-400 mt-0.5">
                {{ formatTime(session.updated_at) }}
              </p>
            </div>
          </div>

          <!-- Action Buttons (show on hover) -->
          <div class="absolute right-2 top-2 hidden group-hover:flex items-center gap-1">
            <button
              @click.stop="handleRenameSession(session)"
              class="p-1.5 text-slate-400 hover:text-slate-800 hover:bg-slate-200 rounded transition-colors"
              title="重命名"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
            </button>
            <button
              @click.stop="handleDeleteSession(session.session_id)"
              class="p-1.5 text-slate-400 hover:text-red-500 hover:bg-slate-100 rounded transition-colors"
              title="删除"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Rename Modal -->
    <div
      v-if="showRenameModal"
      class="absolute inset-0 bg-black/30 flex items-center justify-center z-10"
      @click.self="showRenameModal = false"
    >
      <div class="bg-white rounded-lg p-4 w-64 border border-slate-200 shadow-xl">
        <h3 class="text-lg font-medium text-slate-800 mb-3">重命名会话</h3>
        <input
          v-model="renameInput"
          @keyup.enter="confirmRename"
          type="text"
          class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 text-sm focus:outline-none focus:border-cyan-500"
          placeholder="输入会话标题"
        />
        <div class="flex justify-end gap-2 mt-3">
          <button
            @click="showRenameModal = false"
            class="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmRename"
            class="px-3 py-1.5 text-sm bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg transition-colors"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useSession } from '@/composables/useSession'
import { useAuth } from '@/composables/useAuth'

interface Props {
  isCollapsed: boolean
}

defineProps<Props>()
defineEmits<{
  collapse: []
}>()

const { isLoggedIn } = useAuth()
const {
  sessions,
  currentSessionId,
  isLoading,
  loadSessions,
  createNewSession,
  removeSession,
  renameSession,
  selectSession
} = useSession()

const isCreating = ref(false)
const showRenameModal = ref(false)
const renameInput = ref('')
const renamingSessionId = ref<string | null>(null)

// 监听登录状态，登录后加载会话
watch(isLoggedIn, async (loggedIn) => {
  if (loggedIn) {
    await loadSessions()
  } else {
    sessions.value = []
  }
}, { immediate: true })

// 格式化时间
function formatTime(isoString: string): string {
  const date = new Date(isoString)
  const now = new Date()
  const diff = now.getTime() - date.getTime()

  // 1分钟内
  if (diff < 60 * 1000) {
    return '刚刚'
  }
  // 1小时内
  if (diff < 60 * 60 * 1000) {
    const minutes = Math.floor(diff / (60 * 1000))
    return `${minutes}分钟前`
  }
  // 24小时内
  if (diff < 24 * 60 * 60 * 1000) {
    const hours = Math.floor(diff / (60 * 60 * 1000))
    return `${hours}小时前`
  }
  // 超过24小时显示日期
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

// 新建会话
async function handleNewSession() {
  if (isCreating.value) return

  isCreating.value = true
  try {
    const newSession = await createNewSession()
    if (newSession) {
      selectSession(newSession.session_id)
    }
  } finally {
    isCreating.value = false
  }
}

// 选择会话
function handleSelectSession(sessionId: string) {
  selectSession(sessionId)
}

// 删除会话
async function handleDeleteSession(sessionId: string) {
  if (confirm('确定要删除这个会话吗？')) {
    await removeSession(sessionId)
  }
}

// 重命名会话
function handleRenameSession(session: { session_id: string; title: string }) {
  renamingSessionId.value = session.session_id
  renameInput.value = session.title || ''
  showRenameModal.value = true
}

// 确认重命名
async function confirmRename() {
  if (renamingSessionId.value && renameInput.value.trim()) {
    await renameSession(renamingSessionId.value, renameInput.value.trim())
    showRenameModal.value = false
    renamingSessionId.value = null
    renameInput.value = ''
  }
}
</script>

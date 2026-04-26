<template>
  <!-- 演示模式：完整布局包含侧边栏和头部 -->
  <div v-if="!isTenantMode" class="h-screen flex flex-col bg-gray-50">
    <main class="flex-1 flex overflow-hidden">
      <MenuSidebar
        :is-collapsed="isSidebarCollapsed"
        @collapse="isSidebarCollapsed = true"
      />

      <div class="flex-1 flex flex-col min-w-0">
        <AppHeader
          title="全部历史会话"
          :is-logged-in="effectiveIsLoggedIn"
          :user="effectiveUser"
          @toggle-sidebar="isSidebarCollapsed = !isSidebarCollapsed"
          @logout="handleLogout"
        />

        <div class="flex-1 overflow-y-auto p-6">
          <div class="max-w-4xl mx-auto">
            <!-- Loading State -->
            <div v-if="isLoading" class="flex items-center justify-center py-12">
              <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span class="ml-3 text-gray-500">加载中...</span>
            </div>

            <!-- Empty State -->
            <div v-else-if="sessions.length === 0" class="flex flex-col items-center justify-center py-12">
              <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <p class="text-gray-500 mb-2">暂无会话记录</p>
              <button
                @click="goToChat"
                class="mt-4 px-4 py-2 bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
              >
                开始新会话
              </button>
            </div>

            <!-- Session List -->
            <div v-else class="space-y-1.5">
              <div
                v-for="session in sessions"
                :key="session.session_id"
                :class="[
                  'group relative p-2.5 bg-white rounded-lg border transition-colors cursor-pointer',
                  currentSessionId === session.session_id
                    ? 'border-primary-300 bg-primary-50'
                    : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                ]"
                @click="handleSelectSession(session.session_id)"
              >
                <div class="flex items-center gap-3">
                  <div class="flex-shrink-0 mt-0">
                    <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                    </svg>
                  </div>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between">
                      <div class="flex items-center gap-2 truncate">
                        <h3 class="text-sm font-medium text-gray-800 truncate">
                          {{ session.title || '新会话' }}
                        </h3>
                        <span class="text-xs text-gray-400">
                          · {{ session.context_data?.subagent ? session.context_data.subagent : 'CEO智能体' }}
                        </span>
                      </div>
                      <div class="flex items-center gap-1 ml-3">
                        <span class="text-xs text-gray-400">
                          {{ formatTime(session.updated_at) }}
                        </span>
                        <button
                          @click.stop="handleRenameSession(session)"
                          class="p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors"
                          title="重命名"
                        >
                          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                          </svg>
                        </button>
                        <button
                          @click.stop="handleDeleteSession(session.session_id)"
                          class="p-1 text-gray-400 hover:text-danger-500 hover:bg-danger-50 rounded transition-colors"
                          title="删除"
                        >
                          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1 1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Pagination -->
            <div v-if="totalPages > 1" class="mt-6 flex items-center justify-center">
              <div class="flex items-center gap-2">
                <button
                  :disabled="currentPage <= 1"
                  @click="goToPage(currentPage - 1)"
                  class="px-3 py-1.5 text-sm border rounded-lg bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  上一页
                </button>
                <template v-for="(page, idx) in visiblePages" :key="idx">
                  <span v-if="page === -1" class="px-2 text-gray-400">...</span>
                  <button
                    v-else
                    @click="goToPage(page)"
                    :class="[
                      'px-3 py-1.5 text-sm border rounded-lg transition-colors',
                      page === currentPage
                        ? 'bg-primary-600 text-white border-primary-600'
                        : 'bg-white text-gray-700 hover:bg-gray-50 border-gray-200'
                    ]"
                  >
                    {{ page }}
                  </button>
                </template>
                <button
                  :disabled="currentPage >= totalPages"
                  @click="goToPage(currentPage + 1)"
                  class="px-3 py-1.5 text-sm border rounded-lg bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  下一页
                </button>
              </div>
            </div>

            <div v-if="totalPages > 1" class="mt-2 text-center text-sm text-gray-500">
              共 {{ totalSessions }} 条会话，第 {{ currentPage }} / {{ totalPages }} 页
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Rename Modal -->
    <div
      v-if="showRenameModal"
      class="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      @click.self="showRenameModal = false"
    >
      <div class="bg-white rounded-xl p-6 w-full max-w-md mx-4 shadow-2xl">
        <h3 class="text-lg font-semibold text-gray-800 mb-4">重命名会话</h3>
        <input
          v-model="renameInput"
          @keyup.enter="confirmRename"
          type="text"
          class="w-full px-4 py-2.5 bg-gray-50 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
          placeholder="输入会话标题"
        />
        <div class="flex justify-end gap-3 mt-4">
          <button
            @click="showRenameModal = false"
            class="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmRename"
            class="px-4 py-2 bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- 租户模式：PortalLayout 已经提供侧边栏，只需要内容区域 -->
  <div v-else class="p-6 bg-gray-50 min-h-full">
    <div class="max-w-4xl mx-auto">
      <!-- Loading State -->
      <div v-if="isLoading" class="flex items-center justify-center py-12">
        <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span class="ml-3 text-gray-500">加载中...</span>
      </div>

      <!-- Empty State -->
      <div v-else-if="sessions.length === 0" class="flex flex-col items-center justify-center py-12">
        <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <p class="text-gray-500 mb-2">暂无会话记录</p>
        <button
          @click="goToChat"
          class="mt-4 px-4 py-2 bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
        >
          开始新会话
        </button>
      </div>

      <!-- Session List -->
      <div v-else class="space-y-1.5">
        <div
          v-for="session in sessions"
          :key="session.session_id"
          :class="[
            'group relative p-2.5 bg-white rounded-lg border transition-colors cursor-pointer',
            currentSessionId === session.session_id
              ? 'border-primary-300 bg-primary-50'
              : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
          ]"
          @click="handleSelectSession(session.session_id)"
        >
          <div class="flex items-center gap-3">
            <div class="flex-shrink-0 mt-0">
              <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2 truncate">
                  <h3 class="text-sm font-medium text-gray-800 truncate">
                    {{ session.title || '新会话' }}
                  </h3>
                  <span class="text-xs text-gray-400">
                    · {{ session.context_data?.subagent ? session.context_data.subagent : 'CEO智能体' }}
                  </span>
                </div>
                <div class="flex items-center gap-1 ml-3">
                  <span class="text-xs text-gray-400">
                    {{ formatTime(session.updated_at) }}
                  </span>
                  <button
                    @click.stop="handleRenameSession(session)"
                    class="p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors"
                    title="重命名"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                  </button>
                  <button
                    @click.stop="handleDeleteSession(session.session_id)"
                    class="p-1 text-gray-400 hover:text-danger-500 hover:bg-danger-50 rounded transition-colors"
                    title="删除"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1 1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Pagination -->
      <div v-if="totalPages > 1" class="mt-6 flex items-center justify-center">
        <div class="flex items-center gap-2">
          <button
            :disabled="currentPage <= 1"
            @click="goToPage(currentPage - 1)"
            class="px-3 py-1.5 text-sm border rounded-lg bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            上一页
          </button>
          <template v-for="(page, idx) in visiblePages" :key="idx">
            <span v-if="page === -1" class="px-2 text-gray-400">...</span>
            <button
              v-else
              @click="goToPage(page)"
              :class="[
                'px-3 py-1.5 text-sm border rounded-lg transition-colors',
                page === currentPage
                  ? 'bg-primary-600 text-white border-primary-600'
                  : 'bg-white text-gray-700 hover:bg-gray-50 border-gray-200'
              ]"
            >
              {{ page }}
            </button>
          </template>
          <button
            :disabled="currentPage >= totalPages"
            @click="goToPage(currentPage + 1)"
            class="px-3 py-1.5 text-sm border rounded-lg bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            下一页
          </button>
        </div>
      </div>

      <div v-if="totalPages > 1" class="mt-2 text-center text-sm text-gray-500">
        共 {{ totalSessions }} 条会话，第 {{ currentPage }} / {{ totalPages }} 页
      </div>
    </div>

    <!-- Rename Modal -->
    <div
      v-if="showRenameModal"
      class="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      @click.self="showRenameModal = false"
    >
      <div class="bg-white rounded-xl p-6 w-full max-w-md mx-4 shadow-2xl">
        <h3 class="text-lg font-semibold text-gray-800 mb-4">重命名会话</h3>
        <input
          v-model="renameInput"
          @keyup.enter="confirmRename"
          type="text"
          class="w-full px-4 py-2.5 bg-gray-50 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-100"
          placeholder="输入会话标题"
        />
        <div class="flex justify-end gap-3 mt-4">
          <button
            @click="showRenameModal = false"
            class="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmRename"
            class="px-4 py-2 bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppHeader from './AppHeader.vue'
import MenuSidebar from './MenuSidebar.vue'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useSession } from '@/composables/useSession'
import { useAgent } from '@/composables/useAgent'

const router = useRouter()
const route = useRoute()
const { user: demoUser, isLoggedIn: demoIsLoggedIn, logout: demoLogout } = useDemoAuth()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => {
  return isTenantMode.value ? tenantIsLoggedIn.value : demoIsLoggedIn.value
})

// 统一的用户信息
const effectiveUser = computed(() => {
  if (isTenantMode.value) {
    return tenantAdmin.value ? {
      user_id: tenantAdmin.value.user_id,
      username: tenantAdmin.value.username,
      phone: tenantAdmin.value.phone
    } : null
  }
  return demoUser.value
})
const {
  sessions,
  currentSessionId,
  isLoading,
  currentPage,
  totalPages,
  totalSessions,
  loadSessions,
  goToPage,
  selectSession,
  removeSession,
  renameSession
} = useSession()
const { switchSession } = useAgent()

const isSidebarCollapsed = ref(false)
const showRenameModal = ref(false)
const renameInput = ref('')
const renamingSessionId = ref<string | null>(null)

// 格式化时间
function formatTime(isoString: string): string {
  if (!isoString) return ''
  const dateStr = isoString.endsWith('Z') ? isoString : isoString + 'Z'
  const date = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - date.getTime()

  if (diff < 60 * 1000) return '刚刚'
  if (diff < 60 * 60 * 1000) return `${Math.floor(diff / (60 * 1000))}分钟前`
  if (diff < 24 * 60 * 60 * 1000) return `${Math.floor(diff / (60 * 60 * 1000))}小时前`

  const month = date.getMonth() + 1
  const day = date.getDate()
  const hours = date.getHours().toString().padStart(2, '0')
  const minutes = date.getMinutes().toString().padStart(2, '0')
  return `${month}月${day}日 ${hours}:${minutes}`
}

// 选择会话
function handleSelectSession(sessionId: string) {
  selectSession(sessionId)
  const session = sessions.value.find(s => s.session_id === sessionId)
  switchSession(sessionId).then(() => {
    // 根据会话保存的 subagent_id 跳转到对应路由
    const tenantMatch = route.path.match(/^\/t\/([^\/]+)/)
    if (tenantMatch) {
      // 租户模式
      const tenantId = tenantMatch[1]
      if (session?.context_data?.subagent) {
        router.push(`/t/${tenantId}/chat/${session.context_data.subagent}`)
      } else {
        router.push(`/t/${tenantId}/chat`)
      }
    } else {
      // 普通演示模式
      if (session?.context_data?.subagent) {
        router.push(`/chat/${session.context_data.subagent}`)
      } else {
        router.push('/')
      }
    }
  })
}

// 删除会话
async function handleDeleteSession(sessionId: string) {
  if (confirm('确定要删除这个会话吗？')) {
    await removeSession(sessionId)
    // 删除后重新加载当前页，更新总数
    await loadSessions(currentPage.value)
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

// 返回聊天页面
function goToChat() {
  const tenantMatch = route.path.match(/^\/t\/([^\/]+)/)
  if (tenantMatch) {
    const tenantId = tenantMatch[1]
    router.push(`/t/${tenantId}/chat`)
  } else {
    router.push('/')
  }
}

// 登出
async function handleLogout() {
  if (isTenantMode.value) {
    await tenantLogout()
  } else {
    await demoLogout()
  }
  router.push(isTenantMode.value ? route.path.replace(/\/chat.*/, '') : '/')
}

// 计算可见页码（当页数多时只显示当前页附近几页）
const visiblePages = computed(() => {
  const pages: number[] = []
  const total = totalPages.value
  const current = currentPage.value

  if (total <= 7) {
    for (let i = 1; i <= total; i++) {
      pages.push(i)
    }
  } else {
    if (current <= 3) {
      for (let i = 1; i <= 5; i++) {
        pages.push(i)
      }
      pages.push(-1) // 省略标记
      pages.push(total)
    } else if (current >= total - 3) {
      pages.push(1)
      pages.push(-1)
      for (let i = total - 4; i <= total; i++) {
        pages.push(i)
      }
    } else {
      pages.push(1)
      pages.push(-1)
      for (let i = current - 1; i <= current + 1; i++) {
        pages.push(i)
      }
      pages.push(-1)
      pages.push(total)
    }
  }
  return pages
})

onMounted(async () => {
  await loadSessions(1)
})
</script>

<template>
  <!-- 演示模式：完整布局包含侧边栏和头部 -->
  <div v-if="!isTenantMode" class="h-screen flex flex-col bg-gray-50">
    <main class="flex-1 flex overflow-hidden">
      <MenuSidebar
        :is-collapsed="isSidebarCollapsed"
        :is-mobile="isMobile"
        :current-subagent-id="currentSubagentId"
        :available-subagents="availableSubagents"
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

        <div class="flex-1 overflow-y-auto p-4 md:p-6">
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
                v-for="(session, index) in sessions"
                :key="session.session_id"
                :class="[
                  'group relative p-2.5 rounded-lg border border-gray-200 bg-white transition-colors cursor-pointer',
                  currentSessionId === session.session_id
                    ? 'ring-2 ring-primary-300 bg-primary-50 border-primary-300'
                    : 'hover:bg-gray-50'
                ]"
                @click="handleSelectSession(session.session_id)"
              >
                <div class="flex items-center gap-3">
                  <span class="text-xs text-gray-400 w-5 text-right flex-shrink-0">
                    {{ (currentPage - 1) * pageSize + index + 1 }}
                  </span>
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
                        <div class="flex items-center gap-1">
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
            </div>

            <!-- Pagination -->
            <BasePagination
              v-if="totalPages > 1"
              :total="totalSessions"
              :current-page="currentPage"
              :page-size="pageSize"
              @update:current-page="goToPage"
              @update:page-size="handlePageSizeChange"
            />
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
  <div v-else class="h-safe-screen flex flex-col bg-gray-50">
    <AppHeader
      title="全部历史会话"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />
    <div class="flex-1 overflow-y-auto p-4 md:p-6">
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
          v-for="(session, index) in sessions"
          :key="session.session_id"
          :class="[
            'group relative p-2.5 rounded-lg border border-gray-200 bg-white transition-colors cursor-pointer',
            currentSessionId === session.session_id
              ? 'ring-2 ring-primary-300 bg-primary-50 border-primary-300'
              : 'hover:bg-gray-50'
          ]"
          @click="handleSelectSession(session.session_id)"
        >
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-400 w-5 text-right flex-shrink-0">
              {{ (currentPage - 1) * pageSize + index + 1 }}
            </span>
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
                  <div class="flex items-center gap-1">
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
      </div>

      <!-- Pagination -->
      <BasePagination
        v-if="totalPages > 1"
        :total="totalSessions"
        :current-page="currentPage"
        :page-size="pageSize"
        @update:current-page="goToPage"
        @update:page-size="handlePageSizeChange"
      />
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
import { ref, computed, onMounted, inject } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppHeader from './AppHeader.vue'
import MenuSidebar from './MenuSidebar.vue'
import BasePagination from './ui/BasePagination.vue'
import { type SubagentListItem } from '@/api/subagent'
import { getMyAllowedAgents } from '@/api/saasPermissions'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useSession } from '@/composables/useSession'
import { useAgent } from '@/composables/useAgent'
import { useMobile } from '@/composables/useMobile'

const router = useRouter()
const route = useRoute()
const { isMobile } = useMobile()
const { user: demoUser, isLoggedIn: demoIsLoggedIn, logout: demoLogout } = useDemoAuth()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout, init: initTenantAuth } = useTenantAuth()

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
  pageSize,
  loadSessions,
  goToPage,
  selectSession,
  removeSession,
  renameSession
} = useSession()
const { switchSession } = useAgent()

const isSidebarCollapsed = ref(typeof window !== 'undefined' && window.innerWidth < 768)
const toggleSidebarFn = inject<() => void>('toggleSidebar', () => {})
const showRenameModal = ref(false)
const renameInput = ref('')
const renamingSessionId = ref<string | null>(null)

// 可用的数字员工列表
const availableSubagents = ref<SubagentListItem[]>([])

// 当前选中的子智能体ID（从路由获取）
const currentSubagentId = computed(() => {
  const segments = route.path.split('/').filter(p => p)
  // 租户模式：segments = ['t', 'tenantId', ...]
  if (segments[0] === 't' && segments.length >= 3) {
    if (segments[2] === 'chat' && segments.length >= 4) {
      // 格式: /t/:tenantId/chat/:subagentId
      return segments[3]
    }
    // 格式: /t/:tenantId/:subagentId/* （业务数据页面）
    return segments[2]
  }
  // 非租户模式：匹配 /chat/:subagentId 或 /:subagentId/*
  if (segments.length >= 1) {
    if (segments[0] === 'chat' && segments.length >= 2) {
      // 格式: /chat/:subagentId
      return segments[1]
    }
    // 格式: /:subagentId/* （业务数据页面）
    return segments[0]
  }
  return undefined
})

// 加载数字员工列表
async function loadAvailableSubagents() {
  try {
    // 租户模式下使用 allowed-agents 接口，非租户模式使用 listSubagents
    let res
    if (isTenantMode.value) {
      res = await getMyAllowedAgents()
    } else {
      // 非租户模式仍使用 listSubagents
      const { listSubagents } = await import('@/api/subagent')
      res = await listSubagents()
    }

    if (res.success && res.data) {
      // 后端已返回完整格式，直接使用
      availableSubagents.value = res.data as SubagentListItem[]
    }
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
  }
}

// 格式化时间
function formatTime(isoString: string): string {
  if (!isoString) return ''
  // 直接解析 ISO 字符串，JavaScript 会正确处理本地时间
  const date = new Date(isoString)
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
    const queryParams: Record<string, string> = {}

    // 检查 subagent 是否为有效的子智能体（在 availableSubagents 中存在）
    let subagent = session?.context_data?.subagent as string | undefined
    if (subagent && availableSubagents.value.length > 0) {
      const matched = availableSubagents.value.find(
        (a: any) => a.agent_id === subagent || a.subagent_type === subagent
      )
      if (!matched) {
        // 不是有效的子智能体（可能是 all-sessions、instances 等其他路由参数）
        subagent = undefined
      }
    }

    // 租户模式下传递 instance_id
    if (tenantMatch && session?.instance_id) {
      queryParams.instance_id = session.instance_id
    }

    if (tenantMatch) {
      // 租户模式
      const tenantId = tenantMatch[1]
      const path = subagent
        ? `/t/${tenantId}/chat/${subagent}`
        : `/t/${tenantId}/chat`
      router.push({ path, query: Object.keys(queryParams).length > 0 ? queryParams : undefined })
    } else {
      // 普通演示模式
      const path = subagent
        ? `/chat/${subagent}`
        : '/'
      router.push(path)
    }
  })
}

// 删除会话
async function handleDeleteSession(sessionId: string) {
  if (confirm('确定要删除这个会话吗？')) {
    const success = await removeSession(sessionId)
    // 只有删除成功时才刷新列表，确保数据最新
    // 404 等情况已经在 removeSession 内部处理（从本地列表移除）
    if (success) {
      await loadSessions(currentPage.value, true)
    }
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

// 处理每页条数变化
async function handlePageSizeChange(size: number) {
  pageSize.value = size
  // 强制刷新，因为 pageSize 变了需要重新加载数据
  await loadSessions(1, true)
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

// 处理侧边栏切换
function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  } else {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }
}

onMounted(async () => {
  // 初始化租户认证状态（平台管理员访问租户前台时需要）
  if (isTenantMode.value) {
    await initTenantAuth()
  }
  await loadAvailableSubagents()
  await loadSessions(1)
})
</script>

<template>
  <aside
    :class="[
      'h-full bg-white border-r border-gray-200 flex flex-col transition-all duration-300',
      isCollapsed ? 'w-0 overflow-hidden' : 'w-72'
    ]"
  >
    <!-- System Header - 系统名称区域 -->
    <div class="flex-shrink-0 p-4 border-b border-gray-200">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center flex-shrink-0">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <h1 class="text-base font-semibold text-gray-800 truncate">
          {{ sidebarTitle }}
        </h1>
      </div>
    </div>

    <!-- Navigation Menu - 导航菜单区域 -->
    <div class="flex-shrink-0 p-2 space-y-1">
      <!-- New Session Button -->
      <button
        v-if="showNewSession"
        @click="handleNewSession"
        :disabled="isCreating"
        class="w-full flex items-center gap-3 px-3 py-2.5 bg-primary-600 hover:bg-primary-500 disabled:bg-primary-400 text-white rounded-lg transition-colors"
      >
        <svg v-if="!isCreating" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        <svg v-else class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span class="text-sm font-medium">新会话</span>
      </button>

      <!-- 租户模式菜单 -->
      <template v-if="isTenantMode">
        <!-- 管理菜单（可折叠，仅租户管理员可见） -->
        <div v-if="isTenantAdmin">
          <!-- 管理菜单标题 -->
          <button
            @click="isAdminMenuExpanded = !isAdminMenuExpanded"
            class="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm text-gray-600 hover:bg-gray-50"
          >
            <div class="flex items-center gap-3">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <span>管理菜单</span>
            </div>
            <svg
              :class="['w-4 h-4 transition-transform', isAdminMenuExpanded ? 'rotate-180' : '']"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <!-- 管理子菜单 -->
          <div v-show="isAdminMenuExpanded" class="ml-4 mt-1 space-y-1">
            <button
              v-for="item in adminSubMenuItems"
              :key="item.path"
              @click="router.push(item.path)"
              :class="[
                'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm',
                route.path === item.path
                  ? 'bg-primary-50 text-primary-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-50'
              ]"
            >
              <span class="text-base">{{ item.icon }}</span>
              <span>{{ item.label }}</span>
            </button>
          </div>
        </div>
      </template>

      <!-- 演示模式菜单 -->
      <template v-else>
        <!-- Knowledge Base Menu Item -->
        <button
          @click="goToKnowledgeBase"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
            isKnowledgeBaseActive
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
          <span>企业知识库</span>
        </button>

        <!-- Digital Employee Management -->
        <button
          v-if="isAdmin"
          @click="goToDigitalEmployeeManager"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
            route.path === '/admin/subagents'
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          <span>数字员工</span>
        </button>
      </template>
    </div>

    <!-- Decorative Divider - 装饰性分隔线 -->
    <div v-if="showHistory" class="flex-shrink-0 px-4 py-2">
      <div class="flex items-center gap-3">
        <div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-300 to-transparent"></div>
        <svg class="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-300 to-transparent"></div>
      </div>
    </div>

    <!-- History Sessions Header - 历史会话标题 -->
    <div v-if="showHistory" class="flex-shrink-0 px-4 py-2">
      <div class="flex items-center justify-between">
        <h2 class="text-xs font-medium text-gray-500 uppercase tracking-wider">历史会话</h2>
        <button
          @click="$emit('collapse')"
          class="p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors"
          title="收起侧边栏"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Session List - 会话列表 -->
    <div v-if="showHistory" class="flex-1 overflow-y-auto px-2">
      <div v-if="isLoading" class="p-4 text-center text-gray-500">
        <svg class="w-6 h-6 mx-auto animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <p class="mt-2 text-sm">加载中...</p>
      </div>

      <div v-else-if="filteredSessions.length === 0" class="p-4 text-center text-gray-500">
        <svg class="w-10 h-10 mx-auto mb-2 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <p class="text-xs">暂无会话记录</p>
      </div>

      <div v-else class="space-y-1">
        <div
          v-for="session in recentSessions"
          :key="session.session_id"
          :class="[
            'group relative p-2.5 rounded-lg cursor-pointer transition-colors',
            isHistorySessionActive && currentSessionId === session.session_id
              ? 'bg-primary-50 border border-primary-200'
              : 'hover:bg-gray-50 border border-transparent'
          ]"
          @click="handleSelectSession(session.session_id)"
        >
          <!-- Session Title -->
          <div class="flex items-start gap-2">
            <svg class="w-4 h-4 mt-0.5 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <div class="flex-1 min-w-0">
              <p class="text-sm text-gray-700 truncate">
                {{ session.title || '新会话' }}
              </p>
              <p class="text-xs text-gray-400 mt-0.5">
                {{ formatTime(session.updated_at) }}
              </p>
            </div>
          </div>

          <!-- Action Buttons (show on hover) -->
          <div class="absolute right-1.5 top-1.5 hidden group-hover:flex items-center gap-0.5">
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
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>

        <!-- 全部历史会话链接 -->
        <button
          v-if="filteredSessions.length > 0"
          @click="goToAllSessions"
          class="w-full flex items-center gap-2 px-3 py-2 text-sm text-primary-600 hover:bg-primary-50 rounded-lg transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
          <span>全部历史会话</span>
        </button>
      </div>
    </div>

    <!-- Theme Switcher - 主题切换器 -->
    <div class="flex-shrink-0 p-3 border-t border-gray-200">
      <ThemeSwitcher />
    </div>

    <!-- 退出登录 - 租户模式专用 -->
    <div v-if="isTenantMode" class="flex-shrink-0 px-3 pb-3">
      <button
        @click="handleTenantLogout"
        class="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
        </svg>
        <span>退出登录</span>
      </button>
    </div>

    <!-- Rename Modal -->
    <div
      v-if="showRenameModal"
      class="absolute inset-0 bg-black/30 flex items-center justify-center z-10"
      @click.self="showRenameModal = false"
    >
      <div class="bg-white rounded-lg p-4 w-64 border border-gray-200 shadow-xl">
        <h3 class="text-sm font-medium text-gray-800 mb-3">重命名会话</h3>
        <input
          v-model="renameInput"
          @keyup.enter="confirmRename"
          type="text"
          class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-lg text-gray-800 text-sm focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
          placeholder="输入会话标题"
        />
        <div class="flex justify-end gap-2 mt-3">
          <button
            @click="showRenameModal = false"
            class="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmRename"
            class="px-3 py-1.5 text-sm bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useSession } from '@/composables/useSession'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAgent } from '@/composables/useAgent'
import ThemeSwitcher from './ThemeSwitcher.vue'

interface Props {
  isCollapsed: boolean
  /** 是否显示历史会话区域，默认 true */
  showHistory?: boolean
  /** 是否显示新会话按钮，默认 true */
  showNewSession?: boolean
}

withDefaults(defineProps<Props>(), {
  showHistory: true,
  showNewSession: true
})
defineEmits<{
  collapse: []
}>()

const router = useRouter()
const route = useRoute()
const { isLoggedIn: demoIsLoggedIn, isAdmin } = useDemoAuth()
const { admin: tenantAdmin, tenant, logout: tenantLogout, isLoggedIn: tenantIsLoggedIn } = useTenantAuth()
const {
  sessions,
  currentSessionId,
  isLoading,
  loadSessions,
  removeSession,
  renameSession,
  selectSession
} = useSession()
const { isProcessing, abortStreaming } = useAgent()

const isCreating = ref(false)
const showRenameModal = ref(false)
const renameInput = ref('')
const renamingSessionId = ref<string | null>(null)
const isAdminMenuExpanded = ref(true)

// 判断是否为租户模式（路由以 /t/ 开头）
const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 租户 ID
const tenantId = computed(() => {
  const match = route.path.match(/^\/t\/([^/]+)/)
  return match ? match[1] : null
})

// 是否为租户管理员
const isTenantAdmin = computed(() => {
  return tenantAdmin.value?.role === 'tenant_admin' || tenantAdmin.value?.role === 'platform_admin'
})

// 侧边栏标题
const sidebarTitle = computed(() => {
  if (isTenantMode.value && tenant.value) {
    // 租户模式：只显示租户名称
    return tenant.value.company_name
  }
  // 演示模式：显示默认名称
  return '爱定义工作助理'
})

// 管理子菜单项（租户管理员可见）
const adminSubMenuItems = computed(() => {
  if (!tenantId.value) return []
  const base = `/t/${tenantId.value}`
  return [
    { path: `${base}/users`, label: '用户管理', icon: '👥' },
    { path: `${base}/channels`, label: '渠道配置', icon: '📡' },
    { path: `${base}/knowledge`, label: '企业知识库', icon: '📚' },
    { path: `${base}/settings`, label: '企业设置', icon: '⚙️' },
  ]
})

// 当前子智能体（从路由参数获取）
const currentSubagent = computed<string | null>(() =>
  route.name === 'chat-subagent' ? (route.params.subagent as string) : null
)

// 按子智能体过滤会话列表
const filteredSessions = computed(() => {
  if (!currentSubagent.value) {
    // 主智能体模式：过滤掉有 subagent 标记的会话
    return sessions.value.filter(s => !s.context_data?.subagent)
  }
  // 子智能体模式：只显示同名会话
  return sessions.value.filter(s => s.context_data?.subagent === currentSubagent.value)
})

// 只显示前10个会话
const recentSessions = computed(() => {
  return filteredSessions.value.slice(0, 10)
})

// 判断当前是否在知识库页面
const isKnowledgeBaseActive = computed(() => {
  return route.path === '/knowledge-base'
})

// 判断历史会话是否应该高亮（仅在非知识库页面时）
const isHistorySessionActive = computed(() => {
  return !isKnowledgeBaseActive.value
})

// 跳转到知识库（演示模式）
function goToKnowledgeBase() {
  router.push('/knowledge-base')
}

// 跳转到数字员工管理
function goToDigitalEmployeeManager() {
  router.push('/admin/subagents')
}

// 跳转到全部历史会话
function goToAllSessions() {
  router.push('/all-sessions')
}

// 监听登录状态，登录后加载会话
// 租户模式监听 tenantIsLoggedIn，演示模式监听 demoIsLoggedIn
import { watchEffect } from 'vue'
watchEffect(async () => {
  const isTenantMode = route.path.startsWith('/t/')
  const effectiveLoggedIn = isTenantMode ? tenantIsLoggedIn.value : demoIsLoggedIn.value
  if (effectiveLoggedIn) {
    await loadSessions()
  } else {
    sessions.value = []
  }
})

// 格式化时间（后端 CURRENT_TIMESTAMP 为 UTC，需补 Z 标记确保正确解析）
function formatTime(isoString: string): string {
  // 后端 CURRENT_TIMESTAMP 返回 "2026-03-26 05:00:00" 格式（UTC，无时区标识）
  // JavaScript new Date() 会将其当作本地时间解析，导致差8小时
  // 补上 Z 后缀让 JS 正确识别为 UTC 时间
  const dateStr = isoString.endsWith('Z') ? isoString : isoString + 'Z'
  const date = new Date(dateStr)
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
  // 超过24小时显示日期（使用本地时区即北京时间）
  const month = date.getMonth() + 1
  const day = date.getDate()
  return `${month}月${day}日`
}

function now(): string {
  const d = new Date()
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}.${d.getMilliseconds().toString().padStart(3, '0')}`
}

// 新建会话
async function handleNewSession() {
  console.log(`[${now()}] [ConfirmDialog:MenuSidebar] handleNewSession called, isProcessing=`, isProcessing.value)
  // 根据模式选择正确的登录状态检查
  // 租户模式使用 tenantIsLoggedIn，演示模式使用 demoIsLoggedIn
  const effectiveIsLoggedIn = isTenantMode.value ? tenantIsLoggedIn.value : demoIsLoggedIn.value
  if (!effectiveIsLoggedIn) {
    return
  }

  if (isCreating.value) {
    return
  }

  // 如果当前正在流式响应，需要用户确认是否终止
  if (isProcessing.value) {
    console.log(`[${now()}] [ConfirmDialog:MenuSidebar] isProcessing=true, show confirm dialog`)
    if (!confirm('当前会话还未结束，您希望终止当前会话，进入新会话吗？')) {
      console.log(`[${now()}] [ConfirmDialog:MenuSidebar] user canceled`)
      return
    }
    console.log(`[${now()}] [ConfirmDialog:MenuSidebar] user confirmed, abort streaming`)
    // 用户确认，终止当前流式响应
    await abortStreaming()
  }

  // 优化：点击新会话立即响应，不等待后端 API
  // 直接清空当前会话，导航到空界面，用户输入第一条消息时才真正创建会话
  isCreating.value = true
  try {
    console.log(`[${now()}] [ConfirmDialog:MenuSidebar] go to empty new session immediately`)
    selectSession(null)
    // 导航到对应路由（租户模式使用 /t/:tenant_id/chat）
    const targetPath = currentSubagent.value
      ? `/chat/${currentSubagent.value}`
      : isTenantMode.value
        ? `/t/${tenantId.value}/chat`
        : '/'
    if (route.path !== targetPath) {
      console.log(`[${now()}] [ConfirmDialog:MenuSidebar] router.push to`, targetPath)
      router.push(targetPath)
    } else {
      // 如果已经在目标路由，still need to trigger watch by selecting null
      // 路由相同但 currentSessionId 变化会触发 watch 清空 messages
    }
  } finally {
    isCreating.value = false
    console.log(`[${now()}] [ConfirmDialog:MenuSidebar] handleNewSession finished instantly`)
  }
}

// 选择会话
async function handleSelectSession(sessionId: string) {
  console.log(`[${now()}] [ConfirmDialog:MenuSidebar] handleSelectSession called, sessionId=`, sessionId, 'isProcessing=', isProcessing.value)
  // 如果当前正在流式响应，需要用户确认是否终止
  if (isProcessing.value) {
    console.log(`[${now()}] [ConfirmDialog:MenuSidebar] isProcessing=true, show confirm dialog`)
    if (!confirm('当前会话还未结束，您希望终止当前会话，切换到选中的会话吗？')) {
      console.log(`[${now()}] [ConfirmDialog:MenuSidebar] user canceled`)
      return
    }
    console.log(`[${now()}] [ConfirmDialog:MenuSidebar] user confirmed, abort streaming`)
    // 用户确认，终止当前流式响应
    await abortStreaming()
  }
  console.log(`[${now()}] [ConfirmDialog:MenuSidebar] calling selectSession`, sessionId)
  selectSession(sessionId)
  // 根据会话的 subagent 标记导航到对应路由（租户模式使用 /t/:tenant_id/chat）
  const session = sessions.value.find(s => s.session_id === sessionId)
  const subagent = session?.context_data?.subagent as string | undefined
  const targetPath = subagent
    ? `/chat/${subagent}`
    : isTenantMode.value
      ? `/t/${tenantId.value}/chat`
      : '/'
  if (route.path !== targetPath) {
    console.log(`[${now()}] [ConfirmDialog:MenuSidebar] router.push to`, targetPath)
    router.push(targetPath)
  }
  console.log(`[${now()}] [ConfirmDialog:MenuSidebar] handleSelectSession done`)
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

// 租户模式退出登录
async function handleTenantLogout() {
  await tenantLogout()
  if (tenantId.value) {
    router.push(`/t/${tenantId.value}/login`)
  } else {
    router.push('/')
  }
}
</script>

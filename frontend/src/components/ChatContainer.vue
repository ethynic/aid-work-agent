<template>
  <div class="h-safe-screen flex flex-col bg-gray-50">
    <!-- Main Content -->
    <main class="flex-1 flex overflow-hidden">
      <!-- Session Sidebar - 仅在非 PortalLayout 模式下显示（避免重复） -->
      <MenuSidebar
        v-if="!isInPortalLayout"
        :is-collapsed="isSidebarCollapsed"
        :is-mobile="isMobile"
        :current-subagent-id="currentSubagentId"
        :available-subagents="availableSubagents"
        @collapse="isSidebarCollapsed = true"
      />

      <!-- Right Content Area -->
      <div class="flex-1 flex min-w-0 relative">
        <!-- Chat Area -->
        <div class="flex-1 flex flex-col min-w-0">
          <!-- Header Bar -->
          <AppHeader
            :title="pageTitle"
            :is-logged-in="effectiveIsLoggedIn"
            :user="effectiveUser"
            :available-subagents="availableSubagents"
            :current-subagent-id="currentSubagentId"
            :show-demo-logout="!isTenantMode"
            @toggle-sidebar="handleToggleSidebar"
            @logout="handleLogout"
            @change-subagent="handleSubagentChange"
          >
            <template #menu-items="{ closeMenu }">

              <!-- 结束会话（仅当有实例ID时显示） -->
              <button
                v-if="instanceId"
                @click="handleEndSession(); closeMenu()"
                class="w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 flex items-center gap-2"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
                结束会话释放实例
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
              @quick-prompt="handleSend"
            />
          </div>

          <!-- Input Area -->
          <div class="flex-shrink-0 border-t border-gray-200 bg-white p-2 md:p-4 pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-sticky z-10">
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

        <!-- Attachment Preview Panel -->
        <Transition name="slide">
          <div
            v-if="isPreviewOpen"
            class="absolute inset-y-0 right-0 z-50 w-full md:static md:w-auto md:z-auto"
          >
            <AttachmentPreviewPanel
              :attachment="previewAttachment"
              @close="closePreview"
            />
          </div>
        </Transition>
      </div>
    </main>

    <!-- Login Modal -->
    <LoginModal v-if="isDemoMode"
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
import { ref, onMounted, onUnmounted, watch, computed, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { defineAsyncComponent } from 'vue'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'
import LoginModal from './LoginModal.vue'
import AppHeader from './AppHeader.vue'
import MenuSidebar from './MenuSidebar.vue'
import AttachmentPreviewPanel from './AttachmentPreviewPanel.vue'

// ✅ 优化：对话框组件异步加载，用户点击时才加载
const CredentialManager = defineAsyncComponent(() => import('./CredentialManager.vue'))
const SettingsDialog = defineAsyncComponent(() => import('./SettingsDialog.vue'))
import { useAgent } from '@/composables/useAgent'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useSession } from '@/composables/useSession'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { useSubagentList } from '@/composables/useSubagentList'
import { useMobile } from '@/composables/useMobile'
import { useToast } from 'vue-toastification'
import type { AgentItem } from '@/api/saasPermissions'
const isDemoMode = import.meta.env.VITE_DEMO_ENABLED === 'true'
const router = useRouter()
const toast = useToast()
const { isMobile } = useMobile()

// 从 PortalLayout 注入侧边栏状态（租户前台模式），非租户路由下使用默认值
const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed', { value: typeof window !== 'undefined' && window.innerWidth < 768 })
const toggleSidebarFn = inject<() => void>('toggleSidebar', () => {})

const {
  messages,
  isProcessing,
  currentFiles,
  sendMessage,
  clearSession,
  switchSession,
  clearSessionCache,
  precacheNewSession,
  uploadAttachment,
  removeAttachment,
  clearAttachments,
  abortStreaming,
  sessionId: agentSessionId,
  endSession
} = useAgent()

const { user, isLoggedIn, init: initAuth, logout: doLogout } = useDemoAuth()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init: initTenantAuth } = useTenantAuth()
const { currentSessionId, sessions, createNewSession, loadSessions, loadLatestSession, selectSession, renameSession, clearSessionCache: clearSessionListCache } = useSession()
const { previewAttachment, isPreviewOpen, closePreview } = useAttachmentPreview()

const route = useRoute()
const subagentName = computed<string | null>(() => {
  // 支持两种路由匹配：演示模式 /chat/subagent 和租户模式 /t/tenantId/chat/subagent
  if (route.name === 'chat-subagent') {
    return route.params.subagent as string
  }
  if (route.name === 'tenant-chat-subagent') {
    return route.params.subagent as string
  }
  return null
})

// 从路由 query 参数中获取实例 ID（用于并发控制）
// 优先级：1. 路由参数 2. 当前会话的 instance_id 字段 3. 匹配可用的子智能体实例
const instanceId = computed<string | null>(() => {
  // 1. 优先从路由参数获取（从 InstanceLobby 锁定后跳转）
  if (route.query.instance_id) {
    return route.query.instance_id as string
  }

  // 2. 从当前会话的 instance_id 字段获取
  if (currentSessionId.value) {
    const session = sessions.value.find(s => s.session_id === currentSessionId.value)
    if (session?.instance_id) {
      return session.instance_id
    }
  }

  // 3. 租户模式下，如果有 subagent，从可用实例中匹配
  if (isTenantMode.value && subagentName.value && availableSubagents.value.length > 0) {
    // 先尝试匹配 agent_id
    const matchedByAgent = availableSubagents.value.find(a => a.agent_id === subagentName.value)
    if (matchedByAgent?.instance_id) {
      return matchedByAgent.instance_id
    }
    // 再尝试匹配 subagent_type
    const matchedByType = availableSubagents.value.find(a => a.subagent_type === subagentName.value)
    if (matchedByType?.instance_id) {
      return matchedByType.instance_id
    }
  }

  return null
})

// 从路由 query 参数中获取预生成的会话 ID（从 Lobby 跳转时传入）
const pregeneratedSessionId = computed<string | null>(() => {
  return (route.query._sid as string) || null
})

// 可用的数字员工列表（带缓存，避免重复请求）
const { availableSubagents, loadAvailableSubagents } = useSubagentList()

// 获取租户ID（租户模式下从路径中提取）
const tenantId = computed(() => {
  if (isTenantMode.value) {
    const match = route.path.match(/^\/t\/([^\/]+)/)
    return match ? match[1] : null
  }
  return null
})

// 获取上次选择的数字员工ID
function getLastSelectedAgentId(): string | null {
  const storageKey = tenantId.value ? `last_selected_agent_${tenantId.value}` : 'last_selected_agent_demo'
  return localStorage.getItem(storageKey)
}

// 保存上次选择的数字员工ID
function saveLastSelectedAgentId(agentId: string) {
  const storageKey = tenantId.value ? `last_selected_agent_${tenantId.value}` : 'last_selected_agent_demo'
  localStorage.setItem(storageKey, agentId)
}

// 获取默认数字员工ID（上次选择的或第一个可用的）
function getDefaultAgentId(): string | null {
  // 1. 尝试获取上次选择的
  const lastSelected = getLastSelectedAgentId()
  if (lastSelected) {
    // 验证上次选择是否仍在可用列表中
    const isAvailable = availableSubagents.value.some(agent =>
      agent.agent_id === lastSelected || agent.instance_id === lastSelected
    )
    if (isAvailable) {
      return lastSelected
    }
  }

  // 2. 选择第一个可用的数字员工
  if (availableSubagents.value.length > 0) {
    const firstAgent = availableSubagents.value[0]
    return firstAgent.instance_id || firstAgent.agent_id
  }

  // 3. 没有可用数字员工，返回null（主智能体）
  return null
}

// 当前选中的数字员工ID（null 表示主智能体）
// 租户模式下为实例ID，演示模式下为子智能体类型
const currentSubagentId = computed(() => {
  // 优先使用 instance_id 查询参数（租户模式下实例选择）
  if (isTenantMode.value && instanceId.value) {
    return instanceId.value
  }

  if (!subagentName.value) return undefined

  // 租户模式下，尝试查找匹配的实例
  if (isTenantMode.value && availableSubagents.value.length > 0) {
    // 首先尝试作为实例ID匹配
    const instance = availableSubagents.value.find(a => a.instance_id === subagentName.value)
    if (instance) {
      return instance.instance_id || instance.agent_id
    }
    // 然后尝试作为agent_id匹配（用于主智能体main）
    const agent = availableSubagents.value.find(a => a.agent_id === subagentName.value)
    if (agent) {
      return agent.instance_id || agent.agent_id
    }
  }

  return subagentName.value
})

// 判断是否为租户模式
const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 判断是否在 PortalLayout 内（此时 MenuSidebar 由 PortalLayout 渲染）
const isInPortalLayout = computed(() => route.path.startsWith('/t/') || route.path.startsWith('/portal/'))

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => {
  return isTenantMode.value ? tenantIsLoggedIn.value : isLoggedIn.value
})

// 租户模式下使用租户用户信息，否则使用普通用户信息
const effectiveUser = computed(() => {
  if (isTenantMode.value) {
    return tenantAdmin.value ? {
      user_id: tenantAdmin.value.user_id,
      username: tenantAdmin.value.username,
      phone: tenantAdmin.value.phone
    } : null
  }
  return user.value
})

// 侧边栏折叠状态（优先使用注入的状态，否则使用本地状态）
const localSidebarCollapsed = ref(typeof window !== 'undefined' && window.innerWidth < 768)
const isSidebarCollapsed = computed({
  get: () => sidebarCollapsed?.value ?? localSidebarCollapsed.value,
  set: (val: boolean) => {
    if (sidebarCollapsed) {
      sidebarCollapsed.value = val
    } else {
      localSidebarCollapsed.value = val
    }
  }
})
const showLoginModal = ref(false)
const showCredentialManager = ref(false)
const showSettingsDialog = ref(false)

// 标志位：避免 selectSession + 手动 switchSession 与 watcher 重复执行
const skipNextSwitch = ref(false)

// 计算页面标题（标题中不显示数字员工名称，避免与右侧选择器重复）
const pageTitle = computed(() => {
  if (!currentSessionId.value) {
    return '新会话'
  }
  const session = sessions.value.find(s => s.session_id === currentSessionId.value)
  // 只有当会话有自定义标题（非默认的"新会话"）时才显示"历史会话："前缀
  if (session?.title && session.title !== '新会话') {
    return `会话：${session.title}`
  }
  return '新会话'
})


// 排队轮到了，自动发送消息


// 结束会话，释放实例锁
async function handleEndSession() {
  if (!instanceId.value) return

  const success = await endSession(instanceId.value)
  if (success) {
    toast.success('会话已结束，实例已释放')
  } else {
    toast.error('结束会话失败')
  }
}
// 跳转到定时任务页面
function openScheduledTasks() {
  window.open('/scheduled-tasks', '_blank')
}

// 检查并处理主智能体不可用的情况（ChatContainer 特有逻辑）
async function checkAndRedirectIfMainAgentUnavailable() {
  // 关键修复：如果 currentSessionId 已有值（用户点击了历史会话），不执行重定向
  // 优先加载用户选择的历史会话，而不是强制跳转到子智能体
  if (currentSessionId.value) {
    return
  }

  // 租户模式下，如果当前路由没有指定数字员工，确保有默认选择
  if (isTenantMode.value && !subagentName.value && availableSubagents.value.length > 0) {
    // 获取默认数字员工ID（上次选择的或第一个可用的）
    const defaultAgentId = getDefaultAgentId()

    // 如果默认数字员工存在且不是主智能体，重定向到该数字员工
    if (defaultAgentId && defaultAgentId !== 'main') {
      // 构建重定向路径
      const tenantMatch = route.path.match(/^\/t\/([^\/]+)/)
      if (tenantMatch) {
        const tenantId = tenantMatch[1]
        // 在 availableSubagents 中查找匹配的项
        const matchedAgent = availableSubagents.value.find((agent: AgentItem) =>
          agent.agent_id === defaultAgentId || agent.instance_id === defaultAgentId
        )
        if (matchedAgent) {
          const targetPath = `/t/${tenantId}/chat/${matchedAgent.agent_id}`
          // 使用 router.replace 避免添加历史记录
          await router.replace(targetPath)
        }
      }
    }
  }
}

// 处理数字员工选择变化
async function handleSubagentChange(agentId: string) {
  console.log(`[${now()}] [ConfirmDialog] handleSubagentChange called, isProcessing=`, isProcessing.value, 'agentId=', agentId)
  // 如果当前正在流式响应，需要用户确认是否终止
  if (isProcessing.value) {
    console.log(`[${now()}] [ConfirmDialog] isProcessing=true, show confirm dialog`)
    if (!confirm('当前会话还未结束，您希望终止当前会话，切换数字员工吗？')) {
      console.log(`[${now()}] [ConfirmDialog] user canceled`)
      return
    }
    console.log(`[${now()}] [ConfirmDialog] user confirmed, abort streaming`)
    // 用户确认，终止当前流式响应
    await abortStreaming()
  }

  // 在 availableSubagents 中查找匹配的项
  const matchedAgent = availableSubagents.value.find((agent: AgentItem) =>
    agent.instance_id === agentId || agent.agent_id === agentId
  )

  // 构建目标路由路径和查询参数
  let targetPath: string
  const query: Record<string, string> = {}
  const tenantMatch = route.path.match(/^\/t\/([^\/]+)/)

  if (tenantMatch) {
    // 租户模式
    const tenantId = tenantMatch[1]

    if (agentId === 'main') {
      targetPath = `/t/${tenantId}/chat`
    } else {
      // 如果有匹配的实例，使用其 agent_id 作为路由参数，instance_id 作为查询参数
      if (matchedAgent && matchedAgent.instance_id) {
        targetPath = `/t/${tenantId}/chat/${matchedAgent.agent_id}`
        query.instance_id = matchedAgent.instance_id
      } else {
        // 没有实例（可能是子智能体类型），直接使用 agentId 作为路由参数
        targetPath = `/t/${tenantId}/chat/${agentId}`
      }
    }
  } else {
    // 普通演示模式
    targetPath = agentId === 'main' ? '/' : `/chat/${agentId}`
  }

  console.log(`[${now()}] [ConfirmDialog] router.push to`, targetPath, 'query:', query)
  // 导航到对应路由
  // 现有代码已经监听 subagentName 变化，会自动清空会话并创建新会话
  await router.push({ path: targetPath, query: Object.keys(query).length > 0 ? query : undefined })
  console.log(`[${now()}] [ConfirmDialog] router.push done`)

  // 保存用户选择的数字员工ID
  saveLastSelectedAgentId(agentId)
}

// 模拟在线状态检测
onMounted(async () => {
  // 初始化认证状态（租户模式和演示模式都需要初始化）
  await Promise.all([initAuth(), initTenantAuth()])

  // 加载可用数字员工列表（带缓存，避免重复请求）
  await loadAvailableSubagents(isTenantMode.value)

  // 检查并处理主智能体不可用的情况
  await checkAndRedirectIfMainAgentUnavailable()

  // 检查登录状态
  if (isDemoMode && !effectiveIsLoggedIn.value) {
    showLoginModal.value = true
  } else {
    // 已登录，加载会话列表
    await loadSessions()

    // 子智能体模式下不加载主智能体最近会话
    // 如果 currentSessionId 已经是 null 且 sessions 已经加载（说明用户已经在导航前点击了"新会话"），不要再覆盖它
    // 只有当 currentSessionId 为 null 时（直接打开页面/刷新页面），才需要加载最近会话
    // 如果 currentSessionId 已有值（从历史列表点击跳转过来），保留用户选中的会话
    if (!subagentName.value && currentSessionId.value === null) {
      const hasSession = await loadLatestSession()
      if (hasSession && currentSessionId.value) {
        agentSessionId.value = currentSessionId.value
      }
    } else if (currentSessionId.value) {
      // currentSessionId 已有值（从历史页面跳转过来），需要同步到 agentSessionId
      agentSessionId.value = currentSessionId.value
    }
  }
})

onUnmounted(() => {
})

async function handleSend(content: string) {
  //console.log(`[${now()}] [handleSend] start, content length=${content.length}, currentSessionId=`, currentSessionId.value)
  if (isDemoMode && !effectiveIsLoggedIn.value) {
    showLoginModal.value = true
    return
  }

  // 如果有预生成的会话ID（从 Lobby 跳转），直接使用它
  // 这种情况下不需要创建 DB 会话，锁已通过 InstanceService 预先获取
  let sid: string | null | undefined
  if (pregeneratedSessionId.value) {
    sid = pregeneratedSessionId.value
    // 确保 agent 使用这个 sessionId
    agentSessionId.value = sid
    precacheNewSession(sid)
  } else if (!currentSessionId.value) {
    // 如果没有当前会话，自动创建一个（默认标题"新会话"，发送消息后更新标题）
    const newSession = await createNewSession(undefined, subagentName.value)
    if (newSession) {
      // 新建会话本来就是空的，预先缓存空数组，避免切换时请求后端
      precacheNewSession(newSession.session_id)
      skipNextSwitch.value = true
      selectSession(newSession.session_id)
      agentSessionId.value = newSession.session_id
      // 手动等待 switchSession 完成，避免 watcher 异步覆盖后续 sendMessage 的消息
      await switchSession(newSession.session_id)
      //console.log(`[${now()}] [handleSend] switchSession done, ready to send message`)
      sid = newSession.session_id
    }
  } else {
    sid = currentSessionId.value
  }

  if (!sid) {
    //console.log(`[${now()}] [handleSend] still no sessionId, abort`)
    return
  }

  // 如果是当前会话的首条用户消息（标题还是默认的"新会话"），自动用前10个字更新标题
  // 注意：预生成的会话ID还没有对应的 DB 记录，所以跳过这一步
  if (!pregeneratedSessionId.value) {
    const session = sessions.value.find(s => s.session_id === sid)
    if (session && (!session.title || session.title === '新会话')) {
      const title = content.slice(0, 10).trim() || '新会话'
      await renameSession(sid, title)
    }
  }

  await sendMessage(content, subagentName.value, sid, instanceId.value)

  // 发送成功后清空附件
  clearAttachments()
}

async function handleUpload(file: File) {
  try {
    console.log('前端日志：开始上传文件', file.name, file.size)
    const result = await uploadAttachment(file)
    console.log('前端日志：文件上传成功', result)
  } catch (error: any) {
    console.error('前端日志：文件上传失败', error)
    const errorMsg = error?.message || error?.toString?.() || '文件上传失败'
    console.error('前端日志：错误消息', errorMsg)
    toast.error(errorMsg)
  }
}

function handleRemoveFile(file_id: string) {
  removeAttachment(file_id)
}

// 处理侧边栏切换
function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  } else {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }
}

function now(): string {
  const d = new Date()
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}.${d.getMilliseconds().toString().padStart(3, '0')}`
}

async function handleLogout() {
  await doLogout()
  if (isDemoMode) {
    showLoginModal.value = true
  }
  // 登出时清空所有缓存：会话列表、消息、附件等
  clearSessionCache()
  clearSession()
  clearAttachments()
  clearSessionListCache()
  messages.value = []
}

function handleLoginSuccess() {
  showLoginModal.value = false
  // 登录成功后先清空所有缓存（避免同账号多设备时显示旧数据）
  clearSessionCache()
  clearSession()
  clearAttachments()
  clearSessionListCache()
  messages.value = []
  // 重新加载会话列表并自动打开最近会话
  loadSessions().then(async () => {
    const hasSession = await loadLatestSession()
    if (hasSession && currentSessionId.value) {
      // 同步会话ID到useAgent
      agentSessionId.value = currentSessionId.value
    }
  })
}

// 监听登录状态变化
watch(effectiveIsLoggedIn, async (loggedIn) => {
  if (isDemoMode && !loggedIn) {
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
// 注意：如果 currentSessionId 已有值（从历史会话点击），不执行清空，避免覆盖用户选择
watch(subagentName, () => {
  // 只有当不是从历史会话点击过来时才清空
  // 从历史会话点击的特征：currentSessionId 已有值，且不为 null
  if (!currentSessionId.value) {
    selectSession(null)
    messages.value = []
    clearSession()
    clearAttachments()
  }
})
</script>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s ease;
}
.slide-enter-from,
.slide-leave-to {
  transform: translateX(100%);
  opacity: 0;
}
</style>

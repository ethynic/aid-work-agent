<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Main Content -->
    <main class="flex-1 flex overflow-hidden">
      <!-- Session Sidebar - 仅在非 PortalLayout 模式下显示（避免重复） -->
      <MenuSidebar
        v-if="!isInPortalLayout"
        :is-collapsed="isSidebarCollapsed"
        :current-subagent-id="currentSubagentId"
        :available-subagents="availableSubagents"
        @collapse="isSidebarCollapsed = true"
      />

      <!-- Right Content Area -->
      <div class="flex-1 flex min-w-0">
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

        <!-- Attachment Preview Panel -->
        <Transition name="slide">
          <AttachmentPreviewPanel
            v-if="isPreviewOpen"
            :attachment="previewAttachment"
            @close="closePreview"
          />
        </Transition>
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
import { ref, onMounted, onUnmounted, watch, computed, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'
import LoginModal from './LoginModal.vue'
import AppHeader from './AppHeader.vue'
import MenuSidebar from './MenuSidebar.vue'
import CredentialManager from './CredentialManager.vue'
import SettingsDialog from './SettingsDialog.vue'
import AttachmentPreviewPanel from './AttachmentPreviewPanel.vue'
import { useAgent } from '@/composables/useAgent'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useSession } from '@/composables/useSession'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { listSubagents, type SubagentListItem } from '@/api/adminSubagent'
const router = useRouter()

// 从 PortalLayout 注入侧边栏状态（租户前台模式）
const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')

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
  sessionId: agentSessionId
} = useAgent()

const { user, isLoggedIn, init: initAuth, logout: doLogout } = useDemoAuth()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init: initTenantAuth } = useTenantAuth()
const { currentSessionId, sessions, createNewSession, loadSessions, loadLatestSession, selectSession, renameSession } = useSession()
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

// 可用的数字员工列表
const availableSubagents = ref<SubagentListItem[]>([])

// 当前选中的数字员工ID（null 表示主智能体）
const currentSubagentId = computed(() => subagentName.value ?? undefined)

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
const localSidebarCollapsed = ref(false)
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

// 计算页面标题
const pageTitle = computed(() => {
  // 从 availableSubagents 中查找当前数字员工的名称
  let agentName = ''
  if (subagentName.value) {
    const agent = availableSubagents.value.find(a => a.agent_id === subagentName.value)
    agentName = agent?.name || subagentName.value
  }
  const prefix = agentName ? `${agentName} - ` : ''
  if (!currentSessionId.value) {
    return `${prefix}新会话`
  }
  const session = sessions.value.find(s => s.session_id === currentSessionId.value)
  // 只有当会话有自定义标题（非默认的"新会话"）时才显示"历史会话："前缀
  if (session?.title && session.title !== '新会话') {
    return `${prefix}历史会话：${session.title}`
  }
  return `${prefix}新会话`
})
// 跳转到定时任务页面
function openScheduledTasks() {
  window.open('/scheduled-tasks', '_blank')
}

// 加载数字员工列表
async function loadAvailableSubagents() {
  try {
    const res = await listSubagents()
    if (res.success && res.data) {
      // 在列表最前面插入"主智能体"选项（特殊ID 'main'）
      availableSubagents.value = [
        { agent_id: 'main', name: 'CEO智能体', description: '', capabilities: [], type: 'builtin' },
        ...res.data
      ]
    }
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
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
  // 构建目标路由路径
  let targetPath: string
  const tenantMatch = route.path.match(/^\/t\/([^\/]+)/)
  if (tenantMatch) {
    // 租户模式
    const tenantId = tenantMatch[1]
    targetPath = agentId === 'main'
      ? `/t/${tenantId}/chat`
      : `/t/${tenantId}/chat/${agentId}`
  } else {
    // 普通演示模式
    targetPath = agentId === 'main' ? '/' : `/chat/${agentId}`
  }

  console.log(`[${now()}] [ConfirmDialog] router.push to`, targetPath)
  // 导航到对应路由
  // 现有代码已经监听 subagentName 变化，会自动清空会话并创建新会话
  await router.push(targetPath)
  console.log(`[${now()}] [ConfirmDialog] router.push done`)
}

// 模拟在线状态检测
onMounted(async () => {
  // 初始化认证状态（租户模式和演示模式都需要初始化）
  await Promise.all([initAuth(), initTenantAuth()])

  // 加载可用数字员工列表
  await loadAvailableSubagents()

  // 检查登录状态
  if (!effectiveIsLoggedIn.value) {
    showLoginModal.value = true
  } else {
    // 已登录，加载会话列表
    await loadSessions()
    // 子智能体模式下不加载主智能体最近会话
    // 如果 currentSessionId 已经是 null 且 sessions 已经加载（说明用户已经在导航前点击了"新会话"），不要再覆盖它
    // 只有当页面刚刷新（sessions 为空列表）且 currentSessionId 为 null 时，才需要加载最近会话
    if (!subagentName.value && (currentSessionId.value !== null || sessions.value.length === 0)) {
      const hasSession = await loadLatestSession()
      if (hasSession && currentSessionId.value) {
        agentSessionId.value = currentSessionId.value
      }
    }
  }
})

onUnmounted(() => {
})

async function handleSend(content: string) {
  console.log(`[${now()}] [handleSend] start, content length=${content.length}, currentSessionId=`, currentSessionId.value)
  if (!effectiveIsLoggedIn.value) {
    showLoginModal.value = true
    return
  }

  // 如果没有当前会话，自动创建一个（默认标题"新会话"，发送消息后更新标题）
  if (!currentSessionId.value) {
    console.log(`[${now()}] [handleSend] no current session, creating new session...`)
    const startTime = Date.now()
    const newSession = await createNewSession(undefined, subagentName.value)
    console.log(`[${now()}] [handleSend] createNewSession done in ${Date.now() - startTime}ms, newSession=`, newSession)
    if (newSession) {
      // 新建会话本来就是空的，预先缓存空数组，避免切换时请求后端
      precacheNewSession(newSession.session_id)
      skipNextSwitch.value = true
      selectSession(newSession.session_id)
      agentSessionId.value = newSession.session_id
      // 手动等待 switchSession 完成，避免 watcher 异步覆盖后续 sendMessage 的消息
      await switchSession(newSession.session_id)
      console.log(`[${now()}] [handleSend] switchSession done, ready to send message`)
    }
  }

  const sid = currentSessionId.value
  if (!sid) {
    console.log(`[${now()}] [handleSend] still no sessionId, abort`)
    return
  }

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
watch(effectiveIsLoggedIn, async (loggedIn) => {
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

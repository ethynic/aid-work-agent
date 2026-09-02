<template>
  <div class="h-screen flex flex-col bg-canvas">
    <!-- 顶部标题栏：复用 AppHeader 组件（页面布局规范要求） -->
    <AppHeader
      title="我的数字员工"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <!-- 内容区 -->
    <div class="flex-1 overflow-y-auto p-6">
      <!-- 加载态 -->
      <div v-if="loading" class="flex justify-center py-20">
        <div class="animate-spin rounded-full h-8 w-8 border-2 border-primary-600 border-t-transparent"></div>
      </div>

      <!-- 空状态 -->
      <div
        v-else-if="agents.length === 0"
        class="flex flex-col items-center justify-center py-20 text-muted"
      >
        <svg
          class="w-16 h-16 mb-4 text-gray-300"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" />
        </svg>
        <p class="text-base">暂无可用的数字员工</p>
        <p class="text-sm mt-1">请联系管理员开通权限</p>
      </div>

      <!-- 卡片网格 -->
      <div
        v-else
        class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4"
      >
        <div
          v-for="agent in agents"
          :key="agent.agent_id"
          class="group bg-surface rounded-xl border border-default p-5 cursor-pointer hover:border-primary-300 hover:shadow-md transition-all flex flex-col"
          @click="handleUseAgent(agent)"
        >
          <!-- 图标 + 名称 -->
          <div class="flex items-center gap-3 mb-3">
            <div class="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center text-gray-600 group-hover:bg-primary-50 group-hover:text-primary-600 transition-colors">
              <AgentIcon :agent-id="agent.agent_id" class="w-6 h-6" />
            </div>
            <h3 class="text-base font-semibold text-default truncate flex-1">
              {{ getDisplayName(agent) }}
            </h3>
          </div>

          <!-- 描述（JS 截断 30 字 + CSS line-clamp-2 双保险） -->
          <p class="text-sm text-muted line-clamp-2 break-words flex-1 mb-4">
            {{ truncateDescription(agent.description) }}
          </p>

          <!-- 立即使用按钮：@click.stop 阻止冒泡，避免触发卡片 click 两次 -->
          <button
            class="w-full h-9 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 transition-colors"
            @click.stop="handleUseAgent(agent)"
          >
            立即使用
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import AgentIcon from '@/components/ui/AgentIcon.vue'
import { listSubagents, type SubagentListItem } from '@/api/subagent'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()

const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 是否为租户模式（路径以 /t/ 开头）
const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 统一的登录状态检查（租户认证）
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

// 统一的用户信息
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

const agents = ref<SubagentListItem[]>([])
const loading = ref(true)

// 从 PortalLayout 注入侧边栏控制函数（与 TenantSettings.vue / AllSessions.vue 一致）
// 注：默认值 () => {} 用于未提供注入时避免 undefined 调用
const toggleSidebarFn = inject<() => void>('toggleSidebar', () => {})

function handleToggleSidebar() {
  toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  const tid = route.params.tenant_id
  router.push(`/t/${tid}/login`)
}

// 截断描述为 30 字 + "..."
function truncateDescription(desc: string | undefined): string {
  if (!desc) return '暂无简介'
  return desc.length > 30 ? desc.slice(0, 30) + '...' : desc
}

// 显示名优先用 display_name / instance_name，回退到 name
function getDisplayName(agent: SubagentListItem): string {
  return agent.display_name || agent.instance_name || agent.name
}

// 加载数字员工列表，过滤 main CEO 智能体
async function loadAgents() {
  loading.value = true
  try {
    const res = await listSubagents()
    const list = res.data || []
    // 过滤掉 main（CEO 智能体不展示给用户）
    agents.value = list.filter(a => a.agent_id !== 'main')
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
    agents.value = []
  } finally {
    loading.value = false
  }
}

// 跳转到与该数字员工对话
function handleUseAgent(agent: SubagentListItem) {
  const tid = route.params.tenant_id
  if (isTenantMode.value && tid) {
    router.push(`/t/${tid}/chat/${agent.agent_id}`)
  } else {
    router.push(`/chat/${agent.agent_id}`)
  }
}

onMounted(() => loadAgents())
</script>

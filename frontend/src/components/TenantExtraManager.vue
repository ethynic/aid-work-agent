<template>
  <div class="h-screen flex flex-col bg-canvas">
    <!-- 顶部标题栏：复用 AppHeader 组件（页面布局规范要求） -->
    <AppHeader
      title="定制提示词"
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
          <path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
        </svg>
        <p class="text-base">暂无可定制的数字员工</p>
        <p class="text-sm mt-1">请联系平台管理员开通</p>
      </div>

      <!-- 卡片网格 -->
      <div
        v-else
        class="max-w-6xl mx-auto"
      >
        <!-- 说明 -->
        <div class="bg-info-50 border border-info-200 rounded-lg p-4 mb-6">
          <div class="flex items-start gap-2">
            <svg class="w-5 h-5 text-info-600 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div class="text-sm text-info-700">
              <p class="font-medium mb-1">定制提示词</p>
              <p class="text-xs leading-relaxed">
                选择一个数字员工，为其追加本租户专属的提示词内容。修改后立即生效，且对本租户所有用户的对话生效。
              </p>
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          <div
            v-for="agent in agents"
            :key="agent.agent_id"
            class="group bg-surface rounded-xl border border-default p-5 hover:border-primary-300 hover:shadow-md transition-all flex flex-col"
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

            <!-- 定制按钮：跳转到该数字员工的 extra_md 编辑器 -->
            <button
              class="w-full h-9 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 transition-colors"
              @click="handleCustomize(agent)"
            >
              定制
            </button>
          </div>
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

// 本页仅挂载在租户路由下，登录状态与用户信息直接取租户认证
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : null)

const tenantId = computed(() => String(route.params.tenant_id || ''))

const agents = ref<SubagentListItem[]>([])
const loading = ref(true)

// 从 TenantLayout 注入侧边栏控制函数（与其它租户子页一致）
const toggleSidebarFn = inject<() => void>('toggleSidebar', () => {})
function handleToggleSidebar() {
  toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
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

// 加载数字员工列表，过滤 main CEO 智能体（与 MyDigitalEmployees 一致）
async function loadAgents() {
  loading.value = true
  try {
    const res = await listSubagents()
    const list = res.data || []
    agents.value = list.filter(a => a.agent_id !== 'main')
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
    agents.value = []
  } finally {
    loading.value = false
  }
}

// 跳转到该数字员工的定制提示词编辑器
function handleCustomize(agent: SubagentListItem) {
  router.push(`/t/${tenantId.value}/agent/${agent.agent_id}/prompt`)
}

onMounted(() => loadAgents())
</script>

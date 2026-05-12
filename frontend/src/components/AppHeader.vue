<template>
  <header class="flex-shrink-0 h-14 bg-white border-b border-gray-200 flex items-center px-4 shadow-sticky z-10">
    <div class="flex-1 flex items-center gap-3 min-w-0">
      <!-- Toggle Sidebar Button -->
      <button
        @click="$emit('toggle-sidebar')"
        class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors flex-shrink-0"
        title="切换侧边栏"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h7" />
        </svg>
      </button>

      <!-- Page Title -->
      <div class="flex items-center gap-2 min-w-0">
        <h1 class="text-sm font-bold text-gray-800 truncate">
          <slot name="title">{{ title }}</slot>
        </h1>
      </div>

      <!-- Digital Employee Selector -->
      <div v-if="shouldShowSelector || showReadonlyLabel" class="relative flex-shrink-0 ml-4">
        <!-- 选择框（多个选项时） -->
        <div v-if="shouldShowSelector" class="relative">
          <button
            @click="showSubagentDropdown = !showSubagentDropdown"
            class="flex items-center gap-2 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white border border-primary-700 rounded-lg transition-colors shadow-sm min-w-[120px] md:min-w-[160px]"
            :title="currentSubagentName"
          >
            <span class="text-sm font-medium text-white truncate flex-1">
              {{ currentSubagentName }}
            </span>
            <svg
              class="w-5 h-5 text-white transition-transform flex-shrink-0"
              :class="{ 'rotate-180': showSubagentDropdown }"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <!-- Dropdown Menu -->
          <div
            v-if="showSubagentDropdown"
            class="absolute left-0 top-full mt-2 w-[calc(100vw-2rem)] max-w-64 bg-white border border-gray-200 rounded-lg shadow-lg py-1 z-50 max-h-64 overflow-y-auto"
          >
            <button
              v-for="agent in filteredAvailableSubagents"
              :key="agent.instance_id || agent.agent_id"
              @click="selectSubagent(agent.instance_id || agent.agent_id)"
              :class="[
                'w-full px-4 py-3 text-left text-sm transition-colors flex items-center gap-3',
                isCurrentAgent(agent.instance_id || agent.agent_id)
                  ? 'bg-primary-50 text-primary-700 font-medium'
                  : 'text-gray-700 hover:bg-primary-50 hover:text-primary-700'
              ]"
            >
              <span class="flex-1 truncate text-sm" :title="getAgentDisplayName(agent)">{{ getAgentDisplayName(agent) }}</span>
              <svg
                v-if="isCurrentAgent(agent.agent_id)"
                class="w-4 h-4 text-primary-600 flex-shrink-0"
                fill="currentColor"
                viewBox="0 0 20 20"
              >
                <path
                  fill-rule="evenodd"
                  d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                  clip-rule="evenodd"
                />
              </svg>
            </button>
          </div>

          <!-- Click outside to close -->
          <div
            v-if="showSubagentDropdown"
            class="fixed inset-0 z-40"
            @click="showSubagentDropdown = false"
          ></div>
        </div>
        <!-- 只读标签（单个选项时） -->
        <div v-else-if="showReadonlyLabel" class="px-4 py-2 bg-gray-100 text-gray-700 border border-gray-300 rounded-lg text-sm font-medium truncate min-w-[120px] md:min-w-[160px]">
          {{ currentSubagentName }}
        </div>
      </div>
    </div>

    <!-- Right Side - User Info & Actions -->
    <div v-if="isLoggedIn" class="flex items-center gap-3 flex-shrink-0">
      <div class="hidden md:flex items-center gap-3">
        <!-- User Name -->
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-600">{{ user?.username }}</span>
          <button
            v-if="showDemoLogout"
            @click="$emit('logout')"
            class="px-2 py-1 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded transition-colors"
          >
            退出
          </button>
        </div>

        <!-- More Menu -->
        <div class="relative">
          <button
            @click="showMenuDropdown = !showMenuDropdown"
            class="px-2 py-1.5 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            更多
          </button>

          <!-- Dropdown Menu -->
          <div
            v-if="showMenuDropdown"
            class="absolute right-0 top-full mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg py-1 z-50"
          >
            <slot name="menu-items" :close-menu="closeMenu">
              <slot name="extra-menu-items" :close-menu="closeMenu" />
            </slot>
          </div>
        </div>

        <!-- Click outside to close menu -->
        <div
          v-if="showMenuDropdown"
          class="fixed inset-0 z-40"
          @click="showMenuDropdown = false"
        ></div>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { AgentItem } from '@/api/saasPermissions'

const props = defineProps<{
  title?: string
  isLoggedIn?: boolean
  user?: { username: string; user_id?: string | number } | null
  /** 可用的数字员工列表（演示模式为子智能体类型，租户模式为实例列表） */
  availableSubagents?: AgentItem[]
  /** 当前选中的数字员工ID，null 表示主智能体。租户模式下为 instance_id，演示模式下为 agent_id */
  currentSubagentId?: string | null
  /** 是否显示右上角演示模式退出按钮，默认 true */
  showDemoLogout?: boolean
}>()

const emit = defineEmits<{
  'toggle-sidebar': []
  'logout': []
  'change-subagent': [agentId: string]
}>()

const showMenuDropdown = ref(false)
const showSubagentDropdown = ref(false)




// 过滤后可用的数字员工列表（根据权限过滤）
// 注意：后端已根据权限过滤，这里直接返回即可
const filteredAvailableSubagents = computed(() => {
  return props.availableSubagents || []
})



// 是否应该显示选择框：只有多个选项时才显示
const shouldShowSelector = computed(() => {
  return filteredAvailableSubagents.value && filteredAvailableSubagents.value.length > 1
})

// 是否显示只读标签：只有一个选项时显示只读标签
const showReadonlyLabel = computed(() => {
  return filteredAvailableSubagents.value && filteredAvailableSubagents.value.length === 1
})

// 当前选中的数字员工名称
const currentSubagentName = computed(() => {
  if (props.currentSubagentId == null) {
    // 检查主智能体是否在可用列表中
    const mainAgent = filteredAvailableSubagents.value.find((s: AgentItem) => s.agent_id === 'main')
    if (mainAgent) {
      return 'CEO智能体'
    }
    // 主智能体不可用，返回第一个可用智能体的名称或空字符串
    if (filteredAvailableSubagents.value.length > 0) {
      return getAgentDisplayName(filteredAvailableSubagents.value[0])
    }
    return ''
  }
  const found = filteredAvailableSubagents.value.find((s: AgentItem) =>
    // 租户模式下优先匹配 instance_id，其次匹配 agent_id
    s.instance_id === props.currentSubagentId || s.agent_id === props.currentSubagentId
  )
  return found ? getAgentDisplayName(found) : ''
})

// TODO: 临时修改 - 屏蔽实例并发控制
// 优先显示智能体名称而不是实例名称（后端返回的智能体列表不包含instance_name等字段）
// 未来需要恢复为优先显示实例名称
function getAgentDisplayName(agent: AgentItem): string {
  return agent.name || agent.display_name || agent.instance_name || agent.agent_id
}

// 判断是否为当前选中
function isCurrentAgent(agentId: string): boolean {
  if (agentId === 'main') return props.currentSubagentId == null
  return agentId === props.currentSubagentId
}

// 选择数字员工
function selectSubagent(agentId: string) {
  if (!isCurrentAgent(agentId)) {
    emit('change-subagent', agentId)
  }
  showSubagentDropdown.value = false
}

function closeMenu() {
  showMenuDropdown.value = false
}
</script>

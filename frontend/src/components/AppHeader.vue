<template>
  <header class="flex-shrink-0 h-14 bg-white border-b border-gray-200 flex items-center px-4">
    <div class="flex-1 flex items-center gap-3 min-w-0">
      <!-- Toggle Sidebar Button -->
      <button
        @click="$emit('toggle-sidebar')"
        class="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors flex-shrink-0"
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
      <div v-if="shouldShowSelector" class="relative flex-shrink-0 ml-4">
        <button
          @click="showSubagentDropdown = !showSubagentDropdown"
          class="flex items-center gap-2 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white border border-primary-700 rounded-lg transition-colors shadow-sm min-w-[160px]"
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
          class="absolute left-0 top-full mt-2 w-64 bg-white border border-gray-200 rounded-lg shadow-lg py-1 z-50 max-h-64 overflow-y-auto"
        >
          <button
            v-for="agent in filteredAvailableSubagents"
            :key="agent.agent_id"
            @click="selectSubagent(agent.agent_id)"
            :class="[
              'w-full px-4 py-3 text-left text-sm transition-colors flex items-center gap-3',
              isCurrentAgent(agent.agent_id)
                ? 'bg-primary-50 text-primary-700 font-medium'
                : 'text-gray-700 hover:bg-primary-50 hover:text-primary-700'
            ]"
          >
            <span class="flex-1 truncate text-sm" :title="agent.name">{{ agent.name }}</span>
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
    </div>

    <!-- Right Side - User Info & Actions -->
    <div v-if="isLoggedIn" class="flex items-center gap-3 flex-shrink-0">
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
  </header>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getMyAllowedAgents } from '@/api/saasPermissions'
import type { SubagentListItem } from '@/api/adminSubagent'

const props = defineProps<{
  title?: string
  isLoggedIn?: boolean
  user?: { username: string; user_id?: string | number } | null
  /** 可用的数字员工列表 */
  availableSubagents?: SubagentListItem[]
  /** 当前选中的数字员工ID，null 表示主智能体 */
  currentSubagentId?: string | null
  /** 是否显示右上角演示模式退出按钮，默认 true */
  showDemoLogout?: boolean
}>()

const emit = defineEmits<{
  'toggle-sidebar': []
  'logout': []
  'change-subagent': [agentId: string]
}>()

const route = useRoute()
const showMenuDropdown = ref(false)
const showSubagentDropdown = ref(false)

// 权限：当前用户允许访问的数字员工 ID 列表（仅租户模式需要）
const myAllowedAgentIds = ref<Set<string>>(new Set())

// 判断是否为租户模式
const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 加载当前用户允许的数字员工权限
async function loadMyAllowedAgents() {
  if (!isTenantMode.value) {
    return
  }
  try {
    const res = await getMyAllowedAgents()
    if (res.success && res.data) {
      myAllowedAgentIds.value = new Set(res.data.map(a => a.agent_id))
    }
  } catch (err) {
    console.error('加载用户数字员工权限失败', err)
  }
}

// 过滤后可用的数字员工列表（根据权限过滤）
const filteredAvailableSubagents = computed(() => {
  if (!props.availableSubagents) return []
  if (!isTenantMode.value || myAllowedAgentIds.value.size === 0) {
    // 非租户模式：不过滤，返回全部
    return props.availableSubagents
  }
  // 租户模式：只返回当前用户有权限的
  return props.availableSubagents.filter(s => myAllowedAgentIds.value.has(s.agent_id))
})

// 在挂载时加载权限
onMounted(() => {
  loadMyAllowedAgents()
})

// 是否应该显示选择框：只有多个选项时才显示
const shouldShowSelector = computed(() => {
  return filteredAvailableSubagents.value && filteredAvailableSubagents.value.length > 1
})

// 当前选中的数字员工名称
const currentSubagentName = computed(() => {
  if (props.currentSubagentId == null) return 'CEO智能体'
  const found = filteredAvailableSubagents.value.find((s: SubagentListItem) => s.agent_id === props.currentSubagentId)
  return found?.name || 'CEO智能体'
})

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

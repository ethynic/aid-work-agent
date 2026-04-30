<template>
  <div class="min-h-screen bg-gray-50">
    <!-- Header -->
    <header class="bg-white border-b border-gray-200 shadow-sm">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="flex justify-between items-center py-4">
          <div>
            <h1 class="text-lg font-semibold text-gray-900">
              {{ pageTitle }}
            </h1>
            <p v-if="currentSubagentName" class="text-sm text-gray-500 mt-1">
              {{ currentSubagentName }}
            </p>
          </div>
          <div class="flex items-center gap-3">
            <button
              @click="goBack"
              class="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-md transition-colors"
            >
              返回对话
            </button>
          </div>
        </div>
      </div>
    </header>

    <!-- Content -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <slot></slot>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { type SubagentListItem } from '@/api/subagent'
import { getMyAllowedAgents } from '@/api/saasPermissions'

const route = useRoute()
const router = useRouter()

// 判断是否为租户模式（路由以 /t/ 开头）
const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 租户 ID 从路径提取
const tenantId = computed(() => {
  const match = route.path.match(/^\/t\/([^\/]+)/)
  return match ? match[1] : null
})

// 从路径第一段解析 subagent_id
const subagentId = computed(() => {
  const pathSegments = route.path.split('/').filter(p => p)
  // 租户模式下 pathSegments[0] = 't', pathSegments[1] = tenantId, pathSegments[2] = subagentId
  if (isTenantMode.value && pathSegments.length >= 3) {
    return pathSegments[2]
  }
  // 非租户模式下 pathSegments[0] = subagentId
  return pathSegments.length > 0 ? pathSegments[0] : null
})

// 可用的数字员工列表
const availableSubagents = ref<SubagentListItem[]>([])

// 当前子智能体名称
const currentSubagentName = computed(() => {
  if (!subagentId.value) return ''
  const subagent = availableSubagents.value.find((s: SubagentListItem) => s.agent_id === subagentId.value)
  return subagent?.name || ''
})

// 页面标题
const pageTitle = computed(() => {
  const matched = route.matched[route.matched.length - 1]
  return matched?.name?.toString() || '业务数据'
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

// 返回对话页面
function goBack() {
  let path: string
  if (isTenantMode.value && tenantId.value) {
    path = subagentId.value
      ? `/t/${tenantId.value}/chat/${subagentId.value}`
      : `/t/${tenantId.value}/chat`
  } else {
    path = subagentId.value
      ? `/chat/${subagentId.value}`
      : '/'
  }
  router.push(path)
}

onMounted(() => {
  loadAvailableSubagents()
})
</script>

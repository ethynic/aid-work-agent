<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader
      title="内置数字员工"
      @toggle-sidebar="handleToggleSidebar"
    />

    <div class="flex-1 flex overflow-hidden">
      <!-- 左侧列表 -->
      <div class="w-64 border-r border-default bg-surface flex flex-col flex-shrink-0">
        <div class="p-4 border-b border-default">
          <h2 class="text-sm font-medium text-muted">内置数字员工</h2>
          <p class="text-xs text-muted mt-1">共 {{ builtinList.length }} 个</p>
        </div>
        <div class="flex-1 overflow-y-auto">
          <div
            v-for="agent in builtinList"
            :key="agent.agent_id"
            :class="[
              'p-3 cursor-pointer border-l-2 transition-colors',
              selectedAgent?.agent_id === agent.agent_id
                ? 'border-primary-600 bg-primary-50'
                : 'border-transparent hover:bg-surface-hover'
            ]"
            @click="selectAgent(agent)"
          >
            <div class="text-sm text-default font-medium truncate">{{ agent.name }}</div>
            <div class="text-xs text-muted mt-1 line-clamp-2">{{ agent.description || '无描述' }}</div>
          </div>
          <div v-if="builtinList.length === 0" class="text-xs text-muted text-center py-4">暂无内置数字员工</div>
        </div>
      </div>

      <!-- 右侧详情 -->
      <div class="flex-1 overflow-y-auto bg-surface">
        <template v-if="selectedAgent && detail">
          <div class="p-6">
            <div class="mb-6">
              <h1 class="text-lg font-semibold text-default">{{ detail.name }}</h1>
              <p class="text-sm text-muted mt-1">{{ detail.description || '无描述' }}</p>
              <span class="inline-block mt-2 px-2 py-0.5 text-xs rounded-full bg-info-100 text-info-700">内置</span>
            </div>

            <!-- 基本信息 -->
            <div class="mb-6">
              <h3 class="text-sm font-medium text-default mb-3">基本信息</h3>
              <div class="grid grid-cols-2 gap-4">
                <div>
                  <div class="text-sm text-muted mb-1">智能体ID</div>
                  <div class="text-sm text-default font-mono">{{ detail.agent_id }}</div>
                </div>
                <div v-if="detail.version">
                  <div class="text-sm text-muted mb-1">版本</div>
                  <div class="text-sm text-default">{{ detail.version }}</div>
                </div>
              </div>
            </div>

            <!-- 工具配置 -->
            <div v-if="detail.tools" class="mb-6">
              <h3 class="text-sm font-medium text-default mb-3">工具配置</h3>
              <div v-if="detail.tools.inherit" class="text-sm text-default">继承全部工具</div>
              <div v-else-if="detail.tools.list?.length" class="flex flex-wrap gap-2">
                <BaseBadge v-for="tool in detail.tools.list" :key="tool" intent="warning">{{ tool }}</BaseBadge>
              </div>
              <span v-else class="text-sm text-muted">（空）</span>
            </div>

            <!-- 可用技能 -->
            <div v-if="detail.skills" class="mb-6">
              <h3 class="text-sm font-medium text-default mb-3">可用技能</h3>
              <div v-if="detail.skills.allowed?.length" class="flex flex-wrap gap-2">
                <BaseBadge v-for="skill in detail.skills.allowed" :key="skill" intent="primary">{{ skill }}</BaseBadge>
              </div>
              <span v-else class="text-sm text-muted">（空）</span>
            </div>

            <!-- 系统提示词 -->
            <div v-if="detail.system_prompt" class="mb-6">
              <h3 class="text-sm font-medium text-default mb-3">系统提示词</h3>
              <pre class="bg-gray-50 rounded-lg p-4 text-xs text-default whitespace-pre-wrap overflow-auto max-h-96" v-html="renderedMarkdown"></pre>
            </div>
          </div>
        </template>

        <div v-else class="flex items-center justify-center h-full">
          <div class="text-center">
            <div class="text-4xl mb-4">🤖</div>
            <div class="text-sm text-muted">选择一个数字员工查看详情</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { marked } from 'marked'
import AppHeader from './AppHeader.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  listSubagents,
  getSubagentDetail,
  type SubagentListItem,
  type SubagentDetail,
} from '../api/adminSubagent'

const builtinList = computed(() =>
  allList.value
    .filter(i => i.type === 'builtin')
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
)

const renderedMarkdown = computed(() => {
  if (!detail.value?.system_prompt) return ''
  return marked(detail.value.system_prompt, { breaks: true })
})

const allList = ref<SubagentListItem[]>([])
const selectedAgent = ref<SubagentListItem | null>(null)
const detail = ref<SubagentDetail | null>(null)
const loading = ref(false)

const handleToggleSidebar = () => {
  // PortalLayout 提供，可选
}

async function loadList() {
  try {
    const res = await listSubagents()
    if (res.success) {
      allList.value = res.data || []
    }
  } catch (e: any) {
    console.error('加载列表失败', e)
  }
}

async function selectAgent(item: SubagentListItem) {
  selectedAgent.value = item
  loading.value = true
  try {
    const res = await getSubagentDetail(item.agent_id)
    if (res.success && res.data) {
      detail.value = res.data
    }
  } catch (e: any) {
    console.error('加载详情失败', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadList()
})
</script>

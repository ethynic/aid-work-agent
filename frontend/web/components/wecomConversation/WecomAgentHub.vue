<template>
  <div class="h-screen flex flex-col bg-canvas">
    <!-- 企业微信Agent 工作区切换条（连接中心三入口合并后的唯一顶部导航） -->
    <div class="flex-shrink-0 h-10 bg-white border-b border-gray-200 flex items-center px-4 gap-1">
      <span class="text-sm font-medium text-default mr-3">企业微信Agent</span>
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="px-3 h-7 rounded-md text-sm transition-colors"
        :class="activeTab === tab.key
          ? 'bg-primary-50 text-primary-700 font-medium'
          : 'text-muted hover:bg-surface-hover'"
        :data-testid="`wecom-agent-tab-${tab.key}`"
        @click="switchTab(tab.key)"
      >{{ tab.label }}</button>
    </div>
    <!-- 三个既有页面原样内嵌（embedded：根容器 h-full，页面各自 AppHeader 保留） -->
    <div class="flex-1 min-h-0 overflow-hidden">
      <KeepAlive>
        <component :is="currentComponent" embedded />
      </KeepAlive>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WecomConversationCenter from './WecomConversationCenter.vue'
import WecomConversationDaily from './WecomConversationDaily.vue'
import WecomProxyTasks from './WecomProxyTasks.vue'

type AgentTab = 'conversations' | 'daily' | 'proxy'

const tabs: { key: AgentTab; label: string }[] = [
  { key: 'conversations', label: '会话记录' },
  { key: 'daily', label: '每日总结' },
  { key: 'proxy', label: '聊天配置' },
]

const components: Record<AgentTab, typeof WecomConversationCenter> = {
  conversations: WecomConversationCenter,
  daily: WecomConversationDaily,
  proxy: WecomProxyTasks,
}

const route = useRoute()
const router = useRouter()

const activeTab = computed<AgentTab>(() => {
  const value = route.params.tab as AgentTab | undefined
  return value === 'daily' || value === 'proxy' ? value : 'conversations'
})

const currentComponent = computed(() => components[activeTab.value])

function switchTab(tab: AgentTab) {
  const tenantId = route.params.tenant_id
  if (tab === activeTab.value) return
  router.push({ name: 'tenant-wecom-agent', params: { ...(tenantId ? { tenant_id: tenantId } : {}), tab } })
}
</script>

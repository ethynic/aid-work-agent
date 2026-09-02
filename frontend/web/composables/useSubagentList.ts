import { ref, computed } from 'vue'
import type { SubagentListItem } from '@/api/subagent'
import type { AgentItem } from '@/api/saasPermissions'
import { getMyAllowedAgents } from '@/api/saasPermissions'

// 全局缓存（同一页面内只加载一次）
const availableSubagents = ref<(SubagentListItem | AgentItem)[]>([])
const isLoaded = ref(false)
const isLoading = ref(false)

/**
 * 数字员工列表 composable（带缓存，避免重复请求）
 *
 * 使用场景：
 * - 租户模式（/t/*）：调用 getMyAllowedAgents，返回 AgentItem[]（含实例信息）
 * - 非租户路由（/portal 等）：调用 listSubagents，返回 SubagentListItem[]
 */
export function useSubagentList() {
  /**
   * 加载可用数字员工列表（带缓存）
   * @param isTenantMode 是否为租户模式，影响使用哪个接口
   * @param forceRefresh 是否强制刷新，默认 false
   */
  async function loadAvailableSubagents(isTenantMode = true, forceRefresh = false) {
    // 已有数据且不强制刷新，直接返回
    if (isLoaded.value && !forceRefresh && !isLoading.value) {
      return
    }

    // 避免并发重复请求
    if (isLoading.value) {
      return
    }

    isLoading.value = true
    try {
      let res
      if (isTenantMode) {
        // 租户模式下使用 allowed-agents 接口
        res = await getMyAllowedAgents()
      } else {
        // 非租户路由使用 listSubagents 接口
        const { listSubagents } = await import('@/api/subagent')
        res = await listSubagents()
      }

      if (res.success && res.data) {
        availableSubagents.value = res.data
        isLoaded.value = true
      }
    } catch (e) {
      console.error('加载数字员工列表失败:', e)
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 清空缓存（重新登录或切换租户时调用）
   */
  function clearCache() {
    availableSubagents.value = []
    isLoaded.value = false
    isLoading.value = false
  }

  // 类型转换：作为 AgentItem 类型使用（ChatContainer）
  const asAgentItems = computed(() => availableSubagents.value as AgentItem[])

  // 类型转换：作为 SubagentListItem 类型使用（PortalLayout）
  const asSubagentListItems = computed(() => availableSubagents.value as SubagentListItem[])

  return {
    availableSubagents,
    asAgentItems,
    asSubagentListItems,
    isLoaded,
    isLoading,
    loadAvailableSubagents,
    clearCache,
  }
}

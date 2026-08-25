<template>
  <div class="h-full flex flex-col bg-canvas">
    <!-- 顶部标题栏 -->
    <div class="flex items-center justify-between px-6 py-4 bg-surface border-b border-default flex-shrink-0">
      <div class="flex items-center gap-3">
        <h1 class="text-lg font-semibold text-default">Redis 缓存管理</h1>
        <BaseBadge v-if="overview?.fallback_active" intent="warning">内存降级模式</BaseBadge>
        <BaseBadge v-else-if="overview?.redis_connected" intent="success">Redis 已连接</BaseBadge>
      </div>
      <div class="flex items-center gap-2">
        <BaseButton intent="secondary" size="md" :disabled="loadingOverview" @click="loadOverview">
          <svg class="w-4 h-4 inline-block mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          刷新
        </BaseButton>
        <BaseButton intent="danger" size="md" :disabled="clearingAll" @click="handleClearAllCache">
          <svg class="w-4 h-4 inline-block mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
          清空缓存
        </BaseButton>
      </div>
    </div>

    <!-- 主体内容：左侧树 + 右侧列表 -->
    <div class="flex-1 flex min-h-0 overflow-hidden">
      <!-- 左侧前缀树 -->
      <aside class="w-60 bg-surface border-r border-default flex flex-col flex-shrink-0">
        <div class="px-4 py-3 border-b border-default">
          <h2 class="text-sm font-semibold text-default">前缀分组</h2>
        </div>
        <nav class="flex-1 overflow-y-auto py-2">
          <!-- 全部 -->
          <button
            class="w-full flex items-center justify-between px-4 py-2 text-sm transition-colors hover:bg-surface-hover"
            :class="selectedPrefix === null ? 'bg-primary-50 text-primary-700 border-r-2 border-primary-500' : 'text-default'"
            @click="selectPrefix(null)"
          >
            <span>全部</span>
            <span class="text-xs text-muted">{{ overview?.total_keys ?? '-' }}</span>
          </button>

          <!-- 业务域分组 -->
          <div v-for="group in prefixGroups" :key="group.name" class="mt-2">
            <div class="px-4 py-1 text-xs font-medium text-muted uppercase tracking-wider">
              {{ group.name }}
            </div>
            <button
              v-for="pfx in group.prefixes"
              :key="pfx.value"
              class="w-full flex items-center justify-between px-4 py-2 text-sm transition-colors hover:bg-surface-hover"
              :class="selectedPrefix === pfx.value ? 'bg-primary-50 text-primary-700 border-r-2 border-primary-500' : 'text-default'"
              @click="selectPrefix(pfx.value)"
            >
              <span class="truncate">{{ pfx.label }}</span>
            </button>
          </div>

          <!-- 裸键节点 -->
          <div class="mt-2 border-t border-default pt-2">
            <button
              class="w-full flex items-center justify-between px-4 py-2 text-sm transition-colors hover:bg-surface-hover"
              :class="selectedPrefix === '__bare__' ? 'bg-warning-50 text-warning-700 border-r-2 border-warning-500' : 'text-warning-700'"
              @click="selectPrefix('__bare__')"
            >
              <span class="flex items-center gap-1">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01" />
                </svg>
                <span>裸键</span>
              </span>
              <span class="text-xs">{{ overview?.bare_keys ?? '-' }}</span>
            </button>
          </div>
        </nav>
      </aside>

      <!-- 右侧内容区 -->
      <div class="flex-1 flex flex-col min-h-0 overflow-hidden">
        <!-- 概览栏 -->
        <div class="px-6 py-3 bg-surface border-b border-default flex items-center gap-6 flex-shrink-0">
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted">总键数</span>
            <span class="text-sm font-semibold text-default">{{ overview?.total_keys ?? '-' }}</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted">已登记</span>
            <span class="text-sm font-semibold text-success-700">{{ overview?.registered_keys ?? '-' }}</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted">裸键</span>
            <span class="text-sm font-semibold text-warning-700">{{ overview?.bare_keys ?? '-' }}</span>
          </div>
          <div v-if="overview?.key_prefix" class="flex items-center gap-2">
            <span class="text-xs text-muted">Key Prefix</span>
            <span class="text-sm font-mono text-default">{{ overview.key_prefix }}</span>
          </div>
        </div>

        <!-- 裸键告警提示 -->
        <div
          v-if="selectedPrefix === '__bare__'"
          class="px-6 py-2 bg-warning-50 border-b border-warning-200 text-warning-800 text-xs flex items-start gap-2 flex-shrink-0"
        >
          <svg class="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6">
            <path stroke-linecap="round" stroke-linejoin="round" d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01" />
          </svg>
          <span>这些键未在 CacheKeys 中登记前缀，可能是误用裸键。请排查后到 <code class="px-1 bg-warning-100 rounded">src/core/cache_utils.py</code> 补登记，或确认后删除。</span>
        </div>

        <!-- 搜索区 -->
        <div class="px-6 py-3 bg-surface border-b border-default flex items-center gap-3 flex-shrink-0">
          <BaseInput
            v-model="searchKeyword"
            placeholder="键名子串搜索..."
            size="sm"
            class="w-80"
            @keyup.enter="handleSearch"
          />
          <BaseButton intent="primary" size="sm" :disabled="loadingKeys" @click="handleSearch">搜索</BaseButton>
          <BaseButton intent="secondary" size="sm" :disabled="loadingKeys" @click="handleClearSearch">重置</BaseButton>
          <div class="flex-1"></div>
          <span class="text-xs text-muted">
            已加载 {{ keyItems.length }} 条
            <span v-if="currentFilter">（过滤: {{ currentFilter }}）</span>
          </span>
        </div>

        <!-- 表格区 -->
        <div class="flex-1 overflow-auto p-6 min-h-0">
          <div v-if="loadingKeys && keyItems.length === 0" class="text-center py-12 text-muted">加载中...</div>
          <div v-else-if="!loadingKeys && keyItems.length === 0" class="text-center py-12 text-muted">暂无数据</div>
          <div v-else class="bg-surface rounded-lg border border-default overflow-hidden">
            <BaseTable :columns="columns" :data="keyItems" row-key="key">
              <template #key="{ row }">
                <div class="flex items-center gap-2">
                  <span class="text-sm text-default font-mono truncate">{{ row.key }}</span>
                  <BaseBadge v-if="row.is_bare" intent="warning" size="sm">裸键</BaseBadge>
                </div>
              </template>
              <template #type="{ row }">
                <BaseBadge :intent="typeBadgeIntent(row.type)" size="sm">{{ row.type }}</BaseBadge>
              </template>
              <template #ttl="{ row }">
                <span
                  v-if="row.ttl_info"
                  :class="{
                    'text-danger-600': row.ttl_info.level === 'danger',
                    'text-warning-600': row.ttl_info.level === 'warning',
                    'text-default': row.ttl_info.level === 'normal',
                  }"
                  class="text-sm"
                >
                  {{ row.ttl_info.label }}
                </span>
              </template>
              <template #size="{ row }">
                <span class="text-sm text-muted">{{ row.size_label }}</span>
              </template>
              <template #actions="{ row }">
                <div class="flex items-center gap-2 whitespace-nowrap">
                  <BaseButton intent="ghost" size="sm" @click="openKeyDetail(row.key)">查看</BaseButton>
                  <BaseButton intent="danger-ghost" size="sm" @click="handleDeleteKey(row as RedisKeyItem)">删除</BaseButton>
                </div>
              </template>
            </BaseTable>
          </div>

          <!-- 加载更多 -->
          <div v-if="hasMore" class="mt-4 text-center">
            <BaseButton intent="secondary" size="md" :disabled="loadingKeys" @click="loadMore">
              加载更多
            </BaseButton>
          </div>
        </div>
      </div>
    </div>

    <!-- 单键详情弹框 -->
    <BaseModal
      v-model="showDetailModal"
      :title="'键详情'"
      size="xl"
      :close-on-overlay="true"
    >
      <div v-if="detailLoading" class="py-12 text-center text-muted">加载中...</div>
      <div v-else-if="detailData && detailData.success" class="space-y-4">
        <!-- 基本信息栏 -->
        <div class="grid grid-cols-4 gap-4 pb-4 border-b border-default">
          <div>
            <div class="text-xs text-muted">类型</div>
            <div class="mt-1">
              <BaseBadge v-if="detailData.type" :intent="typeBadgeIntent(detailData.type)" size="sm">
                {{ detailData.type }}
              </BaseBadge>
            </div>
          </div>
          <div>
            <div class="text-xs text-muted">TTL</div>
            <div
              v-if="detailData.ttl_info"
              class="mt-1 text-sm font-medium"
              :class="{
                'text-danger-600': detailData.ttl_info.level === 'danger',
                'text-warning-600': detailData.ttl_info.level === 'warning',
                'text-default': detailData.ttl_info.level === 'normal',
              }"
            >
              {{ detailData.ttl_info.label }}
            </div>
          </div>
          <div>
            <div class="text-xs text-muted">大小</div>
            <div class="mt-1 text-sm text-default">{{ detailData.size_label }}</div>
          </div>
          <div>
            <div class="text-xs text-muted">成员数</div>
            <div class="mt-1 text-sm text-default">{{ detailData.member_count ?? '-' }}</div>
          </div>
        </div>

        <!-- 键名 -->
        <div>
          <div class="text-xs text-muted mb-1">键名</div>
          <div class="px-3 py-2 bg-canvas rounded border border-default text-sm font-mono text-default break-all">
            {{ detailData.key }}
          </div>
        </div>

        <!-- 值（string 类型） -->
        <div v-if="detailData.type === 'string' && detailData.value !== null && detailData.value !== undefined">
          <div class="text-xs text-muted mb-1">值</div>
          <div class="px-3 py-2 bg-canvas rounded border border-default max-h-96 overflow-auto">
            <pre class="text-sm text-default whitespace-pre-wrap break-all">{{ formatValue(detailData.value) }}</pre>
          </div>
        </div>

        <!-- Hash 类型表格 -->
        <div v-else-if="detailData.type === 'hash' && detailData.members">
          <div class="text-xs text-muted mb-1">Hash 字段（{{ detailData.member_count }} 项）</div>
          <div class="border border-default rounded max-h-96 overflow-auto">
            <table class="w-full">
              <thead class="bg-canvas sticky top-0">
                <tr>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted border-b border-default">Field</th>
                  <th class="px-3 py-2 text-left text-xs font-medium text-muted border-b border-default">Value</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-default">
                <tr v-for="(val, field) in detailData.members" :key="String(field)" class="hover:bg-surface-hover">
                  <td class="px-3 py-2 text-sm font-mono text-default align-top break-all">{{ field }}</td>
                  <td class="px-3 py-2 text-sm text-default break-all">{{ formatValue(val) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- List/Set/Zset 类型列表 -->
        <div v-else-if="['list', 'set', 'zset'].includes(detailData.type as string) && detailData.members">
          <div class="text-xs text-muted mb-1">
            {{ detailData.type === 'list' ? 'List' : detailData.type === 'set' ? 'Set' : 'ZSet' }}
            成员（{{ detailData.member_count }} 项{{ detailData.truncated ? '，仅展示前 200 项' : '' }}）
          </div>
          <div class="border border-default rounded max-h-96 overflow-auto">
            <ul class="divide-y divide-default">
              <li
                v-for="(m, idx) in detailData.members"
                :key="idx"
                class="px-3 py-2 text-sm text-default hover:bg-surface-hover break-all"
              >
                <span class="text-xs text-muted mr-2">{{ idx + 1 }}.</span>
                <span class="font-mono">{{ formatValue(m) }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
      <div v-else class="py-12 text-center text-muted">
        {{ detailData?.message || '加载失败' }}
      </div>

      <template #footer>
        <BaseButton intent="secondary" size="md" @click="showDetailModal = false">关闭</BaseButton>
        <BaseButton
          v-if="detailData && detailData.success"
          intent="danger"
          size="md"
          @click="handleDeleteFromDetail"
        >
          删除此键
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import {
  getRedisOverview,
  listRedisKeys,
  getRedisKeyDetail,
  deleteRedisKey,
  clearAllCache,
  type RedisOverview,
  type RedisKeyItem,
  type RedisKeyDetail,
} from '@/api/redisCache'

const toast = useToast()

// ============== 状态 ==============

const overview = ref<RedisOverview | null>(null)
const loadingOverview = ref(false)

const keyItems = ref<RedisKeyItem[]>([])
const loadingKeys = ref(false)
const hasMore = ref(false)
const nextCursor = ref(0)
const currentFilter = ref<string>('')

const selectedPrefix = ref<string | null>(null)
const searchKeyword = ref('')

// 详情弹框
const showDetailModal = ref(false)
const detailData = ref<RedisKeyDetail | null>(null)
const detailLoading = ref(false)

// 清空全部缓存
const clearingAll = ref(false)

// ============== 前缀分组定义 ==============

// CacheKeys 已登记前缀按业务域归类（参考 design-redis-cache-admin.md §4.3）
const prefixGroups = [
  {
    name: '认证与安全',
    prefixes: [
      { value: 'token', label: 'token' },
      { value: 'user', label: 'user' },
      { value: 'user_sessions', label: 'user_sessions' },
    ],
  },
  {
    name: '会话与队列',
    prefixes: [
      { value: 'session', label: 'session' },
      { value: 'session_msgs', label: 'session_msgs' },
      { value: 'ch_session', label: 'ch_session' },
      { value: 'recall_pending', label: 'recall_pending' },
      { value: 'agent_inst', label: 'agent_inst' },
      { value: 'tenant_inst', label: 'tenant_inst' },
    ],
  },
  {
    name: '租户管理',
    prefixes: [
      { value: 'tenant', label: 'tenant' },
      { value: 'tenant_code', label: 'tenant_code' },
      { value: 'tenant_stats', label: 'tenant_stats' },
      { value: 'tenant_sub_cnt', label: 'tenant_sub_cnt' },
    ],
  },
  {
    name: '权限与配额',
    prefixes: [
      { value: 'agent_quota', label: 'agent_quota' },
      { value: 'user_agents', label: 'user_agents' },
    ],
  },
  {
    name: '用量统计',
    prefixes: [
      { value: 'platform_usage', label: 'platform_usage' },
      { value: 'tenant_usage', label: 'tenant_usage' },
      { value: 'tenant_usage_sum', label: 'tenant_usage_sum' },
      { value: 'token_usage', label: 'token_usage' },
    ],
  },
  {
    name: '知识库',
    prefixes: [
      { value: 'docs_list', label: 'docs_list' },
      { value: 'docs_count', label: 'docs_count' },
    ],
  },
  {
    name: 'Prompt',
    prefixes: [
      { value: 'prompt_content', label: 'prompt_content' },
      { value: 'prompt_label', label: 'prompt_label' },
      { value: 'prompt_reg', label: 'prompt_reg' },
      { value: 'prompt_sections', label: 'prompt_sections' },
    ],
  },
  {
    name: '渠道限流',
    prefixes: [
      { value: 'ch_rate_limit', label: 'ch_rate_limit' },
    ],
  },
  {
    name: '上下文压缩',
    prefixes: [
      { value: 'comp_metrics', label: 'comp_metrics' },
    ],
  },
]

// ============== 表格列定义 ==============

const columns = [
  { key: 'key', label: '键名', minWidth: '300px' },
  { key: 'type', label: '类型', width: '90px', thAlign: 'center' as const, tdAlign: 'center' as const },
  { key: 'ttl', label: 'TTL', width: '110px', thAlign: 'center' as const, tdAlign: 'center' as const },
  { key: 'size', label: '大小', width: '90px', thAlign: 'center' as const, tdAlign: 'center' as const },
  { key: 'actions', label: '操作', width: '160px', thAlign: 'center' as const, tdAlign: 'center' as const },
]

// ============== 工具函数 ==============

function typeBadgeIntent(type: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  const map: Record<string, 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'> = {
    string: 'info',
    hash: 'primary',
    list: 'success',
    set: 'warning',
    zset: 'danger',
    none: 'neutral',
  }
  return map[type] || 'neutral'
}

function formatValue(value: any): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

// ============== 数据加载 ==============

async function loadOverview() {
  loadingOverview.value = true
  try {
    overview.value = await getRedisOverview()
  } catch (e: any) {
    toast.error(`加载概览失败: ${e.message}`)
  } finally {
    loadingOverview.value = false
  }
}

async function loadKeys(reset: boolean = true) {
  loadingKeys.value = true
  try {
    if (reset) {
      nextCursor.value = 0
      keyItems.value = []
    }
    const res = await listRedisKeys({
      prefix: selectedPrefix.value,
      search: searchKeyword.value || null,
      cursor: nextCursor.value,
      limit: 200,
    })
    if (reset) {
      keyItems.value = res.items
    } else {
      // 追加，按键名去重
      const existing = new Set(keyItems.value.map(k => k.key))
      const fresh = res.items.filter(k => !existing.has(k.key))
      keyItems.value = [...keyItems.value, ...fresh]
    }
    nextCursor.value = res.next_cursor
    hasMore.value = res.has_more

    // 更新过滤标签
    if (selectedPrefix.value === '__bare__') {
      currentFilter.value = '裸键'
    } else if (selectedPrefix.value) {
      currentFilter.value = `前缀=${selectedPrefix.value}`
    } else if (searchKeyword.value) {
      currentFilter.value = `搜索="${searchKeyword.value}"`
    } else {
      currentFilter.value = ''
    }
  } catch (e: any) {
    toast.error(`加载键列表失败: ${e.message}`)
  } finally {
    loadingKeys.value = false
  }
}

async function loadMore() {
  if (!hasMore.value || loadingKeys.value) return
  await loadKeys(false)
}

// ============== 事件处理 ==============

function selectPrefix(prefix: string | null) {
  selectedPrefix.value = prefix
  loadKeys(true)
}

function handleSearch() {
  loadKeys(true)
}

function handleClearSearch() {
  searchKeyword.value = ''
  loadKeys(true)
}

async function openKeyDetail(key: string) {
  showDetailModal.value = true
  detailData.value = null
  detailLoading.value = true
  try {
    detailData.value = await getRedisKeyDetail(key)
  } catch (e: any) {
    detailData.value = {
      success: false,
      key,
      message: `加载失败: ${e.message}`,
    }
  } finally {
    detailLoading.value = false
  }
}

async function handleDeleteKey(row: RedisKeyItem) {
  if (!confirm(`确定删除键\n${row.key}？\n\n此操作不可恢复！`)) return
  try {
    const res = await deleteRedisKey(row.key)
    if (res.success) {
      toast.success('删除成功')
      // 从列表移除
      keyItems.value = keyItems.value.filter(k => k.key !== row.key)
      // 刷新概览
      loadOverview()
    } else {
      toast.error(res.error || '删除失败')
    }
  } catch (e: any) {
    toast.error(`删除失败: ${e.message}`)
  }
}

async function handleDeleteFromDetail() {
  if (!detailData.value || !detailData.value.key) return
  const key = detailData.value.key
  if (!confirm(`确定删除键\n${key}？\n\n此操作不可恢复！`)) return
  try {
    const res = await deleteRedisKey(key)
    if (res.success) {
      toast.success('删除成功')
      showDetailModal.value = false
      keyItems.value = keyItems.value.filter(k => k.key !== key)
      loadOverview()
    } else {
      toast.error(res.error || '删除失败')
    }
  } catch (e: any) {
    toast.error(`删除失败: ${e.message}`)
  }
}

async function handleClearAllCache() {
  const total = overview.value?.total_keys ?? '所有'
  if (!confirm(`确定清空所有 Redis 缓存？\n\n将删除约 ${total} 个键（按 REDIS_KEY_PREFIX 隔离）。\n此操作不可恢复，可能导致在线用户的会话状态丢失！`)) return
  clearingAll.value = true
  try {
    const res = await clearAllCache()
    if (res.success) {
      toast.success(`已清空缓存，删除了 ${res.deleted ?? 0} 个 key`)
      // 清空后刷新概览与键列表
      await Promise.all([loadOverview(), loadKeys(true)])
    } else {
      toast.error(res.message || res.error || '清空缓存失败')
    }
  } catch (e: any) {
    toast.error(`清空缓存失败: ${e.message}`)
  } finally {
    clearingAll.value = false
  }
}

// ============== 初始化 ==============

onMounted(async () => {
  await loadOverview()
  await loadKeys(true)
})
</script>

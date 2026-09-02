<template>
  <!-- 提示词库页面（Phase 6.4.1）
       按 category 分 Tab：留用 / 黑名单 / 模版
       留用 Tab 行有「升级为模版」按钮（仅租户管理员可见） -->
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="提示词库"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <main class="flex-1 overflow-hidden p-4">
      <div class="page-container h-full">
        <div class="page-content">
          <!-- Tab 页签 -->
          <div class="mb-4 border-b border-default">
            <div class="flex gap-6">
              <button
                v-for="tab in tabs"
                :key="tab.key"
                :class="[
                  'pb-2 text-sm font-medium border-b-2 transition-colors',
                  currentTab === tab.key
                    ? 'text-primary-600 border-primary-600'
                    : 'text-muted border-transparent hover:text-default hover:border-hover',
                ]"
                @click="switchTab(tab.key)"
              >
                {{ tab.label }}
              </button>
            </div>
          </div>

          <div class="table-scroll-wrapper flex-1">
            <BaseTable :columns="columns" :data="items" row-key="id">
              <template #seq="{ index }">
                {{ seqNumber(index) }}
              </template>
              <template #business_prompt="{ row }">
                <button
                  class="text-primary-600 hover:underline truncate max-w-xs"
                  :title="row.business_prompt"
                  @click="handleViewDetail(row)"
                >
                  {{ row.business_prompt }}
                </button>
              </template>
              <template #craft_prompt="{ row }">
                <span class="text-muted truncate max-w-xs" :title="row.craft_prompt">
                  {{ row.craft_prompt }}
                </span>
              </template>
              <template #scene_tag="{ row }">
                <BaseBadge v-if="row.scene_tag" intent="info">{{ row.scene_tag }}</BaseBadge>
                <span v-else class="text-muted">-</span>
              </template>
              <template #created_at="{ row }">
                {{ formatTime(row.created_at) }}
              </template>
              <template #actions="{ row }">
                <BaseButton size="sm" intent="ghost" @click="handleViewDetail(row)">详情</BaseButton>
                <BaseButton
                  v-if="currentTab === 'kept' && isTenantAdmin"
                  size="sm"
                  @click="handlePromote(row)"
                >
                  升级为模版
                </BaseButton>
                <BaseButton size="sm" intent="danger-ghost" @click="handleDelete(row)">删除</BaseButton>
              </template>
              <template #empty>暂无提示词</template>
            </BaseTable>
          </div>

          <BasePagination
            v-if="total > pageSize"
            :total="total"
            v-model:current-page="currentPage"
            :page-size="pageSize"
          />
        </div>
      </div>
    </main>

    <!-- 提示词详情弹框 -->
    <BaseModal v-model="showDetailModal" title="提示词详情" size="lg">
      <div v-if="currentDetail" class="space-y-3 text-sm">
        <div>
          <span class="text-muted">类别：</span>
          <BaseBadge :intent="promptBadgeIntent(currentDetail.category)">
            {{ promptCategoryLabel(currentDetail.category) }}
          </BaseBadge>
        </div>
        <div>
          <div class="text-muted mb-1">业务层提示词</div>
          <div class="p-2 rounded bg-gray-50 text-default">{{ currentDetail.business_prompt || '-' }}</div>
        </div>
        <div>
          <div class="text-muted mb-1">工艺层提示词</div>
          <div class="p-2 rounded bg-gray-50 text-default whitespace-pre-wrap">{{ currentDetail.craft_prompt || '-' }}</div>
        </div>
        <div v-if="currentDetail.model_params">
          <div class="text-muted mb-1">模型参数</div>
          <div class="p-2 rounded bg-gray-50 text-default">
            <div v-for="(v, k) in currentDetail.model_params" :key="k" class="text-xs">
              <span class="text-muted">{{ k }}:</span> {{ v }}
            </div>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3 text-xs">
          <div>
            <span class="text-muted">行业标签：</span>
            <span class="text-default">{{ currentDetail.industry_tag || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">场景标签：</span>
            <span class="text-default">{{ currentDetail.scene_tag || '-' }}</span>
          </div>
          <div>
            <span class="text-muted">创建时间：</span>
            <span class="text-default">{{ formatTime(currentDetail.created_at) }}</span>
          </div>
          <div v-if="currentDetail.promoted_at">
            <span class="text-muted">升级时间：</span>
            <span class="text-default">{{ formatTime(currentDetail.promoted_at) }}</span>
          </div>
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { usePageContext } from '@/composables/usePageContext'
import {
  listPrompts,
  getPrompt,
  promotePrompt,
  deletePrompt,
  type PromptItem,
  type PromptCategory,
} from '@/api/videoAgent'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const tenantId = computed(() => route.params.tenant_id as string | undefined)
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : null)

// 租户管理员才能升级模版（platform_admin 也允许）
const isTenantAdmin = computed(() => {
  const role = tenantAdmin.value?.role
  return role === 'tenant_admin' || role === 'platform_admin'
})

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  router.push(tenantId.value ? `/t/${tenantId.value}/login` : '/')
}

// Tab 切换
const tabs = [
  { key: 'kept' as PromptCategory, label: '留用' },
  { key: 'blacklist' as PromptCategory, label: '黑名单' },
  { key: 'template' as PromptCategory, label: '模版' },
]
const currentTab = ref<PromptCategory>('kept')

// 列表数据
const items = ref<PromptItem[]>([])
const total = ref(0)

const { currentPage, pageSize, seqNumber, refresh } = usePageContext(async () => {
  const res = await listPrompts({
    page: currentPage.value,
    page_size: pageSize.value,
    category: currentTab.value,
  })
  if (res.success && res.data) {
    items.value = res.data.items
    total.value = res.data.total
  } else {
    toast.error(res.error || '加载提示词列表失败')
  }
})

function switchTab(tab: PromptCategory) {
  if (currentTab.value === tab) return
  currentTab.value = tab
  currentPage.value = 1
  refresh()
}

onMounted(() => {
  refresh()
})

const columns = computed(() => [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'business_prompt', label: '业务层提示词' },
  { key: 'craft_prompt', label: '工艺层提示词' },
  { key: 'scene_tag', label: '场景', width: '100px' },
  { key: 'created_at', label: '创建时间', width: '160px' },
  { key: 'actions', label: '操作', width: currentTab.value === 'kept' && isTenantAdmin.value ? '220px' : '160px' },
])

function formatTime(t?: string): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function promptCategoryLabel(c?: PromptCategory): string {
  if (!c) return '-'
  return { kept: '留用', blacklist: '黑名单', template: '模版' }[c] || c
}

function promptBadgeIntent(c?: PromptCategory): 'success' | 'danger' | 'info' {
  if (c === 'kept') return 'success'
  if (c === 'blacklist') return 'danger'
  return 'info'
}

// 详情弹框
const showDetailModal = ref(false)
const currentDetail = ref<PromptItem | null>(null)

async function handleViewDetail(row: any) {
  showDetailModal.value = true
  currentDetail.value = null
  try {
    const res = await getPrompt(row.id)
    if (res.success && res.data) {
      currentDetail.value = res.data
    } else {
      toast.error(res.error || '加载详情失败')
    }
  } catch (e: any) {
    toast.error(e.message || '加载详情失败')
  }
}

async function handlePromote(row: any) {
  if (!confirm(`确定将此提示词升级为模版？`)) return
  try {
    const res = await promotePrompt(row.id)
    if (res.success) {
      toast.success('升级成功')
      refresh()
    } else {
      toast.error(res.error || '升级失败')
    }
  } catch (e: any) {
    toast.error(e.message || '升级失败')
  }
}

async function handleDelete(row: any) {
  if (!confirm(`确定删除此提示词？`)) return
  try {
    const res = await deletePrompt(row.id)
    if (res.success) {
      toast.success('删除成功')
      refresh()
    } else {
      toast.error(res.error || '删除失败')
    }
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}
</script>

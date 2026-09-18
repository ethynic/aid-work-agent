<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      title="充值记录"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <div class="flex-1 min-h-0 flex flex-col p-6">
      <!-- 顶部工具栏 -->
      <div class="page-toolbar justify-end">
        <BaseButton size="sm" @click="loadData(currentPage)">刷新</BaseButton>
      </div>

      <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

      <template v-else>
        <!-- 表格 -->
        <div class="table-scroll-wrapper flex-1 min-h-0">
          <BaseTable :columns="columns" :data="items" row-key="id">
            <template #seq="{ index }">{{ seqNumber(index) }}</template>
            <template #amount_yuan="{ row }">
              <span class="text-default font-medium">¥ {{ formatAmount(row.amount_yuan) }}</span>
              <span v-if="row.is_gift" class="ml-1 px-2 py-0.5 rounded-full text-xs font-medium bg-warning-100 text-warning-700">赠送金额</span>
            </template>
            <template #credits="{ row }">
              <span class="text-primary-600 font-medium">{{ row.credits }}</span>
            </template>
            <template #created_at="{ row }">
              <span class="text-sm text-muted">{{ formatDateTime(row.created_at) }}</span>
            </template>
            <template #empty>暂无充值记录</template>
          </BaseTable>
        </div>

        <!-- 分页器 -->
        <BasePagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :show-size-changer="true"
          @change="onPageChange"
        />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { listTenantRecharges, type RechargeItem } from '@/api/billing'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

// 统一的用户信息
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 从 PortalLayout 注入侧边栏状态
const toggleSidebarFn = inject<() => void>('toggleSidebar')

function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  }
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${route.params.tenant_id}/login`)
}

// 列表数据
const items = ref<RechargeItem[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const loading = ref(true)

const columns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'amount_yuan', label: '充值金额', width: '120px' },
  { key: 'credits', label: '转化积分', width: '120px' },
  { key: 'created_at', label: '创建时间', width: '160px' },
]

function seqNumber(index: number): number {
  return (currentPage.value - 1) * pageSize.value + index + 1
}

function onPageChange(page: number, size: number) {
  currentPage.value = page
  pageSize.value = size
  loadData(page)
}

async function loadData(page: number = 1) {
  loading.value = true
  try {
    const res = await listTenantRecharges({
      page,
      page_size: pageSize.value,
    })
    if (res.success) {
      items.value = (res.items || []) as RechargeItem[]
      total.value = res.total || 0
    } else {
      toast.error(res.message || '加载充值记录失败')
      items.value = []
      total.value = 0
    }
  } catch (e: any) {
    toast.error(e.message || '加载充值记录失败')
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

// 工具函数
function formatAmount(v: number | string): string {
  const n = Number(v) || 0
  return n.toFixed(2)
}

function formatDateTime(datetime: string): string {
  if (!datetime) return ''
  const date = new Date(datetime)
  return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(() => loadData(1))
</script>

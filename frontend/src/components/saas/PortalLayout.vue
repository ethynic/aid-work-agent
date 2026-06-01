<template>
  <div class="h-screen flex bg-surface-hover">
    <!-- 管理后台菜单 - 仅 /portal 路由下显示 -->
    <aside v-if="!isTenantRoute" class="w-60 bg-gray-900 text-white flex flex-col flex-shrink-0">
      <!-- 企业信息 -->
      <div class="p-4 border-b border-gray-700">
        <h2 class="text-lg font-bold truncate">{{ tenant?.company_name || '管理后台' }}</h2>
        <p class="text-sm text-muted mt-1">{{ admin?.username || admin?.phone || '' }}</p>
      </div>

      <!-- 导航菜单 -->
      <nav class="flex-1 py-2 overflow-y-auto">
        <router-link
          v-for="item in currentMenuItems"
          :key="item.path"
          :to="item.path"
          class="flex items-center gap-3 px-4 py-3 text-sm transition-colors hover:bg-gray-800"
          :class="isActive(item.path) ? 'bg-gray-800 text-primary-400 border-r-2 border-primary-400' : 'text-gray-300'"
        >
          <span class="text-base">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>

      <!-- 底部操作 -->
      <div class="p-4 border-t border-gray-700">
        <button
          @click="handleLogout"
          class="w-full py-2 text-sm text-muted hover:text-white transition-colors"
        >
          退出登录
        </button>
      </div>
    </aside>

    <!-- 租户前台菜单 - 登录后才显示 -->
    <MenuSidebar
      v-else-if="isLoggedIn"
      :is-collapsed="sidebarCollapsed"
      :is-mobile="isMobile"
      :show-history="true"
      :show-new-session="true"
      :current-subagent-id="currentSubagentId"
      :available-subagents="availableSubagents"
      @collapse="sidebarCollapsed = true"
    />

    <!-- 主内容区 -->
    <main class="flex-1 overflow-y-auto">
      <!-- 平台管理员访问停用租户时的状态提示 -->
      <div
        v-if="showTenantStatusWarning"
        :class="[
          tenantStatusColorClass === 'red' ? 'bg-danger-50 border-danger-200' : 'bg-amber-50 border-amber-200',
          'border-b px-6 py-3'
        ]"
      >
        <div class="flex items-center gap-2" :class="tenantStatusColorClass === 'red' ? 'text-danger-700' : 'text-amber-800'">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" :class="tenantStatusColorClass === 'red' ? 'text-danger-600' : 'text-amber-600'" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.346 16.5c-.77.833.192 2.5 1.732 2.5z" />
          </svg>
          <div class="flex-1">
            <span class="font-medium">平台管理员代管模式：</span>
            当前租户 <span class="font-semibold">{{ tenant?.company_name }}</span> 的状态为
            <span :class="tenantStatusColorClass === 'red' ? 'font-bold text-danger-700' : 'font-bold text-amber-900'">{{ tenantStatusLabel }}</span>，
            您正在以平台管理员身份访问该租户。
            <span v-if="tenant" class="ml-2">
              (<router-link
                :to="'/portal/tenants'"
                class="underline hover:no-underline"
                :class="tenantStatusColorClass === 'red' ? 'text-danger-700 hover:text-danger-700' : 'text-amber-700 hover:text-amber-900'"
              >
                前往平台管理后台修改状态
              </router-link>)
            </span>
          </div>
        </div>
      </div>
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, computed, ref, provide, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useSubagentList } from '@/composables/useSubagentList'
import { useMobile } from '@/composables/useMobile'
import MenuSidebar from '@/components/MenuSidebar.vue'
import { TenantStatus } from '@/api/enums'

const router = useRouter()
const route = useRoute()
const { admin, tenant, isLoggedIn, init, logout } = useTenantAuth()
const toast = useToast()
const { isMobile } = useMobile()

// 侧边栏折叠状态（手机端默认收起）
const sidebarCollapsed = ref(typeof window !== 'undefined' && window.innerWidth < 768)

// 可用数字员工列表（带缓存，避免重复请求）
const { availableSubagents, loadAvailableSubagents } = useSubagentList()

// 当前选中的子智能体ID（从路由获取）
const currentSubagentId = computed(() => {
  // 匹配 /t/:tenantId/chat/:subagentId 或 /t/:tenantId/:subagentId/*（业务数据页面）
  const segments = route.path.split('/').filter(p => p)
  // segments: ['t', 'tenantId', ...]
  // - 格式1: ['t', 'tenantId', 'chat', 'subagentId'] → index 3
  // - 格式2: ['t', 'tenantId', 'subagentId', ...] → index 2
  if (segments.length >= 3) {
    if (segments[2] === 'chat' && segments.length >= 4) {
      return segments[3]
    }
    // 检查第三段是否是已知的业务页面路由前缀（trade-specialist等），它就是 subagentId
    return segments[2]
  }
  return undefined
})

// 登录/登出或进入登录页时，自动收起侧边栏（避免手机端重新登录后菜单仍展开）
watch(() => route.path, (path) => {
  if (path.endsWith('/login') || path.endsWith('/reset-password')) {
    // 只在手机端强制收起侧边栏，PC 端保持展开
    if (isMobile.value) {
      sidebarCollapsed.value = true
    }
  }
})
watch(isLoggedIn, (loggedIn, oldLoggedIn) => {
  if (!loggedIn) {
    sidebarCollapsed.value = true
  } else if (oldLoggedIn === false && loggedIn === true) {
    // PC端登录成功后默认展开，手机端保持收起
    if (typeof window !== 'undefined' && window.innerWidth >= 768) {
      sidebarCollapsed.value = false
    }
  }
})

// 提供侧边栏状态给子组件
provide('sidebarCollapsed', sidebarCollapsed)
provide('toggleSidebar', () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
})
provide('collapseSidebar', () => {
  sidebarCollapsed.value = true
})

// 判断是否在 /t/:tenant_id 路由下（租户前台）
const tenantId = computed(() => route.params.tenant_id as string)
const isTenantRoute = computed(() => !!tenantId.value)

// 平台管理员访问停用租户时的状态提示
const showTenantStatusWarning = computed(() => {
  return (
    isLoggedIn.value &&
    isTenantRoute.value &&
    admin.value?.role === 'platform_admin' &&
    tenant.value &&
    tenant.value.status !== TenantStatus.ACTIVE
  )
})

// 租户状态显示标签
const tenantStatusLabel = computed(() => {
  if (!tenant.value) return ''
  switch (tenant.value.status) {
    case TenantStatus.SUSPENDED:
      return '停用'
    case TenantStatus.DEACTIVATED:
      return '已删除'
    default:
      return '未知状态'
  }
})

// 租户状态颜色类
const tenantStatusColorClass = computed(() => {
  if (!tenant.value) return 'amber'
  switch (tenant.value.status) {
    case TenantStatus.SUSPENDED:
      return 'amber'
    case TenantStatus.DEACTIVATED:
      return 'red'
    default:
      return 'amber'
  }
})

// /portal 下的菜单（仅平台管理员）
const portalMenuItems = [
  { path: '/portal', label: '仪表盘', icon: '📊' },
  { path: '/portal/tenants', label: '租户管理', icon: '🏢' },
  { path: '/portal/subagents', label: '数字员工管理', icon: '🤖' },
  { path: '/portal/token-usage', label: '平台Token消耗', icon: '📊' },
  { path: '/portal/monitoring', label: '追踪查看', icon: '🔍' },
  { path: '/portal/error-logs', label: '错误日志', icon: '⚠️' },
  { path: '/portal/reply-styles', label: '回复风格', icon: '💬' },
]

// 根据路由选择菜单
const currentMenuItems = computed(() => {
  return portalMenuItems
})

function isActive(path: string) {
  // 处理 /portal 路由
  if (path === '/portal') return route.path === '/portal'
  return route.path.startsWith(path)
}

async function handleLogout() {
  await logout()
  // 根据当前路由决定跳转
  if (isTenantRoute.value) {
    router.push(`/t/${tenantId.value}/login`)
  } else {
    router.push('/portal/login')
  }
}

onMounted(async () => {
  // 登录页和重置密码页不需要初始化 auth 和加载数据
  const routeName = route.name as string
  if (routeName && (routeName.includes('login') || routeName.includes('reset-password'))) {
    return
  }
  await init()
  if (!isLoggedIn.value) {
    // 根据当前路由跳转到对应登录页
    if (isTenantRoute.value) {
      router.push(`/t/${tenantId.value}/login`)
    } else {
      router.push('/portal/login')
    }
    return
  }
  // 登录成功后加载可用数字员工列表
  await loadAvailableSubagents(isTenantRoute.value)
  // /portal 路由下，只允许 platform_admin
  if (!isTenantRoute.value && admin.value?.role !== 'platform_admin') {
    toast.warning('只有平台管理员才能访问管理后台')
    await logout()
    router.push('/portal/login')
  }
  // 租户管理员访问其他租户应被拒绝（在后端处理）
})
</script>

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
          <span class="w-5 h-5 flex items-center justify-center flex-shrink-0">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path :d="item.icon" />
            </svg>
          </span>
          <span>{{ item.label }}</span>
        </router-link>
        <!-- 清空缓存 -->
        <button
          @click="handleClearCache"
          class="w-full flex items-center gap-3 px-4 py-3 text-sm text-gray-300 hover:bg-gray-800 hover:text-danger-400 transition-colors"
        >
          <span class="w-5 h-5 flex items-center justify-center flex-shrink-0">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </span>
          <span>清空缓存</span>
        </button>
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
    <main class="flex-1 flex flex-col overflow-hidden">
      <!-- 平台管理员访问停用租户时的状态提示 -->
      <div
        v-if="showTenantStatusWarning"
        :class="[
          tenantStatusColorClass === 'red' ? 'bg-danger-50 border-danger-200' : 'bg-amber-50 border-amber-200',
          'border-b px-6 py-3 flex-shrink-0'
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
      <router-view class="flex-1 min-h-0" />
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
const { admin, tenant, isLoggedIn, init, logout, getAuthHeader } = useTenantAuth()
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
// icon 字段为统一的 SVG path 数据，使用 stroke="currentColor" 的细线描边风格
const portalMenuItems = [
  // 仪表盘：九宫格统计图（与参考图风格一致）
  { path: '/portal', label: '仪表盘', icon: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z' },
  // 租户管理：办公建筑
  { path: '/portal/tenants', label: '租户管理', icon: 'M3 21h18M5 21V7l7-4 7 4v14M9 9h.01M9 12h.01M9 15h.01M9 18h.01M15 9h.01M15 12h.01M15 15h.01M15 18h.01' },
  // 内置数字员工：机器人/Agent
  { path: '/portal/subagents', label: '内置数字员工', icon: 'M12 4v3M5 8h14a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2v-8a2 2 0 012-2zM9 13h.01M15 13h.01M9 17h6' },
  // 自定义数字员工：工具/扳手
  { path: '/portal/agent-definitions', label: '自定义数字员工', icon: 'M14.7 6.3a4 4 0 00-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 005.4-5.4l-2.4 2.4-2.6-2.6 2.4-2.4z' },
  // 平台Token消耗：柱状图
  { path: '/portal/token-usage', label: '平台Token消耗', icon: 'M3 21h18M6 17V9M11 17V5M16 17v-4M21 17v-7' },
  // RPA 绑定管理：链接/链条
  { path: '/portal/rpa-bindings', label: 'RPA 绑定管理', icon: 'M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71' },
  // 追踪查看：搜索/放大镜
  { path: '/portal/monitoring', label: '追踪查看', icon: 'M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z' },
  // 上下文压缩：压缩/方块缩小
  { path: '/portal/context-compression', label: '上下文压缩', icon: 'M4 9h6V3M20 15h-6v6M4 15l4-4M20 9l-4 4' },
  // 错误日志：三角警告
  { path: '/portal/error-logs', label: '错误日志', icon: 'M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01' },
  // 回复风格：对话气泡
  { path: '/portal/reply-styles', label: '回复风格', icon: 'M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z' },
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

async function handleClearCache() {
  try {
    const response = await fetch('/api/clear_cache', {
      method: 'GET',
      credentials: 'include',
      headers: { ...getAuthHeader() },
    })
    const data = await response.json()
    if (data.success) {
      toast.success(`已清空缓存，删除了 ${data.deleted} 个 key`)
    } else {
      toast.error(data.message || '清空缓存失败')
    }
  } catch (e: any) {
    toast.error(e.message || '清空缓存失败')
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

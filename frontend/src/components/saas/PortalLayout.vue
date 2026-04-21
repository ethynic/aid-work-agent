<template>
  <div class="min-h-screen flex bg-slate-100">
    <!-- 侧边栏 -->
    <aside class="w-60 bg-slate-900 text-white flex flex-col flex-shrink-0">
      <!-- 企业信息 -->
      <div class="p-4 border-b border-slate-700">
        <h2 class="text-lg font-bold truncate">{{ tenant?.company_name || (isTenantRoute ? '租户管理后台' : '爱定义管理后台') }}</h2>
        <p class="text-sm text-slate-400 mt-1">{{ admin?.username || admin?.phone || '' }}</p>
      </div>

      <!-- 导航菜单 -->
      <nav class="flex-1 py-2 overflow-y-auto">
        <router-link
          v-for="item in currentMenuItems"
          :key="item.path"
          :to="item.path"
          class="flex items-center gap-3 px-4 py-3 text-sm transition-colors hover:bg-slate-800"
          :class="isActive(item.path) ? 'bg-slate-800 text-cyan-400 border-r-2 border-cyan-400' : 'text-slate-300'"
        >
          <span class="text-base">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>

      <!-- 底部操作 -->
      <div class="p-4 border-t border-slate-700">
        <button
          @click="handleLogout"
          class="w-full py-2 text-sm text-slate-400 hover:text-white transition-colors"
        >
          退出登录
        </button>
      </div>
    </aside>

    <!-- 主内容区 -->
    <main class="flex-1 overflow-y-auto">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()
const { admin, tenant, isLoggedIn, init, logout } = useTenantAuth()
const toast = useToast()

// 判断是否在 /t/:tenant_id 路由下
const tenantId = computed(() => route.params.tenant_id as string)
const isTenantRoute = computed(() => !!tenantId.value)

// /portal 下的菜单（仅平台管理员）
const portalMenuItems = [
  { path: '/portal', label: '仪表盘', icon: '📊' },
  { path: '/portal/tenants', label: '租户管理', icon: '🏢' },
  { path: '/portal/subagents', label: '数字员工管理', icon: '🤖' },
]

// /t/:tenant_id 下的管理员菜单（platform_admin + tenant_admin）
const tenantAdminMenuItems = computed(() => [
  { path: `/t/${tenantId.value}`, label: '仪表盘', icon: '📊' },
  { path: `/t/${tenantId.value}/users`, label: '用户管理', icon: '👥' },
  { path: `/t/${tenantId.value}/knowledge`, label: '企业知识库', icon: '📚' },
  { path: `/t/${tenantId.value}/channels`, label: '渠道配置', icon: '📡' },
  { path: `/t/${tenantId.value}/settings`, label: '企业设置', icon: '⚙️' },
  { path: `/t/${tenantId.value}/chat`, label: '聊天', icon: '💬' },
])

// /t/:tenant_id 下的普通用户菜单（仅聊天）
const tenantUserMenuItems = computed(() => [
  { path: `/t/${tenantId.value}/chat`, label: '聊天', icon: '💬' },
])

// 根据路由和角色选择菜单
const currentMenuItems = computed(() => {
  // /portal 路由下
  if (!isTenantRoute.value) {
    return portalMenuItems
  }
  // /t/:tenant_id 路由下，根据角色判断
  if (admin.value?.role === 'platform_admin' || admin.value?.role === 'tenant_admin') {
    return tenantAdminMenuItems.value
  }
  // 普通用户
  return tenantUserMenuItems.value
})

function isActive(path: string) {
  // 处理 /portal 路由
  if (path === '/portal') return route.path === '/portal'
  // 处理 /t/:tenant_id 路由 - 需要替换实际 tenant_id
  if (path.startsWith('/t/')) {
    const basePath = path.replace(`/${tenantId.value}`, '')
    return route.path === path || route.path.startsWith(path + '/')
  }
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
  // /portal 路由下，只允许 platform_admin
  if (!isTenantRoute.value && admin.value?.role !== 'platform_admin') {
    toast.warning('只有平台管理员才能访问管理后台')
    await logout()
    router.push('/portal/login')
  }
  // 租户管理员访问其他租户应被拒绝（在后端处理）
})
</script>

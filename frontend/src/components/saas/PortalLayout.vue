<template>
  <div class="min-h-screen flex bg-slate-100">
    <!-- 管理后台菜单 - 仅 /portal 路由下显示，/t/:tenant_id 租户前台不显示 -->
    <aside id="portal-menu" v-if="!isTenantRoute" class="w-60 bg-slate-900 text-white flex flex-col flex-shrink-0">
      <!-- 企业信息 -->
      <div class="p-4 border-b border-slate-700">
        <h2 class="text-lg font-bold truncate">{{ tenant?.company_name || '管理后台' }}</h2>
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

// 判断是否在 /t/:tenant_id 路由下（租户前台）
const tenantId = computed(() => route.params.tenant_id as string)
const isTenantRoute = computed(() => !!tenantId.value)

// /portal 下的菜单（仅平台管理员）
const portalMenuItems = [
  { path: '/portal', label: '仪表盘', icon: '📊' },
  { path: '/portal/tenants', label: '租户管理', icon: '🏢' },
  { path: '/portal/subagents', label: '数字员工管理', icon: '🤖' },
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

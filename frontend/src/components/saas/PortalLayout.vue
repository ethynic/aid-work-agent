<template>
  <div class="min-h-screen flex bg-slate-100">
    <!-- 侧边栏 -->
    <aside class="w-60 bg-slate-900 text-white flex flex-col flex-shrink-0">
      <!-- 企业信息 -->
      <div class="p-4 border-b border-slate-700">
        <h2 class="text-lg font-bold truncate">{{ tenant?.company_name || '企业管理平台' }}</h2>
        <p class="text-sm text-slate-400 mt-1">{{ admin?.name || admin?.phone || '' }}</p>
      </div>

      <!-- 导航菜单 -->
      <nav class="flex-1 py-2 overflow-y-auto">
        <router-link
          v-for="item in menuItems"
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
import { onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()
const { admin, tenant, isLoggedIn, init, logout } = useTenantAuth()

const menuItems = [
  { path: '/portal', label: '仪表盘', icon: '📊' },
  { path: '/portal/instances', label: '智能体管理', icon: '🤖' },
  { path: '/portal/channels', label: '渠道配置', icon: '📡' },
  { path: '/portal/users', label: '用户管理', icon: '👥' },
  { path: '/portal/skills', label: 'Skill 管理', icon: '🧩' },
  { path: '/portal/reports', label: '用量报告', icon: '📈' },
  { path: '/portal/billing', label: '计费管理', icon: '💳' },
  { path: '/portal/settings', label: '企业设置', icon: '⚙️' },
]

function isActive(path: string) {
  if (path === '/portal') return route.path === '/portal'
  return route.path.startsWith(path)
}

async function handleLogout() {
  await logout()
  router.push('/portal/login')
}

onMounted(async () => {
  await init()
  if (!isLoggedIn.value) {
    router.push('/portal/login')
  }
})
</script>

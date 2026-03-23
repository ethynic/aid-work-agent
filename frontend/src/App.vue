<template>
  <el-config-provider :locale="locale">
    <!-- 登录页面布局 -->
    <div v-if="isReady && isLoginPage">
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </div>
    
    <!-- 主应用布局 -->
    <el-container class="app-container" v-else-if="isReady">
      <el-aside width="240px" class="app-sidebar" v-show="!isMobile && isAuthenticated">
        <div class="logo">
          <el-icon :size="32"><DataAnalysis /></el-icon>
          <span>企业智能体</span>
        </div>
        
        <el-menu
          :default-active="activeMenu"
          router
          class="app-menu"
          :collapse-transition="false"
        >
          <el-menu-item index="/dashboard">
            <el-icon><DataAnalysis /></el-icon>
            <span>仪表盘</span>
          </el-menu-item>
          
          <el-menu-item index="/chat">
            <el-icon><ChatDotRound /></el-icon>
            <span>智能对话</span>
          </el-menu-item>
          
          <el-sub-menu index="tools" :popper-append-to-body="false">
            <template #title>
              <el-icon><Tools /></el-icon>
              <span>工具管理</span>
            </template>
            <el-menu-item index="/tools/image">图像生成</el-menu-item>
            <el-menu-item index="/tools/video">视频生成</el-menu-item>
            <el-menu-item index="/tools/mcp">MCP工具</el-menu-item>
          </el-sub-menu>
          
          <el-sub-menu index="database" :popper-append-to-body="false">
            <template #title>
              <el-icon><Connection /></el-icon>
              <span>数据库</span>
            </template>
            <el-menu-item index="/database/connections">连接管理</el-menu-item>
            <el-menu-item index="/database/schema">结构分析</el-menu-item>
            <el-menu-item index="/database/query">SQL查询</el-menu-item>
          </el-sub-menu>
          
          <el-menu-item index="/models">
            <el-icon><Grid /></el-icon>
            <span>模型配置</span>
          </el-menu-item>
          
          <el-menu-item index="/tasks">
            <el-icon><List /></el-icon>
            <span>任务管理</span>
          </el-menu-item>
          
          <el-menu-item index="/ai-employees">
            <el-icon><UserFilled /></el-icon>
            <span>AI数字员工</span>
          </el-menu-item>
          
          <el-menu-item index="/im-apps">
            <el-icon><ChatLineSquare /></el-icon>
            <span>IM应用配置</span>
          </el-menu-item>

          <el-menu-item index="/knowledge">
            <el-icon><Document /></el-icon>
            <span>企业知识库</span>
          </el-menu-item>

          <el-menu-item index="/settings">
            <el-icon><Setting /></el-icon>
            <span>系统设置</span>
          </el-menu-item>
        </el-menu>
      </el-aside>
      
      <el-container>
        <el-header class="app-header">
          <div class="header-left" v-if="!isMobile && isAuthenticated">
            <el-breadcrumb separator="/" :collapse-transition="false">
              <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
              <el-breadcrumb-item>{{ currentPageTitle }}</el-breadcrumb-item>
            </el-breadcrumb>
          </div>
          
          <div class="header-right" v-if="isAuthenticated">
            <el-badge :value="3" class="notification-badge" v-if="!isMobile">
              <el-button :icon="Bell" circle />
            </el-badge>
            
            <el-dropdown v-if="!isMobile">
              <el-button circle>
                <el-icon><User /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item>个人中心</el-dropdown-item>
                  <el-dropdown-item @click="handleLogout">退出登录</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </el-header>
        
        <el-main class="app-main">
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <keep-alive :include="cachedViews">
                <component :is="Component" />
              </keep-alive>
            </transition>
          </router-view>
        </el-main>
      </el-container>
    </el-container>
  </el-config-provider>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell, User, UserFilled, DataAnalysis, ChatDotRound, Tools, Connection, Grid, List, Setting, ChatLineSquare, Document } from '@element-plus/icons-vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { ElMessage } from 'element-plus'
import { useGlobalStore } from './stores'

const locale = zhCn
const route = useRoute()
const router = useRouter()
const globalStore = useGlobalStore()
const isReady = ref(false)
const isMobile = ref(false)
const cachedViews = ref(['Dashboard', 'Chat'])

const activeMenu = computed(() => route.path)
const isAuthenticated = computed(() => globalStore.isAuthenticated)
const isLoginPage = computed(() => {
  const loginPaths = ['/login']
  return loginPaths.includes(route.path)
})

const pageTitles = {
  '/dashboard': '仪表盘',
  '/chat': '智能对话',
  '/tools/image': '图像生成',
  '/tools/video': '视频生成',
  '/tools/mcp': 'MCP工具',
  '/database/connections': '连接管理',
  '/database/schema': '结构分析',
  '/database/query': 'SQL查询',
  '/models': '模型配置',
  '/tasks': '任务管理',
  '/ai-employees': 'AI数字员工',
  '/im-apps': 'IM应用配置',
  '/knowledge': '企业知识库',
  '/settings': '系统设置'
}

const currentPageTitle = computed(() => pageTitles[route.path] || '')

const checkMobile = () => {
  isMobile.value = window.innerWidth < 768
}

// 防抖函数
const debounce = (func, wait) => {
  let timeout
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout)
      func(...args)
    }
    clearTimeout(timeout)
    timeout = setTimeout(later, wait)
  }
}

const debouncedCheckMobile = debounce(checkMobile, 200)

onMounted(() => {
  // 延迟标记应用为就绪，让首屏渲染更快
  setTimeout(() => {
    isReady.value = true
  }, 100)
  
  checkMobile()
  window.addEventListener('resize', debouncedCheckMobile)
})

const handleLogout = () => {
  // 调用全局状态管理的退出登录方法
  globalStore.logout()
  
  // 显示退出成功消息
  ElMessage.success('退出登录成功')
  
  // 跳转到登录页面
  router.push('/login')
}

onUnmounted(() => {
  window.removeEventListener('resize', debouncedCheckMobile)
})
</script>

<style lang="scss" scoped>
.app-container {
  height: 100vh;
  overflow: hidden;
}

.app-sidebar {
  background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
  color: #fff;
  transition: width 0.3s;
  
  .logo {
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    font-size: 18px;
    font-weight: bold;
    border-bottom: 1px solid rgba(255,255,255,0.1);
  }
  
  .app-menu {
    border-right: none;
    background: transparent;
    height: calc(100vh - 60px);
    overflow-y: auto;
    
    // 自定义滚动条样式
    &::-webkit-scrollbar {
      width: 4px;
    }
    
    &::-webkit-scrollbar-track {
      background: rgba(255,255,255,0.1);
    }
    
    &::-webkit-scrollbar-thumb {
      background: rgba(255,255,255,0.3);
      border-radius: 2px;
    }
    
    :deep(.el-menu-item),
    :deep(.el-sub-menu__title) {
      color: rgba(255,255,255,0.7);
      transition: all 0.2s;
      
      &:hover {
        background: rgba(255,255,255,0.1);
        color: #fff;
      }
    }
    
    :deep(.el-menu-item.is-active) {
      background: rgba(64, 158, 255, 0.2);
      color: #409eff;
    }
  }
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  padding: 0 20px;
  height: 60px;
  
  .header-right {
    display: flex;
    align-items: center;
    gap: 15px;
  }
}

.app-main {
  background: #f5f7fa;
  padding: 20px;
  overflow-y: auto;
  
  // 自定义滚动条样式
  &::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }
  
  &::-webkit-scrollbar-track {
    background: #f1f1f1;
    border-radius: 4px;
  }
  
  &::-webkit-scrollbar-thumb {
    background: #c1c1c1;
    border-radius: 4px;
  }
  
  &::-webkit-scrollbar-thumb:hover {
    background: #a8a8a8;
  }
}

// 页面过渡动画
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

// 响应式设计
@media (max-width: 768px) {
  .app-sidebar {
    position: fixed;
    left: 0;
    top: 0;
    height: 100vh;
    z-index: 1000;
    box-shadow: 2px 0 8px rgba(0,0,0,0.15);
  }
  
  .app-main {
    padding: 10px;
  }
}
</style>

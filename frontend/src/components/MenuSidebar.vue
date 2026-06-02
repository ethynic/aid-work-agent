<template>
  <!-- Mobile Backdrop -->
  <div
    v-if="isMobile && !isCollapsed"
    class="fixed inset-0 bg-black/50 z-40"
    @click="$emit('collapse')"
  ></div>

  <aside
    :class="[
      'bg-white border-r border-gray-200 flex flex-col',
      isMobile
        ? [
            'fixed inset-y-0 left-0 z-50 w-72 transition-transform duration-300',
            isCollapsed ? '-translate-x-full' : 'translate-x-0'
          ]
        : [
            'h-full transition-all duration-300',
            isCollapsed ? 'w-0 overflow-hidden' : 'w-72'
          ]
    ]"
  >
    <!-- System Header - 系统名称区域 -->
    <div class="flex-shrink-0 p-4 border-b border-gray-200 bg-gradient-to-r from-white to-gray-50">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center flex-shrink-0">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <h1 class="text-base font-semibold text-gray-800 truncate">
          {{ sidebarTitle }}
        </h1>
      </div>
    </div>

    <!-- Scrollable Content - 中间内容整体滚动 -->
    <div class="flex-1 overflow-y-auto min-h-0">
    <!-- Navigation Menu - 导航菜单区域 -->
    <div class="flex-shrink-0 p-2 space-y-1">
      <!-- New Session Button -->
      <button
        v-if="showNewSession"
        @click="handleNewSession"
        :disabled="isCreating"
        class="w-full flex items-center gap-3 px-3 py-2.5 bg-gradient-to-r from-primary-600 to-primary-700 hover:from-primary-500 hover:to-primary-600 disabled:from-primary-400 disabled:to-primary-500 text-white rounded-lg transition-all shadow-sm hover:shadow-md"
      >
        <svg v-if="!isCreating" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        <svg v-else class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span class="text-sm font-medium">新会话</span>
      </button>

      <!-- 租户模式菜单 -->
      <template v-if="isTenantMode">

        <!-- 管理菜单（可折叠，仅租户管理员可见） -->
        <div v-if="isTenantAdmin" class="hidden md:block">
          <!-- 管理菜单标题 -->
          <button
            @click="isAdminMenuExpanded = !isAdminMenuExpanded"
            class="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm text-gray-600 hover:bg-gray-50"
          >
            <div class="flex items-center gap-3">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <span>管理菜单</span>
            </div>
            <svg
              :class="['w-4 h-4 transition-transform', isAdminMenuExpanded ? 'rotate-180' : '']"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <!-- 管理子菜单 -->
          <div v-show="isAdminMenuExpanded" class="ml-4 mt-1 space-y-1">
            <button
              v-for="item in adminSubMenuItems"
              :key="item.path"
              @click="router.push(item.path)"
              :class="[
                'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm',
                route.path === item.path
                  ? 'bg-primary-50 text-primary-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-50'
              ]"
            >
              <span class="text-base">{{ item.icon }}</span>
              <span>{{ item.label }}</span>
            </button>
          </div>
        </div>
      </template>

      <!-- 演示模式菜单 -->
      <template v-else>
        <!-- Knowledge Base Menu Item -->
        <button
          @click="goToKnowledgeBase"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
            isKnowledgeBaseActive
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
          <span>企业知识库</span>
        </button>


        <!-- Digital Employee Management - only shown in portal management -->
        <button
          v-if="route.path.startsWith('/portal')"
          @click="goToDigitalEmployeeManager"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
            route.path === '/subagents' || route.path === '/portal/subagents'
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          <span>数字员工</span>
        </button>
      </template>
    </div>

    <!-- 业务数据分组 - 按数字员工分组展示 -->
    <!-- 手机端暂时隐藏：业务数据页面尚未适配手机端 -->
    <div v-if="groupedBusinessPages.length > 0 && !props.isMobile" class="flex-shrink-0 p-2">
      <div class="flex-shrink-0 px-2 py-2">
        <div class="flex items-center gap-3">
          <div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-300 to-transparent"></div>
        </div>
      </div>

      <!-- 各数字员工业务菜单分组 -->
      <div class="space-y-1">
        <div v-for="group in groupedBusinessPages" :key="group.subagent.agent_id">
          <button
            @click="toggleGroup(group.subagent.agent_id)"
            class="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm text-gray-600 hover:bg-gray-50"
          >
            <div class="flex items-center gap-3">
              <span class="text-base">📊</span>
              <span>{{ getSubagentDisplayName(group.subagent) }}</span>
            </div>
            <svg
              :class="['w-4 h-4 transition-transform', isGroupExpanded(group.subagent.agent_id) ? 'rotate-180' : '']"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <div v-show="isGroupExpanded(group.subagent.agent_id)" class="mt-1 ml-4 space-y-1">
            <div
              v-for="page in group.pages"
              :key="page.id"
              :class="[
                'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm cursor-pointer',
                route.path === page.route
                  ? 'bg-primary-50 text-primary-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-50'
              ]"
              @click="navigateToBusinessPage(page, group.subagent.agent_id)"
            >
              <span class="text-base">{{ page.icon }}</span>
              <span>{{ page.title }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Decorative Divider - 装饰性分隔线 -->
    <div v-if="showHistory" class="flex-shrink-0 px-4 py-2">
      <div class="flex items-center gap-3">
        <div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-300 to-transparent"></div>
        <svg class="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-300 to-transparent"></div>
      </div>
    </div>

    <!-- History Sessions Header - 历史会话标题（可折叠） -->
    <div v-if="showHistory" class="flex-shrink-0 p-2">
      <div class="flex items-center justify-between gap-3">
        <button
          @click="toggleHistoryExpanded"
          class="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm text-gray-600 hover:bg-gray-50"
        >
          <div class="flex items-center gap-3">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>历史会话</span>
          </div>
          <svg
            :class="['w-4 h-4 transition-transform', isHistoryExpanded ? 'rotate-180' : '']"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        <button
          @click="$emit('collapse')"
          class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors flex-shrink-0"
          title="收起侧边栏"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Session List - 会话列表 -->
    <div v-show="showHistory && isHistoryExpanded" class="px-2 pb-2">
      <div v-if="isLoading" class="p-4 text-center text-gray-500">
        <svg class="w-6 h-6 mx-auto animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <p class="mt-2 text-sm">加载中...</p>
      </div>

      <div v-else-if="filteredSessions.length === 0" class="p-4 text-center text-gray-500">
        <svg class="w-10 h-10 mx-auto mb-2 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <p class="text-xs">暂无会话记录</p>
      </div>

      <div v-else class="space-y-1">
        <div
          v-for="session in recentSessions"
          :key="session.session_id"
          :class="[
            'group relative p-2.5 rounded-lg cursor-pointer transition-colors',
            isHistorySessionActive && currentSessionId === session.session_id
              ? 'bg-primary-50 border border-primary-200'
              : 'hover:bg-gray-50 border border-transparent'
          ]"
          @click="handleSelectSession(session.session_id)"
        >
          <!-- Session Title -->
          <div class="flex items-start gap-2">
            <svg class="w-4 h-4 mt-0.5 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <div class="flex-1 min-w-0">
              <p class="text-sm text-gray-700 truncate">
                {{ session.title || '新会话' }}
              </p>
              <p class="text-xs text-gray-400 mt-0.5">
                {{ formatTime(session.updated_at) }}
              </p>
            </div>
          </div>

          <!-- Action Buttons: 桌面端 hover 显示，移动端始终显示 -->
          <div class="absolute right-1.5 top-1.5 flex items-center gap-0.5 md:hidden">
            <button
              @click.stop="handleRenameSession(session)"
              class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors"
              title="重命名"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
            </button>
            <button
              @click.stop="handleDeleteSession(session.session_id)"
              class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-danger-500 hover:bg-danger-50 rounded transition-colors"
              title="删除"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
          <div class="absolute right-1.5 top-1.5 hidden group-hover:flex md:flex items-center gap-0.5">
            <button
              @click.stop="handleRenameSession(session)"
              class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors"
              title="重命名"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
            </button>
            <button
              @click.stop="handleDeleteSession(session.session_id)"
              class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-danger-500 hover:bg-danger-50 rounded transition-colors"
              title="删除"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>

        <!-- 全部历史会话链接 -->
        <button
          v-if="filteredSessions.length > 0"
          @click="goToAllSessions"
          class="w-full flex items-center gap-2 px-3 py-2 text-sm text-primary-600 hover:bg-primary-50 rounded-lg transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
          <span>全部历史会话</span>
        </button>
      </div>
    </div>

    </div>
    <!-- 底部固定区域：用户菜单 -->
    <div class="mt-auto relative">
      <div class="flex-shrink-0 p-3 border-t border-gray-200">
        <button
          @click="showUserMenu = !showUserMenu"
          class="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg transition-colors"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
          <span class="truncate">{{ currentUsername }}</span>
          <svg
            class="w-4 h-4 ml-auto transition-transform"
            :class="{ 'rotate-180': showUserMenu }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>

      <!-- User Dropdown Menu -->
      <div
        v-if="showUserMenu"
        class="absolute left-3 right-3 bottom-full mb-2 bg-white border border-gray-200 rounded-lg shadow-lg py-1 z-50 max-h-80 overflow-y-auto"
      >
        <!-- 颜色主题 -->
        <button
          @click="showThemeSubmenu = !showThemeSubmenu"
          class="w-full flex items-center gap-3 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
          </svg>
          <span class="flex-1">颜色主题</span>
          <svg
            class="w-4 h-4 transition-transform"
            :class="{ 'rotate-90': showThemeSubmenu }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </button>

        <!-- 主题子菜单 -->
        <div v-show="showThemeSubmenu" class="ml-4 space-y-0">
          <button
            v-for="theme in availableThemes"
            :key="theme.name"
            @click="handleSelectTheme(theme.name)"
            :class="[
              'w-full flex items-center gap-3 px-3 py-2 text-left text-sm transition-colors rounded',
              currentTheme === theme.name
                ? 'bg-primary-50 text-primary-700'
                : 'text-gray-700 hover:bg-gray-50'
            ]"
          >
            <div
              class="w-4 h-4 rounded-full border border-gray-200 flex-shrink-0"
              :style="{ backgroundColor: getThemePreviewColor(theme.name) }"
            />
            <span class="flex-1">{{ theme.label }}</span>
            <svg
              v-if="currentTheme === theme.name"
              class="w-4 h-4 text-primary-600 flex-shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
            </svg>
          </button>
        </div>

        <div class="my-1 border-t border-gray-100"></div>

        <!-- 修改密码 -->
        <button
          v-if="isTenantMode && tenantIsLoggedIn"
          @click="showUserMenu = false; handleModifyPassword()"
          class="w-full flex items-center gap-3 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
          </svg>
          <span>修改密码</span>
        </button>

        <!-- 退出登录 -->
        <button
          @click="handleLogout"
          class="w-full flex items-center gap-3 px-3 py-2 text-left text-sm text-gray-700 hover:bg-danger-50 hover:text-danger-600 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
          </svg>
          <span>退出登录</span>
        </button>
      </div>

      <!-- Click outside to close -->
      <div
        v-if="showUserMenu"
        class="fixed inset-0 z-40"
        @click="showUserMenu = false"
      />
    </div>

    <!-- Rename Modal -->
    <div
      v-if="showRenameModal"
      class="absolute inset-0 bg-black/30 flex items-center justify-center z-10"
      @click.self="showRenameModal = false"
    >
      <div class="bg-white rounded-lg p-4 w-64 border border-gray-200 shadow-xl">
        <h3 class="text-sm font-medium text-gray-800 mb-3">重命名会话</h3>
        <input
          v-model="renameInput"
          @keyup.enter="confirmRename"
          type="text"
          class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-lg text-gray-800 text-sm focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
          placeholder="输入会话标题"
        />
        <div class="flex justify-end gap-2 mt-3">
          <button
            @click="showRenameModal = false"
            class="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmRename"
            class="px-3 py-1.5 text-sm bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useSession } from '@/composables/useSession'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAgent } from '@/composables/useAgent'
import { useTheme, type ThemeName } from '@/composables/useTheme'

import type { SubagentListItem, BusinessPage } from '@/api/subagent'

interface Props {
  isCollapsed: boolean
  /** 是否移动端抽屉模式，默认 false */
  isMobile?: boolean
  /** 是否显示历史会话区域，默认 true */
  showHistory?: boolean
  /** 是否显示新会话按钮，默认 true */
  showNewSession?: boolean
  /** 当前选中的子智能体 ID */
  currentSubagentId?: string
  /** 所有可用的子智能体列表 */
  availableSubagents: SubagentListItem[]
}

const props = withDefaults(defineProps<Props>(), {
  isMobile: false,
  showHistory: true,
  showNewSession: true
})
const emit = defineEmits<{
  collapse: []
}>()

const router = useRouter()
const route = useRoute()
const { user: demoUser, isLoggedIn: demoIsLoggedIn, logout: demoLogout } = useDemoAuth()
const { admin: tenantAdmin, tenant, logout: tenantLogout, isLoggedIn: tenantIsLoggedIn } = useTenantAuth()
const { currentTheme, setTheme, getAvailableThemes } = useTheme()

const showUserMenu = ref(false)
const showThemeSubmenu = ref(false)
const availableThemes = getAvailableThemes()

const currentUsername = computed(() => {
  if (isTenantMode.value && tenantAdmin.value) {
    return tenantAdmin.value.username
  }
  if (demoUser.value) {
    return demoUser.value.username
  }
  return '用户'
})

function handleSelectTheme(theme: ThemeName) {
  setTheme(theme)
}

function getThemePreviewColor(themeName: ThemeName): string {
  switch (themeName) {
    case 'blue': return '#003A8C'
    case 'gray': return '#2F3641'
    case 'pine': return '#164E42'
    case 'burgundy': return '#5C1A21'
    case 'bamboo': return '#25B26B'
    case 'iris': return '#8551F9'
    case 'sunrise': return '#FF7D00'
    case 'peony': return '#D91A80'
    default: return '#003A8C'
  }
}

function handleLogout() {
  showUserMenu.value = false
  if (isTenantMode.value) {
    handleTenantLogout()
  } else {
    handleDemoLogout()
  }
}

async function handleDemoLogout() {
  await demoLogout()
  router.push('/')
}
const {
  sessions,
  currentSessionId,
  isLoading,
  loadSessions,
  removeSession,
  renameSession,
  selectSession
} = useSession()
const { isProcessing, abortStreaming } = useAgent()

const isCreating = ref(false)
const showRenameModal = ref(false)
const renameInput = ref('')
const renamingSessionId = ref<string | null>(null)
const isAdminMenuExpanded = ref(true)
// 历史会话折叠状态（持久化到 localStorage）
const historyStorageKey = 'aid_work_agent:history_expanded'
const isHistoryExpanded = ref(
  localStorage.getItem(historyStorageKey) !== 'false'
)

const toggleHistoryExpanded = () => {
  isHistoryExpanded.value = !isHistoryExpanded.value
  localStorage.setItem(historyStorageKey, String(isHistoryExpanded.value))
}



// 业务菜单分组展开状态（按数字员工 agent_id）
const expandedGroups = ref<Set<string>>(new Set())

function isGroupExpanded(agentId: string): boolean {
  return expandedGroups.value.has(agentId)
}

function toggleGroup(agentId: string) {
  const newSet = new Set(expandedGroups.value)
  if (newSet.has(agentId)) {
    newSet.delete(agentId)
  } else {
    newSet.add(agentId)
  }
  expandedGroups.value = newSet
}

function getSubagentDisplayName(s: SubagentListItem): string {
  return s.display_name || s.name || ''
}

// 判断是否为租户模式（路由以 /t/ 开头）
const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 租户 ID
const tenantId = computed(() => {
  const match = route.path.match(/^\/t\/([^/]+)/)
  return match ? match[1] : null
})

// 是否为租户管理员
const isTenantAdmin = computed(() => {
  return tenantAdmin.value?.role === 'tenant_admin' || tenantAdmin.value?.role === 'platform_admin'
})

// 侧边栏标题
const sidebarTitle = computed(() => {
  if (isTenantMode.value && tenant.value) {
    // 租户模式：只显示租户名称
    return tenant.value.company_name
  }
  // 演示模式：显示默认名称
  return '爱定义工作助理'
})

// 管理子菜单项（租户管理员可见）
const adminSubMenuItems = computed(() => {
  if (!tenantId.value) return []
  const base = `/t/${tenantId.value}`
  return [
    { path: `${base}/users`, label: '用户管理', icon: '👥' },
    { path: `${base}/channels`, label: '渠道配置', icon: '📡' },
    { path: `${base}/knowledge`, label: '企业知识库', icon: '📚' },
    { path: `${base}/settings`, label: '企业设置', icon: '⚙️' },
    { path: `${base}/token-usage`, label: 'Token用量', icon: '📊' },
    { path: `${base}/reply-styles`, label: '回复风格', icon: '💬' },
    { path: `${base}/external-customers`, label: '外部接待客户', icon: '👤' },
  ]
})

// 当前子智能体（从路由参数获取）
// 支持多种路由格式：
// - /chat/:subagent → 普通模式
// - /t/:tenantId/chat/:subagent → 租户模式聊天页
// - /t/:tenantId/:subagent → 租户模式子智能体业务页
const currentSubagent = computed<string | null>(() => {
  // 优先从路由参数获取
  if (route.params.subagent) {
    return route.params.subagent as string
  }
  // 从路径分段提取：/t/{tenantId}/{subagent} 或 /t/{tenantId}/chat/{subagent}
  const segments = route.path.split('/').filter(p => p)
  if (segments.length >= 2 && segments[0] === 't') {
    // segments = ['t', 'tenantId', 'subagentId']
    if (segments.length >= 3 && segments[2] !== 'chat') {
      return segments[2]
    }
    // segments = ['t', 'tenantId', 'chat', 'subagentId']
    if (segments.length >= 4 && segments[2] === 'chat') {
      return segments[3]
    }
  }
  // 普通路径：/chat/:subagent
  if (segments.length >= 2 && segments[0] === 'chat') {
    return segments[1]
  }
  return null
})

// 过滤后可用的数字员工列表（根据权限过滤）
// 注意：后端已根据权限过滤，这里直接返回即可
const filteredAvailableSubagents = computed(() => {
  return props.availableSubagents || []
})

// 永远显示该用户的所有历史会话，不按数字员工过滤
const filteredSessions = computed(() => {
  return sessions.value
})

// 手机端显示6个避免滚动条，桌面端显示10个
const recentSessions = computed(() => {
  const limit = props.isMobile ? 6 : 10
  return filteredSessions.value.slice(0, limit)
})

// 判断当前是否在知识库页面
const isKnowledgeBaseActive = computed(() => {
  return route.path === '/knowledge-base'
})

// 判断历史会话是否应该高亮（仅在非知识库页面时）
const isHistorySessionActive = computed(() => {
  return !isKnowledgeBaseActive.value
})

// 按数字员工分组的业务页面
const groupedBusinessPages = computed(() => {
  return filteredAvailableSubagents.value
    .filter((s: SubagentListItem) => s.business_pages && s.business_pages.length > 0)
    .map((s: SubagentListItem) => ({
      subagent: s,
      pages: s.business_pages!
    }))
})

// 根据当前路由决定哪个分组应该展开
const expandedSubagentId = computed<string | null>(() => {
  const subagentId = props.currentSubagentId || currentSubagent.value
  if (!subagentId) return null
  const matched = filteredAvailableSubagents.value.find(
    (s: SubagentListItem) => s.agent_id === subagentId || s.subagent_type === subagentId
  )
  return matched?.agent_id || null
})

// 判断当前是否在对话界面（路由包含 /chat/）
const isChatPage = computed(() => {
  return route.path.includes('/chat/')
})

// 监听当前数字员工变化，重置展开状态
// 只在对话界面时自动展开/收缩业务数据菜单；非对话界面不做自动展开/收缩动作
watch(() => currentSubagent.value, () => {
  // 非对话界面，保留用户手动展开/收缩状态，不做自动调整
  if (!isChatPage.value) {
    return
  }
  const newSet = new Set<string>()
  const expandedId = expandedSubagentId.value
  if (expandedId) {
    newSet.add(expandedId)
  }
  expandedGroups.value = newSet
}, { immediate: true })

// 初始化时检查 URL 的 expand_menu 参数（新开业务数据页面时展开对应分组）
onMounted(() => {
  const expandMenu = route.query.expand_menu as string | undefined
  if (expandMenu) {
    const newSet = new Set(expandedGroups.value)
    newSet.add(expandMenu)
    expandedGroups.value = newSet
  }
})

// 跳转到业务数据页面
// 非对话界面：在本页打开；对话界面：新开页面并附带 expand_menu 参数以展开对应菜单
function navigateToBusinessPage(page: BusinessPage, agentId: string) {
  const path = isTenantMode.value && tenantId.value
    ? `/t/${tenantId.value}${page.route}`
    : page.route

  if (isChatPage.value) {
    // 对话界面：新开页面，带上 expand_menu 参数让新页面自动展开对应分组
    const separator = path.includes('?') ? '&' : '?'
    window.open(`${path}${separator}expand_menu=${encodeURIComponent(agentId)}`, '_blank')
  } else {
    // 非对话界面：在本页打开
    router.push(path)
  }
}

// 跳转到知识库（演示模式）
function goToKnowledgeBase() {
  router.push('/knowledge-base')
}


// 跳转到数字员工管理
function goToDigitalEmployeeManager() {
  router.push('/portal/subagents')
}

// 跳转到全部历史会话
function goToAllSessions() {
  const targetPath = isTenantMode.value && tenantId.value
    ? `/t/${tenantId.value}/all-sessions`
    : '/all-sessions'
  router.push(targetPath)
  // 手机端点击后自动收缩左侧菜单
  if (props.isMobile) {
    emit('collapse')
  }
}

// 监听登录状态，登录后加载会话
// 租户模式监听 tenantIsLoggedIn，演示模式监听 demoIsLoggedIn
import { watchEffect } from 'vue'
watchEffect(async () => {
  const isTenantMode = route.path.startsWith('/t/')
  const effectiveLoggedIn = isTenantMode ? tenantIsLoggedIn.value : demoIsLoggedIn.value
  if (effectiveLoggedIn) {
    await loadSessions()
  } else {
    sessions.value = []
  }
})

// 格式化时间
function formatTime(isoString: string): string {
  // 直接解析 ISO 字符串，JavaScript 会正确处理本地时间
  const date = new Date(isoString)
  const now = new Date()
  const diff = now.getTime() - date.getTime()

  // 1分钟内
  if (diff < 60 * 1000) {
    return '刚刚'
  }
  // 1小时内
  if (diff < 60 * 60 * 1000) {
    const minutes = Math.floor(diff / (60 * 1000))
    return `${minutes}分钟前`
  }
  // 24小时内
  if (diff < 24 * 60 * 60 * 1000) {
    const hours = Math.floor(diff / (60 * 60 * 1000))
    return `${hours}小时前`
  }
  // 超过24小时显示日期（使用本地时区即北京时间）
  const month = date.getMonth() + 1
  const day = date.getDate()
  return `${month}月${day}日`
}

// 新建会话
async function handleNewSession() {
  // 根据模式选择正确的登录状态检查
  // 租户模式使用 tenantIsLoggedIn，演示模式使用 demoIsLoggedIn
  const effectiveIsLoggedIn = isTenantMode.value ? tenantIsLoggedIn.value : demoIsLoggedIn.value
  if (!effectiveIsLoggedIn) {
    return
  }

  if (isCreating.value) {
    return
  }

  // 如果当前正在流式响应，需要用户确认是否终止
  if (isProcessing.value) {
    if (!confirm('当前会话还未结束，您希望终止当前会话，进入新会话吗？')) {
      return
    }
    // 用户确认，终止当前流式响应
    await abortStreaming()
  }

  // 优化：点击新会话立即响应，不等待后端 API
  // 直接清空当前会话，导航到空界面，用户输入第一条消息时才真正创建会话
  isCreating.value = true
  try {
    selectSession(null)
    // 导航到对应路由（租户模式使用 /t/:tenant_id/chat）
    // 租户模式下，如果有选中的 subagent，尝试匹配 instance_id 传递到 query
    let targetPath = '/'
    const queryParams: Record<string, string> = {}

    // 检查 currentSubagent 是否为有效的子智能体（在 availableSubagents 中存在）
    let effectiveSubagent = currentSubagent.value
    if (effectiveSubagent && props.availableSubagents.length > 0) {
      const matched = props.availableSubagents.find(
        (a: any) => a.agent_id === effectiveSubagent || a.subagent_type === effectiveSubagent
      )
      if (!matched) {
        // 不是有效的子智能体（可能是 all-sessions、instances 等其他路由参数）
        effectiveSubagent = null
      }
    }

    if (effectiveSubagent) {
      if (isTenantMode.value) {
        targetPath = `/t/${tenantId.value}/chat/${effectiveSubagent}`
        // 尝试匹配 instance_id
        const matchedInstance = props.availableSubagents.find(
          (a: any) => a.agent_id === effectiveSubagent || a.subagent_type === effectiveSubagent
        )
        if (matchedInstance?.instance_id) {
          queryParams.instance_id = matchedInstance.instance_id
        }
      } else {
        targetPath = `/chat/${effectiveSubagent}`
      }
    } else if (isTenantMode.value) {
      targetPath = `/t/${tenantId.value}/chat`
    }

    if (route.path !== targetPath || Object.keys(queryParams).length > 0) {
      router.push({ path: targetPath, query: Object.keys(queryParams).length > 0 ? queryParams : undefined })
    } else {
      // 如果已经在目标路由，still need to trigger watch by selecting null
      // 路由相同但 currentSessionId 变化会触发 watch 清空 messages
    }
  } finally {
    isCreating.value = false
  }
  // 手机端点击后自动收起左侧菜单
  if (props.isMobile) {
    emit('collapse')
  }
}

// 选择会话
async function handleSelectSession(sessionId: string) {
  // 如果当前正在流式响应，需要用户确认是否终止
  if (isProcessing.value) {
    if (!confirm('当前会话还未结束，您希望终止当前会话，切换到选中的会话吗？')) {
      return
    }
    // 用户确认，终止当前流式响应
    await abortStreaming()
  }

  selectSession(sessionId)

  // 跳转到聊天页面（如果当前不在聊天页面）
  const session = sessions.value.find(s => s.session_id === sessionId)

  // 从 session 中获取 subagent_id
  // 优先级：1. session.subagent_id 顶层字段 2. context_data.subagent 3. context_data.subagent_id
  let subagent = (session as any)?.subagent_id as string | undefined
  if (!subagent) {
    subagent = session?.context_data?.subagent as string | undefined
  }
  if (!subagent && session?.context_data?.subagent_id) {
    subagent = session.context_data.subagent_id as string
  }

  // 检查 subagent 是否为有效的子智能体（在 availableSubagents 中存在）
  if (subagent && props.availableSubagents.length > 0) {
    const matched = props.availableSubagents.find(
      (a: any) => a.agent_id === subagent || a.subagent_type === subagent
    )
    if (!matched) {
      // 不是有效的子智能体
      subagent = undefined
    }
  }

  let targetPath = '/'
  const queryParams: Record<string, string> = {}

  if (isTenantMode.value && tenantId.value) {
    // 租户模式
    if (subagent) {
      targetPath = `/t/${tenantId.value}/chat/${subagent}`
      // 尝试匹配 instance_id
      const matchedInstance = props.availableSubagents.find(
        (a: any) => a.agent_id === subagent || a.subagent_type === subagent
      )
      if (matchedInstance?.instance_id) {
        queryParams.instance_id = matchedInstance.instance_id
      }
    } else {
      targetPath = `/t/${tenantId.value}/chat`
    }
    if (session?.instance_id) {
      queryParams.instance_id = session.instance_id
    }
  } else if (subagent) {
    targetPath = `/chat/${subagent}`
  }

  if (route.path !== targetPath) {
    router.push({ path: targetPath, query: Object.keys(queryParams).length > 0 ? queryParams : undefined })
  }
  // 手机端点击后自动收起左侧菜单
  if (props.isMobile) {
    emit('collapse')
  }
}

// 删除会话
async function handleDeleteSession(sessionId: string) {
  if (confirm('确定要删除这个会话吗？')) {
    await removeSession(sessionId)
  }
}

// 重命名会话
function handleRenameSession(session: { session_id: string; title: string }) {
  renamingSessionId.value = session.session_id
  renameInput.value = session.title || ''
  showRenameModal.value = true
}

// 确认重命名
async function confirmRename() {
  if (renamingSessionId.value && renameInput.value.trim()) {
    await renameSession(renamingSessionId.value, renameInput.value.trim())
    showRenameModal.value = false
    renamingSessionId.value = null
    renameInput.value = ''
  }
}

// 租户模式修改密码
function handleModifyPassword() {
  if (tenantId.value) {
    router.push(`/t/${tenantId.value}/reset-password`)
  }
  // 手机端点击后自动收起左侧菜单
  if (props.isMobile) {
    emit('collapse')
  }
}

// 租户模式退出登录
async function handleTenantLogout() {
  await tenantLogout()
  if (tenantId.value) {
    router.push(`/t/${tenantId.value}/login`)
  } else {
    router.push('/')
  }
}
</script>

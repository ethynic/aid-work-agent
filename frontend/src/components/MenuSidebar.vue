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
        <img
          v-if="logoUrl"
          :src="logoUrl"
          alt="Logo"
          class="w-8 h-8 rounded-lg object-contain flex-shrink-0 border border-gray-200"
        />
        <div v-else class="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center flex-shrink-0">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <h1 class="text-base font-semibold text-gray-800 truncate">
          {{ sidebarTitle }}
        </h1>
        <!-- 收起侧边栏按钮：固定在名称最右侧 -->
        <button
          @click="$emit('collapse')"
          class="ml-auto p-1.5 flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors flex-shrink-0"
          title="收起侧边栏"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Middle Content - 菜单区固定，仅历史会话列表滚动 -->
    <div class="flex-1 flex flex-col min-h-0">
    <!-- Navigation Menu - 导航菜单区域（固定不滚动） -->
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

      <!-- 我的数字员工入口：与「新会话」相邻，相似功能放一起；显示条件复用 showNewSession -->
      <button
        v-if="showNewSession"
        @click="goToMyAgents"
        :class="[
          'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
          isMyAgentsActive
            ? 'bg-primary-50 text-primary-700 font-medium'
            : 'text-gray-600 hover:bg-gray-50'
        ]"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <!-- 四宫格图标，呼应页面内的卡片网格布局 -->
          <path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" />
        </svg>
        <span>我的数字员工</span>
      </button>

      <!-- 租户模式菜单 -->
      <template v-if="isTenantMode">

        <!-- 经验中心 trigger：hover/click 触发右侧 flyout（手机端隐藏） -->
        <button
          v-if="!props.isMobile"
          :ref="el => setTriggerRef('experience', el)"
          @mouseenter="openFlyout('experience')"
          @mouseleave="scheduleClose()"
          @click="toggleFlyout('experience')"
          :aria-expanded="activeFlyout === 'experience'"
          class="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm text-gray-600 hover:bg-gray-50"
        >
          <div class="flex items-center gap-3">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <!-- 奖杯：象征工作成果与经验沉淀 -->
              <path d="M6 9V4h12v5a6 6 0 01-12 0z" />
              <path d="M4 4h2M18 4h2M9 4v5a3 3 0 006 0V4M12 15v6M9 21h6" />
            </svg>
            <span>经验中心</span>
          </div>
          <!-- Chevron 默认指向右，active 时 rotate-90 转为向下，提示「展开方向是右侧」 -->
          <svg
            :class="['w-4 h-4 transition-transform', activeFlyout === 'experience' ? 'rotate-90' : '']"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </button>

        <!-- 知识中心入口：一级菜单，仅租户管理员可见（手机端隐藏） -->
        <button
          v-if="isTenantAdmin && !props.isMobile"
          @click="router.push(`/t/${tenantId}/knowledge`)"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
            route.path === `/t/${tenantId}/knowledge`
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <!-- 书本：象征知识库 -->
            <path d="M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
          </svg>
          <span>知识中心</span>
        </button>

        <!-- 连接中心入口：一级菜单，仅租户管理员可见（手机端隐藏） -->
        <button
          v-if="isTenantAdmin && !props.isMobile"
          @click="router.push(`/t/${tenantId}/connections`)"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
            route.path === `/t/${tenantId}/connections`
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <!-- 插头/连接：象征连接外部系统 -->
            <path d="M9 7V3M15 7V3M9 21v-4M15 21v-4M5 12H3M21 12h-2M7 9h10a2 2 0 012 2v2a2 2 0 01-2 2H7a2 2 0 01-2-2v-2a2 2 0 012-2z" />
          </svg>
          <span>连接中心</span>
        </button>

        <!-- 管理菜单 trigger：hover/click 触发右侧 flyout（仅租户管理员，手机端隐藏） -->
        <button
          v-if="isTenantAdmin && !props.isMobile"
          :ref="el => setTriggerRef('admin', el)"
          @mouseenter="openFlyout('admin')"
          @mouseleave="scheduleClose()"
          @click="toggleFlyout('admin')"
          :aria-expanded="activeFlyout === 'admin'"
          class="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm text-gray-600 hover:bg-gray-50"
        >
          <div class="flex items-center gap-3">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <span>管理菜单</span>
          </div>
          <svg
            :class="['w-4 h-4 transition-transform', activeFlyout === 'admin' ? 'rotate-90' : '']"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </button>
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
          <span>知识中心</span>
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

    <!-- 业务数据 - 扁平 trigger 列表（每个有业务页的数字员工是一级菜单项） -->
    <!-- 手机端暂时隐藏：业务数据页面尚未适配手机端；flyout 在 isMobile=true 时不会触发 -->
    <div v-if="groupedBusinessPages.length > 0 && !props.isMobile" class="flex-shrink-0 p-2">
      <div class="flex-shrink-0 px-2 py-2">
        <div class="flex items-center gap-3">
          <div class="flex-1 h-px bg-gradient-to-r from-transparent via-gray-300 to-transparent"></div>
        </div>
      </div>

      <!-- 各数字员工业务菜单 trigger 列表（flyout 触发器） -->
      <div class="space-y-1">
        <button
          v-for="group in groupedBusinessPages"
          :key="group.subagent.agent_id"
          :ref="el => setTriggerRef(group.subagent.agent_id, el)"
          @mouseenter="openFlyout(group.subagent.agent_id)"
          @mouseleave="scheduleClose()"
          @click="toggleFlyout(group.subagent.agent_id)"
          :aria-expanded="activeFlyout === group.subagent.agent_id"
          class="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm text-gray-600 hover:bg-gray-50"
        >
          <div class="flex items-center gap-3">
            <MenuIcon icon="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" />
            <span>{{ group.subagent.display_name || group.subagent.name }}</span>
          </div>
          <svg
            :class="['w-4 h-4 transition-transform', activeFlyout === group.subagent.agent_id ? 'rotate-90' : '']"
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
        </button>
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

    <!-- History Sessions Header - 历史会话标题（固定展示，不再支持整体折叠） -->
    <div v-if="showHistory" class="flex-shrink-0 px-5 py-2 flex items-center gap-3 text-sm text-gray-600">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>历史会话</span>
    </div>

    <!-- Session List - 会话列表（侧边栏唯一滚动区域） -->
    <div v-show="showHistory" class="flex-1 min-h-0 overflow-y-auto px-2 pb-2">
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

      <div v-else class="space-y-0.5">
        <div
          v-for="session in displayedSessions"
          :key="session.session_id"
          :class="[
            'group relative px-2.5 py-1.5 rounded-lg cursor-pointer transition-colors',
            isHistorySessionActive && currentSessionId === session.session_id
              ? 'bg-primary-50 border border-primary-200'
              : 'hover:bg-gray-50 border border-transparent'
          ]"
          @click="handleSelectSession(session.session_id)"
          @contextmenu.prevent.stop="openSessionContextMenu($event, session)"
        >
          <!-- Session Title（单行，日期已移除以降低条目高度） -->
          <div class="flex items-center gap-2">
            <svg class="w-4 h-4 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            <p class="flex-1 min-w-0 text-sm text-gray-700 truncate">
              {{ session.title || '新会话' }}
            </p>
            <!-- 流式状态指示：进行中显示旋转 loading，后台完成未查看显示小点 -->
            <svg
              v-if="isSessionRunning(session.session_id)"
              class="animate-spin w-3.5 h-3.5 flex-shrink-0 text-primary-600"
              fill="none" viewBox="0 0 24 24"
            >
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
            <span
              v-else-if="hasSessionUnreadCompletion(session.session_id)"
              class="w-2 h-2 rounded-full bg-success-500 flex-shrink-0"
              title="已完成"
            ></span>
          </div>

          <!-- 移动端操作按钮：无右击可用，保留始终显示的入口打开同一菜单 -->
          <button
            @click.stop="openSessionContextMenu($event, session)"
            class="absolute right-1 top-1/2 -translate-y-1/2 md:hidden p-2 min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded transition-colors"
            title="更多操作"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 5v.01M12 12v.01M12 19v.01" />
            </svg>
          </button>
        </div>

        <!-- 展开更多：默认显示10条，点击后在滚动区内展示全部 -->
        <button
          v-if="!isSessionListExpanded && filteredSessions.length > sessionDisplayLimit"
          @click="isSessionListExpanded = true"
          class="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-50 rounded-lg transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
          <span>展开更多（{{ filteredSessions.length - sessionDisplayLimit }}）</span>
        </button>
        <button
          v-else-if="isSessionListExpanded && filteredSessions.length > sessionDisplayLimit"
          @click="isSessionListExpanded = false"
          class="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-50 rounded-lg transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
          </svg>
          <span>收起</span>
        </button>

        <!-- 全部历史会话链接 -->
        <button
          v-if="filteredSessions.length > 0"
          @click="goToAllSessions"
          class="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-primary-600 hover:bg-primary-50 rounded-lg transition-colors"
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
    <div class="flex-shrink-0 relative">
      <div class="flex-shrink-0 p-3 border-t border-gray-200">
        <button
          v-if="desktopUpdateVisible"
          type="button"
          :disabled="desktopUpdateState.status === 'downloading'"
          :title="desktopUpdateLabel"
          class="w-full mb-1 flex items-center gap-2 px-3 py-2 text-sm text-primary-700 bg-primary-50 hover:bg-primary-100 rounded-lg transition-colors disabled:cursor-wait"
          @click="handleDesktopUpdate"
        >
          <svg class="w-5 h-5 flex-shrink-0" :class="{ 'animate-pulse': desktopUpdateState.status === 'downloading' }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14" />
          </svg>
          <span class="truncate">{{ desktopUpdateLabel }}</span>
          <span v-if="desktopUpdateState.status === 'available'" class="ml-auto w-2 h-2 rounded-full bg-primary-600" aria-hidden="true"></span>
        </button>
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

        <!-- 我的定时任务（手机端隐藏：定时任务页面未适配手机端） -->
        <button
          v-if="!props.isMobile"
          @click="showUserMenu = false; openScheduledTasks()"
          class="w-full flex items-center gap-3 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>我的定时任务</span>
        </button>

        <!-- 本地工具（仅租户模式：普通用户配对管理自己的本机 Runtime 设备） -->
        <button
          v-if="isTenantMode && tenantIsLoggedIn"
          @click="showUserMenu = false; openLocalTools()"
          class="w-full flex items-center gap-3 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
          <span>本地工具</span>
        </button>

        <!-- 设置（手机端隐藏：设置弹窗未适配手机端） -->
        <button
          v-if="!props.isMobile"
          @click="showUserMenu = false; showSettingsDialog = true"
          class="w-full flex items-center gap-3 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          <span>设置</span>
        </button>

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

  <!-- 二级菜单 Flyout 面板（Teleport to body，单实例，由 activeFlyout 切换内容） -->
  <!-- 经验中心/管理菜单/业务数据（每个数字员工）的二级菜单统一在此浮出，不占纵向空间 -->
  <Teleport to="body">
    <div
      v-if="activeFlyout"
      ref="panelRef"
      :style="flyoutStyle"
      class="fixed z-[45] w-56 bg-white border border-gray-200 rounded-lg shadow-lg py-1 max-h-[80vh] overflow-y-auto"
      @mouseenter="cancelClose()"
      @mouseleave="scheduleClose()"
    >
      <!-- 经验中心子菜单 -->
      <template v-if="activeFlyout === 'experience'">
        <button
          v-if="tenantId"
          @click="router.push(`/t/${tenantId}/daily-report`); closeFlyout()"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm',
            route.path === `/t/${tenantId}/daily-report`
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
            <path d="M14 2v6h6M9 13h6M9 17h4" />
            <path d="M19 17l1.5 1.5L23 16" />
          </svg>
          <span>工作日报</span>
        </button>

        <button
          v-if="tenantId"
          @click="router.push(`/t/${tenantId}/work-outcomes`); closeFlyout()"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm',
            route.path === `/t/${tenantId}/work-outcomes`
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
            <path d="M14 2v6h6" />
            <path d="M9 14l2 2 4-4" />
          </svg>
          <span>工作成果</span>
        </button>

        <!-- 外部接待客户：仅租户管理员可见 -->
        <button
          v-if="tenantId && isTenantAdmin"
          @click="router.push(`/t/${tenantId}/external-customers`); closeFlyout()"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm',
            route.path === `/t/${tenantId}/external-customers`
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2M12 11a4 4 0 100-8 4 4 0 000 8z" />
          </svg>
          <span>外部接待客户</span>
        </button>
      </template>

      <!-- 管理子菜单 -->
      <template v-else-if="activeFlyout === 'admin'">
        <button
          v-for="item in adminSubMenuItems"
          :key="item.path"
          @click="router.push(item.path); closeFlyout()"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm',
            route.path === item.path
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
        >
          <MenuIcon :icon="item.icon" />
          <span>{{ item.label }}</span>
        </button>
      </template>

      <!-- 业务数据子菜单（按 agent_id 找回 pages） -->
      <template v-else>
        <div
          v-for="page in pagesForAgent(activeFlyout!)"
          :key="page.id"
          :class="[
            'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm cursor-pointer',
            route.path === page.route
              ? 'bg-primary-50 text-primary-700 font-medium'
              : 'text-gray-600 hover:bg-gray-50'
          ]"
          @click="navigateToBusinessPage(page, activeFlyout!); closeFlyout()"
        >
          <BusinessPageIcon :title="page.title" />
          <span>{{ page.title }}</span>
        </div>
      </template>
    </div>
  </Teleport>

  <!-- 会话右击菜单（重命名/删除） -->
  <div
    v-if="sessionContextMenu.visible"
    class="fixed inset-0 z-[60]"
    @click="closeSessionContextMenu"
    @contextmenu.prevent="closeSessionContextMenu"
  >
    <div
      class="absolute bg-white border border-gray-200 rounded-lg shadow-lg py-1 w-32"
      :style="{ left: sessionContextMenu.x + 'px', top: sessionContextMenu.y + 'px' }"
      @click.stop
    >
      <button
        @click="handleContextRename"
        class="w-full flex items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
        </svg>
        <span>重命名</span>
      </button>
      <button
        @click="handleContextDelete"
        class="w-full flex items-center gap-2 px-3 py-2 text-left text-sm text-danger-600 hover:bg-danger-50 transition-colors"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
        <span>删除</span>
      </button>
    </div>
  </div>

  <!-- Settings Dialog -->
  <SettingsDialog
    :visible="showSettingsDialog"
    @close="showSettingsDialog = false"
  />
</template>

<script setup lang="ts">
import { ref, computed, watch, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { onClickOutside } from '@vueuse/core'
import { useSession } from '@/composables/useSession'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAgent } from '@/composables/useAgent'
import { useTheme, type ThemeName } from '@/composables/useTheme'
import { useDesktopUpdater } from '@/composables/useDesktopUpdater'
import SettingsDialog from './SettingsDialog.vue'
import MenuIcon from './ui/MenuIcon.vue'
import BusinessPageIcon from './ui/BusinessPageIcon.vue'

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
const showSettingsDialog = ref(false)
const showThemeSubmenu = ref(false)
const availableThemes = getAvailableThemes()
const { state: desktopUpdateState, isVisible: desktopUpdateVisible, label: desktopUpdateLabel, activate: handleDesktopUpdate } = useDesktopUpdater()

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
const { isSessionRunning, hasSessionUnreadCompletion, removeStreamState } = useAgent()

const isCreating = ref(false)
const showRenameModal = ref(false)
const renameInput = ref('')
const renamingSessionId = ref<string | null>(null)

// ============== Flyout 二级菜单状态机 ==============
// 单一 activeFlyout 互斥：值域 'experience' | 'admin' | agent_id | null
// hover 即打开、移出延迟 150ms 关闭；click 也切换；路由变化/收起态自动关
const activeFlyout = ref<string | null>(null)
// trigger 元素引用，用于 onClickOutside ignore 和 flyout 定位
const triggerRefs = new Map<string, HTMLElement>()
const panelRef = ref<HTMLElement | null>(null)
let closeTimer: ReturnType<typeof setTimeout> | undefined

function setTriggerRef(key: string, el: any) {
  // Vue 3 :ref 回调，el 可能为 null（卸载时）
  if (el) triggerRefs.set(key, el as HTMLElement)
  else triggerRefs.delete(key)
}

// flyout 定位：从 trigger 元素 getBoundingClientRect 取 right+4 作为 left，top 对齐
const flyoutStyle = reactive<Record<string, string>>({})
function updateFlyoutStyle(key: string) {
  const el = triggerRefs.get(key)
  if (!el) return
  const rect = el.getBoundingClientRect()
  flyoutStyle.left = `${rect.right + 4}px`
  flyoutStyle.top = `${rect.top}px`
}

function openFlyout(key: string) {
  // 收起态/移动端屏蔽（移动端走抽屉模式，不应触发 flyout）
  if (props.isCollapsed || props.isMobile) return
  if (closeTimer) { clearTimeout(closeTimer); closeTimer = undefined }
  // 先定位再切换 activeFlyout，避免初始渲染位置跳动
  updateFlyoutStyle(key)
  activeFlyout.value = key
}

function closeFlyout() {
  activeFlyout.value = null
}

function toggleFlyout(key: string) {
  activeFlyout.value === key ? closeFlyout() : openFlyout(key)
}

function scheduleClose() {
  // 150ms 延迟关闭，避免鼠标从 trigger 移到 flyout 过程中误关
  closeTimer = setTimeout(closeFlyout, 150)
}

function cancelClose() {
  if (closeTimer) { clearTimeout(closeTimer); closeTimer = undefined }
}

// 点击外部关闭：handler 内手动检查 trigger 路径，避免点击 trigger 自身被判定为外部导致抖动
// （onClickOutside v10 的 ignore 选项类型为固定数组，不接受函数，也不响应 triggerRefs 动态变化）
onClickOutside(panelRef, (event) => {
  const path = event.composedPath()
  for (const el of triggerRefs.values()) {
    if (path.includes(el)) return
  }
  closeFlyout()
})

// 路由变化关闭（点击子项跳转后即使 closeFlyout 没显式调，路由 watch 也会兜底）
watch(() => route.path, closeFlyout)
// 收起态自动关闭
watch(() => props.isCollapsed, c => c && closeFlyout())

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

// 租户 Logo URL：有 logo_file_id 时拼接下载地址，否则返回 null（回退到电脑图标）
const logoUrl = computed(() => {
  return tenant.value?.logo_file_id ? `/api/files/${tenant.value.logo_file_id}/download` : null
})

// 管理子菜单项（租户管理员可见）
// icon 字段为统一的 SVG path 数据，使用 stroke="currentColor" 的细线描边风格
const adminSubMenuItems = computed(() => {
  if (!tenantId.value) return []
  const base = `/t/${tenantId.value}`
  return [
    // 用户管理：人形 + 多人
    { path: `${base}/users`, label: '用户管理', icon: 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM22 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75' },
    // 渠道配置：信号波
    { path: `${base}/channels`, label: '渠道配置', icon: 'M5 12.55a11 11 0 0114 0M1.42 9a16 16 0 0121.16 0M8.53 16.11a6 6 0 016.95 0M12 20h.01' },
    // 企微个人RPA：机器人
    { path: `${base}/wecom-personal-rpa`, label: '企微个人RPA', icon: 'M12 4v3M5 8h14a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2v-8a2 2 0 012-2zM9 13h.01M15 13h.01M9 17h6' },
    // 企业设置：齿轮（简化版）
    { path: `${base}/settings`, label: '企业设置', icon: 'M12 8a4 4 0 100 8 4 4 0 000-8zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3h0a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5h0a1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8v0a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z' },
    // 积分用量：柱状图
    { path: `${base}/token-usage`, label: '积分用量', icon: 'M3 21h18M6 17V9M11 17V5M16 17v-4M21 17v-7' },
    // 充值记录：钱包/硬币
    { path: `${base}/recharge-records`, label: '充值记录', icon: 'M21 12a9 9 0 11-18 0 9 9 0 0118 0zM12 7v10M9 10h4.5a1.5 1.5 0 010 3H9' },
    // 回复风格：对话气泡
    { path: `${base}/reply-styles`, label: '回复风格', icon: 'M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z' },
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

// 历史会话展示：默认显示前 N 条（桌面 10 / 移动 6），点击「展开更多」后在滚动区内展示全部
const sessionDisplayLimit = computed(() => (props.isMobile ? 6 : 10))
const isSessionListExpanded = ref(false)
const displayedSessions = computed(() => {
  if (isSessionListExpanded.value) {
    return filteredSessions.value
  }
  return filteredSessions.value.slice(0, sessionDisplayLimit.value)
})

// 会话右击菜单（重命名/删除，桌面右击或移动端「…」按钮触发）
const sessionContextMenu = ref<{
  visible: boolean
  x: number
  y: number
  session: { session_id: string; title: string } | null
}>({ visible: false, x: 0, y: 0, session: null })

function openSessionContextMenu(event: MouseEvent, session: { session_id: string; title: string }) {
  // 防止菜单超出视口右/下边缘（菜单宽约 128px，高约 88px）
  const x = Math.min(event.clientX, window.innerWidth - 140)
  const y = Math.min(event.clientY, window.innerHeight - 100)
  sessionContextMenu.value = { visible: true, x, y, session }
}

function closeSessionContextMenu() {
  sessionContextMenu.value.visible = false
}

function handleContextRename() {
  const session = sessionContextMenu.value.session
  closeSessionContextMenu()
  if (session) {
    handleRenameSession(session)
  }
}

function handleContextDelete() {
  const session = sessionContextMenu.value.session
  closeSessionContextMenu()
  if (session) {
    handleDeleteSession(session.session_id)
  }
}

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

// 判断当前是否在对话界面（路由包含 /chat/）
// navigateToBusinessPage 据此决定：对话界面 window.open 新页（带 expand_menu 参数）；非对话界面 router.push 本页
const isChatPage = computed(() => {
  return route.path.includes('/chat/')
})

// 根据 agent_id 找回对应业务页列表（用于 flyout 渲染）
function pagesForAgent(agentId: string): BusinessPage[] {
  const group = groupedBusinessPages.value.find(g => g.subagent.agent_id === agentId)
  return group?.pages ?? []
}

// 跳转到业务数据页面
// 非对话界面：在本页打开；对话界面：新开页面并附带 expand_menu 参数
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

// 是否处于「我的数字员工」页面（用于菜单高亮）
const isMyAgentsActive = computed(() => {
  return route.path.endsWith('/my-agents')
})

// 跳转到「我的数字员工」页面
// demo 模式与租户模式均支持，路由路径按模式选择
function goToMyAgents() {
  const targetPath = isTenantMode.value && tenantId.value
    ? `/t/${tenantId.value}/my-agents`
    : '/my-agents'
  router.push(targetPath)
  // 手机端点击后自动收缩左侧菜单
  if (props.isMobile) {
    emit('collapse')
  }
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

  // 多会话后台流式：切换/新建会话不再中断正在进行的会话，旧会话在后台继续生成

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
  // 多会话后台流式：切换会话不再中断正在进行的会话，旧会话在后台继续生成
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
    // 先断开并清理该会话的流式状态（若正在后台生成）
    removeStreamState(sessionId)
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

// 我的定时任务
function openScheduledTasks() {
  window.open('/scheduled-tasks', '_blank')
}

// 本地工具设备管理（仅租户模式）
function openLocalTools() {
  if (tenantId.value) {
    router.push(`/t/${tenantId.value}/local-tools`)
  }
  // 手机端点击后自动收起左侧菜单
  if (props.isMobile) {
    emit('collapse')
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

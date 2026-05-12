## Context

当前前端基于 Vue 3 + Tailwind CSS，使用 flex 布局。桌面端布局如下：
- PortalLayout：左侧 sidebar（w-60 或 w-72）+ 右侧主内容区
- ChatContainer：左侧 MenuSidebar（w-72，可折叠为 w-0）+ 右侧聊天区（AppHeader + MessageList + ChatInput）
- MessageItem：用户消息有 `ml-12` 缩进，AI 消息全宽
- AppHeader：h-14，包含汉堡菜单、页面标题、数字员工选择器、用户名、"更多"下拉菜单
- ChatInput：max-w-4xl 居中，底部有 Enter/Shift+Enter 快捷键提示行
- AllSessions：与 ChatContainer 类似的 sidebar + 主内容区结构
- 管理菜单（用户管理、渠道配置、企业知识库、企业设置、Token消耗）在 MenuSidebar 中对租户管理员可见

## Goals / Non-Goals

**Goals:**
1. 让登录页、智能体对话界面、历史会话页在 375px~768px 宽度的屏幕上可用且舒适
2. 移动端 sidebar 以抽屉浮层方式呈现，带黑色遮罩
3. 聊天消息改为类似微信的全宽气泡样式
4. AppHeader 移动端仅保留汉堡菜单和数字员工选择器
5. 管理菜单在移动端完全隐藏
6. 历史会话页操作按钮在移动端常驻显示（替代 group-hover）
7. 不引入新的 UI 库或框架，仅使用 Tailwind CSS 响应式类 + 少量 JS 检测

**Non-Goals:**
1. 不改造管理后台页面（/portal/*）的移动端适配
2. 不改造租户前台的管理功能页面（用户管理、渠道配置、企业知识库、企业设置、Token消耗）
3. 不改造"颜色主题"和"右上角更多菜单"功能（移动端直接隐藏）
4. 不改造业务数据页面（外贸、旅游等 BaseBusinessLayout）
5. 不引入专门的手机端框架（如 vant、ionic）
6. 不对 PWA 或原生 App 做支持

## Decisions

### 1. 移动端检测策略

**方案选择**: 组合式函数 `useMobile()` 监听窗口宽度，breakpoint 设为 768px（`md`），与 Tailwind 默认断点对齐。

**Rationale**:
- Tailwind 的 `md:` 断点就是 `768px`，JS 检测与 CSS 断点保持一致，避免状态不一致
- 不使用 `navigator.userAgent` 检测，因为平板（如 iPad）的 UA 可能含 "Mobile"，但屏幕够大应走桌面布局
- 监听 `resize` 事件，支持横竖屏切换和窗口大小调整

**实现细节**:
```typescript
// useMobile.ts
import { ref, onMounted, onUnmounted } from 'vue'

const MOBILE_BREAKPOINT = 768

export function useMobile() {
  const isMobile = ref(false)

  function check() {
    isMobile.value = window.innerWidth < MOBILE_BREAKPOINT
  }

  onMounted(() => {
    check()
    window.addEventListener('resize', check)
  })

  onUnmounted(() => {
    window.removeEventListener('resize', check)
  })

  return { isMobile }
}
```

### 2. Sidebar 抽屉模式

**方案选择**: `MenuSidebar.vue` 通过 props `is-mobile` 控制渲染模式。移动端使用 `fixed inset-y-0 left-0 z-50 w-72` + 黑色遮罩层，桌面端保持现有 `aside` 行为。

**Rationale**:
- `MenuSidebar` 本身不知道自己是否是抽屉，由父组件（PortalLayout / ChatContainer）传入 `is-mobile` 状态
- 抽屉模式下 sidebar 内部结构和内容完全一致，只是外壳变为 fixed + backdrop
- 点击遮罩或点击"收起"按钮关闭抽屉
- 避免在移动端 DOM 中同时存在两个 sidebar（PortalLayout 渲染一个，ChatContainer 再渲染一个）

**实现细节**:
```vue
<!-- 桌面端 -->
<aside v-if="!isMobile" :class="[...]">...</aside>

<!-- 移动端抽屉 -->
<template v-else>
  <!-- 遮罩 -->
  <div v-if="!isCollapsed" class="fixed inset-0 bg-black/50 z-40" @click="$emit('collapse')"></div>
  <!-- 抽屉本体 -->
  <aside :class="[
    'fixed inset-y-0 left-0 z-50 w-72 bg-white border-r border-gray-200 flex flex-col transition-transform duration-300',
    isCollapsed ? '-translate-x-full' : 'translate-x-0'
  ]">...</aside>
</template>
```

### 3. 聊天消息全宽气泡

**方案选择**: 移动端移除 `ml-12`，用户消息全宽显示，仅通过背景色区分 sender。

**Rationale**:
- `ml-12` 在 375px 宽屏上浪费约 30% 的水平空间，导致消息内容区域极窄
- 微信等主流 IM 应用在手机上都是全宽气泡，用户心智模型已建立
- 保留圆角和背景色区分（用户 `bg-primary-50`，AI `bg-white`）

**实现细节**:
```vue
<div :class="[
  'flex gap-3 p-4 rounded-2xl transition-all',
  message.role === 'user'
    ? 'bg-primary-50 border border-primary-200 md:ml-12'
    : 'bg-white border border-gray-200'
]">
```

### 4. AppHeader 精简

**方案选择**: 移动端隐藏用户名、"更多"菜单按钮，仅保留汉堡菜单 + 数字员工选择器（精简为图标或短文本）。

**Rationale**:
- 移动端顶部宽度有限（375px），数字员工选择器 `min-w-[160px]` 已占大量空间
- 用户名和"更多"菜单不是核心聊天功能，移动端可隐藏
- 退出登录、修改密码等操作可从 sidebar 抽屉底部访问

**实现细节**:
```vue
<!-- 桌面端显示完整信息 -->
<div class="hidden md:flex items-center gap-3 flex-shrink-0">
  <span class="text-sm text-gray-600">{{ user?.username }}</span>
  <button v-if="showDemoLogout" @click="$emit('logout')">退出</button>
  <button @click="showMenuDropdown = !showMenuDropdown">更多</button>
</div>

<!-- 移动端：仅保留选择器，隐藏用户名和更多菜单 -->
<!-- （不需要额外 DOM，用 hidden md:block / hidden md:flex 控制即可） -->
```

### 5. 历史会话操作按钮常驻

**方案选择**: 移动端将 `group-hover:flex` 改为始终 `flex`，按钮尺寸适当缩小。

**Rationale**:
- 触屏设备没有 hover 状态，`group-hover` 在移动端完全不可见
- 左滑手势在 Web 端实现成本高（需处理 touch 事件、滚动冲突、浏览器差异）
- 始终显示操作按钮虽然占用空间，但实现简单、交互明确、零学习成本
- 桌面端保持 group-hover 不变，不破坏现有体验

**实现细节**:
```vue
<div class="flex items-center gap-1 ml-3 md:hidden">
  <!-- 移动端常驻显示 -->
  <button @click.stop="handleRenameSession(session)">...</button>
  <button @click.stop="handleDeleteSession(session.session_id)">...</button>
</div>
<div class="absolute right-1.5 top-1.5 hidden md:group-hover:flex items-center gap-0.5">
  <!-- 桌面端 hover 显示 -->
  ...
</div>
```

### 6. 管理菜单在移动端隐藏

**方案选择**: `MenuSidebar.vue` 中管理菜单（用户管理、渠道配置等）添加 `hidden md:block` 包装，移动端完全不渲染。

**Rationale**:
- 管理功能不需要手机支持，避免误触
- 直接不渲染 DOM，而不是渲染后隐藏，减少移动端 DOM 体积
- 侧边栏抽屉中只保留"新会话"、"历史会话"、"业务数据"、"修改密码"、"退出登录"

## Risks / Trade-offs

### 风险与缓解措施

1. **ChatContainer 和 PortalLayout 同时渲染 sidebar**
   - **风险**: `ChatContainer` 在非 PortalLayout 模式下自己渲染 `MenuSidebar`（`v-if="!isInPortalLayout"`），在 PortalLayout 模式下由 PortalLayout 渲染。移动端如果两边都渲染，会出现两个 sidebar。
   - **缓解**: 移动端统一由 PortalLayout 控制 sidebar 的抽屉状态（通过 provide/inject 或 props 传递 `is-mobile`），`ChatContainer` 的 sidebar 在移动端不渲染。或者更简单：`ChatContainer` 的 sidebar 始终用 `hidden md:flex` 隐藏移动端，PortalLayout 负责移动端抽屉。

2. **数字员工选择器在小屏溢出**
   - **风险**: `min-w-[160px]` 的选择器在 375px 屏上可能撑破顶部栏
   - **缓解**: 移动端将选择器文字最大宽度进一步限制（如 `max-w-[120px]`），或使用图标 + 短文本形式

3. **附件上传在移动端体验差**
   - **风险**: `<input type="file">` 在 iOS/Android 上的行为差异较大
   - **缓解**: 本次变更仅做布局适配，不改造上传交互。已有 `accept` 属性已能唤起系统文件选择器。

### 权衡

- **CSS 断点 vs JS 检测**: 布局差异用 Tailwind 响应式类（`md:`），需要条件渲染的结构差异（如 sidebar 抽屉外壳）用 `useMobile()`。混合方案最灵活。
- **始终显示操作按钮 vs 左滑手势**: 选择始终显示，开发成本低、跨浏览器兼容性好。
- **管理菜单隐藏 vs 保留入口**: 选择完全隐藏，不渲染 DOM，避免误触，符合"管理功能不需要手机支持"的要求。

## Why

当前前端页面仅针对桌面端设计，没有做任何移动端适配。随着用户越来越多地通过手机浏览器访问系统，登录页、智能体对话界面和历史会话页在小屏设备上存在严重的可用性问题：侧边栏占满屏幕、消息气泡边距过大、顶部操作栏溢出、管理菜单对触屏不友好等。本变更旨在让核心用户流程在手机上获得可用、舒适的体验。

## What Changes

- **移动端响应式布局**：PortalLayout / ChatContainer / AllSessions 的 flex 行布局在移动端转为单列全宽，sidebar 变为抽屉覆盖层
- **登录页微调**：UniversalLogin.vue 和 TenantLogin.vue 减少小屏内边距，适配窄屏
- **消息气泡重构**：MessageItem.vue 用户消息移除 `ml-12` 缩进，改为全宽气泡（类似微信）
- **顶部栏精简**：AppHeader.vue 移动端仅保留汉堡菜单和数字员工选择器，隐藏用户名和"更多"菜单
- **聊天输入区优化**：ChatInput.vue 移动端隐藏快捷键提示，放开 `max-w-4xl` 为全宽
- **历史会话页适配**：AllSessions.vue 移动端始终显示操作按钮（替代 group-hover），分页控件适配窄屏
- **管理菜单隐藏**：MenuSidebar.vue 移动端完全隐藏管理菜单入口
- **侧边栏抽屉化**：MenuSidebar.vue 在移动端作为 fixed 抽屉渲染，带黑色遮罩和关闭按钮

## Capabilities

### New Capabilities
- `mobile-chat`: 用户可在手机上与智能体进行完整的对话交互，包括上传附件
- `mobile-history`: 用户可在手机上查看全部历史会话、重命名和删除会话
- `mobile-login`: 用户可在手机上完成租户登录和统一登录

### Modified Capabilities
- `chat-interface`: 对话界面支持桌面端和移动端两种布局
- `sidebar-navigation`: 侧边栏在桌面端为常驻侧边栏，在移动端为抽屉覆盖层

## Impact

- **前端应用**：
  - `PortalLayout.vue`：添加移动端响应式类
  - `ChatContainer.vue`：添加移动端响应式类，侧边栏由 PortalLayout 控制
  - `AppHeader.vue`：移动端精简顶部元素
  - `MenuSidebar.vue`：支持抽屉模式渲染，隐藏管理菜单
  - `MessageItem.vue`：移动端移除用户消息缩进
  - `MessageList.vue`：移动端放开 max-width 限制
  - `ChatInput.vue`：移动端隐藏快捷键提示
  - `AllSessions.vue`：移动端适配布局，操作按钮常驻显示
  - `UniversalLogin.vue` / `TenantLogin.vue`：移动端减少内边距
- **新增文件**：`frontend/src/composables/useMobile.ts`（移动端检测 composable）
- **后端API**：无变更
- **数据库**：无结构变更
- **配置**：无需新增配置项

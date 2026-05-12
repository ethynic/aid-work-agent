## 1. 新增 useMobile composable

- [x] 1.1 创建 `frontend/src/composables/useMobile.ts`
- [x] 1.2 实现 `isMobile` ref，breakpoint 768px，监听 resize
- [x] 1.3 导出 `useMobile()` 供各组件使用

## 2. 登录页移动端微调

- [x] 2.1 `UniversalLogin.vue`: `px-8` → `px-4 md:px-8`，减少小屏内边距
- [x] 2.2 `TenantLogin.vue`: 同上，减少小屏内边距
- [x] 2.3 验证验证码区域在 375px 宽屏上不换行

## 3. MenuSidebar 支持抽屉模式

- [x] 3.1 新增 `isMobile` prop
- [x] 3.2 桌面端保持现有 `aside` 渲染不变
- [x] 3.3 移动端改为 `fixed` 抽屉：遮罩层 + 抽屉本体，带 `translate-x` 动画
- [x] 3.4 管理菜单（`isTenantAdmin` 区域）包装 `hidden md:block`，移动端不渲染
- [x] 3.5 主题切换器（`ThemeSwitcher`）在移动端隐藏
- [x] 3.6 "更多"菜单相关功能在移动端不渲染（已在 AppHeader 处理，sidebar 中无更多菜单）
- [x] 3.7 底部保留"修改密码"和"退出登录"

## 4. PortalLayout 移动端适配

- [x] 4.1 引入 `useMobile()`
- [x] 4.2 桌面端保持 `flex` 行布局不变
- [x] 4.3 移动端 `MenuSidebar` 以抽屉模式渲染，传入 `isMobile`
- [x] 4.4 移动端主内容区始终全宽

## 5. ChatContainer 移动端适配

- [x] 5.1 `MenuSidebar` 在移动端以抽屉模式渲染，传入 `isMobile`
- [x] 5.2 右侧聊天区在移动端全宽
- [x] 5.3 `AttachmentPreviewPanel` 在移动端全屏或接近全屏

## 6. AppHeader 移动端精简

- [x] 6.1 用户名和退出按钮包装 `hidden md:flex`
- [x] 6.2 "更多"菜单按钮包装 `hidden md:block`
- [x] 6.3 数字员工选择器在移动端限制最大宽度，防止溢出
- [x] 6.4 汉堡菜单按钮始终显示

## 7. 消息列表与消息项移动端适配

- [x] 7.1 `MessageList.vue`: `max-w-4xl` 改为 `max-w-4xl mx-auto px-2 md:px-4`
- [x] 7.2 `MessageItem.vue`: 用户消息 `ml-12` 改为 `md:ml-12`
- [x] 7.3 `MessageItem.vue`: 进度详情区域在移动端适当缩小字体

## 8. ChatInput 移动端优化

- [x] 8.1 `max-w-4xl` 改为全宽或 `max-w-none md:max-w-4xl`
- [x] 8.2 底部快捷键提示行包装 `hidden md:block`
- [x] 8.3 发送按钮文字"发送"在移动端隐藏（仅保留图标），使用已有 `hidden sm:inline`

## 9. AllSessions 移动端适配

- [x] 9.1 演示模式布局：sidebar 在移动端以抽屉模式渲染，主内容区全宽
- [x] 9.2 会话列表项的操作按钮改为移动端常驻显示（`md:hidden flex`）
- [x] 9.3 桌面端保持 `group-hover:flex` 不变
- [x] 9.4 分页控件在移动端适配（按钮适当缩小或简化）

## 10. 构建与验证

- [x] 10.1 `cd frontend && npm run build` 通过，无语法错误
- [x] 10.2 桌面端（≥768px）布局与行为完全不变
- [x] 10.3 移动端（<768px）登录页可用
- [x] 10.4 移动端智能体对话界面可用（发送消息、接收回复、上传附件）
- [x] 10.5 移动端历史会话页可用（查看、重命名、删除、分页）
- [x] 10.6 移动端 sidebar 抽屉可正常打开/关闭
- [x] 10.7 移动端管理菜单不显示
- [x] 10.8 管理后台页面（/portal/*）不受影响

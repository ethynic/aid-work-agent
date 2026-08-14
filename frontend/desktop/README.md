# Desktop renderer

独立 Desktop UI 的工程边界。`desktop.html` 固定加载 `desktop/main.ts`，包含独立 Shell、
登录、启动状态机和状态 UI，不包含对话业务。旧 UI 只允许开发命令显式启动，不能进入 production artifact。

约束：

- 可依赖 `@shared/*`，不得依赖 Electron/Node 原生模块。
- 临时依赖 `@web/*` 必须登记在 `config/desktop-web-import-allowlist.json`。
- 不允许引用 Web 页面外壳、路由、认证入口或全局样式。
- 720×500 是最小布局基线；所有状态必须具备键盘路径、焦点态和 ARIA 状态通知。

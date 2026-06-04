# 页面规范 — 列表页与详情页详细实现

> **管辖范围**：本文档是列表页和详情页规范的**索引入口**。详细实现规范已拆分到独立文件中。
>
> **其他页面类型**（统计页、设置页等）的规范见 [page_patterns.md](../../.claude/rules/page_patterns.md)。

## 详细规范文件

| 页面类型 | 规范文件 |
|---------|---------|
| 列表页 | [list-page-convention.md](../../.claude/rules/list-page-convention.md) — 布局、搜索区、表格列、分页器、`usePageContext` Composable |
| 详情页（含新增/编辑） | [detail-page-convention.md](../../.claude/rules/detail-page-convention.md) — BaseModal 尺寸、表单样式、`useModalCloseGuard` 脏检测、表单验证 |

> 全局统一样式（颜色主题、字体、按钮、输入框、表格、分页器等基础规范）见 [page_patterns.md](../../.claude/rules/page_patterns.md) 第1节「全局统一样式」。

本项目有多个列表页和详情页，例如：

- 管理后台，租户管理 `/portal/tenants`
- 租户前台，用户管理 `/t/{tenant_id}/users`
- 租户前台，企业知识库 `/t/{tenant_id}/knowledge`
- 租户前台，旅游咨询顾问 - 车辆价格 `/t/{tenant_id}/travel-consultant/vehicles`

上述列表页上，每条记录有"编辑"按钮，点击后可进入详情页。

在具体实现上，很多细节不统一、不美观，还有很多功能性的缺漏，所以制定本规范文件，提供统一样式、列表页、详情页的实现标准。

---

## 1. 手机端支持

| 页面类型 | 是否需要手机端支持 |
|---------|------------------|
| 管理后台页面（平台管理员使用） | 不需要 |
| 租户前台"管理菜单"下的功能页面（租户管理员使用） | 不需要 |
| 租户前台普通用户使用的功能（对话界面、各智能体业务数据页面） | 需要 |

手机端适配参考响应式断点：`max-width: 768px`（平板及手机），`max-width: 480px`（手机）。

---

## 附录 A：组件快速参考

### 现有 Base 组件

| 组件 | 文件 | 用途 |
|------|------|------|
| `BaseButton` | `components/ui/BaseButton.vue` | 按钮，intent: primary/secondary/danger/ghost，size: sm/md/lg |
| `BaseInput` | `components/ui/BaseInput.vue` | 输入框，state: default/error/success，size: sm/md/lg |
| `BaseSelect` | `components/ui/BaseSelect.vue` | 下拉选择，state: default/error，size: sm/md/lg |
| `BaseTable` | `components/ui/BaseTable.vue` | 表格，columns + data + 具名插槽 |
| `BasePagination` | `components/ui/BasePagination.vue` | 分页，total + currentPage + pageSize |
| `BaseModal` | `components/ui/BaseModal.vue` | 模态框，size: sm/md/lg/xl，scrollable |
| `BaseCard` | `components/ui/BaseCard.vue` | 卡片容器 |
| `BaseBadge` | `components/ui/BaseBadge.vue` | 徽章/标签 |

### 待扩展/待创建的组件

| 组件 | 说明 |
|------|------|
| `BaseCheckbox` | 统一样式的复选框组件 |
| `BaseTextarea` | 统一样式的文本域组件 |
| `BasePagination` 增强 | 添加每页行数选择器（10/20/50/100） |
| `BaseModal` 增强 | 添加脏检测关闭逻辑、全屏按钮 |

### 布局组件

| 组件 | 文件 | 用途 |
|------|------|------|
| `AppHeader` | `components/AppHeader.vue` | 顶部标题栏（汉堡按钮 + 标题 + 更多菜单） |
| `MenuSidebar` | `components/MenuSidebar.vue` | 左侧菜单栏（可收缩） |
| `PortalLayout` | Portal 相关 | 租户前台布局（MenuSidebar + router-view） |
| `BaseBusinessLayout` | `components/BaseBusinessLayout.vue` | 业务数据管理页布局（顶部导航 + router-view） |

---

## 附录 B：Tailwind 主题配置

项目的 `tailwind.config.js` 已将 CSS 变量映射为 Tailwind 颜色类，开发时直接使用语义类名：

```js
// 已配置，可直接使用以下类名：
bg-primary-600       // → var(--color-primary-600)
text-gray-800         // → var(--color-gray-800)
border-default        // → var(--border-default)
bg-surface            // → var(--bg-surface)
text-muted            // → var(--text-muted)
```

> **核心原则**：优先使用 Tailwind 语义类名（`bg-primary-600`），而非直接写 `var(--color-primary-600)`。后者仅在 Tailwind 无法覆盖的场景（如内联 style 动态值）中使用。

---

> **版本历史**：
> - 2026-05-20：初稿，定义列表页、详情页、手机端规范及全局统一样式。
> - 2026-06-03：对齐项目实际 Base 组件体系，修正按钮尺寸/类型、输入框尺寸、Modal 尺寸、表格列顺序等；标注待扩展功能；补充布局组件参考。
> - 2026-06-04：将列表页/详情页/全局样式规范拆分到独立文件，本文档改为索引入口。

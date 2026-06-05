# 列表页规范

> **管辖范围**：本文档定义了项目中所有**列表页**的详细实现规范（CSS 公共类名、`usePageContext` Composable、布局模式、搜索区/表格/分页器完整规范等）。
>
> 要点速查见 [page_patterns.md](../../.claude/rules/page_patterns.md) 第1节。其他页面类型（详情页、统计页、设置页等）规范也在此文件中。
>
> 全局统一样式（颜色主题、字体、按钮、输入框、表格、分页器等基础规范）见 [page_patterns.md](../../.claude/rules/page_patterns.md) 第1节「全局统一样式」。

---

## 1. 整体布局

项目有三种布局模式：

**模式一：独立页面**（如 KnowledgeBase、AllSessions）
- 自己渲染 `MenuSidebar` + `AppHeader` 组合
- 参考 `frontend_dev.md` 中的"场景二：独立页面"

**模式二：PortalLayout 子页面**（租户前台 `/t/:tenant_id/*`）
- 由 `PortalLayout` 提供 `MenuSidebar`，页面只需 `AppHeader` + 内容区
- 参考 `frontend_dev.md` 中的"场景一：PortalLayout 子页面"

**模式三：BaseBusinessLayout 子页面**（业务数据管理页面）
- 用于子智能体的业务数据 CRUD 页面（如 `VehicleManager`）
- 由 `BaseBusinessLayout` 提供顶部导航，页面只需内容区
- 适用于不需要侧边栏的独立业务管理场景

- **宽度**：尽量利用屏幕宽度。如果业务数据列很多，允许出现横向滚动条。
- **高度**：内容区使用 `flex-1 overflow-y-auto`，利用 flex 布局自动填充剩余空间。

> **经验总结**：确保分页器固定在页面底部，需要整条布局链正确设置 flex 样式。详见本文档「6. 分页器固定底部的布局链要求」。

### 公共 CSS 类

列表页布局、搜索区、表格滚动条、表单字段、弹框表单等通用样式已提取到 `frontend/src/styles/page-common.css`（已在 `style.css` 中全局引入）：

| 类名 | 用途 |
|------|------|
| `.page-container` | 列表页顶层容器（`h-full flex flex-col`） |
| `.page-content` | 列表页内容区（`flex-1 flex flex-col min-h-0`） |
| `.page-toolbar` | 搜索区 + 操作按钮区 |
| `.page-toolbar-left` / `.page-toolbar-right` | 工具栏左右分区 |
| `.table-scroll-wrapper` | 表格水平滚动容器（含自定义滚动条） |
| `.form-label` / `.form-required` / `.form-field` | 表单字段标签、必填标记、字段组 |
| `.form-grid-2` / `.form-grid-3` / `.form-grid-4` | 多列表单网格 |
| `.form-section` | 表单字段垂直间距 |
| `.modal-form` / `.modal-form-row` / `.modal-form-full` | 弹框内表单布局 |
| `.text-ellipsis` / `.flex-spacer` | 文字截断 / 弹性间距 |
| `.col-id` / `.col-actions-sm` / `.col-actions-md` / `.col-actions-lg` | 列宽快捷类 |
| `.empty-state` / `.empty-state-icon` | 空状态 |
| `.table-checkbox` | 复选框列样式 |

---

## 2. 顶部区域（搜索区 + 操作按钮区）

**搜索区**：
- 使用 `BaseInput`（`size="sm"`），宽度 `300px`（`w-80`），右侧有搜索按钮（`BaseButton`）。
- 在搜索输入框中按下回车键触发搜索（绑定 `@keyup.enter`）。
- 搜索区避免超过 3 个筛选字段。
- 搜索区与操作按钮区之间保持适当间距。

**操作按钮区**：
- "新增"使用 `primary` 类型，其他按钮使用 `secondary` 类型。
- 表格内的"删除"操作使用 `intent="danger-ghost"`（灰底红字），避免红色背景过于醒目。
- 按钮尺寸使用 `md`（默认）。

---

## 3. 列表页宽度和高度

**宽度规范**：
- 列表页宽度应尽量利用视口宽度，除非确定内容很少，否则不要设置 `600px` 或 `800px` 等宽度限制。
- 表格允许出现横向滚动条（当列较多时）。

**高度规范**：
- 列表页高度应撑满一屏而不出现纵向滚动条。
- 经验值：一页 **20 行**，每行行高 **32px**，在 1920×1080 的屏幕上显示效果较好。
- 当业务需要行高必须超过 32px（如包含多行文本、复杂内容）时，可以默认 **一页 10 行**。

---

## 4. 表格列规范

### 序号列

- 列宽：`60px`。
- 列头："序号"。
- 序号值由前端计算：有分页时 `(currentPage - 1) * pageSize + index + 1`，无分页时 `index + 1`。

### 操作列

- 列宽：`100px~200px`（根据操作按钮数量调整，2个按钮、每个按钮2个字时，建议列宽 `120px`）。
- 列头："操作"，水平居中。
- 放在表格**最后一列**。
- 操作按钮：使用 `BaseButton` 的 `ghost`（默认）+ `sm` 类型；加 `whitespace-nowrap` 以便防换行；按钮文字，添加 `text-xs` 类；设置水平居中。
- 删除按钮使用 `intent="danger-ghost"`（灰底红字），避免红色背景过于醒目；其他操作按钮使用 `ghost`。
- "编辑"和"详情"一般不同时出现，只读场景仅保留"详情"。

### 数据列

- 列头：`text-xs font-medium text-muted uppercase tracking-wider`（BaseTable 内置）。
- 单元格：`text-sm text-default`（BaseTable 内置）。
- 文字超出时显示省略号（`truncate` 类）。
- 所有字段，鼠标悬停，都要显示完整文字内容。

---

## 5. 分页器

- 位于列表页底部，使用 `BasePagination` 组件。
- 分页器应固定居于页面底部，水平居中。
  - **实现方式**：配合 `.page-content` 的 `flex-1 flex flex-col min-h-0` 布局，分页器会自动被推到 flex 容器的底部。
  - **示意**：外层容器使用 `page-container`（`h-full flex flex-col`），内容区使用 `page-content`（`flex-1 flex flex-col min-h-0`），此时分页器在内容区底部自然固定。

---

## 6. 分页器固定底部的布局链要求

分页器固定在页面底部，需要**整条布局链**（从外层布局到页面容器）全部正确设置 flex 样式。以下是各层级的具体要求：
**关键点**：`min-h-0` 是 flex 子元素能收缩的必要条件，缺少它会导致子元素无法被压缩，分页器无法被推到容器底部。

### 常见问题排查

| 现象 | 常见原因 | 解决方案 |
|------|----------|----------|
| 分页器不在底部，而是跟随表格内容滚动 | `.table-scroll-wrapper` 缺少 `overflow-y: auto` | 添加 `overflow-y: auto` |
| 分页器被推到屏幕外 | 父容器使用了 `min-h-screen` 而非 `h-full` | 改为 `h-full` |
| 内容区不能收缩，表格占满屏幕 | 页面根容器使用了 `min-h-screen` | 改为 `h-full`，并确保父级有固定高度 |
| 表格区域不滚动，分页器被挤出视口 | `.table-scroll-wrapper` 缺少 `min-height: 0` | 添加 `min-height: 0` |

---

## 7. 公共 Composable：`usePageContext`

列表页的分页序号计算、搜索、刷新等通用逻辑已封装为 `usePageContext` composable（`frontend/src/composables/usePageContext.ts`）：

```ts
import { usePageContext } from '@/composables/usePageContext'

const items = ref<Item[]>([])
const total = ref(0)
const { currentPage, pageSize, seqNumber, handleSearch, refresh } =
  usePageContext(async () => {
    const res = await api.list({ page: currentPage.value, pageSize: pageSize.value })
    items.value = res.items
    total.value = res.total
  })

// 模板中：序号列使用 seqNumber(index)
// 搜索：@keyup.enter="handleSearch(keyword)"
// 分页切换：handlePageChange(page) / handlePageSizeChange(size)
```

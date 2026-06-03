# 页面规范

本项目有多个列表页和详情页，例如：

- 管理后台，租户管理 `/portal/tenants`
- 租户前台，用户管理 `/t/{tenant_id}/users`
- 租户前台，企业知识库 `/t/{tenant_id}/knowledge`
- 租户前台，旅游咨询顾问 - 车辆价格 `/t/{tenant_id}/travel-consultant/vehicles`

上述列表页上，每条记录有"编辑"按钮，点击后可进入详情页。

在具体实现上，很多细节不统一、不美观，还有很多功能性的缺漏，所以制定本规范文件，提供统一样式、列表页、详情页的实现标准。

---

## 1. 全局统一样式

### 1.1 颜色主题

本项目使用 `useTheme.ts` 的主题系统，用户可在"商务蓝"、"石墨灰"、"墨松绿"、"紫檀红"、"翠竹绿"、"凌霄紫"、"旭日橙"、"牡丹红"等预设主题间切换。主题色以 CSS 变量形式注入，Tailwind 配置已将 CSS 变量映射为 `primary-*`、`success-*` 等语义类名，**开发时直接使用 Tailwind 类名即可，不需要手动写 `var(...)`**。

| 用途 | Tailwind 类名 | 说明 |
|------|-------------|------|
| 主题主色（主按钮、激活态） | `bg-primary-600` `text-primary-600` | 跟随用户所选主题 |
| 主题浅色（悬浮/高亮背景） | `bg-primary-50` | 用于表格行悬浮 |
| 主题边框 | `border-primary-200` | 用于输入框、卡片等边框 |
| 操作成功/通过 | `bg-success-*` `text-success-*` | 绿色，成功状态 |
| 操作警告 | `bg-warning-*` `text-warning-*` | 黄色，警告状态 |
| 操作危险/删除 | `bg-danger-*` `text-danger-*` | 红色，危险/删除状态 |
| 信息提示 | `bg-info-*` `text-info-*` | 天蓝，信息状态 |
| 中性灰 | `bg-gray-*` `text-gray-*` | 所有主题固定不变 |

语义化 Token（在 `tailwind.config.js` 中扩展）：

| 用途 | Tailwind 类名 | 说明 |
|------|-------------|------|
| 页面背景 | `bg-canvas` | 极浅灰页面底色 |
| 卡片/面板背景 | `bg-surface` | 白色 |
| 行/元素悬浮背景 | `bg-surface-hover` | 悬浮态浅色背景 |
| 正文文字 | `text-default` | 深灰色 |
| 次要文字 | `text-muted` | 灰色辅助文字 |
| 反色文字 | `text-inverse` | 深色背景上的浅色文字 |
| 默认边框 | `border-default` | 浅灰色边框 |
| 悬浮边框 | `border-hover` | 悬浮态边框 |

### 1.2 字体规范

字体族通过 Tailwind 的 `fontFamily.sans` 配置，自动应用于所有元素：

```
-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
"Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Helvetica, Arial, sans-serif
```

| 用途 | 字号 | 字重 | 行高 | Tailwind 类名 |
|------|------|------|------|-------------|
| 页面标题 | `18px` | `600` | `1.5` | `text-lg font-semibold` |
| 表格列头 | `14px` | `600` | `1.4` | `text-sm font-semibold` |
| 正文/表格单元格 | `14px` | `400` | `1.5` | `text-sm` |
| 辅助文字/标签 | `12px` | `400` | `1.4` | `text-xs` |
| 输入框/选择器文字 | `14px` | `400` | — | `text-sm`（BaseInput 内置） |
| 按钮文字 | `14px` | `500` | — | `font-medium`（BaseButton 内置） |

### 1.3 按钮规范

统一使用 `BaseButton` 组件，通过 `intent` 和 `size` 控制样式。**禁止自行定义按钮 CSS 类。**

#### 尺寸规格（与 BaseButton 一致）

| 尺寸 | Tailwind | 高度 | 左右内边距 | 字号 | 适用场景 |
|------|----------|------|-----------|------|---------|
| 小按钮（`sm`） | `h-8 px-3` | `32px` | `12px` | `14px` | 表格内操作 |
| 中按钮（`md`，默认） | `h-10 px-4` | `40px` | `16px` | `14px` | 页面操作按钮 |
| 大按钮（`lg`） | `h-12 px-6` | `48px` | `24px` | `16px` | 主 CTA |

#### 颜色类型（与 BaseButton 一致）

| 类型 | 用途 | 说明 |
|------|------|------|
| `primary` | 主要操作（保存、新增、搜索） | 主题色背景，白色文字 |
| `secondary` | 次要操作（取消、返回） | 灰色背景，灰色文字 |
| `danger` | 危险操作（删除） | 红色背景，白色文字 |
| `ghost` | 文字按钮（表格内"编辑"） | 透明背景，灰色文字，悬浮显示背景 |

使用示例：

```vue
<BaseButton @click="save">保存</BaseButton>
<BaseButton intent="secondary" @click="cancel">取消</BaseButton>
<BaseButton intent="danger" size="sm" @click="del(row)">删除</BaseButton>
<BaseButton intent="ghost" size="sm" @click="edit(row)">编辑</BaseButton>
```

### 1.4 输入框/选择器规范

统一使用 `BaseInput` / `BaseSelect` 组件。

#### BaseInput/BaseSelect 尺寸（与组件一致）

| 尺寸 | Tailwind | 高度 | 字号 |
|------|----------|------|------|
| 小（`sm`） | `h-8` | `32px` | `12px` |
| 中（`md`，默认） | `h-10` | `40px` | `14px` |
| 大（`lg`） | `h-12` | `48px` | `16px` |

搜索输入框推荐宽度 `300px`（`max-w-[300px]` 或 `w-80`）。

输入框状态：
- `default`：默认边框 `border-default`
- `error`：红色边框 + 红色聚焦环
- `success`：绿色边框

文本域（textarea）：项目暂无 BaseTextarea 组件，暂用原生 `<textarea>` 并复用输入框的 Tailwind 类名。

### 1.5 复选框规范

项目暂无 BaseCheckbox 组件，暂用原生 `<input type="checkbox">` 并应用以下样式类：

```
w-3.5 h-3.5 rounded border-primary-200 text-primary-600
focus:ring-primary-500
```

### 1.6 滚动条样式

滚动条样式已提取到公共样式文件 `frontend/src/styles/page-common.css`，使用 `.table-scroll-wrapper` 类即可：

```vue
<div class="table-scroll-wrapper">
  <BaseTable :columns="columns" :data="data" />
</div>
```

### 1.7 表格规范

统一使用 `BaseTable` 组件。**禁止自行定义表格 CSS 类。**

| 属性 | Tailwind 类名（已由 table variant 内置） |
|------|----------------------------------------|
| 容器 | `w-full overflow-x-auto rounded-lg border border-default` |
| 列头背景 | `bg-gray-50` |
| 列头文字 | `text-xs text-muted font-medium uppercase tracking-wider` |
| 行悬浮 | `hover:bg-surface-hover transition-colors` |
| 单元格内边距 | `px-4 py-3` |
| 空状态 | `text-center text-muted py-12` |

### 1.8 分页器规范

统一使用 `BasePagination` 组件。

| 属性 | 说明 |
|------|------|
| 页码按钮 | `h-8 w-8 rounded-lg`，当前页 `bg-primary-600 text-white` |
| 信息文字 | 左侧显示 "显示 X-Y 条，共 Z 条" |

> **待增强**：当前 BasePagination 不支持每页行数选择器（10/20/50/100）。如需此功能，需扩展 BasePagination 组件。

---

## 2. 列表页规范要求

### 2.1 整体布局

项目有两种布局模式：

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

#### 公共 CSS 类

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

### 2.2 顶部区域（搜索区 + 操作按钮区）

**搜索区**：
- 使用 `BaseInput`（`size="sm"`），宽度 `300px`（`w-80`），右侧有搜索按钮（`BaseButton`）。
- 在搜索输入框中按下回车键触发搜索（绑定 `@keyup.enter`）。
- 搜索区避免超过 3 个筛选字段。
- 搜索区与操作按钮区之间保持适当间距。

**操作按钮区**：
- "新增"使用 `primary` 类型，"删除"使用 `danger` 类型，其他按钮使用 `secondary` 类型。
- 按钮尺寸使用 `md`（默认）。

### 2.3 表格列规范

#### 序号列

- 列宽：`60px`。
- 列头："序号"。
- 序号值由前端计算：有分页时 `(currentPage - 1) * pageSize + index + 1`，无分页时 `index + 1`。

#### 操作列

- 列宽：`100px~160px`（根据操作按钮数量调整）。
- 列头："操作"。
- 放在表格**最后一列**。
- 内容：使用 `BaseButton` 的 `ghost` + `sm` 类型。
- "编辑"和"详情"一般不同时出现，只读场景仅保留"详情"。

#### 数据列

- 列头：`text-xs font-medium text-muted uppercase tracking-wider`（BaseTable 内置）。
- 单元格：`text-sm text-default`（BaseTable 内置）。
- 文字超出时显示省略号（`truncate` 类）。

### 2.4 分页器

- 位于列表页底部，使用 `BasePagination` 组件。
- 分页器与表格之间保留适当间距。

#### 公共 Composable：`usePageContext`

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

---

## 3. 详情页规范要求

新增页、编辑页、详情页，在页面规范上一致，统一在本节中说明。

### 3.1 弹框形式

使用 `BaseModal` 组件：

```vue
<BaseModal v-model="showModal" :title="editingItem ? '编辑XX' : '新增XX'" size="lg">
  <!-- 表单内容 -->
  <template #footer>
    <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
    <BaseButton @click="handleSave">保存</BaseButton>
  </template>
</BaseModal>
```

**关闭弹框脏检测**：已封装为 `useModalCloseGuard` composable（`frontend/src/composables/useModalCloseGuard.ts`）：

```ts
import { useModalCloseGuard } from '@/composables/useModalCloseGuard'

const form = ref({ name: '', age: 0 })
const {
  showModal, showConfirm, confirmMessage,
  openModal, handleOverlayClick, handleCloseClick,
  confirmSave, confirmDiscard, confirmCancel
} = useModalCloseGuard(form, () => ({ ...form.value }), async () => { await save() })

// 在 BaseModal 上绑定：
// <BaseModal v-model="showModal" :closeOnOverlay="false" @overlay-click="handleOverlayClick">
//   关闭按钮 @click="handleCloseClick"
//   确认弹框三个按钮：confirmSave / confirmDiscard / confirmCancel
```

逻辑说明：
1. 点击遮罩层或关闭按钮时，判断是否有未保存的修改
2. 无修改：直接关闭
3. 有修改：弹出确认对话框（保存 / 不保存 / 取消）
4. 关闭弹框后，列表页自动刷新

### 3.2 弹框按钮

| 场景 | 按钮 |
|------|------|
| 编辑/新增 | "取消"（`secondary`） + "保存"（`primary`） |
| 详情查看 | "关闭"（`secondary`） + "编辑"（仅对有编辑权限的用户显示） |

**禁止只放"保存"按钮而无"取消"/"关闭"按钮。**

### 3.3 弹框尺寸

BaseModal 支持以下尺寸（通过 `size` prop）：

| size | 宽度 | 适用场景 |
|------|------|---------|
| `sm` | `max-w-sm`（384px） | 简单确认对话框 |
| `md`（默认） | `max-w-lg`（512px） | 少于 6 个字段的表单 |
| `lg` | `max-w-2xl`（672px） | 6~12 个字段的表单 |
| `xl` | `max-w-4xl`（896px） | 内容较多的详情页 |

弹框内容区默认 `max-h-[70vh]` 并带纵向滚动条（`scrollable=true`）。

### 3.4 表单字段样式

- 字段标签（Label）：使用 `text-sm text-muted mb-1 block`。
- 必填字段：标签后添加 `<span class="text-danger-500">*</span>`。
- 字段之间的垂直间距：`16px`（`gap-4` 或 `mt-4`）。
- 两个字段同行：使用 `<div class="grid grid-cols-2 gap-4">`。

### 3.5 表单验证

- 保存前验证必填字段，验证失败时 `alert` 提示。
- 输入框错误状态使用 `BaseInput` 的 `state="error"`。

---

## 4. 手机端支持

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

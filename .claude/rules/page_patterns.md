# 前端页面模式规范

> 本文档定义了项目中**所有页面类型**的统一 UI 模式速查（列表页、表单页、详情页、统计页、设置页）。所有新页面必须遵循此规范。
> CSS 方案统一为 Tailwind + 语义 token，使用 `variants/` 变体系统和 `components/ui/` 基础组件。
>
> **管辖范围**：本文档是全局页面模式的**要点速查**，覆盖所有页面类型。其中**列表页**的详细实现规范由 [list-page-convention.md](./list-page-convention.md) 负责，**详情页**的详细实现规范由 [detail-page-convention.md](./detail-page-convention.md) 负责，本文档仅列要点并引用之。
>
> 全局统一样式（颜色主题、字体、按钮、输入框、表格、分页器等基础规范）在本文件第1节。

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
| 普通按钮文字 | `14px` | `500` | — | `font-medium`（BaseButton 内置） |
| 表格内按钮文字 | `12px` | `500` | — | `font-medium`（BaseButton 内置） |

### 1.3 按钮规范

统一使用 `BaseButton` 组件，通过 `intent` 和 `size` 控制样式。**禁止自行定义按钮 CSS 类。**

#### 尺寸规格

| 尺寸 | Tailwind | 高度 | 左右内边距 | 字号 | 适用场景 |
|------|----------|------|-----------|------|---------|
| 小按钮（`sm`） | `h-8 px-3` | `32px` | `12px` | `14px` | 表格内操作 |
| 中按钮（`md`，默认） | `h-10 px-4` | `40px` | `16px` | `14px` | 页面操作按钮 |
| 大按钮（`lg`） | `h-12 px-6` | `48px` | `24px` | `16px` | 主 CTA |

#### 颜色类型

| 类型 | 用途 | 说明 |
|------|------|------|
| `primary` | 主要操作（保存、新增、搜索） | 主题色背景，白色文字 |
| `secondary` | 次要操作（取消、返回） | 灰色背景，灰色文字 |
| `danger` | 危险操作（删除） | 红色背景，白色文字 |
| `ghost` | 文字按钮（表格内"编辑"） | 透明背景，灰色文字，悬浮显示背景 |

### 1.4 输入框/选择器规范

统一使用 `BaseInput` / `BaseSelect` 组件。

#### 尺寸

| 尺寸 | Tailwind | 高度 | 字号 |
|------|----------|------|------|
| 小（`sm`） | `h-8` | `32px` | `12px` |
| 中（`md`，默认） | `h-10` | `40px` | `14px` |
| 大（`lg`） | `h-12` | `48px` | `16px` |

搜索输入框推荐宽度 `300px`（`max-w-[300px]` 或 `w-80`）。

输入框状态：`default`（默认边框）、`error`（红色边框 + 红色聚焦环）、`success`（绿色边框）。

文本域：项目暂无 BaseTextarea 组件，暂用原生 `<textarea>` 并复用输入框的 Tailwind 类名。

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

### 1.9 手机端支持

| 页面类型 | 是否需要手机端支持 |
|---------|------------------|
| 管理后台页面（平台管理员使用） | 不需要 |
| 租户前台"管理菜单"下的功能页面（租户管理员使用） | 不需要 |
| 租户前台普通用户使用的功能（对话界面、各智能体业务数据页面） | 需要 |

手机端适配参考响应式断点：`max-width: 768px`（平板及手机），`max-width: 480px`（手机）。

---

## 2. 列表页规范

> 本节为要点速查。列表页的详细实现规范（CSS 公共类名、`usePageContext` Composable、布局模式、搜索区/表格/分页器完整规范等）见 [list-page-convention.md](./list-page-convention.md)。

### 2.1 布局

列表页必须遵循标准页面布局（见 `frontend_dev.md`）：
- 使用 `AppHeader` 作为顶部标题栏
- 支持三种布局模式：独立页面、PortalLayout 子页面、BaseBusinessLayout 子页面
- **禁止**自行实现标题栏替代 `AppHeader`

### 2.2 表格

使用 `BaseTable` 组件：

```vue
<BaseTable :columns="columns" :data="tableData" row-key="id">
  <template #actions="{ row }">
    <BaseButton intent="ghost" size="sm" @click="openEdit(row)">编辑</BaseButton>
    <BaseButton intent="ghost" size="sm" @click="handleDelete(row)">删除</BaseButton>
  </template>
  <template #empty>暂无数据</template>
</BaseTable>
```

**禁止**自行定义 `.data-table` 等 CSS 类，统一使用 `BaseTable`。

### 2.3 序号列

| 规则 | 说明 |
|------|------|
| 位置 | 表格第一列，表头为"序号" |
| 计算 | 无分页：`{{ index + 1 }}`；有分页：`{{ (currentPage - 1) * pageSize + index + 1 }}` |
| 宽度 | 固定窄列（如 `width: '60px'`） |

### 2.4 筛选/搜索

- 搜索区位于页面内容区顶部，在表格上方
- 使用 `BaseInput`（`size="sm"`，宽度 `300px`），支持回车触发搜索
- 搜索区避免超过 3 个筛选字段
- 所有颜色使用语义 token（`border-default`、`text-muted` 等）

### 2.5 操作按钮

- 新增按钮放在搜索区右侧，使用 `BaseButton`（`primary`，`md`）
- 行内操作按钮放在表格最后一列"操作"列，统一使用 `ghost` + `sm`
- 操作按钮使用文字标签，不使用图标
- 按钮组间距 `gap-2`

### 2.6 分页

- 数据量超过一页时使用 `BasePagination` 组件
- 分页控件放在表格下方

```vue
<BasePagination
  :total="total"
  v-model:current-page="currentPage"
  :page-size="pageSize"
/>
```

---

## 3. 编辑页/新增页/详情页规范

> 本节为要点速查。详情页的详细实现规范（BaseModal 尺寸选择、表单字段样式、关闭弹框脏检测 `useModalCloseGuard`、表单验证等）见 [detail-page-convention.md](./detail-page-convention.md)。

### 3.1 展示模式

使用 `BaseModal` 组件：

```vue
<BaseModal v-model="showModal" title="编辑XX" size="lg">
  <!-- 表单内容 -->
  <template #footer>
    <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
    <BaseButton @click="handleSave">保存</BaseButton>
  </template>
</BaseModal>
```

**禁止**自行定义 `.modal-overlay`、`.modal-content` 等 CSS 类。

### 3.2 弹窗按钮

| 场景 | 按钮 |
|------|------|
| 编辑/新增 | "取消"（`intent="secondary"`） + "保存"（默认 primary） |
| 详情查看 | "关闭"（`intent="secondary"`） + "编辑"（仅对有编辑权限的用户显示） |

**禁止**只放"保存"按钮而无"取消"/"关闭"按钮。

### 3.3 表单布局

- 使用 `BaseInput` 和 `BaseSelect` 组件
- label 显示在输入框上方，样式：`text-sm text-muted mb-1 block`
- 必填字段在 label 后加红色星号 `<span class="text-danger-500">*</span>`
- 两个字段同行：使用 `<div class="grid grid-cols-2 gap-4">` 包裹

### 3.4 表单验证

- 必填字段使用 `*` 标记
- 保存前验证必填字段，验证失败时 `alert` 提示
- 输入框错误状态使用 `state="error"`

### 3.5 弹窗最大高度

`BaseModal` 默认 `scrollable=true`，body 区域 `max-h-[70vh]`。

---

## 4. 按钮样式

统一使用 `BaseButton` 组件，通过 `intent` 和 `size` 控制样式：

```vue
<BaseButton>主按钮</BaseButton>
<BaseButton intent="secondary">次要按钮</BaseButton>
<BaseButton intent="danger">危险按钮</BaseButton>
<BaseButton intent="ghost">幽灵按钮</BaseButton>
<BaseButton size="sm">小按钮</BaseButton>
```

**禁止**自行定义 `.btn-primary`、`.btn-secondary`、`.btn-danger` 等 CSS 类。

---

## 5. 徽章/标签

使用 `BaseBadge` 组件：

```vue
<BaseBadge intent="success">已激活</BaseBadge>
<BaseBadge intent="danger">已禁用</BaseBadge>
<BaseBadge intent="warning">待审核</BaseBadge>
<BaseBadge intent="info">进行中</BaseBadge>
```

---

## 6. 卡片容器

使用 `BaseCard` 组件：

```vue
<BaseCard title="卡片标题">
  <p>内容区域</p>
  <template #footer>
    <BaseButton size="sm">操作</BaseButton>
  </template>
</BaseCard>
```

---

## 7. 统计页规范

统计页使用卡片布局展示关键指标：

```vue
<div class="grid grid-cols-4 gap-4">
  <BaseCard>
    <div class="text-muted text-sm">总用户数</div>
    <div class="text-2xl font-bold text-default mt-1">1,234</div>
  </BaseCard>
</div>
```

---

## 8. 设置页规范

设置页使用卡片分组管理配置项：

```vue
<BaseCard title="基本设置">
  <div class="space-y-4">
    <div>
      <label class="text-sm text-muted mb-1 block">配置项名称</label>
      <BaseInput v-model="config.name" />
    </div>
  </div>
  <template #footer>
    <BaseButton @click="saveSettings">保存设置</BaseButton>
  </template>
</BaseCard>
```

---

## 9. 确认对话框

- 删除等危险操作使用浏览器原生 `confirm()`
- 格式：`confirm('确定删除XX？')`

---

## 10. 颜色使用规范

**所有颜色必须使用语义 token，禁止硬编码颜色值。**

### 语义 token 列表

| 用途 | Token | 示例 |
|------|-------|------|
| 主色调 | `primary-*` | `bg-primary-600`, `text-primary-700` |
| 成功 | `success-*` | `bg-success-100`, `text-success-700` |
| 警告 | `warning-*` | `bg-warning-100`, `text-warning-700` |
| 危险 | `danger-*` | `bg-danger-600`, `text-danger-500` |
| 信息 | `info-*` | `bg-info-100`, `text-info-700` |
| 页面背景 | `bg-canvas` | 灰色页面背景 |
| 卡片背景 | `bg-surface` | 白色面板背景 |
| 悬停背景 | `bg-surface-hover` | 行悬停背景 |
| 主文字 | `text-default` | 正文文字 |
| 次要文字 | `text-muted` | 辅助文字 |
| 边框 | `border-default` | 默认边框 |
| 悬停边框 | `border-hover` | 悬停边框 |

### 禁止项

| 禁止 | 替代 |
|------|------|
| `slate-*` | `gray-*` / 语义 token |
| `cyan-*` | `primary-*` |
| `red-*` | `danger-*` |
| `green-*` | `success-*` |
| `blue-*` | `info-*` / `primary-*` |
| `orange-*` | `warning-*` |
| 自定义 CSS 变量 `var(--bg-primary)` 等 | 使用语义 token |

---

## 11. 已知不一致项及统一规则

| 项目 | 统一规则 |
|------|----------|
| 序号列 | **必须有**（见 2.3） |
| 编辑弹窗按钮 | 编辑/新增用"取消"+ "保存"；详情用"关闭"+"编辑"（见 3.2） |
| CSS 方案 | 统一使用 Tailwind + 语义 token，使用 Base* 组件 |
| 卡片列表 vs 表格 | 数据列表优先用 BaseTable；卡片仅用于特殊场景 |
| 行内编辑 vs 弹窗编辑 | 简单 CRUD 用 BaseModal；复杂多步骤编辑可用行内面板 |

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

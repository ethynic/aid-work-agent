# 前端页面模式规范

> 本文档定义了项目中所有页面的统一 UI 模式。所有新页面必须遵循此规范。
> CSS 方案统一为 Tailwind + 语义 token，使用 `variants/` 变体系统和 `components/ui/` 基础组件。

---

## 页面模式总览

| 模式 | 适用场景 | 核心组件 |
|------|---------|---------|
| 列表页 | 数据表格管理 | BaseTable + BasePagination + BaseButton |
| 表单页（模态） | 新增/编辑 | BaseModal + BaseInput + BaseSelect |
| 详情页（模态） | 只读查看 | BaseModal（无编辑控件） |
| 统计页 | 数据概览 | BaseCard + BaseBadge |
| 设置页 | 配置管理 | BaseCard + BaseInput + BaseButton |

---

## 1. 列表页规范

### 1.1 布局

列表页必须遵循标准页面布局（见 `frontend_dev.md`）：
- 使用 `AppHeader` 作为顶部标题栏
- 页面内容区由 `PortalLayout` 子页面或独立页面模式决定
- **禁止**自行实现标题栏替代 `AppHeader`

### 1.2 表格

使用 `BaseTable` 组件：

```vue
<BaseTable :columns="columns" :data="tableData" row-key="id">
  <template #actions="{ row }">
    <BaseButton intent="ghost" size="sm" @click="openEdit(row)">编辑</BaseButton>
    <BaseButton intent="danger" size="sm" @click="handleDelete(row)">删除</BaseButton>
  </template>
  <template #empty>暂无数据</template>
</BaseTable>
```

**禁止**自行定义 `.data-table` 等 CSS 类，统一使用 `BaseTable`。

### 1.3 序号列

| 规则 | 说明 |
|------|------|
| 位置 | 表格第一列，表头为"序号" |
| 计算 | 无分页：`{{ index + 1 }}`；有分页：`{{ (currentPage - 1) * pageSize + index + 1 }}` |
| 宽度 | 固定窄列（如 `width: '60px'`） |

### 1.4 筛选/搜索

- 筛选条件放在表头右侧（标题旁边），使用 `BaseSelect` 或 `BaseInput`
- 支持搜索时，搜索框放在筛选区域最前面
- 所有颜色使用语义 token（`border-default`、`text-muted` 等）

### 1.5 操作按钮

- 新增按钮放在筛选区域最右侧，使用 `BaseButton`
- 行内操作按钮放在表格最后一列"操作"列
- 操作按钮使用文字标签，不使用图标
- 删除按钮使用 `intent="danger"`
- 按钮组间距 `gap-2`

### 1.6 分页

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

## 2. 编辑页/新增页/详情页规范

### 2.1 展示模式

使用 `BaseModal` 组件：

```vue
<BaseModal v-model="showModal" title="编辑XX" size="md">
  <!-- 表单内容 -->
  <template #footer>
    <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
    <BaseButton @click="handleSave">保存</BaseButton>
  </template>
</BaseModal>
```

**禁止**自行定义 `.modal-overlay`、`.modal-content` 等 CSS 类。

### 2.2 弹窗按钮

| 场景 | 按钮 |
|------|------|
| 编辑/新增 | "取消"（`intent="secondary"`） + "保存"（默认 primary） |
| 详情查看 | "关闭"（`intent="secondary"`） + "编辑"（仅对有编辑权限的用户显示） |

**禁止**只放"保存"按钮而无"取消"/"关闭"按钮。

### 2.3 表单布局

- 使用 `BaseInput` 和 `BaseSelect` 组件
- label 显示在输入框上方，样式：`text-sm text-muted mb-1 block`
- 必填字段在 label 后加红色星号 `<span class="text-danger-500">*</span>`
- 两个字段同行：使用 `<div class="grid grid-cols-2 gap-4">` 包裹

### 2.4 表单验证

- 必填字段使用 `*` 标记
- 保存前验证必填字段，验证失败时 `alert` 提示
- 输入框错误状态使用 `state="error"`

### 2.5 弹窗最大高度

`BaseModal` 默认 `scrollable=true`，body 区域 `max-h-[70vh]`。

---

## 3. 按钮样式

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

## 4. 徽章/标签

使用 `BaseBadge` 组件：

```vue
<BaseBadge intent="success">已激活</BaseBadge>
<BaseBadge intent="danger">已禁用</BaseBadge>
<BaseBadge intent="warning">待审核</BaseBadge>
<BaseBadge intent="info">进行中</BaseBadge>
```

---

## 5. 卡片容器

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

## 6. 统计页规范

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

## 7. 设置页规范

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

## 8. 确认对话框

- 删除等危险操作使用浏览器原生 `confirm()`
- 格式：`confirm('确定删除XX？')`

---

## 9. 颜色使用规范

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

## 10. 已知不一致项及统一规则

| 项目 | 统一规则 |
|------|----------|
| 序号列 | **必须有**（见 1.3） |
| 编辑弹窗按钮 | 编辑/新增用"取消"+ "保存"；详情用"关闭"+"编辑"（见 2.2） |
| CSS 方案 | 统一使用 Tailwind + 语义 token，使用 Base* 组件 |
| 卡片列表 vs 表格 | 数据列表优先用 BaseTable；卡片仅用于特殊场景 |
| 行内编辑 vs 弹窗编辑 | 简单 CRUD 用 BaseModal；复杂多步骤编辑可用行内面板 |

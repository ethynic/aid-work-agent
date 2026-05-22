# 前端页面模式规范

## 1. 列表页规范

### 1.1 布局

列表页必须遵循标准页面布局（见 `frontend_dev.md`）：
- 使用 `AppHeader` 作为顶部标题栏
- 页面内容区由 `PortalLayout` 子页面或独立页面模式决定
- **禁止**自行实现标题栏替代 `AppHeader`

### 1.2 表格

| 规则 | 说明 |
|------|------|
| 元素 | 使用原生 `<table>` 元素，不使用 `el-table` |
| 样式 | 优先使用自定义 scoped CSS（如 `.data-table`），保持统一风格 |
| 表头 | `th` 背景色 `#f5f7fa`，`font-weight: 600` |
| 行交互 | `tr:hover` 背景色 `#fafbfc` |
| 空状态 | 显示"暂无数据"，居中 |
| 加载状态 | 显示"加载中..."，居中 |

### 1.3 序号列

| 规则 | 说明 |
|------|------|
| 位置 | 表格第一列，表头为"序号" |
| 计算 | 无分页：`{{ index + 1 }}`；有分页：`{{ (currentPage - 1) * pageSize + index + 1 }}` |
| 宽度 | 固定窄列（如 `w-16` / `60px`） |

### 1.4 筛选/搜索

- 筛选条件放在表头右侧（标题旁边），使用 `<select>` 或 `<input>`
- 支持搜索时，搜索框放在筛选区域最前面
- 筛选组件样式：`padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px`

### 1.5 操作按钮

- 新增按钮放在筛选区域最右侧，格式：`+ 新增XX`
- 行内操作按钮放在表格最后一列"操作"列
- 操作按钮使用文字标签，不使用图标（如"编辑"、"删除"），避免 tooltip 依赖
- 删除按钮使用红色样式（`.btn-danger`）
- 按钮组间距 `gap: 10px`

### 1.6 分页

- 数据量超过一页时使用分页
- 分页组件包含：首页、上一页、页码、下一页、末页
- 分页控件放在表格下方右侧
- 显示总条数：`共 X 条`

### 1.7 操作列模板

```vue
<td class="actions-cell">
  <button class="btn-sm" @click="openEdit(item)">编辑</button>
  <button class="btn-sm btn-danger" @click="handleDelete(item)">删除</button>
</td>
```

---

## 2. 编辑页/新增页/详情页规范

### 2.1 展示模式

编辑/新增/详情统一使用**弹窗模式**：
- 弹窗使用 `v-if="showModal"` 控制显示
- 遮罩层：固定定位，半透明黑色背景（`rgba(0,0,0,0.4)`）
- 弹窗内容：白色圆角（`rounded-lg` / `rounded-xl`），最大宽度 `600px` 或 `90vw`
- 点击遮罩关闭弹窗：`@click.self="showModal = false"`

### 2.2 弹窗按钮

| 场景 | 按钮 |
|------|------|
| 编辑/新增 | "取消" + "保存"（右侧对齐） |
| 详情查看 | "关闭" + "编辑"（右侧对齐，仅对有编辑权限的用户显示"编辑"） |

**禁止**只放"保存"按钮而无"取消"/"关闭"按钮。

### 2.3 表单布局

- 表单使用 `<div class="form-group">` 组织，每组包含 `<label>` + `<input/select/textarea>`
- 两个字段同行：使用 `<div class="form-row">` 包裹两个 `form-group`
- label 显示在输入框上方，`display: block; margin-bottom: 4px; font-size: 13px; color: #555`
- 输入框统一样式：`width: 100%; padding: 7px 10px; border: 1px solid #ddd; border-radius: 4px`
- 必填字段在 label 后加红色星号 `*`

### 2.4 弹窗底部操作栏

```vue
<div class="modal-actions">
  <button class="btn-secondary" @click="showModal = false">取消</button>
  <button class="btn-primary" @click="handleSave">保存</button>
</div>
```

### 2.5 表单验证

- 必填字段使用 `*` 标记
- 保存前验证必填字段，验证失败时 `alert` 提示
- 复杂验证可使用内联错误提示（红色文字，`font-size: 12px`）

### 2.6 弹窗最大高度

弹窗 `max-height: 85vh`，超出时 `overflow-y: auto`。

---

## 3. 按钮样式统一

```css
/* 主按钮（保存、确认、新增） */
.btn-primary {
  background: #4f46e5;
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 4px;
  cursor: pointer;
}
.btn-primary:hover { background: #4338ca; }

/* 次要按钮（取消、返回、关闭） */
.btn-secondary {
  background: #f3f4f6;
  color: #333;
  border: 1px solid #ddd;
  padding: 8px 16px;
  border-radius: 4px;
  cursor: pointer;
}

/* 小按钮（表格行内操作） */
.btn-sm {
  padding: 4px 10px;
  border: 1px solid #ddd;
  border-radius: 3px;
  background: white;
  cursor: pointer;
  font-size: 12px;
}
.btn-sm:hover { background: #f5f5f5; }

/* 危险按钮（删除） */
.btn-danger {
  color: #dc2626;
  border-color: #dc2626;
}
.btn-danger:hover { background: #fef2f2; }
```

---

## 4. 确认对话框

- 删除等危险操作使用浏览器原生 `confirm()`
- 格式：`confirm('确定删除XX？')`
- 不需要自定义确认弹窗（除非有特殊 UI 需求）

---

## 5. 导入/导出

- 导入按钮放在操作区域，格式："导入 XX"
- 导入中时按钮显示"导入中..."并 `disabled`
- 导入结果使用弹窗展示成功/跳过条数及错误详情
- 导出/下载模板按钮格式："下载模板"、"导出XX"

---

## 6. 已知不一致项及统一规则

| 项目 | 现状 | 统一规则 |
|------|------|----------|
| 序号列 | 有的有、有的没有 | **必须有**（见 1.3） |
| 编辑弹窗关闭按钮 | 有的有"关闭"，有的只有"取消" | 编辑/新增用"取消"+ "保存"；详情用"关闭"+"编辑"（见 2.2） |
| CSS 方案 | Tailwind vs 自定义 CSS | 列表页优先使用自定义 scoped CSS（保持与 `GuideManager` 等一致） |
| 卡片列表 vs 表格 | ChannelConfig 用卡片 | 数据列表优先用表格；卡片仅用于特殊场景（如渠道配置预览） |
| 行内编辑 vs 弹窗编辑 | DigitalEmployeeManager 行内编辑 | 简单 CRUD 用弹窗；复杂多步骤编辑可用行内面板 |

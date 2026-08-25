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

列表页布局、搜索区、表格滚动条、表单字段、弹框表单等通用样式已提取到 `frontend/web/styles/page-common.css`（已在 `style.css` 中全局引入）：

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

## 4. 表格规范

### 表格组件

列表页表格**必须使用** `BaseTable.vue` 组件（`frontend/web/components/ui/BaseTable.vue`），禁止手写原生 `<table>` 或引入第三方表格组件。

- 统一通过 `:columns` 和 `:data` 传入列定义与数据，使用 `row-key` 指定唯一键。
- 统一由 `BaseTable` 提供斑马线、表头样式、单元格 `title` 提示、空状态等基础能力。
- 自定义列通过具名 slot（如 `#actions="{ row, index }"`）实现，但表格主体结构仍由 `BaseTable` 渲染。

### 表格列规范

#### 序号列

- 列宽：`60px`。
- 列头："序号"。
- 序号值由前端计算：
  - **PC 端（分页器，每页数据独立替换）**：`(currentPage - 1) * pageSize + index + 1`
  - **手机端（累积加载，列表是累积数据）**：`index + 1`（`index` 本身就是全局序号）
  - **无分页**：`index + 1`

  **关键陷阱**：手机端累积加载后 `currentPage` 会被更新成新页码，若误用 PC 端公式，前面已加载的行会按新页码重算导致序号整体偏移（如第 2 页加载后前 20 行变成 21-40）。正确做法是用 `seqNumber(index)` 函数根据 `isMobile` 分流，详见「6. 手机端列表页规范（累积加载）」。

#### 操作列

- 列宽：`100px~200px`（根据操作按钮数量调整，2个按钮、每个按钮2个字时，建议列宽 `120px`）。
- 列头："操作"，水平居中。
- 放在表格**最后一列**。
- 操作按钮：使用 `BaseButton` 的 `ghost`（默认）+ `sm` 类型；加 `whitespace-nowrap` 以便防换行；按钮文字，添加 `text-xs` 类；设置水平居中。
- 删除按钮使用 `intent="danger-ghost"`（灰底红字），避免红色背景过于醒目；其他操作按钮使用 `ghost`。
- "编辑"和"详情"一般不同时出现，只读场景仅保留"详情"。

#### 数据列

- 列头：`text-xs font-medium text-muted uppercase tracking-wider`（BaseTable 内置）。
- 单元格：`text-sm text-default`（BaseTable 内置）。
- 文字超出时显示省略号（`truncate` 类）。
- 所有字段，鼠标悬停，都要显示完整文字内容（`BaseTable` 自动为所有单元格设置 `title` 属性实现）。

  **自定义 slot 列的 tooltip**：若单元格使用了自定义 slot（如 `#region="{ row }"`），且实际数据路径与 `row[col.key]` 不同（如数据实际在 `row.metadata.region`），需要在 `columns` 定义中通过 `tooltip` 字段指定数据路径：

  ```ts
  // 正确：tooltip 函数指向实际数据路径
  { key: 'region', label: '区域', tooltip: (row: any) => row.metadata?.region || '-' }
  ```

  `tooltip` 支持字符串（静态内容）或函数（动态计算）。

---

## 5. 分页器

- 位于列表页底部，使用 `BasePagination` 组件。
- 分页器应固定居于页面底部，水平居中。
  - **实现方式**：配合 `.page-content` 的 `flex-1 flex flex-col min-h-0` 布局，分页器会自动被推到 flex 容器的底部。
  - **示意**：外层容器使用 `page-container`（`h-full flex flex-col`），内容区使用 `page-content`（`flex-1 flex flex-col min-h-0`），此时分页器在内容区底部自然固定。
- **仅 PC 端使用**。手机端不显示分页器，改用累积加载，详见「6. 手机端列表页规范（累积加载）」。

---

## 6. 手机端列表页规范（累积加载）

手机端列表页**不使用分页器**，而是滚动到底部时自动请求下一页并**追加**显示。这与 PC 端"分页器 + 当前页替换"模式完全不同，必须在数据加载、行号计算、加载状态管理三处分别处理，否则会出现"加载第 2 页后前 20 行消失/抖动"或"行号整体偏移"等问题。

### 6.1 PC 端 vs 手机端对照

| 维度 | PC 端 | 手机端 |
|------|-------|--------|
| 翻页方式 | 分页器（`BasePagination`） | 滚动到底部自动加载下一页 |
| `sessions` 语义 | 当前页数据（**替换式**） | 累积数据（**追加式**，第 2 页加载后第 1 页数据仍保留） |
| 加载状态标志 | `isLoading` | `isLoading`（初次）+ `isLoadingMore`（追加） |
| 行号公式 | `(currentPage - 1) * pageSize + index + 1` | `index + 1` |
| 底部分页器 | 显示 | 隐藏 |
| 底部加载提示 | 无 | "加载中..." / "没有更多了" |

### 6.2 数据加载：累积追加（核心规则）

**核心规则**：手机端请求下一页时，新数据**追加**到列表数组，**不替换**原有数据。第 1 页 1-20 行保留，第 2 页 21-40 行追加在后面。

Composable 中需提供独立的 `loadMore()` 函数，与 PC 端的 `loadList(page)` 区分：

```ts
// 累积加载示例（追加，不替换）
async function loadMore(): Promise<boolean> {
  if (isLoading.value || isLoadingMore.value) return false
  const nextPage = currentPage.value + 1
  if (nextPage > totalPages.value) return false

  isLoadingMore.value = true  // 关键：用 isLoadingMore，不能用 isLoading
  try {
    const result = await api.list(nextPage, pageSize.value)
    // 追加，不替换；按主键去重防止极端场景重复
    const existingIds = new Set(items.value.map(i => i.id))
    const newItems = result.items.filter(i => !existingIds.has(i.id))
    items.value = [...items.value, ...newItems]
    currentPage.value = result.page
    total.value = result.total
    return newItems.length > 0
  } finally {
    isLoadingMore.value = false
  }
}
```

### 6.3 加载状态：必须用独立的 `isLoadingMore` 标志（关键陷阱）

**核心陷阱**：追加加载**不能**复用 `isLoading` 标志。

列表页模板通常有 `v-if="isLoading"` 控制整个列表区域的挂载/卸载。若 `loadMore()` 也设置 `isLoading = true`，会触发列表卸载、显示"加载中..."全屏占位、加载完成后再挂载回来--DOM 重建导致滚动位置重置和视觉抖动，用户感知为"前 20 行消失，变成 21-40 行"。

**正确做法**：
- `isLoading`：仅用于**初次加载**（列表为空时）
- `isLoadingMore`：用于**追加加载**，列表保持原样不动

模板配合调整：

```vue
<!-- 顶部"加载中..."仅在列表为空时显示，追加加载时不卸载列表 -->
<div v-if="isLoading && items.length === 0" class="flex items-center justify-center py-12">
  加载中...
</div>

<!-- 列表始终渲染，追加加载时不动 -->
<div v-else-if="items.length > 0">
  <!-- 列表项 v-for... -->

  <!-- 底部加载提示（仅手机端） -->
  <div v-if="isMobile" class="py-3 text-center text-xs text-gray-400">
    <span v-if="isLoadingMore">加载中...</span>
    <span v-else-if="!hasMore">没有更多了</span>
  </div>
</div>

<!-- 分页器仅 PC 端显示 -->
<BasePagination v-if="!isMobile" ... />
```

### 6.4 行号计算：与 PC 端不同（关键陷阱）

**核心陷阱**：手机端累积加载后，列表是累积数据（如 1-40 行），但 `currentPage` 也会被 `loadMore()` 更新成新页码（如 2）。若继续用 PC 端公式 `(currentPage - 1) * pageSize + index + 1`，前 20 行会按 `currentPage=2` 重算，全部偏移成 21-40。

**正确做法**：用 `seqNumber(index)` 函数根据 `isMobile` 分流：

```ts
function seqNumber(index: number): number {
  if (isMobile.value) return index + 1  // 累积列表，index 即全局序号
  return (currentPage.value - 1) * pageSize.value + index + 1  // 当前页数据
}
```

模板：`{{ seqNumber(index) }}`

### 6.5 滚动监听实现

```ts
const scrollContainer = ref<HTMLElement | null>(null)
const hasMore = computed(() => items.value.length < total.value)

async function handleScroll() {
  if (!isMobile.value) return  // 仅手机端启用，PC 端用分页器
  const el = scrollContainer.value
  if (!el) return
  // 初次加载或追加加载任一进行中都跳过，防止重复请求
  if (isLoading.value || isLoadingMore.value || !hasMore.value) return

  const { scrollTop, scrollHeight, clientHeight } = el
  // 距离底部 80px 时触发加载
  if (scrollHeight - scrollTop - clientHeight < 80) {
    await loadMore()
  }
}
```

```vue
<div ref="scrollContainer" class="flex-1 overflow-y-auto" @scroll.passive="handleScroll">
  <!-- 列表内容 -->
</div>
```

**实现要点**：
- `@scroll.passive`：必须加 `passive` 修饰符，不阻止滚动默认行为，保证滚动流畅
- 触发阈值：`80px`（可根据行高调整。过小用户需停下来才触发；过大预加载过多）
- 并发保护：`isLoading.value || isLoadingMore.value` 任一为 `true` 都跳过

### 6.6 判断手机端

使用 `useMobile` composable（`frontend/web/composables/useMobile.ts`），断点 768px：

```ts
import { useMobile } from '@/composables/useMobile'
const { isMobile } = useMobile()
```

页面宽度 < 768px 时 `isMobile=true`，自动切换到累积加载模式。

### 6.7 参考实现

`frontend/web/components/AllSessions.vue` 是本规范的参考实现，包含完整的累积加载、行号分流、滚动监听、加载状态隔离逻辑。

---

## 7. 分页器固定底部的布局链要求

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

## 8. 公共 Composable：`usePageContext`

列表页的分页序号计算、搜索、刷新等通用逻辑已封装为 `usePageContext` composable（`frontend/web/composables/usePageContext.ts`）：

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

## 9. 公共 Composable：`useTableSelection`

表格批量选择逻辑已封装为 `useTableSelection` composable（`frontend/web/composables/useTableSelection.ts`）：

### 基本用法

```ts
import { useTableSelection } from '@/composables/useTableSelection'

// 初始化选择逻辑，传入获取行 ID 的函数
const { selectedArr, isAllSelected, toggleAll, clearSelection } = useTableSelection<any>({
  getRowId: (row) => row.doc_id
})
```

### 模板中使用

```vue
<!-- 批量删除按钮 -->
<BaseButton :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">
  批量删除 ({{ selectedArr.length }})
</BaseButton>

<!-- 表格 -->
<BaseTable :columns="columns" :data="currentList" row-key="doc_id">
  <!-- 表头全选框 -->
  <template #checkbox_header>
    <input
      type="checkbox"
      :checked="isAllSelected(currentList)"
      @change="(e) => toggleAll(currentList, ($event.target as HTMLInputElement).checked)"
    />
  </template>
  <!-- 行选择框 -->
  <template #checkbox="{ row }">
    <input type="checkbox" :value="row.doc_id" v-model="selectedArr" />
  </template>
</BaseTable>
```

### 辅助方法

| 方法 | 说明 |
|------|------|
| `selectedArr` | 选中的 ID 数组（可用于 v-model） |
| `isAllSelected(rows)` | 判断是否全选，传入当前列表 |
| `toggleAll(rows, checked)` | 全选/取消全选 |
| `clearSelection()` | 清空选择（翻页、搜索时调用） |
| `isSelected(row)` | 判断某行是否选中 |

### 注意事项

- 翻页、搜索、切换页大小时需调用 `clearSelection()` 清空选择
- 删除单行后需同步更新：`selectedArr.value = selectedArr.value.filter(id => id !== item.doc_id)`
- 批量删除后需调用 `clearSelection()`

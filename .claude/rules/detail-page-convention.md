# 详情页规范

> **管辖范围**：本文档定义了项目中所有**详情页**（含新增页、编辑页）的详细实现规范（BaseModal 尺寸选择、表单字段样式、关闭弹框脏检测 `useModalCloseGuard`、表单验证等）。
>
> **适用范围**：本文档适用于**字段较多的详情页/大表单**（一般几十个字段），操作按钮统一放在**页面顶部第一行**。
>
> **不适用场景**：字段较少的小弹框（如修改密码，仅3个字段），操作按钮放在**底部**。此类小弹框不属于本文档讨论范围。
>
> 要点速查见 [page_patterns.md](../../.claude/rules/page_patterns.md) 第3节。其他页面类型（列表页、统计页、设置页等）规范也在此文件中。
>
> 全局统一样式（颜色主题、字体、按钮、输入框、表格、分页器等基础规范）见 [page_patterns.md](../../.claude/rules/page_patterns.md) 第1节「全局统一样式」。

---

## 1. 弹框形式

使用 `BaseModal` 组件：

```vue
<BaseModal v-model="showModal" :title="editingItem ? 'XX - 编辑' : 'XX - 新增'" size="lg">
  <!-- 弹框右上角按钮 -->
  <template #header-extra>
    <div class="modal-header-actions">
      <button class="modal-fullscreen-btn" title="全屏" @click="toggleFullscreen">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
        </svg>
      </button>
      <button class="modal-close-btn" title="关闭" @click="handleClose">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 6 6 18M6 6l12 12"/>
        </svg>
      </button>
    </div>
  </template>
  <!-- 表单内容 -->
  <template #footer>
    <BaseButton intent="secondary" @click="showModal = false">取消</BaseButton>
    <BaseButton @click="handleSave">保存</BaseButton>
  </template>
</BaseModal>
```

**弹框右上角按钮**：
- **全屏按钮**：使用 `modal-fullscreen-btn` 类，点击后弹框切换为全屏（添加 `modal-fullscreen` 类）
- **关闭按钮**：使用 `modal-close-btn` 类，功能等同于关闭操作，需触发脏检测（如有修改则弹出确认）

### 关闭弹框脏检测

已封装为 `useModalCloseGuard` composable（`frontend/src/composables/useModalCloseGuard.ts`）：

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

---

## 2. 弹框尺寸

BaseModal 支持以下尺寸（通过 `size` prop）：

| size | 宽度 | 适用场景 | 字段排列 |
|------|------|---------|---------|
| `sm` | `max-w-sm`（384px） | 简单确认对话框 | 一般一行 1 个字段 |
| `md`（默认） | `max-w-lg`（512px） | 少于 6 个字段的表单 | 一般一行 1 个字段 |
| `lg` | `max-w-2xl`（672px） | 6~12 个字段的表单 | 一般一行 2 个字段，如果是大文本字段可以一行 1 个 |
| `xl` | `max-w-4xl`（896px） | 内容较多的详情页 | 一般一行 3-4 个字段，如果是大文本字段可以一行 1 个 |

弹框内容区默认 `max-h-[70vh]` 并带纵向滚动条（`scrollable=true`）。

**滚动规范**：
- 如果字段很多，可以出现纵向滚动条
- 应避免出现横向滚动条（字段应自适应弹框宽度）

---

## 3. 详情页头部

详情页头部分为两行布局：

### 3.1 第一行：操作按钮区

位于页面顶部，支持以下操作按钮（根据业务需求取舍）：

| 按钮 | 类型 | 说明 |
|------|------|------|
| 保存 | `primary` | 保存当前修改 |
| 保存并关闭 | `secondary` | 保存后自动关闭弹框 |
| 删除 | `danger-ghost` | 删除当前记录（灰底红字） |
| 刷新 | `ghost` | 重新加载数据 |

**布局规范**：
- 使用 `flex justify-between items-center` 布局
- 左侧可放置页面标题（如"XX 详情"）
- 右侧放置操作按钮，使用 `flex gap-2` 间距
- 按钮尺寸使用 `md`（默认）

```vue
<div class="flex justify-between items-center mb-4">
  <!-- 左侧：页面标题 -->
  <h2 class="text-lg font-semibold text-default">XX 详情</h2>

  <!-- 右侧：操作按钮 -->
  <div class="flex gap-2">
    <BaseButton @click="handleSave">保存</BaseButton>
    <BaseButton intent="secondary" @click="handleSaveAndClose">保存并关闭</BaseButton>
    <BaseButton intent="danger-ghost" @click="handleDelete">删除</BaseButton>
    <BaseButton intent="ghost" @click="handleRefresh">刷新</BaseButton>
  </div>
</div>
```

### 3.2 第二行：Tab 页签区（可选）

如果业务需要，可以有多个 Tab 页签，一般一个 Tab 页对应数据库中的一张表（或同一实体的不同侧面）。

**使用规范**：
- 使用 `BaseTabs` 或项目约定的 Tab 组件
- Tab 切换时应保持表单数据状态（可使用 `keep-alive` 或状态缓存）
- 默认选中第一个 Tab

```vue
<div class="mb-4 border-b border-default">
  <div class="flex gap-6">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      :class="[
        'pb-2 text-sm font-medium border-b-2 transition-colors',
        activeTab === tab.key
          ? 'text-primary-600 border-primary-600'
          : 'text-muted border-transparent hover:text-default hover:border-hover'
      ]"
      @click="activeTab = tab.key"
    >
      {{ tab.label }}
    </button>
  </div>
</div>
```

**Tab 与内容的对应关系**：
- 每个 Tab 对应一个表单区块（或表格区域）
- Tab 内容区使用 `v-show="activeTab === tab.key"` 条件渲染
- 数据量大的 Tab 考虑懒加载（`v-if` + 首次激活时加载）

---

## 4. 表单字段样式

- 字段标签（Label）：使用 `text-sm text-muted mb-1 block`。
- 必填字段：标签后添加 `<span class="text-danger-500">*</span>`。
- 字段之间的垂直间距：`16px`（`gap-4` 或 `mt-4`）。
- 两个字段同行：使用 `<div class="grid grid-cols-2 gap-4">`。
- 普通字段宽度（包含 label 和输入框）建议在 `200px ~ 300px`（压缩模式）或 `300px ~ 500px`（宽松模式）；长文本字段（如地址）建议宽度 `100%`（即一行 1 个）；大文本字段（如备注），在宽度 `100%`（即一行 1 个）的基础上，高度可以占据 3 行或更高。
- 字段较多时，应**优先考虑压缩字段宽度**，使一行可以显示 3~4 个普通字段，减少纵向滚动条长度；避免通过降低字段密度来规避纵向滚动而导致的大量空白。
- 同一表单中，字段宽度应保持统一（要么全是压缩模式，要么全是宽松模式），避免混用。

---

## 5. 表单验证

- 保存前验证必填字段，验证失败时 `alert` 提示。
- 输入框错误状态使用 `BaseInput` 的 `state="error"`。

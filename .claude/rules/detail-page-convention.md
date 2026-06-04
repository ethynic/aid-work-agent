# 详情页规范

> **管辖范围**：本文档定义了项目中所有**详情页**（含新增页、编辑页）的详细实现规范（BaseModal 尺寸选择、表单字段样式、关闭弹框脏检测 `useModalCloseGuard`、表单验证等）。
>
> 要点速查见 [page_patterns.md](../../.claude/rules/page_patterns.md) 第3节。其他页面类型（列表页、统计页、设置页等）规范也在此文件中。
>
> 全局统一样式（颜色主题、字体、按钮、输入框、表格、分页器等基础规范）见 [page_patterns.md](../../.claude/rules/page_patterns.md) 第1节「全局统一样式」。

---

## 1. 弹框形式

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

## 2. 弹框按钮

| 场景 | 按钮 |
|------|------|
| 编辑/新增 | "取消"（`secondary`） + "保存"（`primary`） |
| 详情查看 | "关闭"（`secondary`） + "编辑"（仅对有编辑权限的用户显示） |

**禁止只放"保存"按钮而无"取消"/"关闭"按钮。**

---

## 3. 弹框尺寸

BaseModal 支持以下尺寸（通过 `size` prop）：

| size | 宽度 | 适用场景 |
|------|------|---------|
| `sm` | `max-w-sm`（384px） | 简单确认对话框 |
| `md`（默认） | `max-w-lg`（512px） | 少于 6 个字段的表单 |
| `lg` | `max-w-2xl`（672px） | 6~12 个字段的表单 |
| `xl` | `max-w-4xl`（896px） | 内容较多的详情页 |

弹框内容区默认 `max-h-[70vh]` 并带纵向滚动条（`scrollable=true`）。

---

## 4. 表单字段样式

- 字段标签（Label）：使用 `text-sm text-muted mb-1 block`。
- 必填字段：标签后添加 `<span class="text-danger-500">*</span>`。
- 字段之间的垂直间距：`16px`（`gap-4` 或 `mt-4`）。
- 两个字段同行：使用 `<div class="grid grid-cols-2 gap-4">`。

---

## 5. 表单验证

- 保存前验证必填字段，验证失败时 `alert` 提示。
- 输入框错误状态使用 `BaseInput` 的 `state="error"`。

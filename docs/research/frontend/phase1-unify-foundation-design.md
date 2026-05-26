# 阶段一：统一基础层 — 设计文档

> 创建日期：2026-05-26
> 状态：待实施
> 关联调研：[frontend-style-research.md](./frontend-style-research.md)

---

## 一、目标

在不引入新依赖的前提下，统一项目的前端样式基础：

1. **恢复并增强页面模式规范**（替代已删除的 `page_patterns.md`）
2. **统一 CSS 方案**：消除三种样式模式并存的混乱局面
3. **定义基础组件变体**：用 `tailwind-variants` 建立可复用的组件样式定义
4. **修复主题不一致**：消除 681 处硬编码颜色

---

## 二、现状问题详析

### 2.1 三种样式模式并存

| 模式 | 代表组件 | 问题 |
|------|---------|------|
| **纯 Tailwind + 语义 token** | AppHeader、ThemeSwitcher | ✅ 目标模式 |
| **Scoped CSS + 硬编码颜色** | travel/VehicleManager | ❌ 自造轮子，不响应主题 |
| **Tailwind + 自定义 CSS 变量** | followup/LeadManager | ⚠️ 接近目标但使用了错误的变量名 |

### 2.2 LeadManager 的"伪兼容"问题

LeadManager 使用了 `var(--bg-primary)`、`var(--text-primary)` 等 CSS 变量名，但这些变量名与 `style.css` 中定义的 `--color-primary-*` 系列**不一致**。需要确认这些变量是否在某处定义，否则主题切换对它们无效。

### 2.3 无基础组件的代价

当前 40 个组件中，按钮样式在每个文件中重新定义：

```
VehicleManager.vue → .btn-primary { background: #4f46e5 }
ScheduledTasks.vue → .btn-primary { background: #4f46e5 }
CredentialManager.vue → .btn-primary { ... }
GuideManager.vue → .btn-primary { ... }
```

---

## 三、设计方案

### 3.1 引入 tailwind-variants

**选择 tailwind-variants 而非 CVA 的理由**：

| 特性 | CVA | tailwind-variants |
|------|-----|-------------------|
| Slots（多插槽） | ❌ | ✅ |
| 响应式变体 | ❌ | ✅ |
| 组件组合 | ❌ | ✅ |
| TypeScript 类型 | 基础 | 完整推导 |
| 大小 | 1.2KB | 3.8KB |

**安装**：

```bash
cd frontend && npm install tailwind-variants
```

**无其他依赖**：tailwind-variants 是纯 JS 库，不需要 radix-vue、reka-ui 等。

### 3.2 基础变体定义文件结构

```
frontend/src/
├── variants/                    # 新增：组件变体定义
│   ├── button.ts                # Button 变体
│   ├── input.ts                 # Input 变体
│   ├── select.ts                # Select 变体
│   ├── card.ts                  # Card 变体
│   ├── badge.ts                 # Badge 变体
│   ├── table.ts                 # Table 变体
│   ├── modal.ts                 # Modal 变体
│   ├── pagination.ts            # Pagination 变体
│   └── index.ts                 # 统一导出
├── components/
│   └── ui/                      # 新增：基础 UI 组件（纯渲染壳）
│       ├── BaseButton.vue       # 接受 tv() 变体的按钮
│       ├── BaseInput.vue        # 输入框
│       ├── BaseSelect.vue       # 下拉选择
│       ├── BaseCard.vue         # 卡片容器
│       ├── BaseBadge.vue        # 徽章/标签
│       ├── BaseModal.vue        # 模态框
│       ├── BaseTable.vue        # 表格
│       └── BasePagination.vue   # 分页
```

**说明**：`variants/` 目录只定义样式变体（纯 TS 函数），`components/ui/` 是最薄的渲染壳（接收变体结果并应用到元素上）。这比 shadcn-vue 更轻量，不引入任何无样式原语依赖。

### 3.3 变体定义示例

#### button.ts

```typescript
import { tv, type VariantProps } from 'tailwind-variants'

export const button = tv({
  base: 'inline-flex items-center justify-center rounded-lg font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none',
  variants: {
    intent: {
      primary: 'bg-primary-600 text-white hover:bg-primary-700 focus:ring-primary-500',
      secondary: 'bg-gray-100 text-gray-700 hover:bg-gray-200 focus:ring-gray-400',
      danger: 'bg-danger-600 text-white hover:bg-danger-700 focus:ring-danger-500',
      ghost: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
    },
    size: {
      sm: 'h-8 px-3 text-sm gap-1.5',
      md: 'h-10 px-4 text-sm gap-2',
      lg: 'h-12 px-6 text-base gap-2.5',
    },
    fullWidth: {
      true: 'w-full',
    },
  },
  defaultVariants: {
    intent: 'primary',
    size: 'md',
  },
})

export type ButtonVariants = VariantProps<typeof button>
```

#### input.ts

```typescript
import { tv, type VariantProps } from 'tailwind-variants'

export const input = tv({
  base: 'w-full rounded-lg border bg-white px-3 py-2 text-sm text-default placeholder:text-muted transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500 disabled:bg-gray-50 disabled:text-muted',
  variants: {
    state: {
      default: 'border-default',
      error: 'border-danger-500 focus:ring-danger-500/20 focus:border-danger-500',
      success: 'border-success-500',
    },
    size: {
      sm: 'h-8 text-xs',
      md: 'h-10 text-sm',
      lg: 'h-12 text-base',
    },
  },
  defaultVariants: {
    state: 'default',
    size: 'md',
  },
})

export type InputVariants = VariantProps<typeof input>
```

#### modal.ts

```typescript
import { tv, type VariantProps } from 'tailwind-variants'

export const modal = tv({
  slots: {
    overlay: 'fixed inset-0 z-50 flex items-center justify-center bg-black/40',
    content: 'bg-white rounded-xl shadow-xl w-full overflow-hidden',
    header: 'flex items-center justify-between px-6 py-4 border-b border-default',
    body: 'px-6 py-4 overflow-y-auto',
    footer: 'flex items-center justify-end gap-3 px-6 py-4 border-t border-default',
  },
  variants: {
    size: {
      sm: { content: 'max-w-sm' },
      md: { content: 'max-w-lg' },
      lg: { content: 'max-w-2xl' },
      xl: { content: 'max-w-4xl' },
    },
    scrollable: {
      true: { body: 'max-h-[70vh]' },
    },
  },
  defaultVariants: {
    size: 'md',
    scrollable: true,
  },
})

export type ModalVariants = VariantProps<typeof modal>
```

#### table.ts

```typescript
import { tv, type VariantProps } from 'tailwind-variants'

export const table = tv({
  slots: {
    wrapper: 'w-full overflow-x-auto rounded-lg border border-default',
    table: 'w-full text-sm',
    thead: 'bg-gray-50',
    th: 'px-4 py-3 text-left text-xs font-medium text-muted uppercase tracking-wider',
    tbody: 'divide-y divide-default',
    tr: 'hover:bg-surface-hover transition-colors',
    td: 'px-4 py-3 text-default',
    empty: 'px-4 py-12 text-center text-muted',
  },
})

export type TableVariants = VariantProps<typeof table>
```

### 3.4 基础 UI 组件示例

#### BaseButton.vue

```vue
<template>
  <button :class="classes" :disabled="disabled" @click="$emit('click', $event)">
    <slot />
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { button } from '@/variants/button'

const props = withDefaults(defineProps<{
  intent?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  fullWidth?: boolean
  disabled?: boolean
}>(), {
  intent: 'primary',
  size: 'md',
  fullWidth: false,
  disabled: false,
})

defineEmits<{ click: [e: MouseEvent] }>()

const classes = computed(() =>
  button({ intent: props.intent, size: props.size, fullWidth: props.fullWidth })
)
</script>
```

### 3.5 页面模式规范恢复

恢复 `.claude/rules/page_patterns.md`，内容基于已删除版本增强：

**关键变更**：

1. **CSS 方案统一为 Tailwind + 语义 token**，不再允许 scoped CSS 方案
2. **组件使用 Base* 组件**，不再允许自行定义 `.btn-primary` 等类
3. **颜色强制使用语义 token**（`primary-*`、`danger-*`、`bg-surface` 等）

**页面模式列表**：

| 模式 | 适用场景 | 核心组件 |
|------|---------|---------|
| 列表页 | 数据表格管理 | BaseTable + BasePagination + BaseButton |
| 表单页（模态） | 新增/编辑 | BaseModal + BaseInput + BaseSelect |
| 详情页（模态） | 只读查看 | BaseModal（无编辑控件） |
| 统计页 | 数据概览 | BaseCard + BaseBadge |
| 设置页 | 配置管理 | BaseCard + BaseInput + BaseButton |

### 3.6 硬编码颜色替换策略

681 处 `slate-`/`cyan-` 需要替换为语义 token：

| 硬编码 | 替换为 | 说明 |
|--------|--------|------|
| `slate-50` | `bg-surface` / `bg-canvas` | 背景 |
| `slate-100` | `bg-surface-hover` | 悬停背景 |
| `slate-200` | `border-default` | 边框 |
| `slate-300` | `border-hover` | 边框悬停 |
| `slate-500` | `text-muted` | 次要文字 |
| `slate-600` | `text-default` | 正文文字 |
| `slate-700` | `text-default` | 深色文字 |
| `slate-800` | `text-default` | 标题文字 |
| `slate-900` | `text-default` | 最深文字 |
| `cyan-500` | `primary-500` | 主色调 |
| `cyan-600` | `primary-600` | 主色调 |
| `cyan-700` | `primary-700` | 主色调悬停 |

**替换原则**：按功能语义映射，不是按颜色值一一对应。

---

## 四、实施范围

### 新增文件

| 文件 | 说明 |
|------|------|
| `frontend/src/variants/button.ts` | 按钮变体定义 |
| `frontend/src/variants/input.ts` | 输入框变体定义 |
| `frontend/src/variants/select.ts` | 下拉选择变体定义 |
| `frontend/src/variants/card.ts` | 卡片变体定义 |
| `frontend/src/variants/badge.ts` | 徽章变体定义 |
| `frontend/src/variants/table.ts` | 表格变体定义 |
| `frontend/src/variants/modal.ts` | 模态框变体定义 |
| `frontend/src/variants/pagination.ts` | 分页变体定义 |
| `frontend/src/variants/index.ts` | 统一导出 |
| `frontend/src/components/ui/BaseButton.vue` | 基础按钮组件 |
| `frontend/src/components/ui/BaseInput.vue` | 基础输入框组件 |
| `frontend/src/components/ui/BaseSelect.vue` | 基础选择器组件 |
| `frontend/src/components/ui/BaseCard.vue` | 基础卡片组件 |
| `frontend/src/components/ui/BaseBadge.vue` | 基础徽章组件 |
| `frontend/src/components/ui/BaseModal.vue` | 基础模态框组件 |
| `frontend/src/components/ui/BaseTable.vue` | 基础表格组件 |
| `frontend/src/components/ui/BasePagination.vue` | 基础分页组件 |
| `.claude/rules/page_patterns.md` | 恢复的页面模式规范 |

### 修改文件

| 文件 | 变更内容 |
|------|---------|
| `frontend/package.json` | 添加 `tailwind-variants` 依赖 |
| `.claude/rules/frontend_dev.md` | 更新样式规范，指向新变体系统 |

### 渐进替换（可选，阶段一不强制）

| 组件目录 | 文件数 | 优先级 |
|---------|--------|--------|
| `components/travel/` | 6 | 中（scoped CSS → Tailwind） |
| `components/saas/` | 16 | 高（硬编码 slate/cyan） |
| `components/followup/` | 3 | 中（自定义变量名） |
| `components/complaint/` | 2 | 低（可能已是新模式） |

---

## 五、验收标准

1. `npm run build` 通过
2. `variants/` 目录包含 8 个变体定义 + 1 个统一导出
3. `components/ui/` 目录包含 8 个基础 UI 组件
4. `.claude/rules/page_patterns.md` 恢复并更新
5. 新页面开发时，AI 助手能根据 `page_patterns.md` + `variants/` 生成一致的 UI
6. saas/ 目录下的硬编码颜色替换完成（681 处 → 0 处）

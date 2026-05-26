# 阶段一：统一基础层 — 实施计划

> 创建日期：2026-05-26
> 完成日期：2026-05-26
> 关联设计：[phase1-unify-foundation-design.md](./phase1-unify-foundation-design.md)
> 状态：已完成

---

## 任务总览

| # | 任务 | 预估工时 | 依赖 | 状态 |
|---|------|---------|------|------|
| 1.1 | 安装 tailwind-variants 依赖 | 10min | 无 | ✅ 已完成 |
| 1.2 | 创建 variants/ 目录和 8 个变体定义 | 2h | 1.1 | ✅ 已完成 |
| 1.3 | 创建 components/ui/ 目录和 8 个基础组件 | 2h | 1.2 | ✅ 已完成 |
| 1.4 | 恢复并更新 page_patterns.md | 1h | 1.3 | ✅ 已完成 |
| 1.5 | 更新 frontend_dev.md 引用新规范 | 30min | 1.4 | ✅ 已完成 |
| 1.6 | 替换 saas/ 目录硬编码颜色 | 3h | 1.2 | ✅ 已完成 |
| 1.7 | 替换 travel/ 目录 scoped CSS | 2h | 1.3, 1.4 | ✅ 已完成 |
| 1.8 | 替换 followup/ 目录自定义 CSS 变量 | 1h | 1.2 | ✅ 已完成 |
| 1.9 | 构建验证与修复 | 1h | 1.6, 1.7, 1.8 | ✅ 已完成 |

**总预估工时**：约 13 小时

---

## 详细任务

### 1.1 安装 tailwind-variants 依赖

```bash
cd frontend
npm install tailwind-variants tailwind-merge
```

验证：`node -e "require('tailwind-variants')"` 无报错

> 实际执行：还安装了 `tailwind-merge`（tailwind-variants 的 peer dependency）。

---

### 1.2 创建变体定义文件

创建以下文件：

- [x] `frontend/src/variants/button.ts`
- [x] `frontend/src/variants/input.ts`
- [x] `frontend/src/variants/select.ts`
- [x] `frontend/src/variants/card.ts`
- [x] `frontend/src/variants/badge.ts`
- [x] `frontend/src/variants/table.ts`
- [x] `frontend/src/variants/modal.ts`
- [x] `frontend/src/variants/pagination.ts`
- [x] `frontend/src/variants/index.ts`（统一导出）

每个变体文件参考设计文档中的示例。关键要求：
- 颜色使用语义 token（`primary-*`、`danger-*`、`text-default` 等）
- 提供 TypeScript 类型导出（`VariantProps<typeof xxx>`）
- 合理的默认变体

---

### 1.3 创建基础 UI 组件

创建以下文件：

- [x] `frontend/src/components/ui/BaseButton.vue`
- [x] `frontend/src/components/ui/BaseInput.vue`
- [x] `frontend/src/components/ui/BaseSelect.vue`
- [x] `frontend/src/components/ui/BaseCard.vue`
- [x] `frontend/src/components/ui/BaseBadge.vue`
- [x] `frontend/src/components/ui/BaseModal.vue`
- [x] `frontend/src/components/ui/BaseTable.vue`
- [x] `frontend/src/components/ui/BasePagination.vue`

每个组件是最薄的渲染壳，职责：
- 接收 props（变体参数 + 原生 HTML 属性）
- 调用对应的 `tv()` 函数生成类名
- 透传事件和插槽

---

### 1.4 恢复并更新 page_patterns.md

从 git 历史恢复 `.claude/rules/page_patterns.md`（commit `8422524`），然后更新：

**更新要点**：
- [x] CSS 方案统一为 Tailwind + 语义 token（删除"优先 scoped CSS"的指引）
- [x] 按钮样式使用 BaseButton 组件（删除 `.btn-primary` 等自定义类定义）
- [x] 表格使用 BaseTable 组件（删除原生 table 样式规范）
- [x] 模态框使用 BaseModal 组件（删除 `.modal-overlay` 等自定义类定义）
- [x] 所有颜色引用语义 token
- [x] 添加统计页和设置页的页面模式

---

### 1.5 更新 frontend_dev.md

在 `.claude/rules/frontend_dev.md` 中添加/更新：

- [x] 添加"组件变体系统"章节，指向 `variants/` 目录
- [x] 更新"基础组件"章节，指向 `components/ui/` 目录
- [x] 添加"颜色使用规范"章节，明确禁止硬编码颜色
- [x] 添加 `page_patterns.md` 的引用

---

### 1.6 替换 saas/ 目录硬编码颜色

**范围**：15 个文件 + 1 个公共组件（DigitalEmployeeManager.vue），所有 `slate-`/`cyan-` 硬编码颜色

替换映射（参考设计文档 §3.6）：

```
slate-50  → bg-canvas / bg-surface（按上下文判断）
slate-100 → bg-surface-hover / border-default / divide-default
slate-200 → border-default / bg-gray-200（进度条等场景）
slate-300 → border-hover / bg-gray-300（disabled 场景）
slate-400 → text-muted
slate-500 → text-muted
slate-600 → text-default
slate-700 → text-default / border-gray-700（dark 侧边栏）
slate-800 → text-default / bg-gray-800（dark 侧边栏）
slate-900 → text-default / bg-gray-900（登录页深色背景）
cyan-50   → primary-50
cyan-400  → primary-400
cyan-500  → primary-500
cyan-600  → primary-600
cyan-700  → primary-700
red-*     → danger-*
green-*   → success-*
blue-*    → info-*
orange-*  → warning-*
indigo-*  → primary-*
```

**已处理文件**：
1. [x] TenantMgmt.vue
2. [x] TenantSettings.vue
3. [x] TenantDashboard.vue
4. [x] TenantLogin.vue
5. [x] PortalLayout.vue
6. [x] BillingView.vue
7. [x] ChannelConfig.vue
8. [x] ErrorLogs.vue
9. [x] InstanceManager.vue
10. [x] PlatformTokenUsage.vue
11. [x] ResetPassword.vue
12. [x] SkillManager.vue
13. [x] TenantTokenUsage.vue
14. [x] TenantUserManager.vue
15. [x] UsageReports.vue

> 实际执行：DigitalEmployeeManager.vue 中的硬编码颜色也一并替换了。此外，LoginModal.vue、UniversalLogin.vue、ProgressPanel.vue 等公共组件中的 slate-/cyan- 也同步清理。

---

### 1.7 替换 travel/ 目录 scoped CSS

**范围**：6 个文件

每个文件：
- [x] 删除 `<style scoped>` 块中的自定义 CSS 类
- [x] 将模板中的 CSS 类名替换为 Tailwind 工具类
- [x] 将 `#4f46e5` 等硬编码颜色替换为语义 token
- [x] 将 `.btn-primary`/`.btn-secondary`/`.btn-danger` 替换为 Tailwind 等效类
- [x] 将 `.data-table`/`.modal-overlay`/`.modal-content`/`.form-group` 等自定义类替换为 Tailwind 工具类

**已处理文件**：
1. [x] VehicleManager.vue
2. [x] AttractionManager.vue
3. [x] HotelManager.vue
4. [x] MealManager.vue
5. [x] GuideManager.vue
6. [x] FeeManager.vue

---

### 1.8 替换 followup/ 目录自定义 CSS 变量

**范围**：3 个文件 + 2 个 complaint 文件（发现也使用了相同的自定义 CSS 变量）

已确认 `var(--bg-primary)`、`var(--text-primary)` 等变量未在项目任何地方定义，属于"伪兼容"变量。

- [x] 将 `var(--bg-primary)` / `bg-[var(--bg-primary)]` 替换为 `bg-surface`
- [x] 将 `var(--bg-secondary)` / `bg-[var(--bg-secondary)]` 替换为 `bg-canvas`
- [x] 将 `var(--bg-hover)` / `hover:bg-[var(--bg-hover)]` 替换为 `bg-surface-hover`
- [x] 将 `var(--text-primary)` / `text-[var(--text-primary)]` 替换为 `text-default`
- [x] 将 `var(--text-secondary)` / `text-[var(--text-secondary)]` 替换为 `text-muted`
- [x] 将 `var(--text-tertiary)` / `text-[var(--text-tertiary)]` 替换为 `text-muted`
- [x] 将 `var(--accent-primary)` 替换为 `primary-600`
- [x] 将 `var(--border-primary)` 替换为 `border-default`
- [x] 删除定义 CSS 变量的 `<style scoped>` 块

**已处理文件**：
1. [x] LeadManager.vue
2. [x] FollowupRecords.vue
3. [x] SalesRepManager.vue
4. [x] ComplaintList.vue（额外发现并处理）
5. [x] ComplaintStats.vue（额外发现并处理）

---

### 1.9 构建验证与修复

- [x] `cd frontend && npm run build` 通过（vue-tsc + vite build 无错误）
- [x] 确认无硬编码颜色残留：`grep -r "slate-" frontend/src/components/` 中仅剩 `translate-x`/`translate-y` CSS 变换（非颜色）
- [x] 确认无硬编码颜色残留：`grep -r "cyan-" frontend/src/components/` 为空
- [x] 确认无自定义 CSS 变量残留：`grep -r "var(--" frontend/src/components/` 为空
- [x] 修复 BaseTable.vue TypeScript 编译错误（未使用的 `props` 变量）

---

## 实际执行备注

1. **并行执行**：使用多个子智能体并行处理 saas/、travel/、followup/ 三个目录的替换工作，显著提高效率
2. **sed 批量替换**：子智能体的逐文件替换不够彻底，后续使用 `sed` + `replace_all` 进行批量清理，确保所有硬编码颜色被替换
3. **范围扩展**：除了计划中的 saas/、travel/、followup/，还同步清理了 complaint/、LoginModal.vue、UniversalLogin.vue、ProgressPanel.vue 等组件中的硬编码颜色
4. **dist/ 体积**：CSS 产出从 ~118KB 降至 ~110KB（减少了 ~7%），说明语义 token 复用效果良好

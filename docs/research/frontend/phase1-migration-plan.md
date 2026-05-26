# Phase 1 落地迁移计划 — Base* 组件实战推广

> 创建日期：2026-05-26
> 状态：已完成
> 背景：Phase 1 建立了变体系统 + 8 个 Base* 组件，但业务页面采用率为零。Phase 2（shadcn-vue）和 Phase 3（v4/暗色/响应式）经评估暂无必要，搁置处理。
> 目标：通过逐页面迁移到 Base* 组件，验证组件体系是否完善，积累使用经验。

---

## 一、迁移策略

### 1.1 迁移范围

**第一批：travel/ 目录（6 个文件）** — 作为实验对象

选择理由：
- 文件数量适中（6 个），结构高度相似
- 覆盖了 Base* 组件的全部类型（Button、Input、Select、Table、Modal、Pagination、Badge）
- 功能独立，不影响其他业务模块

### 1.2 Base* 组件对照表

当前 6 个 travel 文件全部使用原生 HTML + 内联 Tailwind，需替换为：

| 原生 HTML | Base* 组件 | 关键 Props |
|-----------|-----------|------------|
| `<button class="bg-primary-600 hover:bg-primary-700 ...">` | `<BaseButton>` | `intent="primary"`, `intent="secondary"`, `intent="danger"`, `intent="ghost"` |
| `<button>` 行内操作按钮 | `<BaseButton intent="ghost" size="sm">` | - |
| `<input v-model="x" class="...border...">` | `<BaseInput v-model="x">` | `state="error"` 验证失败时 |
| `<select v-model="x" class="...">` | `<BaseSelect v-model="x">` | 内部放 `<option>` |
| `<table>` + 手写 th/td 样式 | `<BaseTable :columns :data>` | 具名插槽自定义列渲染 |
| `div.fixed.inset-0` 手写弹窗 | `<BaseModal v-model="show" title="..." size="md">` | `#footer` 插槽放按钮 |
| 手写分页逻辑 | `<BasePagination>` | `v-model:current-page`, `:total`, `:page-size` |

### 1.3 迁移步骤（每个文件的通用流程）

1. 添加 Base* 组件 import
2. **按钮**：替换 `<button>` 为 `<BaseButton>`，通过 `intent`/`size` 控制
3. **输入框**：替换 `<input>` 为 `<BaseInput v-model="x">`
4. **下拉框**：替换 `<select>` 为 `<BaseSelect v-model="x">`
5. **表格**：定义 `columns` 数组，用 `<BaseTable>` 替换手写 `<table>`
6. **弹窗**：用 `<BaseModal v-model="show">` 替换手写 overlay div
7. **分页**：用 `<BasePagination>` 替换手写分页逻辑
8. 删除冗余的内联 Tailwind 类
9. 验证功能 + 视觉一致

---

## 二、迁移任务清单

### 任务总览

| # | 任务 | 文件 | 复杂度 | 状态 |
|---|------|------|--------|------|
| M1 | VehicleManager.vue 迁移 | travel/VehicleManager.vue | 低 | ⬜ 待开始 |
| M2 | MealManager.vue 迁移 | travel/MealManager.vue | 低 | ⬜ 待开始 |
| M3 | GuideManager.vue 迁移 | travel/GuideManager.vue | 低 | ⬜ 待开始 |
| M4 | FeeManager.vue 迁移 | travel/FeeManager.vue | 中 | ⬜ 待开始 |
| M5 | HotelManager.vue 迁移 | travel/HotelManager.vue | 高 | ⬜ 待开始 |
| M6 | AttractionManager.vue 迁移 | travel/AttractionManager.vue | 高 | ⬜ 待开始 |
| M7 | 清理 5 个文件中的 dark: 死代码 | followup/ + complaint/ | 低 | ⬜ 待开始 |
| M8 | 构建验证 + 验收 | 全部 | - | ⬜ 待开始 |

### 迁移顺序说明

先做简单文件（M1-M3），积累经验后再做复杂文件（M5-M6）。FeeManager（M4）夹在中间，因为它是唯一有 Tab 切换的文件，可以验证 Base* 组件在多 Tab 场景下的适配性。

---

### M1: VehicleManager.vue（212 行，低复杂度）

- [ ] 替换 3 个 `<button>` 为 `<BaseButton>`
- [ ] 替换 1 个筛选 `<input>` 为 `<BaseInput>`
- [ ] 替换手写 `<table>` 为 `<BaseTable>`（10 列，含序号列 + 操作列）
- [ ] 替换编辑/新增弹窗为 `<BaseModal>`（8 个表单字段：3 select + 5 input）
- [ ] 替换弹窗内 3 个 `<select>` 为 `<BaseSelect>`
- [ ] 替换弹窗内 5 个 `<input>` 为 `<BaseInput>`
- [ ] 替换导入结果弹窗为 `<BaseModal>`
- [ ] `npm run build` 通过
- [ ] 视觉验证：列表、新增、编辑、删除、导入

**无分页、无搜索、无批量删除，最适合第一个练手。**

---

### M2: MealManager.vue（213 行，低复杂度）

- [ ] 替换 3 个 `<button>` 为 `<BaseButton>`
- [ ] 替换筛选 `<input>` 为 `<BaseInput>`
- [ ] 替换筛选 `<select>`（meal tier）为 `<BaseSelect>`
- [ ] 替换手写 `<table>` 为 `<BaseTable>`（10 列）
- [ ] 替换编辑/新增弹窗为 `<BaseModal>`（9 字段：3 select + 6 input）
- [ ] 替换弹窗内 `<select>` 和 `<input>` 为 Base 组件
- [ ] 替换导入结果弹窗为 `<BaseModal>`
- [ ] `npm run build` 通过
- [ ] 视觉验证

**与 M1 几乎同构，多了筛选 select。**

---

### M3: GuideManager.vue（227 行，低复杂度）

- [ ] 替换 3 个 `<button>` 为 `<BaseButton>`
- [ ] 替换筛选 `<input>` 和 `<select>` 为 Base 组件
- [ ] 替换手写 `<table>` 为 `<BaseTable>`（11 列，最多列数）
- [ ] 替换编辑/新增弹窗为 `<BaseModal>`（11 字段，最多字段）
- [ ] 替换导入结果弹窗为 `<BaseModal>`
- [ ] `npm run build` 通过
- [ ] 视觉验证

**测试 BaseTable/BaseModal 在字段多时的表现。**

---

### M4: FeeManager.vue（338 行，中复杂度）

- [ ] 替换 Tab 切换 UI（手写 tab 按钮改为 BaseButton ghost）
- [ ] 替换筛选 `<select>` 为 `<BaseSelect>`
- [ ] 替换 2 个手写 `<table>` 为 2 个 `<BaseTable>`（fees 表 9 列 + seasons 表 8 列）
- [ ] 替换 2 个编辑/新增弹窗为 `<BaseModal>`
  - Fee 弹窗：8 字段（3 select + 5 input）
  - Season 弹窗：6 字段（含 `<input type="date">`，需验证 BaseInput 是否支持）
- [ ] 替换导入结果弹窗为 `<BaseModal>`
- [ ] `npm run build` 通过
- [ ] 视觉验证：两个 Tab 各自的 CRUD 流程

**验证：多 Tab 场景、date 类型 input、双表格双弹窗。**

---

### M5: HotelManager.vue（362 行，高复杂度）

- [ ] 替换搜索框为 `<BaseInput>`
- [ ] 替换所有 `<button>` 为 `<BaseButton>`
- [ ] 替换手写 `<table>` 为 `<BaseTable>`（含 checkbox 列、批量选择）
- [ ] 实现分页：用 `<BasePagination>` 替换手写 pageNumbers 逻辑
- [ ] 替换编辑弹窗为 `<BaseModal>`（含 textarea 字段）
- [ ] 替换详情查看弹窗为 `<BaseModal>`（只读，用 `<pre>` 展示）
- [ ] 替换导入结果弹窗为 `<BaseModal>`
- [ ] `npm run build` 通过
- [ ] 视觉验证：搜索、分页、批量删除、编辑、详情查看、导入

**验证：分页、搜索、批量选择（checkbox 列）、详情查看、textarea。**

---

### M6: AttractionManager.vue（372 行，高复杂度）

- [ ] 与 M5 几乎同构，执行相同替换
- [ ] 额外注意：3 个 textarea 字段（info、ticket_table、project_table）
- [ ] `npm run build` 通过
- [ ] 视觉验证

---

### M7: 清理 dark: 死代码

5 个文件中有 51 个 `dark:` 类，由于 `darkMode` 未配置，这些是死代码：

- [ ] `followup/LeadManager.vue`（12 处）
- [ ] `followup/FollowupRecords.vue`（11 处）
- [ ] `followup/SalesRepManager.vue`（4 处）
- [ ] `complaint/ComplaintList.vue`（22 处）
- [ ] `complaint/ComplaintStats.vue`（2 处）

每个文件：删除 `dark:` 相关的类字符串。通常是条件类中的 `dark:bg-xxx dark:text-xxx` 等。

---

### M8: 构建验证与验收

- [ ] `cd frontend && npm run build` 通过
- [ ] 所有 6 个 travel 页面视觉与迁移前一致
- [ ] 确认所有 Base* 组件在 8 个主题下正确渲染
- [ ] 评估是否需要补充 Base* 组件（如 BaseTabs、BaseCheckbox、BaseFileInput）
- [ ] 记录迁移中发现的问题和改进建议

---

## 三、Base* 组件能力缺口预判

以下交互模式当前 Base* 组件不支持，迁移过程中可能遇到：

| 缺口 | 涉及文件 | 处理方式 |
|------|---------|---------|
| Checkbox 多选 | HotelManager、AttractionManager | 用原生 `<input type="checkbox">` + Tailwind 样式 |
| Textarea | HotelManager、AttractionManager（编辑弹窗） | 用原生 `<textarea>` + BaseInput 同款样式 class |
| File Input（隐藏） | 全部 6 个文件 | 保持原生 `<input type="file" style="display:none">` 不变 |
| Tab 切换 | FeeManager | 用 `<BaseButton intent="ghost">` 组合实现 |
| Date Input | FeeManager（season 日期字段） | 用原生 `<input type="date">` + BaseInput 同款样式 |
| Search（客户端搜索） | HotelManager、AttractionManager | 逻辑层不变，只替换 UI 元素 |

**原则**：迁移过程中发现 Base* 组件覆盖不了的场景，先用原生 HTML + Tailwind 样式凑合，记录缺口。全部迁移完成后统一评估是否需要新增 Base 组件。

---

## 四、验收标准

1. 6 个 travel 文件全部使用 Base* 组件（Button、Input、Select、Table、Modal、Pagination）
2. 所有 CRUD 功能正常（列表、新增、编辑、删除、导入）
3. `npm run build` 通过
4. 视觉与迁移前基本一致（允许细微的间距/圆角差异）
5. 5 个文件的 `dark:` 死代码已清理
6. 记录了 Base* 组件的改进建议清单

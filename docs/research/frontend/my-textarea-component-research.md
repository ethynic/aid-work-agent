# 前端 MyTextarea 通用组件调研与设计

> 调研日期：2026-06-11
> 文档类型：选型记录 + 组件设计 + 实施步骤
> 关联功能：`#33` 之后的前端基础设施类目

---

## 一、背景与动机

现有页面中，多个管理后台存在"大文本框输入 + 频繁编写长内容"的场景：

- `AgentDefinitionManager`：System Prompt 模板（200+ 行常见）、分段变量值
- `saas/SkillManager`：Skill 描述
- `saas/ReplyStyleManager` / `saas/SystemReplyStyleManager`：风格描述、示例
- `saas/TenantMgmt`：备注字段
- `travel/HotelManager` / `travel/AttractionManager`：描述字段
- `followup/LeadManager`：备注字段

当前问题：
- 文本框行数固定（`min-height: 200px` 等），内容多时滚动查看体验差
- 无法全屏编辑，长 Prompt 编写受限
- 无 MD 预览能力，Prompt 中的 Markdown / 代码块需切换外部工具校验
- 各页面重复实现工具栏、自动撑高、字数统计等

需要一个**通用大文本框组件**，统一全屏、字数、MD 预览等能力，并预留扩展点（占位符识别、AI 优化等）。

---

## 二、候选方案对比

### 2.1 候选清单

| 组件 | 体积（min+gzip） | Vue 3 + TS | 二次改造 | 默认风格 |
|------|------------------|------------|----------|----------|
| md-editor-v3 | ~150 KB | ✅ | 中 | 工具栏极丰富 |
| mavon-editor | ~250 KB | ⚠️（Vue 2 迁移） | 高 | 老旧 |
| AiEditor | ~800 KB | ✅ | 高 | 富文本堆叠 |
| @bytemd/vue3 | ~120 KB | ✅ | 低 | 极简 |
| **自研 + 复用 marked** | **~30 KB** | ✅ | **极低** | **完全可控** |

### 2.2 关键约束

- 项目**已依赖** `marked@17` + `marked-highlight@2` + `highlight.js@11` + `@tailwindcss/typography`
- 0 新增依赖即可实现"MD 渲染 + 代码高亮"
- 项目风格"克制、专业、蓝色 `#003A8C`" —— 与 md-editor-v3 默认 30+ 工具栏的"富文本"形态冲突
- 后续要加"AI 优化按钮"、"占位符识别"等业务能力时，可控性是首要诉求

### 2.3 结论

**采用自研方案**，不复用第三方编辑器组件。理由：
1. 真正痛点只有"全屏 + MD 预览"两点，30~50 行代码即可覆盖
2. 0 新增依赖，包体积零增长
3. 与现有 `marked` / `highlight.js` 100% 复用，无重复依赖
4. 后续业务扩展（AI 优化 slot、占位符高亮）可控性最强
5. 团队维护成本最低：350~450 行代码人人能改

> **何时回头重新评估**：若未来出现"全平台富文本编辑"诉求（如邮件正文排版、可视化模板），再评估 `md-editor-v3` / `bytemd`，不在本阶段提前过度设计。

---

## 三、组件设计

### 3.1 组件清单

```
frontend/src/components/ui/
  ├─ MyTextarea.vue               # 包装：标签 + 工具栏 + textarea + 预览
  ├─ FullscreenTextEditor.vue     # 全屏编辑器
  └─ MarkdownPreview.vue          # MD 渲染（封装 marked + highlight.js）
```

预估总代码量：**350~450 行**（含注释、prop 类型、样式）。

### 3.2 `MyTextarea.vue` API

```ts
defineProps<{
  modelValue: string
  label?: string
  placeholder?: string
  rows?: number                          // 默认 4
  monospace?: boolean                    // 等宽字体（System Prompt 场景默认 true）
  showCharCount?: boolean                // 字数统计
  showFullscreen?: boolean               // 全屏按钮，默认 true
  enablePreview?: boolean                // 是否提供编辑/预览切换
  readonly?: boolean
  disabled?: boolean
  containerClass?: string                // 父级覆盖容器
  textareaClass?: string                 // 父级覆盖文本框
}>()

defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'input', e: Event): void           // 透传原生 input，便于外部联动
}>()

// slots
// #extra — 工具栏右侧自定义按钮（AI 优化、占位符列表等）
```

### 3.3 `FullscreenTextEditor.vue` API

```ts
defineProps<{
  modelValue: string
  title: string
  monospace?: boolean
  enableMarkdownPreview?: boolean        // 默认 false，预留
  showStatusBar?: boolean                // 默认 true
}>()
defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'close', payload: { saved: boolean }): void
}>()
```

**内部能力**：
- 顶部：标题 + 字数 + 关闭 + 保存
- 中部：编辑/预览 Tab 切换（`enableMarkdownPreview=true` 时）
- 底部状态栏：快捷键提示（Esc 关闭、Ctrl/Cmd+S 保存、Ctrl/Cmd+P 切换预览）
- `Teleport to="body"` + `body { overflow: hidden }`
- 脏数据保护：内容变化时，Esc/X/遮罩点击 → 二次确认（沿用 `BaseModal` 思路）

### 3.4 `MarkdownPreview.vue`

```vue
<template>
  <div class="markdown-body prose prose-sm max-w-none p-4 overflow-auto h-full"
    v-html="rendered" />
</template>
```

30 行实现，复用项目已有 `marked` + `marked-highlight` + `highlight.js`。

> ⚠️ **XSS 风险说明**：marked 17 默认不做 HTML 转义，`v-html` 渲染存在风险。**P0 阶段**因为输入方是内部已登录用户可控的 Prompt 内容，**风险可接受**。**P3 阶段**若向外部/不可信内容开放，需引入 `DOMPurify.sanitize(marked.parse(...))`，2 行代码改造。

### 3.5 复用点（按阶段）

| 页面 | 改造点 | 阶段 |
|------|--------|------|
| `AgentDefinitionManager` | 模板 + 分段值 + 新建弹窗 | P1 |
| `saas/SkillManager` | Skill 描述 | P2 |
| `saas/ReplyStyleManager` | 风格描述/示例 | P2 |
| `saas/SystemReplyStyleManager` | 系统回复模板 | P2 |
| `saas/TenantMgmt` | 备注 | P2 |
| `travel/HotelManager` / `AttractionManager` | 描述 | P2 |
| `followup/LeadManager` | 备注 | P2 |

> `ChatInput.vue` 不复用（含文件上传、发送按钮等特殊逻辑，不通用）。

---

## 四、实施步骤

### P0 — 基础组件（约 0.5 天）

1. 新建 `frontend/src/components/ui/MarkdownPreview.vue`
2. 新建 `frontend/src/components/ui/MyTextarea.vue`
   - 受控 textarea + v-model
   - 标签 / 占位符 / 字号 / 字符数 / 全屏按钮
   - 预留 `enablePreview` 槽位（暂不接 Preview）
3. `npm run build` 验证

### P1 — 全屏编辑 + 业务验证（约 0.5 天）

1. 新建 `frontend/src/components/ui/FullscreenTextEditor.vue`
   - Teleport to body、body 滚动锁定
   - 编辑/预览 Tab（接 `MarkdownPreview`）
   - 快捷键 + 脏数据保护
2. 改造 `AgentDefinitionManager.vue`：
   - 3 处大文本框 → `MyTextarea`
   - 移除 `autoResizeAll` 的 DOM 选择器逻辑（改用 MyTextarea 内部自动撑高）
3. 验证：输入 → 全屏 → 编辑 → 保存 → 原"保存草稿/提交新版本/全部保存"三按钮正常工作
4. `npm run build` 验证

### P2 — 全量替换（约 1 天）

按 3.5 节复用点清单逐个页面替换，确保：
- 旧 `@input` 钩子通过 `MyTextarea` 的 `input` 事件透传
- 旧 `class="section-auto-textarea"` 等自定义样式可被 `textareaClass` 覆盖
- 行为等价无回归

### P3 — 增强能力（约 0.5 天，按需）

- [ 已实现 ] MD 预览 Tab 完善（双栏/单栏切换、字号、滚动同步）
- [ ] 模板变量高亮（占位符 `{var_name}` 识别 + 跳转到对应分段）
- [ 已实现 ] AI 优化按钮 slot（接 `optimizeSection` 现有接口）
- [ ] 全文查找替换（Ctrl/Cmd+F）
- [ ] XSS 加固（DOMPurify）

---

## 五、关键风险与缓解

| 风险 | 缓解 |
|------|------|
| `v-html` 引入 XSS | P0 内部用户可控可接受；P3 引入 DOMPurify |
| 替换 `autoResizeAll` 的 `querySelectorAll` 逻辑导致旧 watch 失效 | MyTextarea 内部默认自动撑高；旧 watch 改为遍历 ref 数组调用 `resize()` |
| 父级 `overflow-hidden` 容器裁剪全屏 | `Teleport to="body"` 彻底脱离父级 DOM |
| 全屏与父级弹窗叠加（z-index 冲突） | Teleport 默认 z-index 60+，BaseModal 已用 z-50，预留 60 |
| `marked@17` 升级带来的 breaking | 锁定到当前 `^17.0.6`，若大版本变更需重新评估 |

---

## 六、验收标准

- [ ] 三个组件 `vue-tsc` 通过、`npm run build` 无错
- [ ] `AgentDefinitionManager` 三处大文本框全屏进/出无 bug、关闭不丢字
- [ ] MD 预览正确渲染标题、列表、代码块（含高亮）
- [ ] 字符数实时更新、与 `modelValue` 同步
- [ ] 全屏 Esc 关闭、Ctrl/Cmd+S 保存、Ctrl/Cmd+P 切换预览 三个快捷键生效
- [ ] 内容脏数据时关闭弹二次确认
- [ ] P2 阶段 5 个管理页面替换后无视觉/功能回归

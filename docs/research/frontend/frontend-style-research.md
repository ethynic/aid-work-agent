# 前端样式规划深度调研报告

> 调研日期：2026-05-26
> 目标：研究业界 AI Coding 友好的前端样式体系和设计规范，为本项目制定样式规划提供依据

---

## 一、调研概览

本报告调研了 2024-2026 年间主流的 AI Coding 前端样式方案，重点关注：与 Tailwind CSS 兼容、Vue 3 生态、AI 助手可理解/可执行的设计规范格式。

---

## 二、核心发现

### 2.1 Anthropic 官方前端设计 Skill

**来源**：[github.com/anthropics/claude-code](https://github.com/anthropics/claude-code)

Anthropic 官方发布的 `frontend-design` Skill，约 400 token 的指令集：

- **核心理念**：要求 AI 在生成代码前先确定"大胆的美学方向"
- **反模式清单**：禁止 Inter/Roboto 字体、紫色渐变白底等 AI 常见"塑料感"设计
- **技术要求**：CSS 变量统一颜色、区分字重、添加动效、渐变网格/噪点纹理背景
- **本项目状态**：已安装（`.claude/skills/frontend-design/SKILL.md`）

**适用性**：本项目已在使用，作为"创造阶段"的指导。

### 2.2 Anthropic 前端美学 Cookbook

**来源**：[platform.claude.com/cookbook](https://platform.claude.com/cookbook/coding-prompting-for-frontend-aesthetics)

三种策略指导 AI 生成高质量前端：

1. **逐维度引导**：分别指定排版、颜色、间距、动效等维度的具体要求
2. **参考设计灵感**：指定 IDE 主题、文化美学等具体风格参考
3. **显式排除默认值**：明确列出要避免的"AI 默认审美"

**适用性**：可将关键维度规则提取到项目的 CLAUDE.md 或 rules 文件中。

### 2.3 Vercel v0 系统提示词

**来源**：[agentic-design.ai](https://agentic-design.ai/prompt-hub/vercel/v0-20250306)

Vercel 的 v0 生成式 UI 工具的系统提示词：

- **严格技术栈**：React + Tailwind CSS + shadcn/ui
- **输出约束**：详细的代码格式规范，确保每次输出一致、可生产部署
- **组件复用**：优先复用已有组件，避免重复造轮子

**适用性**：其约束输出质量的思路值得借鉴，但技术栈是 React，不能直接套用。

### 2.4 shadcn/ui & shadcn-vue

**来源**：[ui.shadcn.com](https://ui.shadcn.com) | [shadcn-vue.com](https://www.shadcn-vue.com)

**shadcn/ui 的核心理念**：

- **非 npm 包**：组件源码直接复制到项目中，AI 可完整阅读和理解
- **一致性 API**：所有组件使用统一的变体系统（CVA + cn()）
- **AI 原生设计**：官方文档明确声明"为 AI 工具设计"

**shadcn-vue**（Vue 移植版）：

- 保持相同的"源码复制"理念
- 使用 `radix-vue` / `reka-ui` 作为无样式原语
- 支持 `class-variance-authority` (CVA) + `tailwind-merge` 变体系统

**对本项目的意义**：

| 维度 | 评估 |
|------|------|
| 技术兼容 | Vue 3 + Tailwind 3，完美兼容 |
| AI 友好度 | 极高——组件源码在项目中，AI 可完全理解 |
| 主题适配 | 基于 CSS 变量，可与现有 useTheme 系统整合 |
| 实施成本 | 中等——需要安装依赖、配置工具链、逐步替换现有组件 |
| 代码质量 | 显著提升——统一变体系统替代当前每个组件内联样式 |

### 2.5 Tailwind Variants（tailwind-variants）

**来源**：[tailwind-variants.org](https://www.tailwind-variants.org)

CVA 的增强替代品，专为 Vue/React 组件变体设计：

```typescript
import { tv } from 'tailwind-variants'

const button = tv({
  base: 'rounded-lg font-medium transition-colors',
  variants: {
    intent: {
      primary: 'bg-primary-600 text-white hover:bg-primary-700',
      secondary: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
      danger: 'bg-danger-600 text-white hover:bg-danger-700',
    },
    size: {
      sm: 'px-3 py-1.5 text-sm',
      md: 'px-4 py-2 text-base',
      lg: 'px-6 py-3 text-lg',
    },
  },
  defaultVariants: {
    intent: 'primary',
    size: 'md',
  }
})

// 使用
button({ intent: 'primary', size: 'sm' })
// → "rounded-lg font-medium transition-colors bg-primary-600 text-white hover:bg-primary-700 px-3 py-1.5 text-sm"
```

**优势**：

- **Slots 支持**：一个变体定义可包含多个插槽（如 button 的 base、icon、label）
- **响应式变体**：`md:intent="secondary"` 开箱即用
- **组件组合**：子组件可继承父组件的变体
- **TypeScript 类型安全**：完整的类型推导

**适用性**：非常适合解决当前项目"每个组件内联 Tailwind 类"的问题。即使不引入 shadcn-vue，也可独立使用。

### 2.6 Tailwind CSS v4 Design Tokens

**来源**：[tailwindcss.com/blog/tailwindcss-v4](https://tailwindcss.com/blog/tailwindcss-v4)

v4 的重大变化：

```css
@import "tailwindcss";

@theme {
  --color-primary: #3b82f6;
  --font-heading: "Inter", sans-serif;
  --breakpoint-xs: 475px;
}
```

- **配置移到 CSS 中**：不再需要 `tailwind.config.js`
- **CSS 自定义属性**：所有设计令牌自动成为 CSS 变量
- **零配置内容检测**：自动扫描项目文件

**对本项目的影响**：

| 维度 | 评估 |
|------|------|
| 当前版本 | Tailwind 3.4.1，稳定可用 |
| 升级时机 | 建议在下一个大版本迭代时升级 |
| 当前方案 | CSS 变量 + Tailwind config 映射已足够用 |

**结论**：当前项目已有的 CSS 变量 + Tailwind config 方案是正确的，升级到 v4 可作为后续优化方向。

### 2.7 Vercel Agent Skills 生态

**来源**：[github.com/vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills)

三个相关的 Skill：

| Skill | 内容 | 本项目状态 |
|-------|------|-----------|
| web-design-guidelines | 拉取最新 Vercel 设计准则审查 UI | 已安装 |
| composition-patterns | 复合组件模式、避免布尔属性泛滥 | 未安装 |
| react-best-practices | 58 条规则，8 个类别 | React 专用，不适用 |

**composition-patterns 的 Vue 适用规则**：

- 避免 boolean prop 泛滥（用变体替代多个 boolean）
- 复合组件模式（父组件 + 子组件通过 provide/inject 协作）
- 显式的组件变体定义

### 2.8 dx-tooling/landingpages-ai-template

**来源**：[github.com/dx-tooling/landingpages-ai-template](https://github.com/dx-tooling/landingpages-ai-template)

一个提供"Living Styleguide + AI IDE 工具链"的模板项目：

- **核心理念**：将设计规范作为活文档，与代码同步维护
- **AI 集成**：IDE 中的 AI 助手读取 styleguide 生成一致的新页面
- **模板系统**：预定义页面模板（列表页、详情页、表单页等）

**适用性**：其"页面模板"概念与本项目之前删除的 `page_patterns.md` 非常相似，值得借鉴其结构化方式。

---

## 三、本项目现状分析

### 3.1 已有的样式基础设施

| 基础设施 | 状态 | 评价 |
|---------|------|------|
| CSS 变量体系 | ✅ 完善 | 6 色阶，11 级色阶，语义 token |
| Tailwind 配置 | ✅ 完善 | 映射到 CSS 变量，支持主题切换 |
| 主题切换系统 | ✅ 完善 | 8 套主题色，useTheme composable |
| AppHeader 组件 | ✅ 规范 | 统一页面头部，含汉堡按钮和更多菜单 |
| MenuSidebar 组件 | ✅ 规范 | 可收缩侧边栏 |
| 页面布局规范 | ✅ 有文档 | frontend_dev.md 中详细描述 |

### 3.2 存在的问题

| 问题 | 严重度 | 说明 |
|------|--------|------|
| 无共享基础组件 | 🔴 高 | 40 个组件，无 Button/Input/Card/Modal 等基础组件 |
| 样式方案不一致 | 🔴 高 | 新组件用语义 token，28 个旧组件硬编码 slate-/cyan-（681 处） |
| 无组件变体系统 | 🟡 中 | 每个组件内联 Tailwind 类，重复代码多 |
| 双 CSS 方案并存 | 🟡 中 | Tailwind 工具类 vs scoped 自定义 CSS，无明确规范 |
| 无暗色模式 | 🟡 中 | 无 darkMode 配置，无 dark: 类 |
| page_patterns.md 已删 | 🟡 中 | 页面模式规范丢失，列表页/表单页无统一模式 |
| Tailwind v3 | 🟢 低 | 当前版本稳定，但 v4 有更好体验 |

### 3.3 现有设计系统已有的能力

```
✅ CSS 变量 + Tailwind 映射（主题切换基础设施）
✅ 语义 token（bg-surface, text-default, border-default 等）
✅ 8 套主题色配置
✅ 页面三区域布局规范（MenuSidebar + AppHeader + Content）
✅ 自定义滚动条、动画、Markdown 渲染样式
✅ 安全区域高度工具类（h-safe-screen）
✅ 语义化阴影（shadow-message, shadow-float, shadow-sticky）
```

---

## 四、推荐方案

基于调研结果和项目现状，推荐**渐进式增强**方案，分为三个阶段：

### 阶段一：统一基础层（低风险，高回报）

1. **恢复并增强 page_patterns.md**：基于已删除的页面模式规范，补充列表页、表单页、详情页的标准模板
2. **统一 CSS 方案**：明确所有组件使用 Tailwind + 语义 token，逐步替换 681 处硬编码颜色
3. **定义基础组件变体**：使用 `tailwind-variants` 定义 Button、Input、Card 等基础组件的变体

### 阶段二：引入组件系统（中等风险）

1. **引入 shadcn-vue**：逐步添加基础 UI 组件（Button、Input、Dialog、Select 等）
2. **整合主题系统**：将 shadcn-vue 的 CSS 变量映射到现有 useTheme 系统
3. **建立组件库目录**：`src/components/ui/` 存放基础组件

### 阶段三：精细化优化（长期）

1. **升级 Tailwind v4**：利用 @theme 块简化配置
2. **添加暗色模式**：基于 CSS 变量系统实现
3. **响应式优化**：统一的断点和响应式规范

---

## 五、参考资源链接

| 资源 | 链接 | 说明 |
|------|------|------|
| Anthropic frontend-design Skill | github.com/anthropics/claude-code | 已安装 |
| Anthropic 前端美学 Cookbook | platform.claude.com/cookbook | 可提取规则 |
| Vercel v0 系统提示词 | agentic-design.ai/prompt-hub | 输出约束思路 |
| shadcn/ui | ui.shadcn.com | React 组件库 |
| shadcn-vue | shadcn-vue.com | Vue 组件库 |
| Tailwind Variants | tailwind-variants.org | 组件变体系统 |
| CVA (Class Variance Authority) | cva.style/docs | 变体定义工具 |
| Vercel Agent Skills | github.com/vercel-labs/agent-skills | composition-patterns |
| Tailwind CSS v4 | tailwindcss.com/blog/tailwindcss-v4 | 下一代配置方式 |
| Tailwind 大型代码库指南 | makersden.io/blog | 维护性模式 |
| dx-tooling AI 模板 | github.com/dx-tooling/landingpages-ai-template | Living Styleguide |
| Preline UI | preline.co | Tailwind 组件库 |
| Magic UI | magicui.design | 动效组件 |
| Aceternity UI | ui.aceternity.com | 高级动效组件 |

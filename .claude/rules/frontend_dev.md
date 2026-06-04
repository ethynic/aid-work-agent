# 前端开发指南

## 组件变体系统

项目使用 `tailwind-variants` 建立统一的组件样式变体系统。

### 变体定义

所有组件样式变体定义在 `frontend/src/variants/` 目录：

| 文件 | 用途 |
|------|------|
| `variants/button.ts` | 按钮（primary/secondary/danger/ghost × sm/md/lg） |
| `variants/input.ts` | 输入框（default/error/success × sm/md/lg） |
| `variants/select.ts` | 下拉选择 |
| `variants/card.ts` | 卡片容器（多插槽） |
| `variants/badge.ts` | 徽章/标签（primary/success/warning/danger/info/neutral） |
| `variants/table.ts` | 表格（多插槽） |
| `variants/modal.ts` | 模态框（sm/md/lg/xl，多插槽） |
| `variants/pagination.ts` | 分页 |

### 基础 UI 组件

所有基础组件在 `frontend/src/components/ui/` 目录，是最薄的渲染壳：

| 组件 | 用途 |
|------|------|
| `BaseButton.vue` | 按钮 — 通过 `intent`/`size` 控制样式 |
| `BaseInput.vue` | 输入框 — 支持 `v-model`、`state` |
| `BaseSelect.vue` | 下拉选择 — 支持 `v-model` |
| `BaseCard.vue` | 卡片容器 — 支持 `title`、`header/footer` 插槽 |
| `BaseBadge.vue` | 徽章 — 通过 `intent` 控制颜色 |
| `BaseModal.vue` | 模态框 — 支持 `v-model`、`size`、`scrollable` |
| `BaseTable.vue` | 表格 — 通过 `columns`/`data` + 具名插槽 |
| `BasePagination.vue` | 分页 — 支持 `v-model:currentPage` |

### 使用原则

1. **新页面必须使用 Base* 组件**，不允许自行定义 `.btn-primary`、`.data-table` 等 CSS 类
2. **颜色必须使用语义 token**（`primary-*`、`danger-*`、`bg-surface` 等）
3. **页面模式规范要点速查** [page_patterns.md](./page_patterns.md)
4. **创建列表页时**，必须按 [list-page-convention.md](./list-page-convention.md) 规范执行
5. **创建编辑页、详情页时**，必须按 [detail-page-convention.md](./detail-page-convention.md) 规范执行

## 颜色使用规范

**所有颜色必须使用语义 token，禁止硬编码颜色值。**

### 可用的语义 token

| 用途 | Token 类名 | 说明 |
|------|-----------|------|
| 主色调 | `primary-50` ~ `primary-950` | 品牌色，用于主要操作 |
| 成功 | `success-50` ~ `success-950` | 成功状态 |
| 警告 | `warning-50` ~ `warning-950` | 警告状态 |
| 危险 | `danger-50` ~ `danger-950` | 错误/删除操作 |
| 信息 | `info-50` ~ `info-950` | 提示信息 |
| 中性灰 | `gray-50` ~ `gray-950` | 通用灰色 |
| 页面背景 | `bg-canvas` | 页面底色 |
| 卡片背景 | `bg-surface` | 白色面板 |
| 悬停背景 | `bg-surface-hover` | 行悬停 |
| 主文字 | `text-default` | 正文 |
| 次要文字 | `text-muted` | 辅助文字 |
| 边框 | `border-default` | 默认边框 |
| 悬停边框 | `border-hover` | 悬停边框 |

### 禁止的硬编码颜色

| 禁止 | 替代为 |
|------|--------|
| `slate-*` | `gray-*` / 语义 token |
| `cyan-*` | `primary-*` |
| `red-*` | `danger-*` |
| `green-*` | `success-*` |
| `blue-*` | `info-*` / `primary-*` |
| `orange-*` | `warning-*` |
| 自定义 CSS 变量（`var(--bg-primary)` 等） | 使用上述语义 token |

## 🎨 UI/UX 设计与审查工作流

当任务涉及前端页面生成、UI 组件开发或界面优化时，严格遵循以下双技能协作流程：

### 1. 创造阶段：frontend-design
**触发条件**：任何从零开始生成页面、组件，或需要大幅重构 UI 视觉风格的任务。
**执行要求**：
- 在编写任何前端代码前，必须优先调用 `frontend-design` 技能。
- 确保生成的界面具备高级审美，避免 AI 常见的“塑料感”和同质化设计。

### 2. 审查阶段：web-design-guidelines
**触发条件**：在 `frontend-design` 完成代码生成后，或针对现有的 UI 代码进行优化时。
**执行要求**：
- 必须调用 `web-design-guidelines` 技能对产出代码进行“体检”。
- 重点审查：可访问性（Accessibility）、响应式布局、语义化标签及 Vercel 设计准则的合规性。
- 根据审查结果自动修复代码中的硬伤，确保交付专业、合规的前端代码。

### ⚠️ 核心约束
**IMPORTANT**: 严禁跳过审查阶段直接交付代码。必须形成“先设计创造，后规范审查”的完整闭环。

## 命令

```bash
cd frontend && npm install               # 安装前端依赖
cd frontend && npm run dev               # 开发服务器（端口 5173）
cd frontend && npm run build             # 生产环境构建
```

### 测试
```bash
cd frontend && npm test                   # 运行前端测试
cd frontend && npm run test:coverage      # 带覆盖率
cd frontend && npm run test:watch         # 监听模式
```

## 开发规范

### 日志规范
```javascript
// 长期保留日志
console.log('前端日志：开始验证用户凭证', { username });

// 临时调试日志（bug 修复后删除）
console.log('临时调试：API 响应', response);
```

### 缓存使用规范
**非必要，不使用缓存。** 优先使用直接请求：
```javascript
// ✅ 推荐
export const knowledgeAPI = {
  list: (params) => api.get('/knowledge/', { params }),
}
```

### 页面布局一致性规范

#### 标准页面框架
所有从左侧菜单导航进入的页面，必须遵循统一的三区域布局：

```
┌─────────────────────────────────────────────┐
│  ┌─────────┐  ┌───────────────────────────┐ │
│  │         │  │ ┌─────┬────────┬────────┐ │ │
│  │ 菜单栏   │  │ │ 汉堡 │  标题  │  更多  │ │ │  ← AppHeader
│  │(可收缩)  │  │ └─────┴────────┴────────┘ │ │
│  │         │  │                           │ │
│  │         │  │      页面具体内容区域      │ │
│  │         │  │                           │ │
│  └─────────┘  └───────────────────────────┘ │
└─────────────────────────────────────────────┘
```

**核心要求**：
1. **左侧菜单栏**：由 `MenuSidebar`（租户前台）或 `PortalLayout` 中的固定菜单（平台管理后台）提供，支持收缩/展开
2. **右侧上方标题栏**：必须使用 `AppHeader` 组件，包含：
   - 左侧 **汉堡按钮**（`@toggle-sidebar`）：用于展开/收起左侧菜单栏
   - 中间 **页面标题**
   - 右侧 **"更多"菜单**：放置页面相关快捷操作（如返回对话、凭据管理等）
3. **右侧中下方内容区**：放置页面具体内容

#### 场景一：PortalLayout 子页面（租户前台 /t/:tenant_id/*）

这些页面已被 `PortalLayout` 包裹，`MenuSidebar` 由布局统一渲染。**页面组件只需负责右侧区域**，但必须使用 `AppHeader`：

```vue
<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="页面标题"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <!-- 更多菜单项 -->
        <button @click="goToChat(); closeMenu()">返回对话</button>
      </template>
    </AppHeader>

    <div class="flex-1 overflow-y-auto p-6">
      <!-- 页面具体内容 -->
    </div>
  </div>
</template>

<script setup>
import { inject } from 'vue'
import AppHeader from '@/components/AppHeader.vue'

// 从 PortalLayout 注入侧边栏控制
const toggleSidebarFn = inject<() => void>('toggleSidebar')

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
</script>
```

**参考实现**：`TenantSettings.vue`、`TenantUserManager.vue`、`ChannelConfig.vue`

#### 场景二：独立页面（非 PortalLayout 子页面）

这些页面需要自己渲染完整的 `MenuSidebar` + `AppHeader` 组合：

```vue
<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <main class="flex-1 flex overflow-hidden">
      <MenuSidebar
        :is-collapsed="isSidebarCollapsed"
        ...
        @collapse="isSidebarCollapsed = true"
      />
      <div class="flex-1 flex flex-col min-w-0">
        <AppHeader
          title="页面标题"
          @toggle-sidebar="isSidebarCollapsed = !isSidebarCollapsed"
          ...
        />
        <div class="flex-1 overflow-hidden p-6">
          <!-- 页面具体内容 -->
        </div>
      </div>
    </main>
  </div>
</template>
```

**参考实现**：`KnowledgeBase.vue`、`AllSessions.vue`

#### 禁止项

❌ **严禁页面自行实现标题栏替代 `AppHeader`**。以下做法会导致菜单栏收缩后无法展开，必须禁止：

```vue
<!-- ❌ 错误示例：自行实现标题栏，缺少汉堡按钮 -->
<div class="p-6">
  <div class="flex items-center justify-between mb-6">
    <h1 class="text-2xl font-bold">页面标题</h1>
    <div>页面操作按钮...</div>
  </div>
  ...
</div>
```

如果发现已有页面存在此问题，必须按上述标准模板重构。

### 枚举值定义规范
**涉及到字段枚举值的判断代码，必须以 `frontend/src/api/enums.ts` 为准。**

所有 SaaS 相关表字段的枚举值（如租户状态、订阅状态、支付状态等）统一在 `frontend/src/api/enums.ts` 中定义。

```typescript
// ✅ 正确：使用枚举 + 映射表
import { TenantStatus, TenantStatusMap } from '@/api/enums'

const label = TenantStatusMap[TenantStatus.ACTIVE].label  // '正常'

// ❌ 错误：硬编码数字或字符串
if (status === 1) { ... }
if (status === 'active') { ... }
```

#### 枚举值设计原则

1. **数据库字段类型尽量使用 TEXT**：这样管理员在检查数据时无需查找枚举定义即可理解字段含义。

2. **前后端枚举值尽量与数据库存储值一致**：减少 mapping 翻译，提高代码可读性。

```typescript
// ✅ 推荐：枚举值 = 数据库存储值
export enum TenantStatus {
  ACTIVE = 'active',       // 数据库存 "active"
  SUSPENDED = 'suspended', // 数据库存 "suspended"
}

// ❌ 不推荐：枚举值需额外映射
export enum TenantStatus {
  ACTIVE = 1,      // 数据库存 1，需翻译为 "active"
  SUSPENDED = 0,   // 数据库存 0，需翻译为 "suspended"
}
```

3. **前端显示值不受此限制**：显示值通常为中文，通过映射表实现（如 `TenantStatusMap`）。

**如需修改字段枚举值，注意前后端协调修改**：同时更新 `frontend/src/api/enums.ts`（前端）和 `src/saas/models/enums.py`（后端）。

### 时间显示规范

本项目后端使用 PostgreSQL `TIMESTAMP`（无时区），API 返回的时间字符串为本地时间，不带 `Z` 后缀（例如 `"2026-05-15T11:49:45.429739"`）。前端直接解析即可，**禁止手动拼接时区后缀**。

```javascript
// ✅ 正确：直接解析后端返回的本地时间字符串
const date = new Date('2026-05-15T11:49:45.429739')

// ❌ 错误：强行添加 Z 会导致浏览器按 UTC 解析，再转回本地时区，多出 8 小时
new Date('2026-05-15T11:49:45.429739' + 'Z')  // 结果会变成 19:49
```

## 运行构建
所有前端代码修改后，都需要运行构建命令，确保没有语法错误。构建命令如下：

```bash
cd frontend
npm run build
```

## 测试指南

### 测试目录结构

```
frontend/src/__tests__/      # 前端测试（Vitest + Vue Test Utils + MSW）
├── setup.ts                 # jsdom 环境 + MSW 启动
├── mocks/                   # API mock（handlers.ts、server.ts、fixtures.ts）
├── composables/             # Composable 逻辑测试
├── api/                     # API 层测试
└── components/              # 组件测试
```

### 为新功能编写测试

**前端组件/Composable 测试**，在 `frontend/src/__tests__/` 对应目录添加 `.test.ts` 文件。API 请求由 MSW 自动拦截（配置在 `mocks/handlers.ts`）。

## 语言说明

UI 文本为中文。代码标识符为英文。

## SaaS 租户隔离规范

### 核心规则

租户前台页面 `/t/{tenant_id}` 中，**所有需要认证的 API 请求都必须传递 `X-Tenant-Id` header**。

### 如何正确传递 X-Tenant-Id

| API 文件位置 | 使用方式 |
|-------------|----------|
| 独立使用认证头 | 使用 `getAuthHeader()` from `@/api/auth`，该函数已自动包含 `X-Tenant-Id` |
| SaaS 管理 API | 使用 `getSaasAuthHeader()` from `@/api/saasTenant`，该函数已自动包含 `X-Tenant-Id` |
| 自定义 | 从 URL `^/t/([^/]+)` 提取 `tenant_id` 并添加到 headers |

### 正确示例

```typescript
// ✅ 正确：使用 getAuthHeader() 自动包含 X-Tenant-Id
import { getAuthHeader } from '@/api/auth'

export async function listDocuments(): Promise<DocumentListResponse> {
  const response = await fetch(`${API_BASE}/documents`, {
    headers: { ...getAuthHeader() }
  })
  return response.json()
}
```

### 必须传递 X-Tenant-Id 的场景

1. **平台管理员访问租户前台**：平台管理员账号本身没有租户属性，必须通过 `X-Tenant-Id` 告诉后端当前操作哪个租户
2. **租户管理员和普通用户**：虽然用户自身 token 包含 tenant_id，但为了保持一致性，前端仍需传递 `X-Tenant-Id`，后端会进行校验

### 检查清单

添加新 API 时，请确认：
- [ ] 是否使用了 `getAuthHeader()` 或 `getSaasAuthHeader()` 获取认证头
- [ ] 认证头是否正确传递给 `fetch`
- [ ] 不要自己硬编码认证头，复用现有工具函数

### 已修复的问题记录

以下文件已修复遗漏 `X-Tenant-Id` 的问题：
- `api/auth.ts` - `getAuthHeader()` 添加 `X-Tenant-Id`
- `api/knowledge.ts` - 所有 API 添加认证头
- `api/agent.ts` - `deleteFile()` 添加认证头
- `composables/useTenantAuth.ts` - `getAuthHeader()` 添加 `X-Tenant-Id`
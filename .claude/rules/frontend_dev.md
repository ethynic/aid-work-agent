# 前端开发指南

## 页面设计
**调用 'frontend-design' 技能来设计前端页面** 每次前端页面改动，都调用 `frontend-design` 技能，以获得高质量、有设计感的前端代码。

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
从 `MenuSidebar` 导航进入的页面**必须**保留 `MenuSidebar` + `AppHeader` 布局。

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
# 业务页面元数据注册表设计

## Context

配置子智能体的业务页面时，用户不知道有哪些页面可用，只能手动输入路由路径。需要一个页面元数据注册表，让用户能浏览、搜索、选择可用的业务页面，并支持 AI 根据智能体描述自动推荐相关页面。

## 数据结构设计

### 页面元数据文件 `configs/page_metadata.yaml`

每个页面条目包含：

| 字段 | 类型 | 说明 |
|------|------|------|
| page_id | string | 全局唯一 ID，格式 `{domain}-{child-route}` |
| title | string | 页面中文显示名 |
| description | string | 页面功能描述（中文，用于搜索匹配和 AI 推荐） |
| route | string | 路由路径（绝对路径，不含租户前缀） |
| icon | string | emoji 图标 |
| domain | string | 所属业务域（对应路由的父级路径段） |
| domain_label | string | 业务域的中文名（用于分组显示） |
| status | string | 开发状态：`published`（已发布）、`developing`（开发中）、`planned`（规划中） |

```yaml
domains:
  - id: trade-specialist
    label: 外贸获客
  - id: travel-consultant
    label: 旅游咨询
  - id: customer-followup
    label: 客户跟进
  - id: complaint
    label: 投诉处理
  - id: after-sales
    label: 售后服务

pages:
  - page_id: trade-specialist-customers
    title: 客户管理
    description: 管理外贸客户信息，包括客户基础资料、联系方式、跟进状态等
    route: /trade-specialist/customers
    icon: "👥"
    domain: trade-specialist
    status: published

  - page_id: travel-consultant-vehicles
    title: 车辆价格
    description: 管理旅游用车信息，包括车型、座位数、租车价格等
    route: /travel-consultant/vehicles
    icon: "🚗"
    domain: travel-consultant
    status: published

  # ... 其余页面 ...

  - page_id: travel-consultant-itinerary
    title: 行程规划
    description: 管理旅游行程模板，包括行程天数、每日安排、费用预算等
    route: /travel-consultant/itinerary
    icon: "🗺️"
    domain: travel-consultant
    status: developing   # 开发中，不暴露给用户选择
```

API 默认只返回 `status: published` 的页面，排除 `developing` 和 `planned` 状态。

metadata 文件可以记录还未开发的页面及其路由，通过 `status` 字段标记状态，选择器只展示已发布的页面。

## 后端 API 设计

### 文件：`src/api/page_metadata.py`（新建）

接入到已有的 `agent_definitions.router`（与 `/meta/tools`、`/meta/skills` 同级）。

#### 端点 1：`GET /api/admin/agent-definitions/meta/pages`

返回已发布的页面元数据列表。

- 查询参数：`domain`（可选，按业务域过滤）
- 只返回 `status: published` 的页面
- 首次调用时加载 YAML 并缓存到模块级变量

#### 端点 2：`POST /api/admin/agent-definitions/meta/pages/recommend`

AI 推荐相关页面。

请求体：
```json
{
  "agent_description": "一个旅游咨询智能体，帮助客户规划旅行行程",
  "current_pages": ["travel-consultant-vehicles"]
}
```

响应：
```json
{
  "success": true,
  "data": {
    "recommended": [
      {
        "page_id": "travel-consultant-attractions",
        "title": "景点门票",
        "reason": "智能体涉及旅行规划，景点门票是核心数据",
        "relevance_score": 0.95
      }
    ]
  }
}
```

实现要点：
- 只从 `published` 页面中推荐
- 排除 `current_pages` 中已选的页面
- 复用 `llm_gateway.chat()` 调用 LLM，temperature=0.1
- LLM 返回 JSON，解析失败时降级返回所有未选页面（不排序）

## 前端设计

### 文件：`frontend/src/api/agentDefinitions.ts`（修改）

新增类型和 API 函数：

```typescript
export interface PageMeta {
  page_id: string
  title: string
  description: string
  route: string
  icon: string
  domain: string
  domain_label: string
}

export interface RecommendedPage {
  page_id: string
  title: string
  reason: string
  relevance_score: number
}

export function listPageMeta(params?: { domain?: string }): Promise<...>
export function recommendPages(data: { agent_description: string; current_pages?: string[] }): Promise<...>
```

### 文件：`frontend/src/components/PageMetaSelector.vue`（新建）

页面选择器弹框组件，使用 `BaseModal`（size="lg"）。

**Props**：
- `v-model` 控制显示
- `selectedPageIds: string[]` 已选页面 ID
- `agentDescription: string` 智能体描述（用于 AI 推荐）

**布局**：
```
┌─────────────────────────────────────────┐
│  选择业务页面                        [X] │
├─────────────────────────────────────────┤
│  [🔍 搜索页面名称或功能...          ]   │
│  [✨ AI 智能推荐]                       │
├─────────────────────────────────────────┤
│  ▼ 旅游咨询                             │
│    ☑ 🚗 车辆价格  /travel-consultant/.. │
│    ☐ 🏛️ 景点门票  /travel-consultant/.. │
│    ☐ 🏨 酒店房型  /travel-consultant/.. │
│  ▼ 投诉处理                             │
│    ☐ 📋 投诉列表  /complaint/list       │
├─────────────────────────────────────────┤
│  已选 3 个页面        [确认选择]         │
└─────────────────────────────────────────┘
```

**AI 推荐区域**：点击"AI 智能推荐"后，在搜索栏下方展示推荐结果，每条显示推荐原因，带一键选择按钮。

**选择逻辑**：
- 每个页面一行：复选框 + icon + title + route
- 按业务域分组显示，组可折叠
- 搜索时过滤 title/description
- 确认时将选中的页面转为 `BusinessPage` 格式（`{ id, title, icon, route }`）emit 回去

### 文件：`frontend/src/components/AgentDefinitionManager.vue`（修改）

业务页面配置区域改造：
- 保留已选页面展示（icon + title + route，可删除）
- 原来的"添加页面"按钮改为"从库中选择"，打开 PageMetaSelector
- 保留一个"手动添加"入口作为兜底（用于不在注册表中的自定义页面）

## 新页面注册流程

### 现状问题

当前开发一个新的业务页面，开发者需要改 3 处互不关联的地方：
1. 创建 Vue 组件（如 `frontend/src/components/travel/ItineraryManager.vue`）
2. 在 `frontend/src/main.ts` 中注册路由（demo 模式 + tenant 模式各一份）
3. 在子智能体的 `business_pages` 配置中手动添加条目

没有统一的地方告诉开发者"系统里有哪些页面"、"一个新页面需要做哪些事"。

### 注册机制：metadata 优先，路由跟随

核心思路：**`configs/page_metadata.yaml` 是页面的唯一注册入口**，路由注册通过读取 metadata 自动生成。

#### 开发一个新页面的步骤

```
1. 在 configs/page_metadata.yaml 中添加页面条目（status: planned）
   ↓
2. 创建 Vue 组件（frontend/src/components/<domain>/XXXManager.vue）
   ↓
3. 在 main.ts 中注册路由（demo + tenant 两份）
   ↓
4. 开发完成后，将 status 改为 published
```

**第 1 步：在 metadata 中注册（最早做）**

在 `configs/page_metadata.yaml` 的 `pages` 列表末尾添加条目：

```yaml
  - page_id: travel-consultant-itinerary
    title: 行程规划
    description: 管理旅游行程模板，包括行程天数、每日安排、费用预算等
    route: /travel-consultant/itinerary
    icon: "🗺️"
    domain: travel-consultant
    status: planned        # 规划阶段
```

即使页面还没开发，也先注册进去，状态标为 `planned`。这有以下好处：
- 其他开发者能看到这个页面已被规划
- AI 推荐时知道它的存在（但不会推荐给用户，因为是 planned 状态）
- 路由路径在设计阶段就确定，避免后续冲突

**第 2 步：创建 Vue 组件**

按 `frontend/src/components/<domain>/` 目录组织。组件名遵循 `{功能}Manager.vue` 命名。

**第 3 步：注册路由**

在 `frontend/src/main.ts` 中，参照现有模式添加路由。每个业务域的路由需要注册两份：

```ts
// Demo 模式（顶级路由）
{
  path: '/travel-consultant',
  component: () => import('./components/BaseBusinessLayout.vue'),
  children: [
    // ... 现有子路由
    { path: 'itinerary', name: 'travel-consultant-itinerary', component: () => import('./components/travel/ItineraryManager.vue') },
  ]
}

// Tenant 模式（/t/:tenant_id 下）
{
  path: 'travel-consultant',
  component: () => import('./components/BaseBusinessLayout.vue'),
  children: [
    // ... 现有子路由
    { path: 'itinerary', name: 'tenant-travel-consultant-itinerary', component: () => import('./components/travel/ItineraryManager.vue') },
  ]
}
```

**第 4 步：发布**

开发完成并测试通过后，将 metadata 中的 `status` 改为 `published`。此时页面才会出现在选择器中，供智能体配置时选择。

#### 状态流转

```
planned → developing → published
                        ↑
              开发完成，测试通过
```

| 状态 | 含义 | 选择器可见 | AI 推荐可见 |
|------|------|-----------|------------|
| `planned` | 规划中，未开始开发 | 否 | 否 |
| `developing` | 开发中，路由可能未就绪 | 否 | 否 |
| `published` | 已发布，可正常使用 | 是 | 是 |

#### metadata 与路由的一致性检查

后端 API 加载 metadata 时，不会做路由存在性检查（因为后端不知道前端路由）。一致性由开发流程保证：

- 新页面必须先在 metadata 注册，再开发组件和路由
- 页面状态变更（developing → published）时，开发者应确认路由已注册且组件可用
- 未来可考虑添加一个前端启动时的校验脚本，比对 metadata 中的 published 路由是否都有对应的 Vue Router 路由

## 实现步骤

1. 创建 `configs/page_metadata.yaml` — 所有页面元数据（含 status 字段）
2. 创建 `src/api/page_metadata.py` — 后端 API（列表 + AI 推荐）
3. 在 `src/api/agent_definitions.py` 中注册新路由
4. 更新 `frontend/src/api/agentDefinitions.ts` — 添加前端 API 调用
5. 创建 `frontend/src/components/PageMetaSelector.vue` — 选择器组件
6. 修改 `frontend/src/components/AgentDefinitionManager.vue` — 接入选择器

## 验证方式

1. 调用 `GET /api/admin/agent-definitions/meta/pages` 验证只返回 published 页面
2. 调用 `POST /api/admin/agent-definitions/meta/pages/recommend` 验证 AI 推荐结果合理
3. 在管理后台打开子智能体编辑页，点击"从库中选择"，验证页面列表、搜索、分组显示
4. 选择页面后保存，在聊天侧边栏验证业务页面链接正常显示
5. `cd frontend && npm run build` 确保构建通过

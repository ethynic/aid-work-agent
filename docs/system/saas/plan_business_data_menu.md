# 业务数据菜单设计计划

> **[2026-09-02 注记]** demo 模式与 `SAAS_ENABLED` 开关已从代码库彻底移除，系统恒为 SaaS 多租户模式。本文档描述的历史方案仅供追溯，其中 demo / 非 SaaS 分支内容已失效。

## 背景

当前数字员工的业务数据（如"我的客户"）放在右上角"更多"下拉菜单中，存在以下问题：

1. **入口深**：需要两次点击（更多 → 我的客户）才能到达
2. **与数字员工无关联**：无论切换到哪个数字员工，"更多"菜单内容都一样，用户无法直接感知"当前数字员工有什么业务数据"
3. **体验不一致**：外贸智能体有"我的客户"业务数据，但CEO智能体没有，混在一起不够清晰

---

## 需求变更

### 1. 新增左侧"业务数据"菜单

在左侧菜单的"历史会话"上方，新增"业务数据"分组，放在"新会话"和"知识中心"之后。

**菜单结构示意**：

```
┌─────────────────────────┐
│  [新会话]                │
│  知识中心               │
├─────────────────────────┤
│  📊 业务数据              │  ← 新增分组标题
│  ── 外贸获客智能体 ──     │  ← 当前数字员工名称标签
│    · 👥 我的客户          │  ← 点击跳转 /trade-specialist/customers
│    · 📧 邮件记录          │  ← 点击跳转 /trade-specialist/email-records
│    · 📊 匹配统计          │  ← 点击跳转 /trade-specialist/match-stats
├─────────────────────────┤
│  🕐 历史会话              │
└─────────────────────────┘
```

### 2. 子智能体定义自己的业务数据菜单

在 `SUBAGENT.md` 的 YAML Front Matter 中增加 `business_pages` 字段，允许每个子智能体声明自己的业务数据页面。

**示例配置**：

```yaml
# subagents/trade-specialist/SUBAGENT.md
---
name: 外贸获客智能体
description: 帮助企业获取海外客户
capabilities:
  - 客户匹配
  - 邮件推广
  - 客户管理
triggers:
  keywords:
    - 外贸
    - 客户
    - 海外
business_pages:
  - id: customers
    title: 我的客户
    icon: 👥
    route: /trade-specialist/customers
  - id: email-records
    title: 邮件记录
    icon: 📧
    route: /trade-specialist/email-records
  - id: match-stats
    title: 匹配统计
    icon: 📊
    route: /trade-specialist/match-stats
---
```

### 3. 上下文感知的菜单切换

切换数字员工时，左侧"业务数据"分组的内容随之变化：

| 当前数字员工 | 业务数据菜单 |
|-------------|-------------|
| 外贸获客智能体 | 我的客户、邮件记录、匹配统计 |
| HR智能体 | 员工管理、考勤记录（待实现） |
| CEO智能体 | （无业务数据，分组隐藏） |

**用户体验考量**：

- 菜单变化是**自洽且可理解的**，因为分组标题清晰标注了当前数字员工名称
- 切换到无业务数据的数字员工（如CEO智能体）时，整个"业务数据"分组自动隐藏
- 这类似于手机切换 App 时 Tab 栏会变化，用户不会感到困惑

### 4. 将"我的客户"从"更多"迁移到左侧

当前"我的客户"硬编码在 `ChatContainer.vue` 的 `#menu-items` slot 中，需要：

1. 将其从右上角"更多"菜单移除
2. 改为从子智能体配置中动态获取
3. 统一展示在左侧"业务数据"分组下

### 5. CEO智能体的"工作总览"

**TODO**：CEO智能体作为总调度者，未来可配置"工作总览"页面，提供跨子智能体的聚合视图。但MVP阶段先不做，留作后续扩展点。

### 6. 租户隔离设计

业务数据必须支持多租户隔离，具体规则如下：

| 模式 | tenant_id 行为 | 说明 |
|------|---------------|------|
| **演示模式** | `tenant_id = null`（空） | 所有业务数据不关联租户，共享数据 |
| **租户模式** | `tenant_id` 必填 | 所有业务数据必须关联租户 ID，实现完全隔离 |

**数据模型扩展**：

所有业务数据表（客户表、邮件记录表、匹配统计表等）都需要增加 `tenant_id` 字段：

```sql
-- 示例：客户表
CREATE TABLE customers (
    id UUID PRIMARY KEY,
    tenant_id UUID,                    -- 租户ID，演示模式为空
    user_id UUID,                      -- 创建人
    company_name VARCHAR(255),
    contact_name VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 索引：租户隔离查询
CREATE INDEX idx_customers_tenant_id ON customers(tenant_id);
```

**API 请求规范**：

```
┌─────────────────────────────────────────────────────────────┐
│ 演示模式（/api/*）                                            │
│ - 请求 Header：不带 X-Tenant-Id                               │
│ - SQL 查询：tenant_id IS NULL                                 │
├─────────────────────────────────────────────────────────────┤
│ 租户模式（/api/saas/*）                                      │
│ - 请求 Header：必须带 X-Tenant-Id                             │
│ - SQL 查询：tenant_id = :current_tenant_id                   │
└─────────────────────────────────────────────────────────────┘
```

**前端处理**：

```typescript
// 业务数据 API 调用时，根据当前模式自动携带 tenant_id

// 演示模式
const customerList = await getCustomerList()  // 无 tenant_id

// 租户模式
const headers = getSaasAuthHeader()  // 自动包含 X-Tenant-Id
const customerList = await getCustomerList(headers)
```

**数据访问控制**：

1. 演示模式下，用户只能操作 `tenant_id = NULL` 的数据
2. 租户模式下，用户只能操作 `tenant_id = 当前租户ID` 的数据
3. 不同租户之间的数据完全隔离，互不可见

---

## 实施方案

### 一、数据模型设计

#### 1.1 前端类型扩展

```typescript
// frontend/src/types/index.ts 新增

export interface BusinessPage {
  id: string
  title: string
  icon: string
  route: string
}

export interface SubagentListItem {
  agent_id: string
  name: string
  description: string
  capabilities: string[]
  type: 'builtin' | 'custom'
  business_pages?: BusinessPage[]  // 新增
}
```

#### 1.2 后端子智能体列表API扩展

后端在返回子智能体列表时，需要解析 `SUBAGENT.md` 中的 `business_pages` 并一起返回。

---

### 二、后端修改

#### 2.1 子智能体注册时解析 business_pages

在 `SubagentRegistry` 加载 `SUBAGENT.md` 时，解析 YAML 中的 `business_pages` 字段：

```python
# src/subagents/registry.py 新增解析逻辑

def _parse_subagent_md(self, md_path: Path) -> dict:
    # ... 现有解析逻辑 ...

    # 新增：解析 business_pages
    if 'business_pages' in front_matter:
        subagent['business_pages'] = front_matter['business_pages']

    return subagent
```

#### 2.2 API 返回时包含 business_pages

确保 `GET /api/admin/subagents` 接口返回的数据包含 `business_pages` 字段。

---

### 三、前端修改

#### 3.1 MenuSidebar 改造

新增 props：

```typescript
// MenuSidebar.vue
const props = defineProps<{
  currentSubagentId?: string
  availableSubagents: SubagentListItem[]
}>()
```

新增"业务数据"分组渲染逻辑：

```vue
<!-- 业务数据分组 - 在历史会话上方 -->
<div v-if="currentBusinessPages.length > 0" class="business-data-section">
  <div class="section-header" @click="toggleBusinessData">
    <span>📊 业务数据</span>
    <span class="toggle-icon">{{ isBusinessDataExpanded ? '▼' : '▶' }}</span>
  </div>

  <div v-show="isBusinessDataExpanded" class="section-content">
    <!-- 当前数字员工标签 -->
    <div class="subagent-label">
      ── {{ currentSubagentName }} ──
    </div>

    <!-- 业务菜单项 -->
    <div class="menu-items">
      <div
        v-for="page in currentBusinessPages"
        :key="page.id"
        class="menu-item"
        @click="navigateToBusinessPage(page)"
      >
        <span class="menu-icon">{{ page.icon }}</span>
        <span class="menu-title">{{ page.title }}</span>
      </div>
    </div>
  </div>
</div>
```

新增响应式状态和计算属性：

```typescript
// 折叠状态持久化到 localStorage
const storageKey = 'aid_work_agent:business_data_expanded'
const isBusinessDataExpanded = ref(
  localStorage.getItem(storageKey) !== 'false'
)

const toggleBusinessData = () => {
  isBusinessDataExpanded.value = !isBusinessDataExpanded.value
  localStorage.setItem(storageKey, String(isBusinessDataExpanded.value))
}

const currentBusinessPages = computed(() => {
  const subagent = props.availableSubagents.find(
    s => s.agent_id === props.currentSubagentId
  )
  return subagent?.business_pages || []
})

const currentSubagentName = computed(() => {
  const subagent = props.availableSubagents.find(
    s => s.agent_id === props.currentSubagentId
  )
  return subagent?.name || ''
})
```

#### 3.2 ChatContainer 调整

**移除**：将"我的客户"从右上角"更多"菜单的 `#menu-items` slot 中移除。

**传递**：将 `currentSubagentId` 和 `availableSubagents` 传递给 `MenuSidebar`：

```vue
<!-- ChatContainer.vue -->
<MenuSidebar
  :current-subagent-id="currentSubagentId"
  :available-subagents="availableSubagents"
  @select-subagent="handleSubagentChange"
/>
```

#### 3.3 路由配置

业务数据页面的路由，按子智能体 id 划分命名空间：

```typescript
// main.ts 新增路由
{
  path: '/trade-specialist',
  name: 'trade-specialist',
  component: () => import('./components/BaseBusinessLayout.vue'),  // 基础布局组件
  children: [
    {
      path: 'customers',
      name: 'trade-specialist-customers',
      component: () => import('./components/CustomerInfo.vue')
    },
    {
      path: 'email-records',
      name: 'trade-specialist-email-records',
      component: () => import('./components/EmailRecords.vue')  // 待创建
    },
    {
      path: 'match-stats',
      name: 'trade-specialist-match-stats',
      component: () => import('./components/MatchStats.vue')  // 待创建
    },
  ]
},
{
  path: '/hr-expert',
  name: 'hr-expert',
  component: () => import('./components/BaseBusinessLayout.vue'),
  children: [
    {
      path: 'employees',
      name: 'hr-expert-employees',
      component: () => import('./components/EmployeeMgmt.vue')  // 待创建
    },
  ]
},
```

**路由命名规范**：`/{subagent_id}/{business_page_id}`

| 子智能体 | 路由前缀 | 示例 |
|---------|---------|------|
| 外贸获客智能体 | `/trade-specialist` | `/trade-specialist/customers` |
| HR专家智能体 | `/hr-expert` | `/hr-expert/employees` |
| 代码审查智能体 | `/code-reviewer` | `/code-reviewer/reports` |

**基础布局组件** `BaseBusinessLayout.vue` 负责：

- 从路径第一段解析出 `subagent_id`（例如 `/trade-specialist/customers` → `trade-specialist`），高亮左侧菜单中对应的数字员工
- 统一页面标题、操作按钮等布局

---

### 四、CEO智能体"工作总览"（TODO）

**TODO**：未来为CEO智能体配置"工作总览"页面，提供跨子智能体的聚合视图，包括：

- 任务统计概览（总任务、已完成、进行中、失败）
- 最近任务列表
- 定时任务管理
- 数字员工数据快捷入口

**前置条件**：

- 需要后端提供跨子智能体的聚合查询 API
- 需要任务执行日志的标准化存储

**当前阶段**：MVP先不做，保持CEO智能体无"业务数据"分组。

---

## 需要修改的文件清单

| 文件 | 修改内容 |
|------|----------|
| **后端** | |
| `src/subagents/registry.py` | 解析 `SUBAGENT.md` 中的 `business_pages` 字段 |
| `src/api/admin_subagent.py` | 确保 API 返回包含 `business_pages` |
| `subagents/trade-specialist/SUBAGENT.md` | 新增 `business_pages` 配置 |
| **前端** | |
| `frontend/src/types/index.ts` | 新增 `BusinessPage` 和 `SubagentListItem.business_pages` 类型 |
| `frontend/src/components/MenuSidebar.vue` | 新增"业务数据"分组渲染逻辑 |
| `frontend/src/components/ChatContainer.vue` | 移除"更多"菜单中的"我的客户"，传递 subagent props |
| `frontend/src/api/adminSubagent.ts` | 类型扩展对应修改 |
| `frontend/src/main.ts` | 新增业务数据页面路由 |
| `frontend/src/components/CustomerInfo.vue` | 可能需要适配新的菜单展示方式 |

---

## 验证方式

1. **外贸智能体业务数据**：
   - 切换到外贸获客智能体
   - 左侧菜单显示"业务数据"分组
   - 分组标题显示"── 外贸获客智能体 ──"
   - 菜单项显示"我的客户"（跳转 `/trade-specialist/customers`）、"邮件记录"（跳转 `/trade-specialist/email-records`）、"匹配统计"（跳转 `/trade-specialist/match-stats`）
   - 点击"我的客户"可正常跳转

2. **上下文感知切换**：
   - 切换到CEO智能体
   - "业务数据"分组自动隐藏
   - 切换回外贸获客智能体
   - "业务数据"分组重新出现，菜单项正确

3. **右上角"更多"菜单**：
   - "更多"菜单中不再显示"我的客户"
   - 其他功能（凭据管理、定时任务、设置）保持正常

4. **新增子智能体的业务数据**：
   - 为HR智能体配置 `business_pages`
   - 切换到HR智能体后，左侧显示对应的业务菜单

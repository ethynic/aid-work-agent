# 租户自助复制/定制数字员工（数字员工实例）设计

> 日期：2026-10-14
> 状态：设计定稿
> 关联：[AgentRunner 架构](../agent-application-architecture-design.md)、[数字员工栏目授权设计](knowledge-base/subagent-kb-category-authorization-design.md)

## 1. 背景与目标

当前数字员工分两层：

| 层 | 定义位置 | 管理方 |
|---|---|---|
| 内置数字员工 | `subagents/<name>/SUBAGENT.md` 文件 | 开发人员（/portal/subagents 只读查看） |
| 平台自定义数字员工 | DB `subagent_definitions` 表 | 产品经理（/portal/agent-definitions） |

租户管理员目前只能填写租户 extra.md（扩展提示词）和上传模板。本设计增加租户级「数字员工实例」：租户管理员可对任意已订阅数字员工做**重命名、扩展提示词、指定知识库栏目、覆盖简介与快捷按钮**，并可**复制**出本租户专属副本实例（如「销售助手」->「南京销售助手」「北京销售助手」，分别绑定不同知识栏目）。

**术语**：

| 术语 | 定义 | 租户管理员删除 |
|---|---|---|
| 直用实例 | 租户直接使用基础员工形成的实例行（`agent_id = base_agent_id`）。可能未经任何定制，首次修改覆盖项时按需补建实例行 | 不可删除 |
| 副本实例 | 租户管理员复制产生的智能体实例（`agent_id != base_agent_id`），是运行时实例，不进全局 registry | 可删除（软删归档） |

复制是**继承**语义：副本实例 system_prompt、工具、技能、模型配置实时取自父定义，平台管理员更新父定义后副本实例自动同步生效；租户仅可覆盖 4 类字段，不可改工具/技能/模型。

## 2. 已定决策

- 副本实例 agent_id：`{base}-{租户串}-{4位随机}`，纯小写连字符，**禁止下划线**。原因：渠道 session_id 格式 `tenant_{tid}_{channel}_{user}_{subagent}`，recap 触发入口（`src/services/recap/runner.py` `_subagent_name_from_session`）按 `_` 取末段提取 subagent_id，tenant_id 本身含下划线，副本实例 ID 若含下划线会破坏提取。
- 存储：新建 `tenant_agent_instances` 表；现有 prompt_versions `scope='tenant_extra'` 的定制提示词**迁移进新表**（`extra_prompt` 字段），过渡期双读双写。
- 统一模型：租户直接使用基础员工时也落实例表一行（直用实例行，`agent_id = base_agent_id`，无新 ID）；复制产生的为副本实例行（`agent_id != base_agent_id`）。
- 渠道（企微客服/钉钉/飞书）同时支持绑定副本实例。
- 副本实例继承基础员工的订阅权限（`subscriptions.subagent_type` 绑定基础 id，运行时展开）。复制来源可以是基础员工（内置/平台自定义，需订阅）或本租户已有实例行；来源为实例行时，新副本实例的 `base_agent_id` 取源实例的根父定义（继承链保持一层，运行时无需递归解析），并克隆源实例的覆盖项（description/extra_prompt/greeting_*）作为初始覆盖。
- 副本实例数量上限：config.yaml `agent.tenant_instance_max`（默认 20），防滥用。

## 3. 核心架构

租户实例**不进全局 SubagentRegistry**（registry 无租户上下文，混入会跨租户泄漏且随租户数膨胀）。新增运行时解析层：

```
已知 tenant_id 的入口（agent_router / 渠道分发 / greeting API / 列表 API）
  -> 查 tenant_agent_instances（实时读 DB，多 worker 天然一致）
  -> 命中：「父定义 + 覆盖项」合并出 SubagentConfig（dir_name=实例 agent_id）
  -> 未命中：走原路径
```

父定义获取：内置员工走 `registry.get(base_agent_id)`，平台自定义走 `AgentFactory._load_single_from_db`（与现有自定义员工实时重读一致）。

`SubagentConfig` 增加字段：`base_agent_id: Optional[str]`、`tenant_instance: bool`；name/description 用覆盖值，system_prompt/tools/skills/llm 全取父定义。

覆盖项注入点：

| 覆盖项 | 注入点 |
|---|---|
| display_name / description | SubagentConfig 合并（列表、greeting、会话展示自动生效） |
| extra_prompt | `prompt_sources._load_extra_md` 优先读实例行，回退 prompt_resolver tenant_extra（过渡双读） |
| 知识栏目 / 模板文件 | 现有 `subagent_knowledge_sources` / `subagent_template_files` 按 (tenant_id, subagent_name) 隔离，运行时按实例 `dir_name`（= 副本实例 agent_id）读取。副本实例是**新** agent_id，无既有行，故复制时必须克隆：`subagent_assets.copy_template_files`（磁盘复制 + 新 file_id + 永久元数据）与 `copy_knowledge_sources`（原样复制栏目项，含共享来源）。克隆来源为被点击的 agent_id（基础员工或本租户实例行），best-effort 不阻断复制 |
| greeting_summary / greeting_prompts | `/api/subagents/{agent_id}/greeting` 先查实例行，非空直接返回（缓存键加租户维度）；否则按原 LLM 生成链路 |

## 4. 数据模型

```sql
CREATE TABLE IF NOT EXISTS tenant_agent_instances (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    agent_id TEXT NOT NULL UNIQUE,          -- 直用实例行=基础名；副本实例行=新唯一 ID（纯连字符）
    base_agent_id TEXT NOT NULL,            -- 继承的父定义 agent_id
    base_source TEXT,                       -- 'builtin' | 'custom'
    display_name TEXT,                      -- NULL=继承父名
    description TEXT,                       -- NULL=继承父描述
    extra_prompt TEXT,                      -- 扩展提示词（迁移自 tenant_extra）
    greeting_summary TEXT,                  -- 简介覆盖（NULL=LLM 生成/兜底）
    greeting_prompts JSONB,                 -- 快捷按钮覆盖 [{label,message}]
    status TEXT DEFAULT 'active',           -- active | archived（软删）
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

索引：`(tenant_id, status)`、`(tenant_id, base_agent_id)`。无外键/触发器（项目规范）。

**数据迁移**：prompt_registry（scope='tenant_extra'）JOIN prompt_versions 取 production 内容，解析 scope_id `extra:{sub}:{tenant}` -> 插入直用实例行（extra_prompt=content），`ON CONFLICT (agent_id) DO NOTHING`，幂等。旧 prompt_versions 数据保留不删。

## 5. 租户 API

前缀 `/api/saas/tenant/agent-instances`，鉴权复用 `require_admin`（同 `src/api/subagent_knowledge_source.py` 模式）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 本租户实例列表 |
| POST | `/` | 复制 `{base_agent_id, display_name}` -> 新 agent_id |
| PUT | `/{agent_id}` | 更新覆盖项（display_name/description/extra_prompt/greeting_summary/greeting_prompts） |
| DELETE | `/{agent_id}` | 软删 archived；被渠道配置引用时直接拒绝（`ChannelConfigDB.is_subagent_used` 校验，需先解除渠道绑定） |

extra 写路径（`subagent_extra.py` / `prompt_management.py`）过渡期双写实例表。

**副本实例可见性**：副本实例是运行时实例，不进全局 registry，因此以下三处必须显式补入（`build_tenant_instance_items`）：
- `/api/subagents` 列表（「我的数字员工」卡片）
- `/api/saas/permissions/my/allowed-agents`（新会话页可选数字员工）
- `/api/saas/permissions/tenant/available-user-agents`（用户管理-数字员工授权 Tab 可选列表）

`set_user_agent_permissions` 保存前先 `expand_with_tenant_instances` 展开租户允许集合，避免用户勾选的副本实例被当越权过滤掉。

## 6. 前端

`/t/:tid/my-agents`（MyDigitalEmployees.vue）「定制」弹框（新组件 TenantAgentCustomizeDialog.vue）：重命名、扩展提示词、知识栏目（内嵌复用 KnowledgeAuthMatrix.vue，传实例 agent_id）、简介+快捷按钮覆盖、复制副本（输入新显示名，基础员工与副本卡片均可复制）。副本卡片带「副本」徽标，并带「删除」按钮（`type === 'tenant_instance'` 且租户管理员可见，确认后调 DELETE；被渠道占用时后端拒绝并提示）。TenantPromptEditor 旧路由保留跳转弹框。greeting 前端链路不变自动生效。

## 7. 渠道支持

渠道配置（channel_config）数字员工可选项 = 基础订阅员工 + 本租户实例；保存校验：实例须 tenant_id 匹配、基础 id 须在订阅内（防跨租户绑定）。session_id 生成不变，纯连字符保证 recap 末段提取正确。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 多 worker 一致性 | 实例行每次实时读 DB；列表缓存短 TTL + 写后失效 |
| 副本实例删除后历史会话 | 软删 archived + resolver 降级返回 base 配置只读兜底，新会话拒绝 |
| 迁移双读期不一致 | extra 写路径双写，旧 prompt_versions 保留，稳定后另发增量清理 |
| 撞名 | 创建时与 registry 现有 id、subagent_definitions、实例表三方查重 |
| 跨租户越权 | 所有读写带 tenant_id 过滤，实例归属校验 fail-closed |

## 9. 测试要点

实例 CRUD、id 生成唯一性/连字符约束、resolver 合并（内置父/DB 父/覆盖项/父更新同步/archived 降级）、权限展开（未订阅 base 不可见）、greeting 覆盖优先于 LLM 且缓存键租户隔离、迁移 SQL 幂等、跨租户读写 403/404、复制时克隆模板文件与知识栏目（best-effort 不阻断）、副本实例可见性补入（`build_tenant_instance_items`）、删除副本实例的渠道占用守卫（被引用拒绝、校验异常放行）。

# 子智能体 Prompt 管理 — 第一优先级开发计划

> 对应设计文档：[prompt-lifecycle-design.md](./prompt-lifecycle-design.md)
> 对应调研报告：[prompt-version-management-research.md](../research/prompt-version-management-research.md)
> 创建日期：2026-06-02
> 更新日期：2026-06-03（Phase 3 重做：知识库关联配置优先，extra_md 迁移后移至 Phase 4；移除 capabilities 字段）
> 状态：Phase 2 代码完成，待验证

---

## 范围说明

本开发计划围绕**子智能体的 Prompt 管理**展开，核心认识是：

> **子智能体 = 定义部分（YAML 配置） + System Prompt（可版本化）**

- **定义部分**：name、description、tools、skills、knowledge_sources 等配置，通过子智能体管理界面修改，支持 LLM 智能推荐工具/技能
- **System Prompt 部分**：Markdown body，纳入版本管理，支持编辑 → 提交版本 → 对比 → 回滚
- **租户定制 Prompt（extra_md）**：独立的第三层，同样纳入版本管理

**不在本计划范围内**：
- §九 A/B 测试（二期）
- §十 效果评估 Pipeline（二期）
- 系统模板版本化（远期）
- 技能 Prompt 版本化（二期）

---

## Phase 0：Prompt 内容优化（P0 ~ P3） ✅ 已完成

> 改动范围：仅修改模板文件和工具代码，不改 agent.py 核心逻辑

- [x] 修正"透明化"矛盾：subagent_base.md 和 master_agent.md 改为"专业沟通"
- [x] 精简 http_api usage_guide：去掉与 JSON Schema 重复的参数说明
- [x] 评估 {available_tools_list}：结论保留（token 消耗低，去掉收益不大）

---

## Phase 1：版本管理数据库 + 基础服务层 ✅ 代码完成

> 设计文档参考：§三（数据模型）、§六（运行时 Prompt 解析流程）、§七（API 设计）
> 目标：建立 Prompt 版本管理的数据库表和后端服务
> 预计工期：1 周

### 文件变更清单

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `deploy/db_update.sql` | 追加 | 4 张表 DDL（幂等） |
| 2 | `deploy/init-postgres.sql` | 追加 | 相同 DDL（全新部署） |
| 3 | `src/core/cache_utils.py` | 修改 | CacheKeys 加 3 个常量 |
| 4 | `src/db/prompt_db.py` | **新建** | 4 个 DB 访问类 |
| 5 | `src/prompts/prompt_resolver.py` | **新建** | PromptCache + PromptResolver |
| 6 | `src/prompts/prompt_registry_service.py` | **新建** | PromptRegistryService 服务层 |
| 7 | `src/api/prompt_management.py` | **新建** | admin_router + tenant_router |
| 8 | `src/main.py` | 修改 | 注册两个新 router |

### 阶段 1.1：数据库表创建

> 前置依赖：无
> 关键文件：`deploy/db_update.sql`、`deploy/init-postgres.sql`

- [x] **1.1.1 在 deploy/db_update.sql 添加 prompt 相关表**
  - 添加以下表（全部 `IF NOT EXISTS`）：
    - `prompt_registry` — Prompt 注册表
    - `prompt_versions` — Prompt 版本（不可变）
    - `prompt_labels` — 标签（环境部署）
    - `prompt_drafts` — 草稿
  - 不添加外键约束（遵循 database_dev.md 规范）
  - ✅ 已完成

- [x] **1.1.2 在 deploy/init-postgres.sql 添加相同的建表语句**
  - 保证全新部署环境也能创建完整表结构
  - ✅ 已完成

- [x] **1.1.3 验证 _apply_db_updates() 能自动执行新增的 SQL**
  - ✅ 已完成（e2e 测试验证通过）

<details>
<summary>DDL 详细设计</summary>

```sql
-- 2026-6-2, Prompt Version Management System

-- 1. prompt_registry — Prompt 注册表
CREATE TABLE IF NOT EXISTS prompt_registry (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT,                          -- NULL = 系统级
    scope         TEXT NOT NULL,                 -- 'subagent'|'skill'|'system_template'|'tenant_extra'
    scope_id      TEXT NOT NULL,                 -- 子智能体 dir_name / 'extra:{subagent}:{tenant}'
    prompt_type   TEXT DEFAULT 'normal',         -- 'normal' | 'snippet'
    display_name  TEXT,
    description   TEXT,
    latest_version INTEGER DEFAULT 0,
    created_by    TEXT,
    updated_by    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 唯一索引用 COALESCE 避免 NULL 不等导致重复
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_registry_tenant_scope
    ON prompt_registry (COALESCE(tenant_id, ''), scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_prompt_registry_scope
    ON prompt_registry (scope, scope_id);

-- 2. prompt_versions — Prompt 版本（不可变）
CREATE TABLE IF NOT EXISTS prompt_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id      UUID NOT NULL,
    version        INTEGER NOT NULL,             -- 自增版本号
    content        TEXT NOT NULL,
    variables      JSONB,                        -- 变量定义
    model_config   JSONB,                        -- 模型参数覆盖
    commit_message TEXT,
    content_hash   TEXT,                         -- SHA-256 去重
    parent_version INTEGER,
    created_by     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_versions_prompt_ver
    ON prompt_versions (prompt_id, version);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt
    ON prompt_versions (prompt_id, created_at DESC);

-- 3. prompt_labels — 标签（命名指针）
CREATE TABLE IF NOT EXISTS prompt_labels (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL,
    version_id    UUID NOT NULL,
    label         TEXT NOT NULL,                 -- 'production'|'staging'|'experiment-*'
    created_by    TEXT,
    updated_by    TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_labels_prompt_label
    ON prompt_labels (prompt_id, label);

-- 4. prompt_drafts — 草稿（每 Prompt 最多一条）
CREATE TABLE IF NOT EXISTS prompt_drafts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt_id     UUID NOT NULL UNIQUE,
    content       TEXT NOT NULL,
    variables     JSONB,
    base_version  INTEGER,                       -- 基于哪个版本编辑
    updated_by    TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**设计要点**：
- 唯一索引用 `COALESCE(tenant_id, '')` 替代直接 `(tenant_id, scope, scope_id)`，因为 PostgreSQL 中 NULL ≠ NULL，会导致系统级 Prompt 重复注册
- JSONB 字段（`variables`、`model_config`）由 psycopg2 自动序列化/反序列化，无需手动 `json.dumps()`
- 版本号自增通过 `UPDATE prompt_registry SET latest_version = latest_version + 1 RETURNING latest_version` 原子操作
- 标签 UPSERT 语义：`ON CONFLICT (prompt_id, label) DO UPDATE SET version_id = EXCLUDED.version_id`
</details>

### 阶段 1.2：DB 访问层 + 服务层

> 前置依赖：1.1
> 新建文件：`src/db/prompt_db.py`（DB 层）、`src/prompts/prompt_registry_service.py`（服务层）

- [x] **1.2.1 实现 DB 访问层（`src/db/prompt_db.py`）**
  - 4 个类：`PromptRegistryDB`、`PromptVersionDB`、`PromptLabelDB`、`PromptDraftDB`
  - 全部 `@staticmethod` 方法，遵循 `ReplyStyleDB` 模式
  - ✅ 已完成（389 行，含 commit_version_atomic 原子操作）

- [x] **1.2.2 实现 PromptRegistryService 服务层**
  - 注册管理、版本管理、草稿管理、标签管理
  - ✅ 已完成（285 行，含 SHA-256 去重、高危 staging 校验）

- [x] **1.2.3 缓存 Key 注册**
  - 在 `src/core/cache_utils.py` 的 `CacheKeys` 类中添加 prompt 相关常量
  - ✅ 已完成（PROMPT_CONTENT / PROMPT_LABEL / PROMPT_REGISTRY）

<details>
<summary>DB 层详细设计</summary>

**遵循现有模式**：
- `with get_db_connection() as conn: cursor = conn.cursor(); ... conn.commit()`
- 返回 `Optional[Dict[str, Any]]`（单条）、`List[Dict[str, Any]]`（列表）、`Dict` 含 total/page/page_size（分页）
- `RealDictCursor` 自动返回 dict-like 对象，`dict(row)` 转换

#### PromptRegistryDB

| 方法 | 说明 |
|------|------|
| `create(tenant_id, scope, scope_id, ...)` | UUID 通过 Python `str(uuid.uuid4())` 生成 |
| `get_by_id(prompt_id)` | 按 id 查找 |
| `get_by_scope(tenant_id, scope, scope_id)` | 按唯一索引查找。**注意**：`tenant_id=None` 时用 `IS NULL` 而非 `= %s` |
| `list_prompts(tenant_id, scope, page, page_size)` | 分页，返回 `{total, page, page_size, items}` |
| `update(prompt_id, **kwargs)` | 更新 display_name/description/prompt_type + updated_at |
| `delete(prompt_id)` | 级联删除：labels → drafts → versions → registry（一个连接内完成） |
| `increment_version(prompt_id)` | `UPDATE SET latest_version+1 RETURNING latest_version` |

#### PromptVersionDB

| 方法 | 说明 |
|------|------|
| `create(prompt_id, version, content, ...)` | 基本插入 |
| `get_by_id(version_id)` | 按 id 查找 |
| `get_by_prompt_and_version(prompt_id, version)` | 按版本号查找 |
| `list_versions(prompt_id, page, page_size)` | 按 version DESC 分页 |
| `get_latest(prompt_id)` | `ORDER BY version DESC LIMIT 1` |
| `commit_version_atomic(conn, prompt_id, content, ...)` | **关键方法**：接收外部连接，在同一事务内完成 `UPDATE registry SET latest_version+1 RETURNING` + `INSERT INTO versions`，避免并发竞态 |

**并发安全**：`commit_version_atomic` 在单个事务内完成版本号自增和版本插入，消除读写间隙。

#### PromptLabelDB

| 方法 | 说明 |
|------|------|
| `upsert(prompt_id, version_id, label, created_by)` | `ON CONFLICT (prompt_id, label) DO UPDATE SET version_id = EXCLUDED.version_id, ...` |
| `get_by_label(prompt_id, label)` | 查指定标签 |
| `list_labels(prompt_id)` | 列出所有标签 |
| `delete(prompt_id, label)` | 删除标签 |

#### PromptDraftDB

| 方法 | 说明 |
|------|------|
| `upsert(prompt_id, content, variables, base_version, updated_by)` | `ON CONFLICT (prompt_id) DO UPDATE SET ...` |
| `get(prompt_id)` | 获取草稿 |
| `delete(prompt_id)` | 删除草稿 |
</details>

<details>
<summary>服务层详细设计</summary>

`PromptRegistryService` 编排 DB 层 + 缓存失效，业务逻辑集中于此。

#### 注册管理

- `register_prompt(scope, scope_id, tenant_id, ...)` — 先查 `get_by_scope()` 是否已存在，存在则返回，否则创建
- `get_prompt(prompt_id)` → `PromptRegistryDB.get_by_id()`
- `get_prompt_by_scope(tenant_id, scope, scope_id)` → `PromptRegistryDB.get_by_scope()`
- `list_prompts(...)` → `PromptRegistryDB.list_prompts()`
- `delete_prompt(prompt_id)` — 调 `PromptRegistryDB.delete()` + `invalidate_prompt()`

#### 版本管理

- `commit_version(prompt_id, content, variables, model_config, commit_message, created_by)`
  1. `content_hash = hashlib.sha256(content.encode()).hexdigest()`
  2. 检查最新版本是否相同 content_hash（去重），相同则返回已有版本 + `{"dedup": True}`
  3. 调 `commit_version_atomic(conn, ...)` 原子提交
  4. 缓存失效
- `get_version(prompt_id, version)` / `list_versions(...)` / `diff_versions(prompt_id, from_ver, to_ver)`
  - `diff_versions` 返回两个版本的原始 content，前端计算 diff 展示

#### 草稿管理

- `get_draft(prompt_id)` / `save_draft(...)` / `delete_draft(prompt_id)`
- `commit_draft(prompt_id, commit_message, created_by)`
  1. 取草稿 → 无草稿报错
  2. 调 `commit_version(prompt_id, draft.content, ...)`
  3. 成功后删草稿

#### 标签管理

- `get_label(prompt_id, label)` / `list_labels(prompt_id)` / `delete_label(prompt_id, label)`
- `set_label(prompt_id, label, version, created_by)`
  1. 验证版本存在
  2. **高危 Prompt staging 校验**：`scope` 为 `subagent` 或 `system_template` 时，`label == "production"` 必须先经过 `staging` 验证（即存在 staging 标签指向同一版本）。首次部署无 staging 时仅 log warning 不阻塞
  3. `PromptLabelDB.upsert(...)`
  4. 缓存失效
</details>

<details>
<summary>缓存 Key 设计</summary>

在 `src/core/cache_utils.py` 的 `CacheKeys` 类中添加：

```python
PROMPT_CONTENT = "prompt_content"    # prompt_content:{prompt_id}:{version} → 版本内容, TTL 600s
PROMPT_LABEL = "prompt_label"        # prompt_label:{prompt_id}:{label} → version 号, TTL 300s
PROMPT_REGISTRY = "prompt_reg"       # prompt_reg:{tenant_id}:{scope}:{scope_id} → registry 记录, TTL 300s
```

使用现有 `get_cached/set_cached/delete_cached/delete_cached_pattern` 函数。

**缓存失效时机**：
- 版本提交 → `delete_cached(PROMPT_CONTENT, prompt_id, ...)` + `delete_cached(PROMPT_REGISTRY, ...)`
- 标签变更 → `delete_cached(PROMPT_LABEL, prompt_id, label)` + 如果是 production 则也失效 content
- Prompt 删除 → `delete_cached_pattern(PROMPT_CONTENT, prompt_id, "")` 全部清掉
</details>

### 阶段 1.3：PromptResolver 运行时解析层

> 前置依赖：1.2
> 新建文件：`src/prompts/prompt_resolver.py`

- [x] **1.3.1 实现 PromptCache 缓存层**
  - `get/set_resolved_content(prompt_id, version)` — TTL 600s
  - `get/set_label_version(prompt_id, label)` — TTL 300s
  - `invalidate_prompt(prompt_id)` — 删除该 prompt 所有缓存 key
  - ✅ 已完成（prompt_resolver.py:22-70）

- [x] **1.3.2 实现 PromptResolver.resolve(scope, scope_id, tenant_id)**
  - 降级策略：production label → latest label → latest_version → None
  - ✅ 已完成（prompt_resolver.py:73-162，全局单例 prompt_resolver）

<details>
<summary>运行时解析流程</summary>

```python
class PromptResolver:
    def resolve(self, scope: str, scope_id: str, tenant_id: str = None) -> Optional[str]:
        """
        返回解析后的 Prompt content，供 agent._build_system_prompt() 使用。
        全部 miss 返回 None，调用方降级到文件系统。
        """
        # 1. 查 registry（缓存 → DB）
        registry = self._get_registry(scope, scope_id, tenant_id)
        if not registry:
            return None

        prompt_id = registry["id"]

        # 2. 查 production label（缓存 → DB）
        version = self._resolve_label(prompt_id, "production")
        if not version:
            # 3. 降级：latest label
            version = self._resolve_label(prompt_id, "latest")
        if not version:
            # 4. 降级：latest_version
            latest = PromptVersionDB.get_latest(prompt_id)
            version = latest["version"] if latest else None

        if not version:
            return None

        # 5. 获取内容（缓存 → DB）
        content = self._get_content(prompt_id, version)
        return content
```

**线程安全**：PromptResolver 无状态（所有状态在 Redis），无需加锁。
</details>

### 阶段 1.4：后端 CRUD API

> 前置依赖：1.2
> 新建文件：`src/api/prompt_management.py`

- [x] **1.4.1 平台管理员 API**（`/api/admin/prompts`）
  - 使用 `_require_admin` 模式（参考 `admin_subagent.py`）
  - ✅ 已完成（prompt_management.py:94-404，17 个端点）

- [x] **1.4.2 租户管理员 API**（`/api/prompts`）
  - 使用 `require_admin` from `tenant_auth.py`，限定 `scope=tenant_extra`
  - ✅ 已完成（prompt_management.py:406-639，10 个端点）

- [x] **1.4.3 注册路由到 main.py**
  - ✅ 已完成（main.py:1607-1609）

<details>
<summary>API 端点详细设计</summary>

#### 平台管理员 API — `admin_router`

`APIRouter(prefix="/api/admin/prompts", tags=["Prompt 管理"])`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列出 Prompt（分页，按 scope/tenant_id 筛选） |
| POST | `/` | 注册新 Prompt |
| GET | `/{prompt_id}` | 获取 Prompt 详情 |
| PUT | `/{prompt_id}` | 更新 Prompt 元数据 |
| DELETE | `/{prompt_id}` | 删除 Prompt + 所有版本/标签/草稿 |
| POST | `/{prompt_id}/versions` | 提交新版本 |
| GET | `/{prompt_id}/versions` | 列出版本（分页） |
| GET | `/{prompt_id}/versions/{version}` | 获取特定版本 |
| GET | `/{prompt_id}/versions/diff?v1=X&v2=Y` | 两个版本 diff |
| GET | `/{prompt_id}/draft` | 获取草稿 |
| PUT | `/{prompt_id}/draft` | 保存草稿 |
| DELETE | `/{prompt_id}/draft` | 删除草稿 |
| POST | `/{prompt_id}/draft/commit` | 提交草稿为新版本 |
| GET | `/{prompt_id}/labels` | 列出所有标签 |
| PUT | `/{prompt_id}/labels/{label}` | 设置标签 |
| DELETE | `/{prompt_id}/labels/{label}` | 删除标签 |

#### 租户管理员 API — `tenant_router`

`APIRouter(prefix="/api/prompts", tags=["租户 Prompt 管理"])`

所有操作限定 `scope=tenant_extra`，通过 `request.state.tenant_id`（或 `user['tenant_id']`）隔离。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列出租户的 Prompt |
| POST | `/` | 注册租户 Prompt |
| GET | `/{scope_id}` | 获取租户 Prompt |
| POST | `/{scope_id}/versions` | 提交版本 |
| GET | `/{scope_id}/versions` | 列出版本 |
| GET | `/{scope_id}/draft` | 获取草稿 |
| PUT | `/{scope_id}/draft` | 保存草稿 |
| POST | `/{scope_id}/draft/commit` | 提交草稿 |
| GET | `/{scope_id}/labels` | 列出标签 |
| PUT | `/{scope_id}/labels/{label}` | 设置标签 |

#### Pydantic 请求模型

```python
class RegisterPromptRequest(BaseModel):
    scope: str = Field(..., pattern=r"^(subagent|skill|system_template|tenant_extra)$")
    scope_id: str = Field(..., min_length=1, max_length=100)
    tenant_id: Optional[str] = None
    prompt_type: str = "normal"
    display_name: Optional[str] = None
    description: Optional[str] = None

class CommitVersionRequest(BaseModel):
    content: str = Field(..., min_length=1)
    variables: Optional[Dict[str, Any]] = None
    model_config: Optional[Dict[str, Any]] = None
    commit_message: Optional[str] = None

class SaveDraftRequest(BaseModel):
    content: str = Field(..., min_length=1)
    variables: Optional[Dict[str, Any]] = None
    base_version: Optional[int] = None

class SetLabelRequest(BaseModel):
    version: int = Field(..., ge=1)
```
</details>

### 阶段 1.5：单元测试

- [x] **1.5.1 PromptRegistryService 单元测试**
  - ✅ 已完成（e2e 测试覆盖：注册、提交版本、去重、标签、草稿、staging 校验、级联删除）
- [x] **1.5.2 PromptResolver 单元测试**
  - ✅ 已完成（e2e 测试覆盖：resolve production → staging → latest_version → None 降级链）
- [x] **1.5.3 API 集成测试**
  - ✅ 已完成（e2e 测试 17 步全部 PASS）

### Phase 1 完成标准

- [x] 四张表创建成功，`_apply_db_updates()` 自动建表
- [x] PromptRegistryService、PromptResolver、PromptCache 正常工作
- [x] 所有 API 端点正确响应（admin + tenant）
- [x] 租户隔离：租户 A 看不到租户 B 的数据
- [x] 高危 Prompt staging 校验生效
- [x] 缓存正确：提交/标签变更后缓存失效
- [x] 测试通过

---

## Phase 2：独立智能体管理页面（重做） 🔧 进行中

> 设计文档参考：§八.1（子智能体管理）、§三.2（subagent_definitions 表）
> 目标：新增独立的智能体管理页面，子智能体定义存数据库，与旧页面完全解耦
> 预计工期：1.5 周
> 前置依赖：Phase 1 ✅

### 变更说明

**原方案（已作废）**：在现有 `DigitalEmployeeManager.vue`（`/portal/subagents`）上改造，数据源仍是文件系统。
**新方案**：新增独立页面 `/portal/agent-definitions`，子智能体定义存 `subagent_definitions` 数据库表，system_prompt 复用 `prompt_versions` 版本管理。旧页面保持不动。

### 文件变更清单

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `deploy/db_update.sql` + `deploy/init-postgres.sql` | 追加 | `subagent_definitions` 表 DDL ✅ |
| 2 | `src/db/subagent_definition_db.py` | **新建** | DB 访问层 ✅ |
| 3 | `src/services/subagent_definition_service.py` | **新建** | 服务层（编排 DB 定义 + Prompt 版本） ✅ |
| 4 | `src/api/agent_definitions.py` | **新建** | `/api/admin/agent-definitions` 路由 ✅ |
| 5 | `src/models/subagent.py` | 修改 | `SubagentConfig` 新增 `from_db` 字段 ✅ |
| 6 | `src/subagents/registry.py` | 修改 | 新增 `load_from_db()` 方法（DB 优先覆盖） ✅ |
| 7 | `src/core/agent.py` | 修改 | `_build_system_prompt()` 移除独立 prompt_resolver 调用 + 委派路径 DB 兜底 ✅ |
| 8 | `src/subagents/factory.py` | 修改 | 新增 `_load_single_from_db()` 按需加载 + `create_standalone_subagent` DB 兜底 ✅ |
| 9 | `src/tools/agent/delegate_tool.py` | 修改 | 委派查找路径 DB 兜底 ✅ |
| 10 | `src/main.py` | 修改 | 注册新 router + 启动时调用 `load_from_db()` ✅ |
| 11 | `frontend/src/api/agentDefinitions.ts` | **新建** | 前端 API 客户端 ✅ |
| 12 | `frontend/src/components/AgentDefinitionManager.vue` | **新建** | 智能体管理页面（列表 + 两区编辑） ✅ |
| 13 | `frontend/src/main.ts` | 修改 | 添加路由 ✅ |
| 14 | `frontend/src/components/saas/PortalLayout.vue` | 修改 | 添加"智能体管理"菜单项 ✅ |

### 阶段 2A：数据库表 + DB 访问层

> 前置依赖：Phase 1（prompt 版本管理表已就绪）

- [x] **2A.1 在 deploy/db_update.sql 和 init-postgres.sql 追加 `subagent_definitions` 表**
  - 字段：agent_id, name, description, tools(JSONB), skills(JSONB), context(JSONB), triggers(JSONB), status 等
  - system_prompt 不在此表中，由 prompt_versions 管理
  - ✅ 已完成

- [x] **2A.2 实现 `src/db/subagent_definition_db.py` DB 访问层**
  - 方法：create, get_by_id, get_by_agent_id, list_definitions, update, delete, list_active
  - 遵循项目现有 DB 访问模式（RealDictCursor + get_db_connection）
  - ✅ 已完成

### 阶段 2B：服务层 + API

> 前置依赖：2A

- [x] **2B.1 实现 `src/services/subagent_definition_service.py`**
  - 编排 SubagentDefinitionDB（定义）+ PromptRegistryService（Prompt 版本）
  - create_definition：插入定义 → 注册 prompt → 提交 V1 → 标记 production
  - update_definition：更新定义 → 如 prompt 变更则提交新版本
  - delete_definition：删定义 + 级联删 prompt
  - to_subagent_config：DB 行 → SubagentConfig（给运行时用）
  - ✅ 已完成

- [x] **2B.2 实现 `src/api/agent_definitions.py` API 路由**
  - 路由前缀：`/api/admin/agent-definitions`
  - 定义 CRUD：list, create, get, update, delete, duplicate
  - Prompt 版本管理：委托给 PromptRegistryService（versions/draft/labels）
  - ✅ 已完成

- [x] **2B.3 注册路由到 main.py**
  - ✅ 已完成

### 阶段 2C：前端

> 前置依赖：2B

- [x] **2C.1 新建 `frontend/src/api/agentDefinitions.ts` API 客户端**
  - 类型定义 + API 函数（定义 CRUD + Prompt 版本管理）
  - ✅ 已完成

- [x] **2C.2 新建 `AgentDefinitionManager.vue` 智能体管理页面**
  - 左侧列表（搜索 + 卡片）+ 右侧编辑面板
  - 编辑面板两区分离：定义区（name/tools/skills 等）+ Prompt 区（编辑器 + 版本历史）
  - ✅ 已完成

- [x] **2C.3 路由和菜单注册**
  - `main.ts` 添加 `/portal/agent-definitions` 路由
  - `PortalLayout.vue` 添加"智能体管理"菜单项
  - ✅ 已完成

### 阶段 2D：运行时集成（DB 优先，文件系统兜底）

> 前置依赖：2A
> 核心原则：DB 优先于文件系统。DB 中有定义 + system_prompt 的子智能体覆盖文件系统版本，DB 中没有的保留文件系统版本。

- [x] **2D.1 `SubagentConfig` 新增 `from_db` 标记**
  - 在 `src/models/subagent.py` 的 `SubagentConfig` 中添加 `from_db: bool = False`
  - `True` 表示来自数据库加载，`False` 表示来自文件系统
  - ✅ 已完成

- [x] **2D.2 `SubagentRegistry.load_from_db()` — DB 优先覆盖**
  - 从 subagent_definitions 查询 active 记录
  - 对每条定义，通过 `prompt_resolver.resolve()` 检查是否有 system_prompt
  - **缺 prompt 则跳过**：有定义但无 prompt → `continue`，保留文件系统版本
  - 两者都有 → 创建 `SubagentConfig(from_db=True, system_prompt=prompt_content)` **覆盖**文件系统版本
  - ✅ 已完成

- [x] **2D.3 调整 `_build_system_prompt()` 的降级逻辑**
  - **Phase 1 已实现的独立 `prompt_resolver.resolve()` 调用已移除**
  - 改为根据 `self.subagent_config.from_db` 标记决定行为：
    - `from_db=True` → 直接用 `config.system_prompt`（DB 加载时已解析）
    - `from_db=False` → 直接用 `config.system_prompt`（文件系统的 SUBAGENT.md 内容）
  - 不再在 `_build_system_prompt()` 中独立调用 `prompt_resolver.resolve()`
  - ✅ 已完成

- [x] **2D.4 启动时调用 `load_from_db()`**
  - 在 main.py 初始化阶段，master_agent 创建后调用
  - ✅ 已完成

- [x] **2D.5 运行时按需加载（`AgentFactory._load_single_from_db()`）**
  - 新增 `AgentFactory._load_single_from_db(registry, agent_id)` 方法
  - 当 registry 中找不到子智能体时，从 DB 按需加载单个并注册到 registry
  - 三个入口已接入：`create_standalone_subagent`、`_delegate_to_subagent`、`DelegateTool.execute`
  - 确保 DB-only 智能体（文件系统中无 SUBAGENT.md）也能正常加载
  - ✅ 已完成

### 阶段 2E：验证

- [ ] **2E.1 后端验证**
  - 创建子智能体 → subagent_definitions + prompt_versions V1 + production
  - 编辑 system_prompt → V2 + production 更新
  - 运行时 load_from_db() 加载正确
  - `_build_system_prompt()` 根据 `from_db` 标记正确选择来源
  - **DB 优先验证**：DB 有定义+prompt → 覆盖文件系统版本；DB 有定义无 prompt → 保留文件系统版本；DB-only 智能体（文件系统中不存在）→ 按需加载正常工作
  - [ ] 待验证

- [ ] **2E.2 前端验证**
  - 列表、创建、编辑、删除正常
  - 两区编辑：定义区和 Prompt 区独立操作
  - 版本历史面板正确展示、diff 对比和回滚正常
  - [ ] 待验证

- [x] **2E.3 前端构建验证**
  - `cd frontend && npm run build` 无编译错误
  - ✅ 已通过

### Phase 2 完成标准

- [ ] 子智能体定义存数据库（subagent_definitions），不再依赖文件系统
- [ ] 新页面 `/portal/agent-definitions` 独立运作，旧页面 `/portal/subagents` 不受影响
- [ ] Prompt 版本管理完整（提交/对比/回滚/草稿）
- [ ] 运行时 SubagentRegistry 从数据库加载子智能体（DB 优先覆盖文件系统版本）
- [ ] DB-only 智能体（文件系统中无 SUBAGENT.md）通过按需加载正常工作
- [ ] `_build_system_prompt()` 根据 `from_db` 标记选择来源，不再独立调用 prompt_resolver
- [ ] LLM 智能推荐工具/技能功能正常
- [ ] 前端构建无错误

---

## Phase 3：知识库关联配置 + 工具技能元数据 API

> 设计文档参考：§八.1.3（知识库关联配置）、§八.1.4（工具/技能元数据 API）
> 目标：子智能体支持配置知识库 source_type 过滤；前端展示可用工具/技能清单供选择
> 预计工期：1 周
> 前置依赖：Phase 2

### 阶段 3.1：移除 capabilities 字段

- [ ] **3.1.1 移除 subagent_definitions 表的 capabilities 列**
  - `deploy/db_update.sql` 添加 `ALTER TABLE subagent_definitions DROP COLUMN IF EXISTS capabilities;`
  - `deploy/init-postgres.sql` 建表语句移除 `capabilities` 字段
  - [ ] 未开始

- [ ] **3.1.2 移除代码中的 capabilities 引用**
  - `src/db/subagent_definition_db.py` — 移除 capabilities 的读写
  - `src/models/subagent.py` — SubagentConfig 移除 capabilities 字段
  - `src/subagents/loader.py` — YAML 解析移除 capabilities
  - `src/subagents/registry.py` — 移除 `_capability_index` 和 `match_by_capability()`
  - `src/core/agent.py` — 移除 `subagent_descriptions` 中的 capabilities 展示
  - `src/subagents/executor.py` — 移除 capabilities 日志
  - 前端 `AgentDefinitionManager.vue` — 移除 capabilities 编辑区
  - [ ] 未开始

### 阶段 3.2：知识库关联配置

- [ ] **3.2.1 subagent_definitions 表添加 knowledge_sources 字段**
  - `ALTER TABLE subagent_definitions ADD COLUMN IF NOT EXISTS knowledge_sources JSONB DEFAULT '[]';`
  - `deploy/init-postgres.sql` 同步更新
  - [ ] 未开始

- [ ] **3.2.2 SubagentConfig 添加 knowledge_sources 字段**
  - `src/models/subagent.py` — `knowledge_sources: List[Dict[str, str]] = field(default_factory=list)`
  - [ ] 未开始

- [ ] **3.2.3 DB 层和服务层支持 knowledge_sources 读写**
  - `src/db/subagent_definition_db.py` — create/update 读写 knowledge_sources
  - `src/services/subagent_definition_service.py` — 透传
  - [ ] 未开始

- [ ] **3.2.4 运行时注入知识库约束到 system prompt**
  - `_build_system_prompt()` 中，如果 `subagent_config.knowledge_sources` 非空，在 `{subagent_constraint_section}` 末尾追加知识库使用约束提示
  - 格式：列出允许的 source_type 和描述，指导 LLM 调用 `knowledge_base_search` 时传入正确的 source_type
  - [ ] 未开始

- [ ] **3.2.5 前端添加知识库关联配置区**
  - 在 AgentDefinitionManager.vue 的定义区增加"知识库关联"配置
  - 复选框列表，展示系统可用的 source_type 及说明
  - [ ] 未开始

### 阶段 3.3：工具/技能元数据 API

- [ ] **3.3.1 新增元数据 API**
  - `GET /api/admin/agent-definitions/meta/tools` — 返回所有工具的 id/name/description
  - `GET /api/admin/agent-definitions/meta/skills` — 返回所有技能的 id/name/description
  - `GET /api/admin/agent-definitions/meta/source-types` — 返回所有可用的 source_type 及说明
  - 数据来源：`ToolRegistry`、`SkillRegistry`、`documents` 表
  - [ ] 未开始

- [ ] **3.3.2 前端改造工具/技能选择器**
  - 从手动输入 ID 改为下拉选择列表
  - 每个选项展示 name + description
  - 工具和技能列表通过元数据 API 获取
  - [ ] 未开始

### 阶段 3.4：验证

- [ ] **3.4.1 后端验证**
  - capabilities 字段完全移除，无残留引用
  - knowledge_sources 读写正常
  - 元数据 API 返回正确的工具/技能/source_type 列表
  - 知识库约束注入到 system prompt 后 LLM 能正确使用 source_type 参数
  - [ ] 未开始

- [ ] **3.4.2 前端验证**
  - 知识库关联配置正确展示和保存
  - 工具/技能选择器展示名称和说明
  - 前端构建无错误
  - [ ] 未开始

### Phase 3 完成标准

- [ ] capabilities 字段已从数据库和代码中完全移除
- [ ] knowledge_sources 配置功能正常，可指定允许的知识库 source_type
- [ ] 运行时 knowledge_base_search 根据 knowledge_sources 配置限制检索范围
- [ ] 工具/技能元数据 API 正常工作
- [ ] 前端选择器展示工具/技能的名称和说明
- [ ] 前端构建无错误

---

## Phase 4：extra_md 迁移 + 租户前台编辑器

> 设计文档参考：§八.2（租户定制 extra.md 集成）、§八.3.2（租户前台编辑入口）
> 目标：将 extra_md 从文件系统迁移到数据库 + 租户前台编辑器
> 预计工期：1.5 周
> 前置依赖：Phase 1

### 阶段 4.1：extra_md 迁移

- [ ] **4.1.1 编写文件系统 → 数据库迁移脚本**
  - 扫描 `storage/subagents/` 下所有 `extra_<tenant_id>.md` 文件
  - 为每个文件创建 prompt_registry + prompt_versions + prompt_labels 记录
  - 支持 `--dry-run`，幂等安全
  - [ ] 未开始

- [ ] **4.1.2 改造 `_load_extra_md()` 为数据库优先**
  - 先查 PromptResolver，miss 时降级到文件系统
  - [ ] 未开始

- [ ] **4.1.3 改造 extra_md API 为数据库驱动**
  - `src/api/subagent_extra.py` 改为调用 PromptRegistryService
  - 保持 API 签名不变
  - [ ] 未开始

- [ ] **4.1.4 验证迁移和降级**
  - 数据迁移正确 + 降级路径正常 + API 兼容
  - [ ] 未开始

### 阶段 4.2：租户前台编辑器

- [ ] **4.2.1 DigitalEmployeeManager 租户卡片添加"定制提示词"按钮**
  - 点击跳转到 `/t/{tenant_id}/agent/{subagent_name}/prompt`
  - [ ] 未开始

- [ ] **4.2.2 新建 TenantPromptEditor.vue**
  - Markdown 编辑器 + 版本历史面板（复用 PromptVersionHistory.vue）
  - 草稿自动保存 + 提交版本（tenant_extra 直接标记 production）
  - [ ] 未开始

- [ ] **4.2.3 路由注册和前端构建验证**
  - [ ] 未开始

### Phase 4 完成标准

- [ ] extra_md 已迁移到数据库，降级路径正常
- [ ] 租户管理员可在线编辑定制 Prompt
- [ ] 版本管理闭环完整（编辑 → 提交 → 对比 → 回滚）
- [ ] 前端构建无错误

---

## 总体进度追踪

| Phase | 内容 | 预计工期 | 状态 |
|-------|------|---------|------|
| Phase 0 | Prompt 内容优化（P0~P3） | 2 天 | ✅ 已完成 |
| Phase 1 | 版本管理数据库 + 基础服务层 | 1 周 | ✅ 代码完成（e2e 测试通过） |
| Phase 2 | 独立智能体管理页面（重做） | 1.5 周 | 🔧 代码完成，待验证 |
| Phase 3 | 知识库关联配置 + 工具技能元数据 | 1 周 | ⬜ 未开始 |
| Phase 4 | extra_md 迁移 + 租户前台编辑器 | 1.5 周 | ⬜ 未开始 |

**总工期：约 5 周**

### 关键里程碑

| 里程碑 | 完成标志 | 对应任务 |
|--------|---------|---------|
| M0: Prompt 优化完成 | 透明化矛盾修复 + usage_guide 精简 | Phase 0 ✅ |
| M1: 版本管理可用 | 数据库表 + 服务层 + API 可工作 | Phase 1 |
| M2: 智能体管理独立页面 | DB 定义 + 两区编辑 + LLM 智能推荐 + 版本管理 | Phase 2 |
| M3: 知识库关联 + 工具展示 | knowledge_sources 配置 + 工具/技能元数据 API | Phase 3 |
| M4: 租户编辑器可用 | extra_md 迁移 + 租户前台编辑器 | Phase 4 |

### 依赖关系

```
Phase 0 ─── ✅ 已完成
Phase 1 ─── 无前置，可立即开始
Phase 2 ─── 依赖 Phase 1（需要版本管理服务层）
Phase 3 ─── 依赖 Phase 2（需要智能体管理页面和 subagent_definitions 表）
Phase 4 ─── 依赖 Phase 1（需要版本管理服务层），可与 Phase 3 并行
```

### 风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 数据库与文件系统不一致 | 多 Worker 加载到不同 Prompt | 双写机制 + 降级测试 |
| 版本数量膨胀 | 数据库存储压力 | 版本保留策略（默认保留最近 100 个版本） |
| LLM 推荐不准确 | 工具/技能配置不合理 | 推荐结果供管理员确认，不自动保存 |
| 两区分离 UI 改动量大 | DigitalEmployeeManager.vue 改造影响现有功能 | 分步改造，先加 Prompt 区，再调定义区 |

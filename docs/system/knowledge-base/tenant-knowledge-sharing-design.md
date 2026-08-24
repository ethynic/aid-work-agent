# 租户间知识库共享设计

## 一、背景与目标

企业智能体平台的多租户 SaaS 模式中，各租户（企业）自行维护自己的知识库（按 `knowledge_categories` 分类、文档存 `documents` 表）。实际业务中存在**知识复用**需求：某租户（A）整理好的高质量知识库，其他租户（B）也希望能在自己的数字员工问答中检索到，而不必重复上传整理。

**目标**：实现租户间知识库共享。平台管理员在管理后台为 B 租户接入 A 租户的知识库分类后，B 租户的数字员工检索知识库时，既能搜本租户内容，也能搜到 A 租户共享的内容。共享只读，仅限平台管理员操作，租户不可自行操作。

**范围约束**：
- 共享粒度：**按知识库分类（source_type）**，A 租户某个分类整体共享给 B。
- 授权层级：**租户级共享授权 + 数字员工级启用**。A→B 的共享关系建一次（租户级），B 的每个数字员工在各自知识库关联弹框中独立勾选是否启用该共享分类。
- 平台管理员在管理后台两步操作，租户无操作入口。

## 二、术语与概念

| 术语 | 说明 |
|------|------|
| **知识库接入**（KB 接入） | 第一步操作：平台管理员在 **B 租户基本信息** 中，为 B 租户「接入」其他租户（A）的知识库，建立 **A→B 租户级共享授权**。 |
| **知识库启用**（关联） | 第二步操作：在 **B 租户数字员工授权 - 知识库关联弹框** 中，勾选本租户分类 + A 租户的知识库分类，决定该数字员工实际可检索的库。 |
| **共享提供方**（from_tenant / A） | 知识库内容的拥有租户。 |
| **共享接收方**（to_tenant / B） | 被接入共享知识库的租户。 |
| **共享分类** | 数字员工启用清单中来自其他租户的分类项：`(owner_tenant_id=A, source_type=X)`，表示 B 的数字员工可检索 A 租户的 X 分类。由第二步勾选决定，租户级授权表不记录分类。 |

**两级模型关系**：

```
租户级共享授权（第一步，B 基本信息配置，只建一次）
    A 租户知识库 --整体授权给--> B 租户（不涉及具体分类）
        │
        ▼
数字员工级启用（第二步，每个数字员工独立配置）
    子智能体 S 的启用清单 = 本租户分类 + 已接入租户(A)选定的分类(owner_tenant_id=A)
        │
        ▼
运行时检索（B 租户的子智能体 S 检索 source_type=X）
    范围 = (B, X) 本租户 ∪ (A, X) 已启用共享分类
```

## 三、现状分析（相关链路）

### 3.1 数据模型

| 表 | 关键字段 | 说明 |
|----|---------|------|
| `documents` | `tenant_id TEXT`、`source_type TEXT`、`user_id` | 租户隔离基于 `documents.tenant_id` 单值 |
| `knowledge_categories` | `tenant_id`、`source_type`、`display_name`、`UNIQUE(tenant_id, source_type)` | 租户自己的知识库分类 |
| `subagent_knowledge_sources` | `tenant_id`、`subagent_name`、`sources JSONB`、`UNIQUE(tenant_id, subagent_name)` | 每租户每子智能体关联的知识库清单 `[{source_type, display_name}]` |
| `chunks` / `chunks_vec` | `chunk_id`、`text`、`text_vec` / `embedding vector(1024)` | **无 tenant_id**，靠 JOIN `documents` 过滤 |

### 3.2 检索流程

```
KnowledgeBaseTool.execute  (src/tools/knowledge/knowledge_base_tool.py)
  └─ 从 current_tool_execution_context() 读 tenant_id
  └─ HybridRetriever.retrieve(query, top_k, tenant_id, source_type)
       ├─ VectorDBPostgreSQL.search   (src/knowledge/vector_db/vector_db.py)  向量检索
       │     JOIN documents d ... WHERE d.tenant_id=%s [AND d.source_type=%s]
       └─ _postgres_fts_search        (src/knowledge/retriever/hybrid_retriever.py)  FTS
             JOIN documents d ... WHERE d.tenant_id=%s [AND d.source_type=%s]
  └─ 标题回查 SELECT id,title,file_path FROM documents WHERE id IN (...) AND tenant_id=%s
```

关键：租户过滤均为 `d.tenant_id = 单值`，无 tenant_id 时只查 `(tenant_id='demo' OR IS NULL)`（防泄露）。三处（向量检索、FTS、标题回查）均需改造。

### 3.3 知识库工具与 Prompt 注入

- `KnowledgeBaseTool`（`knowledge_base_search`）：入参 `query/top_k/source_type`，`source_type` 由 LLM 从 prompt 注入的清单中选择。
- `_load_knowledge_sources()`（`src/core/agent.py`）：读 `SubagentKnowledgeSourceDB.get(tenant_id, subagent_name)`，注入 `## 可用知识库` 清单，提示 LLM 传入正确的 `source_type`。

### 3.4 管理后台

- `TenantMgmt.vue`：租户管理列表 → 编辑租户弹框，Tab 页 `basic`（基本信息）/ `agents`（数字员工授权）/ `migration` / `activation`。
- 数字员工授权 Tab 每行有「知识库」按钮 → 知识库关联弹框：加载 `listTenantKnowledgeCategories(B)`（本租户分类）+ `getSubagentKnowledgeSources(B, subagent)`（已关联），勾选后 `setSubagentKnowledgeSources` 全量覆盖。
- 后端 API：`/api/saas/tenant/subagent-knowledge/{subagent_name}` GET/PUT/DELETE（`require_admin`，tenant_id 从 `get_current_tenant_id()` 读）。分类 API：`/api/knowledge/categories`。

## 四、总体设计

### 4.1 两级模型

**第一步（租户级共享授权）**：新增 `tenant_knowledge_shares` 表，记录 `from_tenant_id -> to_tenant_id` 的**租户级**授权，**不涉及具体分类**。平台管理员在 B 租户基本信息中配置「知识库接入」：选择来源租户 A，即建立 A→B 租户级共享授权（`to_tenant_id=B` 固定为当前编辑租户）。

**第二步（数字员工级启用）**：`subagent_knowledge_sources.sources` 项扩展 `owner_tenant_id` 字段（`null`/缺省 = 本租户分类，非空 = 共享自该租户）。B 数字员工知识库关联弹框可勾选「本租户分类」+「已接入租户（A）的具体知识库分类」，**本次勾选才决定共享哪些分类**。

### 4.2 检索范围语义（核心）

对 B 租户的子智能体 S，其启用清单为：
```
enabled = { (null, source_type) 本租户分类 }
          ∪ { (from_tenant_id, source_type) | S 已启用的共享分类 }
```

LLM 传入 `source_type=X` 时，检索范围 = **本租户 X 分类** ∪ **所有已启用共享分类中 source_type=X 的分类**（来自不同租户）：

```
range = { (B, X) } ∪ { (F, X) | (F, X) ∈ enabled }
```

B 租户可同时接入**多个来源租户**（如 A1、A2、A3），`enabled` 清单包含来自各来源租户的分类项，检索范围自然合并全部来源。

> 设计决策：同名 `source_type` 跨租户合并检索（多知识源合并），LLM 无需感知来源租户，只需传 `source_type`，实现简单、对 LLM 友好。检索结果保留来源标注（见 §6.4）。如需「只要 A 不要 B」的精确来源控制，作为可选扩展（工具入参增加 `owner_tenant_id`），本期不做。

**共享范围来源与安全**：检索的共享范围**不信任 LLM/前端传入**，由工具层从 DB 读取数字员工启用清单（含 `owner_tenant_id`），校验来源租户存在有效租户级授权（`tenant_knowledge_shares`）且分类属于来源租户后生成，防止越权检索任意租户（见 §6.5）。

## 五、数据模型

### 5.1 新增 `tenant_knowledge_shares` 表（租户级共享授权）

```sql
CREATE TABLE IF NOT EXISTS tenant_knowledge_shares (
    id SERIAL PRIMARY KEY,
    from_tenant_id TEXT NOT NULL,     -- 知识库提供租户（A）
    to_tenant_id TEXT NOT NULL,       -- 知识库接收租户（B）
    created_by TEXT,                  -- 创建人（平台管理员 user_id）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (from_tenant_id, to_tenant_id)
);
```

- **授权粒度是租户级**：`(from_tenant_id, to_tenant_id)` 一条记录表示 A 租户整个知识库对 B 租户可见；具体共享哪些分类由第二步（数字员工启用清单）决定，不在本表记录。
- 无外键约束（遵循 database_dev.md 规范，外键引用完整性在应用层校验）。
- 不存公司名快照：展示名一律实时查 `tenants` 表（§6.4 注入、§7.1 GET 均如此），避免来源租户改名后快照失真。
- 删除授权：删除记录即撤销；B 侧数字员工已启用的共享项在检索时因校验失败自动失效。

### 5.2 `subagent_knowledge_sources.sources` 结构扩展

`source` 项增加 `owner_tenant_id`（可选）：
```json
[
  { "source_type": "product",  "display_name": "商品库", "owner_tenant_id": null },
  { "source_type": "industry", "display_name": "行业库", "owner_tenant_id": "tenant_A" }
]
```

- 兼容旧数据：`owner_tenant_id` 缺失/为 `null` 视为本租户分类，旧数据无需迁移。
- 无 DDL 变更，仅 JSONB 语义扩展。

### 5.3 数据库变更文件

| 文件 | 变更 |
|------|------|
| `deploy/init-postgres.sql` | 新增 `tenant_knowledge_shares` 建表语句 |
| `deploy/db_update.sql` | 追加 `CREATE TABLE IF NOT EXISTS tenant_knowledge_shares ...`（注释注明日期与说明） |

## 六、检索链路改造

### 6.1 检索范围参数

`HybridRetriever.retrieve` / `VectorDBPostgreSQL.search` / `_postgres_fts_search` 的租户过滤由「单值 `tenant_id` + 可选 `source_type`」扩展为「**范围表达式**」：

新增参数 `shared_ranges: List[Tuple[str, str]]`，元素为 `(from_tenant_id, source_type)`，来自当前数字员工**已启用的共享分类**（精确对，两个元素均必填；LLM 传了 `source_type=X` 时取启用清单中 `source_type=X` 的项，未传时取全部启用项）。本租户仍用原 `tenant_id` + `source_type` 表达。

> **设计约束：共享范围不允许出现"某来源租户全部分类"的形式。** LLM 未传 `source_type` 时，本租户搜全部分类，共享侧仍只搜已启用的各 `(F, X)` 精确对。否则会绕过第二步（数字员工级启用）清单，检索到未授权分类，违反 §4.2 的越权防护。

**SQL 形态**（向量检索 / FTS / 标题回查三处统一）：

```sql
-- LLM 传了 source_type=X：
WHERE (d.tenant_id = %s AND d.source_type = %s)        -- 本租户 X
   OR (d.tenant_id = %s AND d.source_type = %s)        -- 共享库 F1 的 X
   OR (d.tenant_id = %s AND d.source_type = %s)        -- 共享库 F2 的 X
-- 动态拼接，参数列表动态添加

-- LLM 未传 source_type（本租户搜全部，共享侧仍按已启用的精确对）：
WHERE d.tenant_id = %s                                  -- 本租户全部
   OR (d.tenant_id = %s AND d.source_type = %s)         -- 已启用的共享对 (F1, X1)
   OR (d.tenant_id = %s AND d.source_type = %s)         -- 已启用的共享对 (F2, X2)
```

保留 demo 模式分支（无 tenant_id 时 `(tenant_id='demo' OR IS NULL)`），不受影响。

### 6.2 改造点

| 文件 | 改动 |
|------|------|
| `src/knowledge/retriever/hybrid_retriever.py` | `retrieve` / `_postgres_fts_search` / `_build_results`（doc_id 回查）支持 `shared_ranges` |
| `src/knowledge/vector_db/vector_db.py` | `VectorDBPostgreSQL.search` 向量检索 SQL 支持 `shared_ranges` |
| `src/tools/knowledge/knowledge_base_tool.py` | `execute` 构造并校验 `shared_ranges` 后传给 retriever；标题回查 SQL 同步扩展 |

> `src/knowledge/service.py` 的 `search_documents` **不在改造范围**：它只服务于 `/api/knowledge/search_documents`（B 租户知识库管理页搜索），无数字员工上下文，无法构造启用清单，保持只搜本租户。共享检索仅发生在数字员工问答链路（工具层）。若未来管理页也需搜共享内容，需单独定义范围（如 B 已在任一数字员工启用的共享分类的并集），作为可选扩展（见 §11）。

### 6.3 共享范围来源（工具层）

`KnowledgeBaseTool.execute` 步骤：
1. 从 `current_tool_execution_context()` 读 `tenant_id` 和 `subagent_id`（两者均为 `ToolExecutionContext` **现有字段**；`subagent_id` 由 Agent 构造时填充为 `subagent_config.dir_name`，与 `SubagentKnowledgeSourceDB` 的 key 一致，无需新增字段）。
2. 单条 SQL 一次往返取「启用清单 + 有效授权」，不引入快照/缓存（授权判断必须读权威表，撤销立即生效）：

   ```sql
   SELECT
     (SELECT sources FROM subagent_knowledge_sources
      WHERE tenant_id = %s AND subagent_name = %s) AS sources,
     COALESCE((SELECT json_agg(from_tenant_id) FROM tenant_knowledge_shares
      WHERE to_tenant_id = %s), '[]'::json) AS share_owners
   ```

3. Python 侧取 `sources` 中 `owner_tenant_id` 非空的项，与 `share_owners` 求交集，生成 `shared_ranges`（精确 `(F, X)` 对，见 §6.1）。不在交集内的项（授权已撤销）静默剔除。
4. 调用 `retriever.retrieve(query, top_k, tenant_id, source_type, shared_ranges)`。

> 检索时**不校验** `source_type` 是否仍属于来源租户 `knowledge_categories`：该校验只在 `PUT subagent-knowledge` 保存时做（防保存垃圾数据）。来源租户后来删除分类 X，B 搜 `(A, X)` 自然返回 0 条，无害。
>
> 校验放在服务端，撤销授权 / 删除租户后立即失效，前端即使残留 owner 项也不会越权检索。
>
> 主智能体不参与共享检索：`_load_knowledge_sources()` 仅对子智能体生效（现状），主智能体工具上下文 `subagent_id` 为空，`shared_ranges` 恒为空，行为与现状一致。

### 6.4 system prompt 注入改造

`_load_knowledge_sources()`（`src/core/agent.py`）解析 sources 时区分来源：
- 本租户项：`- product（商品库）`
- 共享项：`- industry（A公司 · 行业库）`（`owner_tenant_id` 查 `tenants` 表转公司名，查不到用租户 ID 兜底）

检索结果格式化时，共享库结果附来源标注（如 `metadata.owner_tenant_id` / `doc_title` 前缀），供 LLM 感知内容来源。

### 6.5 安全与边界

| 项 | 设计 |
|----|------|
| 共享只读 | B 只能检索 A 的文档（vector/FTS/标题/正文回查全部限范围），不能修改/删除/管理 A 的知识库分类与文档。注意：`/api/knowledge/documents/{doc_id}/download` 现状无租户过滤（存量漏洞），必须随本需求修复，否则 B 可凭检索结果中的 doc_id 下载 A 原文（见 §11） |
| 越权防护 | `shared_ranges` 由工具层单条 SQL 读取启用清单并与有效授权求交集生成（§6.3），不信任 LLM/前端传入的租户；共享范围只含精确 `(F, X)` 对，无"全部分类"形式 |
| 授权撤销 | 删除 `tenant_knowledge_shares` 记录后，B 侧检索即失效（授权交集为空，读权威表、无快照，见 §6.3） |
| 来源租户状态不影响读取 | A 租户即使停用（`suspended`）也不影响 B 租户读取其共享知识库资料；共享有效性只依赖 `tenant_knowledge_shares` 授权记录与文档数据本身，**不校验来源租户状态**。仅当来源租户/文档被物理清理时共享才失效 |
| 计费归属 | B 发起的检索，embedding 计费归属 B 的会话记录（现有 `record.add_embedding_usage` 链路，无需改） |
| demo/无租户模式 | 不受影响，保留原分支 |

## 七、后端 API 设计

### 7.1 共享授权 API（新增，平台管理员）

前缀：`/api/saas/tenant/knowledge-shares`（`require_admin`，`to_tenant_id` 从 `get_current_tenant_id()` 读，即当前编辑租户 B）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/saas/tenant/knowledge-shares` | 返回 B 已接入的来源租户列表：`[{from_tenant_id, from_company_name}]`（公司名实时查 `tenants` 表，无快照） |
| PUT | `/api/saas/tenant/knowledge-shares` | 全量覆盖 B 已接入的来源租户列表 `[{from_tenant_id}]`（校验来源租户存在、非本租户，租户级授权无分类） |

来源租户分类浏览（第二步弹框用）：弹框「选择来源租户 → 展示该租户分类」可复用现有 `GET /api/knowledge/categories`（前端带 `X-Tenant-Id=来源租户` 请求）。注意该路由无路由级鉴权，依赖中间件解析 tenant_id，平台管理员跨租户拉取能否放行需验证；此验证**提前至 Phase 1 启动时定案**（第二步弹框的硬依赖），若不允许则新增平台管理员专用候选接口。

### 7.2 知识库关联 API（扩展）

`GET/PUT /api/saas/tenant/subagent-knowledge/{subagent_name}`：
- `sources` 项支持 `owner_tenant_id`（可选字段）。
- PUT 时服务端校验：`owner_tenant_id` 非空的项必须满足「`(owner_tenant_id, to_tenant_id=当前租户)` 存在于 `tenant_knowledge_shares`（租户级授权）且 `source_type` 属于来源租户 `knowledge_categories`」，否则拒绝保存，防越权引用未授权租户/分类。

### 7.3 校验规则汇总

| 场景 | 校验 |
|------|------|
| PUT knowledge-shares | 来源租户存在且非本租户（租户级授权，无分类） |
| PUT subagent-knowledge | `owner_tenant_id` 非空项须有租户级授权 + `source_type` 属于来源租户 |
| 检索 | 共享范围 = 启用清单 ∩ 租户级授权（单条 SQL，§6.3）；不校验来源分类存在性（仅 PUT 时校验） |

## 八、管理后台 UI 改造（TenantMgmt.vue）

### 8.1 第一步：B 租户基本信息 - 知识库接入

`basic` Tab 底部新增「知识库接入」区块/按钮（平台管理员可编辑时显示），弹框内容：

```
┌─ 知识库接入（B公司）────────────────────────┐
│  说明：接入其他租户的知识库（租户级授权），       │
│        具体共享的分类在数字员工「知识库关联」中勾选。│
│                                              │
│  已接入（可多个来源租户）：                   │
│  ☑ A1公司（来源租户）                [移除] │
│  ☑ A2公司（来源租户）                [移除] │
│  ☑ A3公司（来源租户）                [移除] │
│                                              │
│  [＋ 添加共享租户]                            │
│    选择来源租户: [下拉/搜索 其他租户]           │
│  [取消]                      [保存]           │
└──────────────────────────────────────────────┘
```

- 保存调用 `PUT /api/saas/tenant/knowledge-shares` 全量覆盖（仅来源租户列表，无分类）。
- 支持同时接入多个来源租户（A1/A2/A3），各自的知识库分类在第二步分别选择。
- 词义：按钮/弹框标题用「知识库接入」。
- 本步**不涉及具体分类**；分类选择在第二步数字员工「知识库关联」弹框中完成。

### 8.2 第二步：数字员工授权 - 知识库关联弹框

现有知识库关联弹框（`openKnowledgeDialog`）扩展：
- 加载本租户分类（现有 `listTenantKnowledgeCategories(B)`）+ 已接入来源租户（`GET /api/saas/tenant/knowledge-shares`，`to_tenant_id=B`），并拉取每个来源租户的知识库分类（`GET /api/knowledge/categories`，带 `X-Tenant-Id=来源租户`）。
- 展示两组（或按来源租户分组）：
  - **本租户知识库**：现有分类勾选列表。
  - **其他租户共享**：`A1公司 · 行业库`、`A2公司 · 产品库` 等（按来源租户分组），可分别勾选多个来源租户的分类，勾选即确定共享对应租户的该分类。
- 保存 `setSubagentKnowledgeSources` 时，共享项带 `owner_tenant_id=来源租户`。
- 共享分类在弹框中始终显示来源公司名，防止与本租户同名分类混淆。

## 九、实现要点与影响面

| 文件 | 改动类型 |
|------|---------|
| `deploy/init-postgres.sql` / `deploy/db_update.sql` | 新增 `tenant_knowledge_shares` 表 |
| `src/db/tenant_knowledge_share_db.py`（新增） | 共享授权 DB 层：get/set（全量覆盖）/delete/list + 校验 |
| `src/saas/api/knowledge_share.py`（新增） | 共享授权 API（§7.1）。注：知识库关联 API 现位于 `src/api/subagent_knowledge_source.py`（路由 `/api/saas/tenant/subagent-knowledge`） |
| `src/db/subagent_knowledge_source_db.py` | sources 项透传 `owner_tenant_id`（轻微调整，无 DDL） |
| `src/api/subagent_knowledge_source.py` | PUT 校验 `owner_tenant_id` 授权（§7.2） |
| `src/knowledge/retriever/hybrid_retriever.py` | 检索范围支持 `shared_ranges` |
| `src/knowledge/vector_db/vector_db.py` | 向量检索 SQL 范围化 |
| `src/tools/knowledge/knowledge_base_tool.py` | 单条 SQL 取启用清单与有效授权交集、生成 `shared_ranges`，标题回查范围化 |
| `src/core/agent.py` | `_load_knowledge_sources` 共享项注入（`ToolExecutionContext.subagent_id` 已存在，无需改 `src/tools/context.py`） |
| `src/knowledge/api.py` | `/documents/{doc_id}/download` 增加租户/共享范围校验（存量漏洞修复，共享后必须） |
| `frontend/web/api/saasPermissions.ts` | 新增共享授权 API + 知识库关联 API 支持 owner |
| `frontend/web/components/saas/TenantMgmt.vue` | basic 接入弹框 + 知识库关联弹框扩展 |

**测试关注**（遵循 testing.md / dev_workflow.md 三智能体流程）：
- 检索范围 SQL：本租户命中、共享库命中、同名 source_type 合并、未传 source_type（共享侧仍限已启用精确对）、demo 模式回归。
- 安全：越权构造 `shared_ranges` 被拒、撤销授权后检索立即失效、来源租户停用**不**影响读取（回归验证）、未启用的共享分类不被检索。
- 下载：B 用户下载共享范围内文档成功、下载非共享/未启用文档被拒、跨租户越权下载被拒。
- 主智能体：无 `subagent_id` 时 `shared_ranges` 为空、检索行为与现状一致。
- 前端：接入弹框增删、知识库关联弹框两组勾选、保存回显。

## 十、开发计划（Phase）

| Phase | 内容 | 产出 |
|-------|------|------|
| **Phase 1 数据 + 共享授权 API + 接入 UI** | `tenant_knowledge_shares` 表 + DB 层 + 共享授权 API + basic Tab 知识库接入弹框 + 跨租户分类浏览权限验证（§7.1，硬依赖，启动时定案） | 平台管理员可配置 A→B 租户级共享授权 |
| **Phase 2 检索链路改造** | retriever/vector_db/tool 范围化 + 单条 SQL 取启用清单与授权交集（§6.3）+ 下载接口租户/共享范围校验修复（§6.5） | 检索层就绪（尚无入口启用共享分类，无用户可见变化） |
| **Phase 3 关联弹框 + Prompt 注入 + 联调** | 知识库关联弹框显示/启用共享分类、`subagent_knowledge_sources` owner 支持、PUT 校验、`_load_knowledge_sources` 注入来源标识 + 端到端验证 | B 数字员工可勾选并检索到 A 共享知识库 |

> Phase 顺序说明：先改造检索层、后上启用入口。若先上关联弹框与 prompt 注入，会出现「LLM 看到共享分类并调用、但检索不生效」的中间态；检索层先行则全程无用户可见的断裂行为。

## 十一、风险与待确认

| 项 | 说明 |
|----|------|
| 同名 `source_type` 合并检索 | 设计为按 `source_type` 匹配所有启用来源；如需「精确来源」扩展工具入参 `owner_tenant_id`（后续可选） |
| LLM 对共享库的感知 | 依赖 prompt 注入清单标注来源公司名；需实测 LLM 是否正确选用共享 `source_type` |
| 跨租户分类浏览权限 | `GET /api/knowledge/categories` 是否允许平台管理员带 `X-Tenant-Id=来源租户` 拉取（该路由无路由级鉴权，依赖中间件解析），Phase 1 启动时验证定案；不允许则新增平台管理员专用接口 |
| 共享库文档下载 | **已升级为 Phase 2 必做**：`/api/knowledge/documents/{doc_id}/download` 现状无任何租户过滤（存量漏洞，任意 doc_id 可下载任意租户原文），共享后 B 检索结果暴露 A 的 doc_id，等同开放 A 原文下载。修复方案：下载接口增加租户/共享范围校验；同时评估检索结果中 `file_path` 字段是否需脱敏 |
| 知识库管理页搜共享内容 | `service.search_documents`（管理页搜索路径）保持只搜本租户；如需搜共享内容需单独定义范围（如已启用共享分类的并集），本期不做（可选扩展） |

## 十二、边界与边缘情况

| 情况 | 设计行为 |
|------|---------|
| **多来源租户接入** | B 租户可同时接入多个来源租户（如 A1、A2、A3）：第一步在 B 基本信息中逐个接入；第二步数字员工弹框按来源租户分组，可分别勾选 A1/A2/A3 的知识库分类，检索时全部来源合并进入范围（§4.2）。`tenant_knowledge_shares` 每来源租户一行，`subagent_knowledge_sources.sources` 可含多个 `owner_tenant_id` 项。 |
| **来源租户状态不影响读取** | A 租户即使被停用（`suspended`），不影响 B 租户读取其共享知识库资料；共享有效性只依赖 `tenant_knowledge_shares` 授权记录与文档数据本身，不校验来源租户状态（§6.5）。仅当来源租户/文档被物理清理时共享才失效。 |
| **同名 `source_type` 跨租户合并** | 本租户与各来源租户存在同名 `source_type` 时，按 `source_type` 匹配合并检索，结果保留来源标注（§4.2）。 |
| **共享只读** | B 只能检索共享知识库，不能修改/删除/管理来源租户的知识库分类与文档（§6.5）。 |
| **授权撤销** | 删除 `tenant_knowledge_shares` 授权记录后，B 侧已启用的共享项在下次检索时因授权交集为空自动失效（§6.3 读权威表、无快照，撤销立即生效）。 |
| **来源租户无关联数字员工** | 接入某来源租户但 B 未在任何数字员工中勾选其分类时，该来源租户知识库不参与任何检索；勾选后才进入对应数字员工的检索范围。 |
| **主智能体** | 主智能体无知识库清单注入（现状 `_load_knowledge_sources` 仅对子智能体生效），工具上下文 `subagent_id` 为空时 `shared_ranges` 恒为空，不参与共享检索，行为与现状一致。 |
| **来源分类被删除** | A 删除分类 X 后，B 已启用的 `(A, X)` 项检索自然返回 0 条（检索时不校验来源分类存在性，见 §6.3）；PUT 保存时校验仍会拒绝新增对该分类的引用。 |

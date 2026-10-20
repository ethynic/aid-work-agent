# 知识库检索规范（跨租户共享）

> 适用：所有新增或修改的「搜索/读取知识库」的工具（`src/tools/`）、技能脚本（`src/skills/`、`subagents/`）、独立 API（`src/api/`）。只要检索 `documents` / `chunks` / `chunks_vec` 数据，**必须支持跨租户共享搜索**，不得只按本租户 `tenant_id` 过滤。

## 1. 共享范围唯一来源

- 权威读取函数：`load_shared_ranges(tenant_id, subagent_id, source_type)`（`src/knowledge/retriever/tenant_range.py`），内部 = 数字员工级启用（`subagent_knowledge_sources`）∩ 租户级授权（`tenant_knowledge_shares`），返回精确 `(from_tenant_id, source_type)` 对；授权撤销立即生效（无快照）。
- **不信任 LLM / 前端传入的租户**，共享范围一律由工具层从 DB 读取并校验。
- 共享侧必须按 `source_type` 过滤，只搜已启用的精确 `(F, X)` 对，禁止「某来源租户全部分类」。

## 2. 子智能体上下文

从 `current_tool_execution_context()`（`src/tools/context.py`）读取 `tenant_id` + `subagent_id`。**仅子智能体 + 租户模式生效**（`subagent_id` 非空）；主智能体（`subagent_id` 为空）退化为仅本租户（行为不回归）。

## 3. 实现模式

### 模式 A（优先使用）：走 `HybridRetriever`
参考 `src/tools/knowledge/knowledge_base_tool.py` / `src/tools/data_analysis/analysis_agent.py`：
- `shared_ranges = load_shared_ranges(tenant_id, subagent_id, source_type)`
- `retriever.retrieve(..., shared_ranges=shared_ranges)`
- 回查 `documents` 表（标题/元信息）时用 `build_tenant_range_conditions(tenant_id, source_type, shared_ranges, alias)`
- 共享侧始终按精确 `(from_tenant_id, source_type)` 对过滤，不丢失 source_type 精度，**多分类组合检索必须用本模式**

### 模式 B：工具内直接 SQL（仅限单一分类检索）
参考 `src/tools/knowledge/attraction_search_tool.py` / `hotel_search_tool.py`：
- 实现 `_build_tenant_scope(tenant_id, subagent_id)` 返回 `(sql_fragment, params)`
- `subagent_id` 非空 → `"d.tenant_id = ANY(%s)"`（聚合本租户 + 共享来源租户）；为空 → `"d.tenant_id = %s"`
- SQL 用 `{tenant_sql}` f-string 拼接 + 参数化传值（`tenant_sql` 为内部常量串，无注入风险）

**⚠️ 模式 B 前提与陷阱（缺一不可，否则会搜到共享来源租户的未授权分类）**：
1. 工具只检索**单一固定分类**：`_build_tenant_scope` 必须用该分类（如 `self.SOURCE_TYPE`）调 `load_shared_ranges`，只聚合该分类的共享来源租户
2. 检索 SQL 必须带 `d.source_type = %s`（等于该分类）硬过滤
3. 根因：`_build_tenant_scope` 把精确 `(from_tenant_id, source_type)` 对**降维成纯 tenant_id 列表**，丢失 source_type 精度。一旦检索 SQL 缺 `d.source_type` 过滤（或覆盖多分类），会把来源租户的**全部分类**搜进来（如来源启用了 a+b+c，本租户只想要 a+b、自建更优的 c1，结果却混入来源 c）
4. **检索覆盖多个 source_type 的组合入口，禁止用本模式**，必须走模式 A（`shared_ranges` 精确对）或 `build_tenant_range_conditions` 精确 OR 条件

### 模式 C：独立 API（无子智能体上下文）
参考 `src/api/travel_quote.py`：
- 聚合该租户**所有**子智能体的共享范围：`_resolve_travel_shared_tenant_ids(tenant_id, source_type)`（枚举 `subagent_knowledge_sources` 全部 `subagent_name` → `load_shared_ranges` → 聚合去重）
- 底层 retriever 提供可选 `shared_tenant_ids` 覆盖参数（不传时行为不变），参考 `src/skills/travel-quote/scripts/hotel_retriever.py` / `attraction_retriever.py`

## 4. 本租户栏目授权（2026-09-20）

- 权威读取函数：`load_authorized_source_types(tenant_id, subagent_id)`（`tenant_range.py`），读 `subagent_knowledge_sources` 中本租户自有栏目项（`owner_tenant_id` 为空）。**返回 None = 未配置（允许全部栏目，默认）；返回列表 = 仅允许列表内栏目**。空自有项（无行 / 仅共享项 / 勾选被清空）均视为未配置。
- 收口统一走 `resolve_category_scope(tenant_id, subagent_id, requested_source_type)`：传参未授权 → 拒绝并返回各授权栏目文档数（帮助 LLM 自纠）；未传参 → 收窄为授权集合（list），`build_tenant_range_conditions` 对 list 生成 `source_type = ANY(%s)`。
- 仅子智能体 + 租户模式生效；主智能体 / 无租户上下文不受限（返回 None）。
- 共享侧不受自有授权影响，恒为 `load_shared_ranges` 精确对。**注意**：source_type 为收窄 list 时调 `load_shared_ranges` 必须传 None（其 source_type 参数仅支持 str 等值过滤；精确对本身已约束共享侧）。
- 已收口入口：`knowledge_base_search` / `knowledge_file_search`。豁免：`analysis_agent` 数据表发现/加载（`[数据表]` 标题前缀移动兜底，见 [栏目授权设计](../../docs/system/knowledge-base/subagent-kb-category-authorization-design.md) §4.4）。**栏目豁免不豁免表级名单**：数据分析相关入口仍受 §4.1 的 `allowed_agent_ids` 约束。
- schema_saver 去重不限定 source_type（`title = '[数据表] ' || table_name` + table_name/source_info 判重），表文档被移动后不重复注册。

## 4.1 数据表表级名单（2026-10-20）

- 权威模块：`src/services/data_analysis/access_scope.py`——名单标准化（`read_stored_agent_scope`/`normalize_agent_scope_ids`）、单表判断、SQL 条件（`agent_scope_visible_sql`、`agent_scope_document_filter_sql`）、有效数据表识别（`valid_data_table_condition`）。字段为数据表 metadata 保留键 `allowed_agent_ids`：缺失/null/`[]` = 本租户全部智能体共享；非空数组 = 仅名单内**本租户**智能体可用；异常形态 fail-closed（不当成共享）。
- 跨租户关系：限定表必须 `tenant_id` = 当前租户，即使两个租户存在同名 agent_id 也不放行；跨租户共享仅对**空名单**表保留原有行为。身份未知（有租户、无可信工具身份）只可见共享表，不得映射 main；身份一律取 `ToolExecutionContext`（`subagent_id` 空且上下文存在 → main），不取 LLM/前端参数。
- **cast 安全**：`metadata::jsonb` 只允许作用于已识别为数据表形态的行（source_type 判等或 `[数据表] ` 标题前缀先收窄）；普通知识文档行不得进入 cast 路径，否则单行损坏 JSON 会使整个检索 500。
- 检索透传：`HybridRetriever.retrieve` / `VectorDatabase.search` / FTS / `search_documents_by_title` 均有可选 `agent_id`，名单条件在租户范围 OR 括号外 AND、排名/LIMIT 前过滤；不传时仅排除非空名单记录，其余文档行为不变。分析产物复用按 `source_table_ids` 完整来源集合判定（超限/未知 → null 不复用），见 analysis_artifacts/analysis_agent。
- 管理端写入口径（角色、显式清空/省略、重复注册保持）见数据表按智能体配置设计与计划，不在此重复。

## 5. 检查清单（新增/修改检索入口必查）

- [ ] 检索 SQL / retriever 是否只按本租户过滤？（必须支持共享）
- [ ] 候选 SQL 是否带文档可见性过滤 `build_active_document_condition()`（`tenant_range.py`，status='active' 且未过期，排序/LIMIT 前）？核心检索（vector_db/hybrid_retriever）已内置；新增直接 SQL 检索入口必须复用
- [ ] `subagent_id` 是否从 `current_tool_execution_context()` 读取？
- [ ] 共享范围是否经 `load_shared_ranges` 从 DB 读取（不信任 LLM/前端）？
- [ ] 主智能体（`subagent_id` 为空）是否退化为本租户？
- [ ] 共享侧是否按 `source_type` 精确过滤？
- [ ] 检索 SQL 是否带 `source_type` 硬过滤（模式 B 缺它会把来源租户全部分类搜进来）？
- [ ] 本租户栏目授权是否经 `resolve_category_scope` 收口（传未授权栏目拒绝、未传参收窄为授权集合）？
- [ ] 涉及数据表可见性的入口是否叠加 §4.1 表级名单（`agent_id` 从可信上下文取、cast 只落数据表行、限定分支绑定当前租户）？
- [ ] 新增单测覆盖共享路径（subagent 命中共享 / 无 subagent 退化本租户）与栏目授权路径（拒绝 / 收窄 / 未配置透传）

## 6. 已实现入口（参考）

| 入口 | 模式 |
|------|------|
| `knowledge_base_tool.py`（knowledge_base_search，内容语义检索） | A + §4.1 表级名单 |
| `knowledge_file_search_tool.py`（knowledge_file_search，按文件名定位文档，底层 `KnowledgeService.search_documents_by_title`） | A + §4.1 表级名单 |
| `analysis_agent.py`（search_data_tables / list_data_tables / load_table） | A + SQL 租户范围 + §4.1 表级名单 |
| `attraction_search_tool.py` / `hotel_search_tool.py` | B |
| travel_quote 独立 API + hotel/attraction retriever | C |

非检索消费方：`cp_tool.py` 的文件交付（2026-10-10）对来源租户的
`knowledge/{source_type}/` 文件按 `load_shared_ranges` 精确对放行复制
（`extract_knowledge_refs` 解析 + fail-closed，仅子智能体 + 租户模式生效），
与检索/读取同一授权边界；conversation 等非 knowledge 场景外来路径仍一律拒绝。

**旧版平铺路径兜底（2026-10-10，agent1 仿真修复）**：存量文档磁盘与
`documents.file_path` 登记多为 `knowledge/{filename}` 平铺形态（无
source_type 段），路径无法确认授权归属。`extract_flat_knowledge_ref`
识别该形态后，由 `resolve_source_types_by_file`（tenant_range.py）按
owner 目录名 + `RIGHT(file_path)` 后缀精确回查 documents 登记确定
source_type（仅认可 active 且未过期登记，与检索侧可见性同界）；
回查结果仍与 `load_shared_ranges` 精确对比对，任一登记命中即放行，
查无登记或 DB 异常一律拒绝（fail-closed）。

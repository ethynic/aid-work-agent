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

## 4. 检查清单（新增/修改检索入口必查）

- [ ] 检索 SQL / retriever 是否只按本租户过滤？（必须支持共享）
- [ ] `subagent_id` 是否从 `current_tool_execution_context()` 读取？
- [ ] 共享范围是否经 `load_shared_ranges` 从 DB 读取（不信任 LLM/前端）？
- [ ] 主智能体（`subagent_id` 为空）是否退化为本租户？
- [ ] 共享侧是否按 `source_type` 精确过滤？
- [ ] 检索 SQL 是否带 `source_type` 硬过滤（模式 B 缺它会把来源租户全部分类搜进来）？
- [ ] 新增单测覆盖共享路径（subagent 命中共享 / 无 subagent 退化本租户）

## 5. 已实现入口（参考）

| 入口 | 模式 |
|------|------|
| `knowledge_base_tool.py` | A |
| `analysis_agent.py`（search_data_tables / list_data_tables / load_table） | A + SQL 租户范围 |
| `attraction_search_tool.py` / `hotel_search_tool.py` | B |
| travel_quote 独立 API + hotel/attraction retriever | C |
